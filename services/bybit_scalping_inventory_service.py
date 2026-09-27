"""Owned-inventory ledger for Bybit Spot orders placed by the scalping terminal.

An order-link id is reserved before the broker request. Execution events that
arrive before the REST acknowledgement are held unapplied until the acknowledgement
maps that order-link id to its broker order id.
"""

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from uuid import uuid4

from database import scalping_db
from database.scalping_db import (
    BybitScalpingSpotExecution,
    BybitScalpingSpotInventory,
    BybitScalpingSpotOrder,
    db_session,
    engine,
)
from utils.event_bus import bus
from utils.logging import get_logger

logger = get_logger(__name__)

_DECIMAL_PRECISION = 50
_UNMATCHED_EXECUTION_RETENTION = timedelta(minutes=5)
_TERMINAL_ORDER_STATES = {"cancelled", "complete", "rejected"}
_listener_started = False


class InsufficientScalpingInventory(ValueError):
    """Raised when a Spot sell would use inventory not acquired by scalping."""

    def __init__(self, requested_quantity: Decimal, available_quantity: Decimal):
        self.requested_quantity = requested_quantity
        self.available_quantity = available_quantity
        super().__init__(
            "Sell quantity exceeds scalping-owned Spot inventory "
            f"(requested {requested_quantity}, available {available_quantity})"
        )


@contextmanager
def _session_scope(immediate: bool = False):
    """Use a fresh scoped-session connection and always return it to NullPool."""
    session = db_session()
    try:
        if immediate and engine.dialect.name == "sqlite":
            # Serialize sell reservations and execution application before
            # reading balances; SQLite otherwise starts a deferred transaction.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        db_session.remove()


def _decimal(value, field_name: str, *, positive: bool = False) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite decimal") from exc
    if not result.is_finite() or (positive and result <= 0):
        qualifier = "positive " if positive else ""
        raise ValueError(f"{field_name} must be a finite {qualifier}decimal")
    return result


def _stored_decimal(value: str | None) -> Decimal:
    return Decimal(value or "0")


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _mode(value: str) -> str:
    normalized = str(value or "").lower()
    if normalized not in {"analyze", "live"}:
        raise ValueError("mode must be 'analyze' or 'live'")
    return normalized


def _normalized_order_fields(
    *,
    user_id: str,
    symbol: str,
    exchange: str,
    mode: str,
    side: str,
    quantity,
    base_coin: str,
    market_unit: str,
):
    normalized_side = str(side or "").upper()
    normalized_market_unit = str(market_unit or "")
    if normalized_side not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")
    expected_unit = "quoteCoin" if normalized_side == "BUY" else "baseCoin"
    if normalized_market_unit != expected_unit:
        raise ValueError(f"Bybit Spot {normalized_side} must use market_unit={expected_unit}")
    normalized_user = str(user_id or "").strip()
    normalized_symbol = str(symbol or "").strip().upper()
    normalized_exchange = str(exchange or "").strip().upper()
    normalized_base = str(base_coin or "").strip().upper()
    if not all((normalized_user, normalized_symbol, normalized_exchange, normalized_base)):
        raise ValueError("user_id, symbol, exchange, and base_coin are required")
    return {
        "user_id": normalized_user,
        "symbol": normalized_symbol,
        "exchange": normalized_exchange,
        "mode": _mode(mode),
        "side": normalized_side,
        "quantity": _decimal(quantity, "quantity", positive=True),
        "base_coin": normalized_base,
        "market_unit": normalized_market_unit,
    }


def _order_dict(order: BybitScalpingSpotOrder) -> dict:
    return {
        "order_link_id": order.order_link_id,
        "order_id": order.order_id,
        "user_id": order.user_id,
        "mode": order.mode,
        "symbol": order.symbol,
        "exchange": order.exchange,
        "base_coin": order.base_coin,
        "side": order.side,
        "requested_quantity": order.requested_quantity,
        "market_unit": order.market_unit,
        "status": order.status,
        "filled_quantity": order.filled_quantity,
    }


def reserve_bybit_scalping_spot_order(
    user_id: str,
    symbol: str,
    exchange: str,
    mode: str,
    side: str,
    quantity,
    base_coin: str,
    market_unit: str,
    order_link_id: str | None = None,
) -> dict:
    """Reserve a scalping Spot order before submitting it to Bybit.

    The returned ``order_link_id`` must be sent unchanged as Bybit's
    ``orderLinkId``. BUY quantity is a quote-currency budget and is not used as
    base inventory. SELL quantity is a base-coin amount and is reserved against
    inventory acquired by scalping only.
    """
    fields = _normalized_order_fields(
        user_id=user_id,
        symbol=symbol,
        exchange=exchange,
        mode=mode,
        side=side,
        quantity=quantity,
        base_coin=base_coin,
        market_unit=market_unit,
    )
    link_id = str(order_link_id or f"sc-{uuid4().hex}").strip()
    if not link_id or len(link_id) > 36:
        raise ValueError("order_link_id must contain 1 to 36 characters")

    with localcontext() as context, _session_scope(immediate=True) as session:
        context.prec = _DECIMAL_PRECISION
        existing = (
            session.query(BybitScalpingSpotOrder)
            .filter_by(
                user_id=fields["user_id"],
                mode=fields["mode"],
                order_link_id=link_id,
            )
            .first()
        )
        if existing is not None:
            same_intent = (
                existing.symbol == fields["symbol"]
                and existing.exchange == fields["exchange"]
                and existing.base_coin == fields["base_coin"]
                and existing.side == fields["side"]
                and existing.requested_quantity == _decimal_text(fields["quantity"])
                and existing.market_unit == fields["market_unit"]
            )
            if same_intent and existing.status == "reserved":
                return _order_dict(existing)
            raise ValueError("order_link_id has already been used for another order")

        if fields["side"] == "SELL":
            inventory = (
                session.query(BybitScalpingSpotInventory)
                .filter_by(
                    user_id=fields["user_id"],
                    symbol=fields["symbol"],
                    exchange=fields["exchange"],
                    mode=fields["mode"],
                )
                .with_for_update()
                .first()
            )
            available = Decimal("0")
            if inventory is not None:
                if inventory.base_coin != fields["base_coin"]:
                    raise ValueError("base_coin does not match the scalping inventory ledger")
                available = max(
                    Decimal("0"),
                    _stored_decimal(inventory.owned_quantity)
                    - _stored_decimal(inventory.reserved_quantity),
                )
            if fields["quantity"] > available:
                raise InsufficientScalpingInventory(fields["quantity"], available)
            inventory.reserved_quantity = _decimal_text(
                _stored_decimal(inventory.reserved_quantity) + fields["quantity"]
            )

        order = BybitScalpingSpotOrder(
            user_id=fields["user_id"],
            mode=fields["mode"],
            symbol=fields["symbol"],
            exchange=fields["exchange"],
            base_coin=fields["base_coin"],
            side=fields["side"],
            order_link_id=link_id,
            requested_quantity=_decimal_text(fields["quantity"]),
            market_unit=fields["market_unit"],
            status="reserved",
            filled_quantity="0",
        )
        session.add(order)
        session.flush()
        return _order_dict(order)


def _event_value(event, name: str, default=""):
    if isinstance(event, dict):
        return event.get(name, default)
    return getattr(event, name, default)


def _normalize_execution_event(event) -> dict | None:
    broker = str(_event_value(event, "broker")).lower()
    exchange = str(_event_value(event, "exchange")).upper()
    side = str(_event_value(event, "side")).upper()
    user_id = str(_event_value(event, "user_id")).strip()
    exec_id = str(_event_value(event, "exec_id")).strip()
    order_id = str(_event_value(event, "order_id")).strip()
    order_link_id = str(_event_value(event, "order_link_id")).strip()
    symbol = str(_event_value(event, "symbol")).strip().upper()
    fee_currency = str(_event_value(event, "fee_currency")).strip().upper()

    if (
        broker != "bybit"
        or exchange != "CRYPTO"
        or side not in {"BUY", "SELL"}
        or not all((user_id, exec_id, order_id, symbol))
    ):
        return None
    try:
        quantity = _decimal(_event_value(event, "quantity"), "execution quantity", positive=True)
        price = _decimal(_event_value(event, "price"), "execution price", positive=True)
        fee_amount = _decimal(_event_value(event, "fee_amount"), "execution fee")
    except ValueError:
        return None
    return {
        "user_id": user_id,
        "exec_id": exec_id,
        "order_id": order_id,
        "order_link_id": order_link_id,
        "symbol": symbol,
        "exchange": exchange,
        "side": side,
        "quantity": quantity,
        "price": price,
        "fee_amount": fee_amount,
        "fee_currency": fee_currency,
        "broker": broker,
    }


def _execution_matches_order(
    execution: BybitScalpingSpotExecution, order: BybitScalpingSpotOrder
) -> bool:
    return (
        execution.user_id == order.user_id
        and execution.order_id == order.order_id
        and execution.symbol == order.symbol
        and execution.exchange == order.exchange
        and execution.side == order.side
        and execution.broker == "bybit"
        and (
            not execution.order_link_id
            or execution.order_link_id == order.order_link_id
        )
    )


def _get_or_create_inventory(session, order: BybitScalpingSpotOrder) -> BybitScalpingSpotInventory:
    inventory = (
        session.query(BybitScalpingSpotInventory)
        .filter_by(
            user_id=order.user_id,
            symbol=order.symbol,
            exchange=order.exchange,
            mode=order.mode,
        )
        .with_for_update()
        .first()
    )
    if inventory is None:
        inventory = BybitScalpingSpotInventory(
            user_id=order.user_id,
            mode=order.mode,
            symbol=order.symbol,
            exchange=order.exchange,
            base_coin=order.base_coin,
            owned_quantity="0",
            reserved_quantity="0",
            bought_quantity="0",
            sold_quantity="0",
            quote_spent="0",
            quote_received="0",
            fees_by_currency="{}",
        )
        session.add(inventory)
        session.flush()
    elif inventory.base_coin != order.base_coin:
        raise ValueError("tracked Spot order base_coin conflicts with its inventory ledger")
    return inventory


def _apply_execution(
    session,
    execution: BybitScalpingSpotExecution,
    order: BybitScalpingSpotOrder,
) -> bool:
    if execution.processed or not _execution_matches_order(execution, order):
        return False

    inventory = _get_or_create_inventory(session, order)
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        quantity = _stored_decimal(execution.quantity)
        price = _stored_decimal(execution.price)
        fee = _stored_decimal(execution.fee_amount)
        if execution.fee_currency and fee:
            fee_totals = json.loads(inventory.fees_by_currency or "{}")
            fee_totals[execution.fee_currency] = _decimal_text(
                _stored_decimal(fee_totals.get(execution.fee_currency, "0")) + fee
            )
            inventory.fees_by_currency = json.dumps(
                fee_totals, sort_keys=True, separators=(",", ":")
            )
        base_fee = fee if execution.fee_currency == order.base_coin else Decimal("0")
        owned = _stored_decimal(inventory.owned_quantity)
        if order.side == "BUY":
            inventory.owned_quantity = _decimal_text(owned + quantity - base_fee)
            inventory.bought_quantity = _decimal_text(
                _stored_decimal(inventory.bought_quantity) + quantity
            )
            inventory.quote_spent = _decimal_text(
                _stored_decimal(inventory.quote_spent) + quantity * price
            )
        else:
            inventory.owned_quantity = _decimal_text(owned - quantity - base_fee)
            inventory.sold_quantity = _decimal_text(
                _stored_decimal(inventory.sold_quantity) + quantity
            )
            inventory.quote_received = _decimal_text(
                _stored_decimal(inventory.quote_received) + quantity * price
            )

        previous_fill = _stored_decimal(order.filled_quantity)
        new_fill = previous_fill + quantity
        previous_reserved = Decimal("0")
        remaining_reserved = Decimal("0")
        if order.side == "SELL" and order.status not in _TERMINAL_ORDER_STATES:
            requested = _stored_decimal(order.requested_quantity)
            previous_reserved = max(Decimal("0"), requested - previous_fill)
            remaining_reserved = max(Decimal("0"), requested - new_fill)
        if order.side == "SELL" and previous_reserved > remaining_reserved:
            inventory.reserved_quantity = _decimal_text(
                max(
                    Decimal("0"),
                    _stored_decimal(inventory.reserved_quantity)
                    - previous_reserved
                    + remaining_reserved,
                )
            )
        order.filled_quantity = _decimal_text(new_fill)

    execution.mode = order.mode
    execution.processed = True
    execution.processed_at = datetime.now(UTC)
    return True


def _apply_pending_for_order(session, order: BybitScalpingSpotOrder) -> int:
    if not order.order_id:
        return 0
    pending = (
        session.query(BybitScalpingSpotExecution)
        .filter_by(user_id=order.user_id, order_id=order.order_id, processed=False)
        .order_by(BybitScalpingSpotExecution.id)
        .all()
    )
    return sum(_apply_execution(session, execution, order) for execution in pending)


def handle_bybit_scalping_execution(event) -> bool:
    """Ingest one Bybit execution; unmatched executions stay unapplied.

    The event contract intentionally has no orderLinkId. The inbox bridges the
    REST-ack race: once the acknowledgement binds the reserved orderLinkId to
    ``order_id``, any matching pre-ack executions are applied in the same DB
    transaction. Unmatched events never change owned inventory.
    """
    fields = _normalize_execution_event(event)
    if fields is None:
        return False

    with localcontext() as context, _session_scope(immediate=True) as session:
        context.prec = _DECIMAL_PRECISION
        cutoff = datetime.now(UTC) - _UNMATCHED_EXECUTION_RETENTION
        session.query(BybitScalpingSpotExecution).filter(
            BybitScalpingSpotExecution.processed.is_(False),
            BybitScalpingSpotExecution.created_at < cutoff,
        ).delete(synchronize_session=False)

        duplicate = (
            session.query(BybitScalpingSpotExecution.id)
            .filter_by(user_id=fields["user_id"], exec_id=fields["exec_id"])
            .first()
        )
        if duplicate is not None:
            return False

        execution = BybitScalpingSpotExecution(
            user_id=fields["user_id"],
            exec_id=fields["exec_id"],
            order_id=fields["order_id"],
            order_link_id=fields["order_link_id"] or None,
            symbol=fields["symbol"],
            exchange=fields["exchange"],
            side=fields["side"],
            quantity=_decimal_text(fields["quantity"]),
            price=_decimal_text(fields["price"]),
            fee_amount=_decimal_text(fields["fee_amount"]),
            fee_currency=fields["fee_currency"],
            broker=fields["broker"],
            processed=False,
        )
        order = (
            session.query(BybitScalpingSpotOrder)
            .filter_by(user_id=fields["user_id"], order_id=fields["order_id"])
            .first()
        )
        if order is None and fields["order_link_id"]:
            order = (
                session.query(BybitScalpingSpotOrder)
                .filter_by(
                    user_id=fields["user_id"],
                    order_link_id=fields["order_link_id"],
                )
                .first()
            )
            if order is not None:
                _bind_order_id(session, order, fields["order_id"])
        if order is not None and (
            order.symbol != fields["symbol"]
            or order.exchange != fields["exchange"]
            or order.side != fields["side"]
            or (
                fields["order_link_id"]
                and order.order_link_id != fields["order_link_id"]
            )
        ):
            return False
        if order is None:
            return False

        session.add(execution)
        session.flush()
        _apply_execution(session, execution, order)
        return True


def _release_remaining_sell_reservation(
    session, order: BybitScalpingSpotOrder, terminal_status: str
) -> None:
    if order.side == "SELL" and order.status not in _TERMINAL_ORDER_STATES:
        inventory = (
            session.query(BybitScalpingSpotInventory)
            .filter_by(
                user_id=order.user_id,
                symbol=order.symbol,
                exchange=order.exchange,
                mode=order.mode,
            )
            .with_for_update()
            .first()
        )
        if inventory is not None:
            remaining = max(
                Decimal("0"),
                _stored_decimal(order.requested_quantity) - _stored_decimal(order.filled_quantity),
            )
            inventory.reserved_quantity = _decimal_text(
                max(
                    Decimal("0"),
                    _stored_decimal(inventory.reserved_quantity) - remaining,
                )
            )
    order.status = terminal_status


def _bind_order_id(
    session,
    order: BybitScalpingSpotOrder,
    order_id: str | None,
) -> None:
    normalized_id = str(order_id or "").strip()
    if not normalized_id:
        return
    if order.order_id and order.order_id != normalized_id:
        raise ValueError("order_link_id is already bound to a different broker order id")
    other = (
        session.query(BybitScalpingSpotOrder)
        .filter_by(user_id=order.user_id, order_id=normalized_id)
        .first()
    )
    if other is not None and other.id != order.id:
        raise ValueError("broker order id is already tracked by another scalping order")
    order.order_id = normalized_id


def reconcile_bybit_scalping_spot_order(
    user_id: str,
    order_link_id: str,
    order_id: str | None,
    *,
    accepted: bool,
) -> dict:
    """Reconcile an order-link reservation with its REST acknowledgement.

    Call with ``accepted=True`` and the Bybit order id after acknowledgement.
    On rejection, call with ``accepted=False``; the remaining Spot sell
    reservation is released. ``order_id`` may be supplied on rejection when the
    response includes one, so any executions already buffered can still match.
    """
    normalized_user = str(user_id or "").strip()
    normalized_link = str(order_link_id or "").strip()
    if not normalized_user or not normalized_link:
        raise ValueError("user_id and order_link_id are required")
    if accepted and not str(order_id or "").strip():
        raise ValueError("an accepted Spot order must include its Bybit order id")

    with localcontext() as context, _session_scope(immediate=True) as session:
        context.prec = _DECIMAL_PRECISION
        order = (
            session.query(BybitScalpingSpotOrder)
            .filter_by(user_id=normalized_user, order_link_id=normalized_link)
            .first()
        )
        if order is None:
            raise ValueError("scalping Spot order reservation was not found")
        _bind_order_id(session, order, order_id)

        if accepted:
            if order.status == "rejected":
                raise ValueError("cannot acknowledge an already terminal scalping Spot order")
            if order.status not in _TERMINAL_ORDER_STATES:
                order.status = "accepted"
        else:
            _release_remaining_sell_reservation(session, order, "rejected")

        applied = _apply_pending_for_order(session, order)
        result = _order_dict(order)
        result["applied_pending_executions"] = applied
        return result


def reconcile_bybit_scalping_spot_order_data(
    user_id: str,
    order_data: dict,
    broker: str,
    *,
    accepted: bool,
    order_id: str | None = None,
) -> bool:
    """Reconcile a Scalping Spot reservation from a direct or queued order payload."""
    if str(broker or "").lower() != "bybit" or not isinstance(order_data, dict) or not (
        str(order_data.get("strategy") or "").lower() == "scalping"
        and str(order_data.get("exchange") or "").upper() == "CRYPTO"
        and str(order_data.get("category") or "").lower() == "spot"
        and order_data.get("order_link_id")
    ):
        return False
    reconcile_bybit_scalping_spot_order(
        user_id=user_id,
        order_link_id=order_data["order_link_id"],
        order_id=order_id,
        accepted=accepted,
    )
    return True


def release_bybit_scalping_spot_order(
    user_id: str,
    order_link_id: str,
    *,
    status: str = "cancelled",
    order_id: str | None = None,
) -> dict:
    """Release an unfilled sell reservation after cancellation or termination."""
    terminal_status = str(status or "").lower()
    if terminal_status not in _TERMINAL_ORDER_STATES:
        raise ValueError(f"status must be one of {sorted(_TERMINAL_ORDER_STATES)}")
    normalized_user = str(user_id or "").strip()
    normalized_link = str(order_link_id or "").strip()
    if not normalized_user or not normalized_link:
        raise ValueError("user_id and order_link_id are required")

    with localcontext() as context, _session_scope(immediate=True) as session:
        context.prec = _DECIMAL_PRECISION
        order = (
            session.query(BybitScalpingSpotOrder)
            .filter_by(user_id=normalized_user, order_link_id=normalized_link)
            .first()
        )
        if order is None:
            raise ValueError("scalping Spot order reservation was not found")
        _bind_order_id(session, order, order_id)
        _release_remaining_sell_reservation(session, order, terminal_status)
        applied = _apply_pending_for_order(session, order)
        result = _order_dict(order)
        result["applied_pending_executions"] = applied
        return result


def handle_bybit_scalping_order_update(event) -> bool:
    """Release unused Spot sell quantity when its tracked order becomes terminal."""
    if str(_event_value(event, "broker")).lower() != "bybit":
        return False
    if str(_event_value(event, "exchange")).upper() != "CRYPTO":
        return False
    request_data = _event_value(event, "request_data", {})
    if not isinstance(request_data, dict):
        return False
    user_id = str(request_data.get("user_id") or "").strip()
    order_id = str(_event_value(event, "orderid")).strip()
    order_link_id = str(_event_value(event, "order_link_id")).strip()
    status = str(_event_value(event, "order_status")).lower()
    if status not in _TERMINAL_ORDER_STATES or not user_id or not (order_id or order_link_id):
        return False

    with _session_scope(immediate=True) as session:
        query = session.query(BybitScalpingSpotOrder).filter_by(user_id=user_id)
        order = (
            query.filter_by(order_id=order_id).first()
            if order_id
            else query.filter_by(order_link_id=order_link_id).first()
        )
        if order is None and order_link_id:
            order = query.filter_by(order_link_id=order_link_id).first()
        if order is None or (order_link_id and order.order_link_id != order_link_id):
            return False
        _bind_order_id(session, order, order_id)
        if status in _TERMINAL_ORDER_STATES:
            _release_remaining_sell_reservation(session, order, status)
        elif order.status not in _TERMINAL_ORDER_STATES:
            order.status = "accepted"
        return True


def _inventory_dict(inventory: BybitScalpingSpotInventory) -> dict:
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        owned = _stored_decimal(inventory.owned_quantity)
        reserved = _stored_decimal(inventory.reserved_quantity)
        fees = json.loads(inventory.fees_by_currency or "{}")
        result = {
            "symbol": inventory.symbol,
            "exchange": inventory.exchange,
            "mode": inventory.mode,
            "base_coin": inventory.base_coin,
            "owned_quantity": _decimal_text(owned),
            "reserved_quantity": _decimal_text(reserved),
            "available_quantity": _decimal_text(max(Decimal("0"), owned - reserved)),
            "bought_quantity": inventory.bought_quantity,
            "sold_quantity": inventory.sold_quantity,
            "quote_spent": inventory.quote_spent,
            "quote_received": inventory.quote_received,
            "fees_by_currency": {
                currency: _decimal_text(_stored_decimal(amount))
                for currency, amount in sorted(fees.items())
            },
        }
        return result


def get_bybit_scalping_spot_inventory(
    user_id: str,
    symbol: str,
    exchange: str,
    mode: str,
    base_coin: str | None = None,
) -> dict:
    """Return exact owned, reserved, and available inventory for one symbol."""
    normalized_user = str(user_id or "").strip()
    normalized_symbol = str(symbol or "").strip().upper()
    normalized_exchange = str(exchange or "").strip().upper()
    normalized_mode = _mode(mode)
    if not all((normalized_user, normalized_symbol, normalized_exchange)):
        raise ValueError("user_id, symbol, and exchange are required")
    with _session_scope() as session:
        inventory = (
            session.query(BybitScalpingSpotInventory)
            .filter_by(
                user_id=normalized_user,
                symbol=normalized_symbol,
                exchange=normalized_exchange,
                mode=normalized_mode,
            )
            .first()
        )
        if inventory is not None:
            return _inventory_dict(inventory)
        return {
            "symbol": normalized_symbol,
            "exchange": normalized_exchange,
            "mode": normalized_mode,
            "base_coin": str(base_coin or "").strip().upper(),
            "owned_quantity": "0",
            "reserved_quantity": "0",
            "available_quantity": "0",
            "bought_quantity": "0",
            "sold_quantity": "0",
            "quote_spent": "0",
            "quote_received": "0",
            "fees_by_currency": {},
        }


def list_bybit_scalping_spot_inventory(user_id: str, mode: str | None = None) -> list[dict]:
    """List inventory ledger rows for one user, optionally by trading mode."""
    normalized_user = str(user_id or "").strip()
    if not normalized_user:
        raise ValueError("user_id is required")
    normalized_mode = _mode(mode) if mode is not None else None
    with _session_scope() as session:
        query = session.query(BybitScalpingSpotInventory).filter_by(user_id=normalized_user)
        if normalized_mode is not None:
            query = query.filter_by(mode=normalized_mode)
        rows = query.order_by(
            BybitScalpingSpotInventory.mode,
            BybitScalpingSpotInventory.symbol,
            BybitScalpingSpotInventory.exchange,
        ).all()
        return [_inventory_dict(row) for row in rows]


def start_bybit_scalping_inventory_listener() -> bool:
    """Ensure ledger tables exist and subscribe once to Spot execution events."""
    global _listener_started
    if _listener_started:
        return False
    scalping_db.init_db()
    bus.subscribe(
        "execution.update",
        handle_bybit_scalping_execution,
        name="BybitScalpingSpotInventory",
    )
    bus.subscribe(
        "order.update",
        handle_bybit_scalping_order_update,
        name="BybitScalpingSpotInventory",
    )
    _listener_started = True
    return True


def stop_bybit_scalping_inventory_listener() -> bool:
    """Unsubscribe the execution handler; primarily useful for controlled shutdown."""
    global _listener_started
    if not _listener_started:
        return False
    bus.unsubscribe("execution.update", handle_bybit_scalping_execution)
    bus.unsubscribe("order.update", handle_bybit_scalping_order_update)
    _listener_started = False
    return True
