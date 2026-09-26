from decimal import Decimal
from uuid import uuid4

import pytest

from database import scalping_db
from database.scalping_db import (
    BybitScalpingSpotExecution,
    BybitScalpingSpotInventory,
    BybitScalpingSpotOrder,
    db_session,
)
from events import ExecutionUpdateEvent, OrderUpdateEvent
from services.bybit_scalping_inventory_service import (
    InsufficientScalpingInventory,
    get_bybit_scalping_spot_inventory,
    handle_bybit_scalping_execution,
    handle_bybit_scalping_order_update,
    reconcile_bybit_scalping_spot_order,
    reconcile_bybit_scalping_spot_order_data,
    release_bybit_scalping_spot_order,
    reserve_bybit_scalping_spot_order,
)


@pytest.fixture
def user_id():
    scalping_db.init_db()
    test_user = f"scalping-inventory-{uuid4().hex}"
    yield test_user
    _delete_test_user(test_user)


def _delete_test_user(test_user):
    session = db_session()
    try:
        for model in (
            BybitScalpingSpotExecution,
            BybitScalpingSpotOrder,
            BybitScalpingSpotInventory,
        ):
            session.query(model).filter_by(user_id=test_user).delete()
        session.commit()
    finally:
        db_session.remove()


def _reserve_buy(user_id, *, symbol="BTCUSDT", base_coin="BTC", mode="live"):
    return reserve_bybit_scalping_spot_order(
        user_id=user_id,
        symbol=symbol,
        exchange="CRYPTO",
        mode=mode,
        side="BUY",
        quantity="100",
        base_coin=base_coin,
        market_unit="quoteCoin",
    )


def _event(
    user_id,
    order_id,
    exec_id,
    *,
    side="BUY",
    quantity="1",
    price="10",
    fee_amount="0",
    fee_currency="USDT",
    symbol="BTCUSDT",
    order_link_id="",
):
    return ExecutionUpdateEvent(
        exec_id=exec_id,
        order_id=order_id,
        user_id=user_id,
        symbol=symbol,
        exchange="CRYPTO",
        side=side,
        quantity=Decimal(quantity),
        price=Decimal(price),
        fee_amount=Decimal(fee_amount),
        fee_currency=fee_currency,
        broker="bybit",
        order_link_id=order_link_id,
    )


def test_duplicate_execution_is_idempotent_and_base_fee_reduces_owned_quantity(user_id):
    reserved = _reserve_buy(user_id)
    reconcile_bybit_scalping_spot_order(
        user_id, reserved["order_link_id"], "order-buy-1", accepted=True
    )
    fill = _event(
        user_id,
        "order-buy-1",
        "exec-buy-1",
        quantity="0.123456789123456789123456789012345678",
        price="2",
        fee_amount="0.000000000000000000000000000000000001",
        fee_currency="BTC",
    )

    assert handle_bybit_scalping_execution(fill) is True
    assert handle_bybit_scalping_execution(fill) is False

    inventory = get_bybit_scalping_spot_inventory(user_id, "BTCUSDT", "CRYPTO", "live", "BTC")
    assert inventory["owned_quantity"] == "0.123456789123456789123456789012345677"
    assert inventory["available_quantity"] == inventory["owned_quantity"]
    assert inventory["quote_spent"] == "0.246913578246913578246913578024691356"
    assert inventory["fees_by_currency"] == {"BTC": "0.000000000000000000000000000000000001"}


def test_rejected_order_data_releases_reservation_only_for_bybit(user_id):
    buy = _reserve_buy(user_id)
    reconcile_bybit_scalping_spot_order(
        user_id, buy["order_link_id"], "order-buy", accepted=True
    )
    assert handle_bybit_scalping_execution(
        _event(user_id, "order-buy", "exec-buy", quantity="0.5")
    )

    sell = reserve_bybit_scalping_spot_order(
        user_id=user_id,
        symbol="BTCUSDT",
        exchange="CRYPTO",
        mode="live",
        side="SELL",
        quantity="0.4",
        base_coin="BTC",
        market_unit="baseCoin",
    )
    order_data = {
        "strategy": "scalping",
        "exchange": "CRYPTO",
        "category": "spot",
        "order_link_id": sell["order_link_id"],
    }

    assert not reconcile_bybit_scalping_spot_order_data(
        user_id, order_data, "delta", accepted=False
    )
    inventory = get_bybit_scalping_spot_inventory(
        user_id, "BTCUSDT", "CRYPTO", "live", "BTC"
    )
    assert inventory["available_quantity"] == "0.1"

    assert reconcile_bybit_scalping_spot_order_data(
        user_id, order_data, "bybit", accepted=False
    )
    inventory = get_bybit_scalping_spot_inventory(
        user_id, "BTCUSDT", "CRYPTO", "live", "BTC"
    )
    assert inventory["available_quantity"] == "0.5"


def test_pre_ack_execution_is_attributed_by_exact_client_link_id(user_id):
    reserved = _reserve_buy(user_id)
    fill = _event(
        user_id,
        "order-racing-ack",
        "exec-racing",
        quantity="0.4",
        order_link_id=reserved["order_link_id"],
    )

    assert handle_bybit_scalping_execution(fill) is True
    before_ack = get_bybit_scalping_spot_inventory(
        user_id, "BTCUSDT", "CRYPTO", "live", "BTC"
    )
    assert before_ack["owned_quantity"] == "0.4"

    result = reconcile_bybit_scalping_spot_order(
        user_id, reserved["order_link_id"], "order-racing-ack", accepted=True
    )
    assert result["applied_pending_executions"] == 0
    assert (
        get_bybit_scalping_spot_inventory(user_id, "BTCUSDT", "CRYPTO", "live", "BTC")[
            "owned_quantity"
        ]
        == "0.4"
    )


def test_pre_ack_fill_requires_exact_order_link_id(user_id):
    reserved = _reserve_buy(user_id)

    assert not handle_bybit_scalping_execution(
        _event(user_id, "manual-order", "manual-fill", quantity="2")
    )
    assert not handle_bybit_scalping_execution(
        _event(
            user_id,
            "other-scalping-order",
            "other-fill",
            quantity="2",
            order_link_id="different-link-id",
        )
    )
    assert handle_bybit_scalping_execution(
        _event(
            user_id,
            "actual-scalping-order",
            "actual-fill",
            quantity="0.4",
            order_link_id=reserved["order_link_id"],
        )
    )

    reconcile_bybit_scalping_spot_order(
        user_id, reserved["order_link_id"], "actual-scalping-order", accepted=True
    )
    inventory = get_bybit_scalping_spot_inventory(user_id, "BTCUSDT", "CRYPTO", "live", "BTC")
    assert inventory["owned_quantity"] == "0.4"


def test_unowned_and_other_users_executions_do_not_enter_inventory(user_id):
    assert not handle_bybit_scalping_execution(
        _event(user_id, "untracked-order", "exec-untracked", quantity="2")
    )
    reserved = _reserve_buy(user_id)
    other_user = f"other-{uuid4().hex}"
    assert not handle_bybit_scalping_execution(
        _event(other_user, "order-owned-by-someone-else", f"exec-{other_user}")
    )
    _delete_test_user(other_user)

    inventory = get_bybit_scalping_spot_inventory(user_id, "BTCUSDT", "CRYPTO", "live", "BTC")
    assert inventory["owned_quantity"] == "0"
    assert inventory["available_quantity"] == "0"
    assert reserved["status"] == "reserved"


def test_partial_sell_fills_reduce_reservation_and_prevent_overselling(user_id):
    buy = _reserve_buy(user_id)
    reconcile_bybit_scalping_spot_order(user_id, buy["order_link_id"], "buy-1", accepted=True)
    for exec_id, quantity in (("buy-fill-a", "0.75"), ("buy-fill-b", "1.25")):
        assert handle_bybit_scalping_execution(_event(user_id, "buy-1", exec_id, quantity=quantity))

    sell = reserve_bybit_scalping_spot_order(
        user_id=user_id,
        symbol="BTCUSDT",
        exchange="CRYPTO",
        mode="live",
        side="SELL",
        quantity="1.25",
        base_coin="BTC",
        market_unit="baseCoin",
    )
    reconcile_bybit_scalping_spot_order(user_id, sell["order_link_id"], "sell-1", accepted=True)
    assert handle_bybit_scalping_execution(
        _event(user_id, "sell-1", "sell-fill-a", side="SELL", quantity="0.5")
    )

    inventory = get_bybit_scalping_spot_inventory(user_id, "BTCUSDT", "CRYPTO", "live", "BTC")
    assert inventory["owned_quantity"] == "1.5"
    assert inventory["reserved_quantity"] == "0.75"
    assert inventory["available_quantity"] == "0.75"
    with pytest.raises(InsufficientScalpingInventory) as error:
        reserve_bybit_scalping_spot_order(
            user_id=user_id,
            symbol="BTCUSDT",
            exchange="CRYPTO",
            mode="live",
            side="SELL",
            quantity="0.75000001",
            base_coin="BTC",
            market_unit="baseCoin",
        )
    assert error.value.available_quantity == Decimal("0.75")

    released = release_bybit_scalping_spot_order(user_id, sell["order_link_id"], status="cancelled")
    assert released["status"] == "cancelled"
    inventory = get_bybit_scalping_spot_inventory(user_id, "BTCUSDT", "CRYPTO", "live", "BTC")
    assert inventory["reserved_quantity"] == "0"
    assert inventory["available_quantity"] == "1.5"


def test_terminal_order_update_releases_unfilled_sell_reservation(user_id):
    buy = _reserve_buy(user_id)
    reconcile_bybit_scalping_spot_order(user_id, buy["order_link_id"], "buy-3", accepted=True)
    assert handle_bybit_scalping_execution(_event(user_id, "buy-3", "buy-fill-3", quantity="1"))
    sell = reserve_bybit_scalping_spot_order(
        user_id=user_id,
        symbol="BTCUSDT",
        exchange="CRYPTO",
        mode="live",
        side="SELL",
        quantity="0.8",
        base_coin="BTC",
        market_unit="baseCoin",
    )
    reconcile_bybit_scalping_spot_order(user_id, sell["order_link_id"], "sell-3", accepted=True)

    updated = OrderUpdateEvent(
        broker="bybit",
        exchange="CRYPTO",
        orderid="sell-3",
        order_link_id=sell["order_link_id"],
        order_status="cancelled",
        request_data={"user_id": user_id},
    )
    assert handle_bybit_scalping_order_update(updated)
    inventory = get_bybit_scalping_spot_inventory(user_id, "BTCUSDT", "CRYPTO", "live", "BTC")
    assert inventory["reserved_quantity"] == "0"
    assert inventory["available_quantity"] == "1"


def test_rejected_sell_releases_its_unfilled_reservation(user_id):
    buy = _reserve_buy(user_id)
    reconcile_bybit_scalping_spot_order(user_id, buy["order_link_id"], "buy-2", accepted=True)
    assert handle_bybit_scalping_execution(_event(user_id, "buy-2", "buy-fill", quantity="1"))
    sell = reserve_bybit_scalping_spot_order(
        user_id=user_id,
        symbol="BTCUSDT",
        exchange="CRYPTO",
        mode="live",
        side="SELL",
        quantity="0.6",
        base_coin="BTC",
        market_unit="baseCoin",
    )

    rejected = reconcile_bybit_scalping_spot_order(
        user_id, sell["order_link_id"], None, accepted=False
    )
    assert rejected["status"] == "rejected"
    inventory = get_bybit_scalping_spot_inventory(user_id, "BTCUSDT", "CRYPTO", "live", "BTC")
    assert inventory["owned_quantity"] == "1"
    assert inventory["reserved_quantity"] == "0"
    assert inventory["available_quantity"] == "1"


def test_analyze_and_live_inventory_are_segregated(user_id):
    live = _reserve_buy(user_id, mode="live")
    analyze = _reserve_buy(user_id, mode="analyze")
    reconcile_bybit_scalping_spot_order(user_id, live["order_link_id"], "live-order", accepted=True)
    reconcile_bybit_scalping_spot_order(
        user_id, analyze["order_link_id"], "analyze-order", accepted=True
    )
    assert handle_bybit_scalping_execution(
        _event(user_id, "live-order", "live-exec", quantity="0.4")
    )
    assert handle_bybit_scalping_execution(
        _event(user_id, "analyze-order", "analyze-exec", quantity="0.9")
    )

    live_inventory = get_bybit_scalping_spot_inventory(user_id, "BTCUSDT", "CRYPTO", "live", "BTC")
    analyze_inventory = get_bybit_scalping_spot_inventory(
        user_id, "BTCUSDT", "CRYPTO", "analyze", "BTC"
    )
    assert live_inventory["owned_quantity"] == "0.4"
    assert analyze_inventory["owned_quantity"] == "0.9"
