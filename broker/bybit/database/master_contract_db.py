import os
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import pandas as pd
from sqlalchemy import Column, Float, Index, Integer, Sequence, String, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker

from broker.bybit.api.rest_client import request
from database.engine_factory import create_db_engine
from extensions import socketio
from utils.logging import get_logger

logger = get_logger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_db_engine(DATABASE_URL)
db_session = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
Base = declarative_base()
Base.query = db_session.query_property()


class SymToken(Base):
    __tablename__ = "symtoken"
    id = Column(Integer, Sequence("symtoken_id_seq"), primary_key=True)
    symbol = Column(String, nullable=False, index=True)
    brsymbol = Column(String, nullable=False, index=True)
    name = Column(String)
    exchange = Column(String, index=True)
    brexchange = Column(String, index=True)
    token = Column(String, index=True)
    expiry = Column(String)
    strike = Column(Float)
    lotsize = Column(Integer)
    instrumenttype = Column(String)
    tick_size = Column(Float)
    contract_value = Column(Float, default=1.0)
    category = Column(String(16))
    qty_step = Column(Float)
    min_qty = Column(Float)
    max_qty = Column(Float)
    base_precision = Column(Float)
    quote_precision = Column(Float)
    min_order_amt = Column(Float)
    max_market_qty = Column(Float)
    max_limit_qty = Column(Float)
    base_coin = Column(String(20))
    quote_coin = Column(String(20))
    settle_coin = Column(String(20))
    __table_args__ = (Index("idx_symbol_exchange", "symbol", "exchange"),)


def init_db():
    Base.metadata.create_all(bind=engine)
    try:
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(engine)
        columns = {col["name"] for col in insp.get_columns("symtoken")}
        missing_columns = {
            "contract_value": "REAL DEFAULT 1.0",
            "category": "VARCHAR(16)",
            "qty_step": "REAL",
            "min_qty": "REAL",
            "max_qty": "REAL",
            "base_precision": "REAL",
            "quote_precision": "REAL",
            "min_order_amt": "REAL",
            "max_market_qty": "REAL",
            "max_limit_qty": "REAL",
            "base_coin": "VARCHAR(20)",
            "quote_coin": "VARCHAR(20)",
            "settle_coin": "VARCHAR(20)",
        }
        with engine.begin() as conn:
            for column, column_type in missing_columns.items():
                if column not in columns:
                    conn.execute(
                        text(f"ALTER TABLE symtoken ADD COLUMN {column} {column_type}")
                    )
    except Exception as exc:  # pragma: no cover - migration-path guard
        logger.warning("Could not migrate symtoken contract_value: %s", exc)


def delete_symtoken_table():
    try:
        SymToken.query.delete()
        db_session.commit()
    except Exception:
        logger.warning("symtoken table not present yet; creating it before delete", exc_info=True)
        init_db()
        SymToken.query.delete()
        db_session.commit()


def copy_from_dataframe(df):
    data = df.to_dict(orient="records")
    if not data:
        return 0
    init_db()
    existing = {row.token for row in db_session.query(SymToken.token).all()}
    filtered = [row for row in data if row.get("token") not in existing]
    if filtered:
        db_session.bulk_insert_mappings(SymToken, filtered)
        db_session.commit()
    return len(filtered)


def _parse_decimal(value, default=0.0):
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_lot_size(value):
    parsed = _parse_decimal(value)
    if parsed <= 0:
        return 1
    return int(parsed) if float(parsed).is_integer() else 1


def _optional_decimal(value):
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid Bybit instrument number: {value}") from exc
    if not parsed.is_finite():
        raise ValueError(f"Invalid Bybit instrument number: {value}")
    return float(parsed)


def _expiry_from_delivery_time(value):
    delivery_ms = _optional_decimal(value)
    if delivery_ms is None or delivery_ms <= 0:
        return ""
    return datetime.fromtimestamp(float(delivery_ms) / 1000, timezone.utc).strftime(
        "%d-%b-%y"
    ).upper()


def _build_instrument(item, category):
    base_coin = str(item.get("baseCoin") or "").upper()
    quote_coin = str(item.get("quoteCoin") or "").upper()
    settle_coin = str(item.get("settleCoin") or "").upper()
    native_symbol = str(item.get("symbol") or "").upper()
    symbol_id = str(item.get("symbolId") or "").strip()
    price_filter = item.get("priceFilter") or {}
    lot_filter = item.get("lotSizeFilter") or {}
    tick_size = _optional_decimal(price_filter.get("tickSize"))

    if not base_coin or not quote_coin or not native_symbol or not symbol_id or not tick_size:
        return None

    expiry = _expiry_from_delivery_time(item.get("deliveryTime"))
    strike = 0.0
    instrumenttype = ""
    lot_size = 1
    category = category.lower()

    if category == "spot":
        symbol = native_symbol
        instrumenttype = "SPOT"
        name = base_coin
        base_precision = _optional_decimal(lot_filter.get("basePrecision"))
        quote_precision = _optional_decimal(lot_filter.get("quotePrecision"))
        qty_step = None
        min_qty = _optional_decimal(lot_filter.get("minOrderQty"))
        max_qty = _optional_decimal(lot_filter.get("maxOrderQty"))
        min_order_amt = _optional_decimal(lot_filter.get("minOrderAmt"))
        max_market_qty = _optional_decimal(lot_filter.get("maxMarketOrderQty"))
        max_limit_qty = _optional_decimal(lot_filter.get("maxLimitOrderQty"))
    elif category in {"linear", "inverse"}:
        contract_type = item.get("contractType")
        if contract_type in {"LinearPerpetual", "InversePerpetual"}:
            symbol = f"{base_coin}{quote_coin}FUT"
            instrumenttype = "PERPFUT"
            expiry = ""
        elif contract_type in {"LinearFutures", "InverseFutures"} and expiry:
            prefix = base_coin if category == "linear" and quote_coin == "USDT" else f"{base_coin}{quote_coin}"
            symbol = f"{prefix}{expiry.replace('-', '')}FUT"
            instrumenttype = "FUT"
        else:
            return None
        name = base_coin
        qty_step = _optional_decimal(lot_filter.get("qtyStep"))
        min_qty = _optional_decimal(lot_filter.get("minOrderQty"))
        max_qty = _optional_decimal(lot_filter.get("maxOrderQty"))
        max_market_qty = _optional_decimal(
            lot_filter.get("maxMktOrderQty") or lot_filter.get("maxMarketOrderQty")
        )
        max_limit_qty = max_qty
        base_precision = None
        quote_precision = None
        min_order_amt = _optional_decimal(lot_filter.get("minNotionalValue"))
        lot_size = _parse_lot_size(qty_step)
    elif category == "option":
        match = re.fullmatch(
            r"([A-Z0-9]+)-(\d{2}[A-Z]{3}\d{2})-([0-9]+(?:\.[0-9]+)?)-([CP])-([A-Z0-9]+)",
            native_symbol,
        )
        option_type = str(item.get("optionsType") or "").lower()
        if not match or option_type not in {"call", "put"} or not expiry:
            return None
        _, symbol_expiry, strike_text, native_side, _ = match.groups()
        symbol_expiry_date = datetime.strptime(symbol_expiry, "%d%b%y").date()
        delivery_date = datetime.strptime(expiry, "%d-%b-%y").date()
        expected_side = "C" if option_type == "call" else "P"
        if symbol_expiry_date != delivery_date or native_side != expected_side:
            return None
        strike_decimal = Decimal(strike_text)
        if strike_decimal <= 0:
            return None
        strike = float(strike_decimal)
        option_suffix = "CE" if option_type == "call" else "PE"
        symbol = (
            f"{base_coin}{quote_coin}{symbol_expiry}"
            f"{format(strike_decimal.normalize(), 'f')}{option_suffix}"
        )
        instrumenttype = option_suffix
        name = base_coin
        qty_step = _optional_decimal(lot_filter.get("qtyStep"))
        min_qty = _optional_decimal(lot_filter.get("minOrderQty"))
        max_qty = _optional_decimal(lot_filter.get("maxOrderQty"))
        max_market_qty = None
        max_limit_qty = max_qty
        base_precision = None
        quote_precision = None
        min_order_amt = None
        lot_size = _parse_lot_size(qty_step)
    else:
        return None

    result = {
        "symbol": symbol,
        "brsymbol": native_symbol,
        "name": name,
        "exchange": "CRYPTO",
        "brexchange": "bybit",
        "token": f"{category}:{symbol_id}",
        "expiry": expiry,
        "strike": strike,
        "lotsize": lot_size,
        "instrumenttype": instrumenttype,
        "tick_size": tick_size,
        "contract_value": 1.0,
        "category": category,
        "qty_step": qty_step,
        "min_qty": min_qty,
        "max_qty": max_qty,
        "base_precision": base_precision,
        "quote_precision": quote_precision,
        "min_order_amt": min_order_amt,
        "max_market_qty": max_market_qty,
        "max_limit_qty": max_limit_qty,
        "base_coin": base_coin,
        "quote_coin": quote_coin,
        "settle_coin": settle_coin or None,
    }
    return result


def _fetch_category_items(category):
    cursor = ""
    page_count = 0
    items = []

    while True:
        params = {"category": category}
        if category != "spot":
            params["limit"] = 1000
            if category == "option":
                params["baseCoin"] = "All"
            if cursor:
                params["cursor"] = cursor

        payload = request("/v5/market/instruments-info", params=params)

        result = payload.get("result") or {}
        if not isinstance(result, dict) or not isinstance(result.get("list"), list):
            raise ValueError(f"Bybit {category} instrument response is malformed")
        items.extend(result["list"])

        next_cursor = result.get("nextPageCursor") or ""
        if category == "spot" or not next_cursor:
            break
        page_count += 1
        if page_count >= 50:
            raise ValueError(f"Bybit {category} instrument pagination exceeded 50 pages")
        cursor = next_cursor

    return items


def _status_supported(category, item):
    status = str(item.get("status") or "").lower()
    if category == "spot":
        return status == "trading"
    if category in {"linear", "inverse"}:
        return status in {"trading", "pendingopen"}
    return status in {"prelaunch", "trading", "delivering"}


def _emit_master_contract_status(status, broker="bybit", **payload):
    """Safely emit the progress event even when no SocketIO server is attached."""
    if socketio is not None and getattr(socketio, "server", None) is not None:
        event_payload = {"status": status, "broker": broker}
        event_payload.update(payload)
        try:
            socketio.emit("master_contract_download", event_payload)
        except Exception:  # pragma: no cover - socketio may not be bound in unit tests
            logger.debug("SocketIO master_contract_download emit skipped because no live server is bound", exc_info=True)


def master_contract_download():
    """Download and atomically replace the Bybit Spot, Linear, Inverse, and Option master."""
    logger.info("Bybit master_contract_download requested")
    _emit_master_contract_status("pending")

    rows = []
    try:
        for category in ("spot", "linear", "inverse", "option"):
            _emit_master_contract_status("downloading", category=category)
            items = _fetch_category_items(category)
            if not items:
                raise ValueError(
                    f"Bybit returned no instruments for {category}; existing symbols were kept"
                )

            category_rows = []
            for item in items:
                if not isinstance(item, dict) or not _status_supported(category, item):
                    continue
                canonical = _build_instrument(item, category)
                if canonical:
                    category_rows.append(canonical)
            if not category_rows:
                raise ValueError(
                    f"Bybit returned no supported {category} instruments; existing symbols were kept"
                )
            rows.extend(category_rows)

        symbols = [(row["symbol"], row["exchange"]) for row in rows]
        if len(symbols) != len(set(symbols)):
            raise ValueError("Bybit instrument master contains duplicate OpenAlgo symbols")

        df = pd.DataFrame(rows)
        if df.empty:
            _emit_master_contract_status("success", count=0)
            return 0

        delete_symtoken_table()
        inserted = copy_from_dataframe(df)
        _emit_master_contract_status("success", count=inserted)
        return inserted
    except Exception as exc:  # pragma: no cover - network dependent
        logger.exception("Bybit master contract download failed")
        _emit_master_contract_status("error", error=str(exc))
        return False
    finally:
        db_session.remove()
