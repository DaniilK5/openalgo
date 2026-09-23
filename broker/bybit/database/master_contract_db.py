import os
import re

import pandas as pd
from sqlalchemy import Column, Float, Index, Integer, Sequence, String, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker

from broker.bybit.api.baseurl import get_url
from database.engine_factory import create_db_engine
from extensions import socketio
from utils.httpx_client import get_httpx_client
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
    __table_args__ = (Index("idx_symbol_exchange", "symbol", "exchange"),)


def init_db():
    Base.metadata.create_all(bind=engine)
    try:
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(engine)
        columns = {col["name"] for col in insp.get_columns("symtoken")}
        if "contract_value" not in columns:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE symtoken ADD COLUMN contract_value REAL DEFAULT 1.0"))
                conn.commit()
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


def _to_expiry_string(symbol: str):
    match = re.search(r"-(\d{2})([A-Z]{3})(\d{2})$", symbol, re.IGNORECASE)
    if not match:
        return ""
    day, month, year = match.groups()
    return f"{day}-{month.upper()}-{year}"


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


def _build_linear_symbol(item):
    contract_type = item.get("contractType")
    base_coin = str(item.get("baseCoin") or "").upper()
    quote_coin = str(item.get("quoteCoin") or "").upper()
    native_symbol = str(item.get("symbol") or "").upper()

    if contract_type == "LinearPerpetual":
        symbol = f"{base_coin}{quote_coin}FUT"
        expiry = ""
        instrumenttype = "PERPFUT"
        name = base_coin
    elif contract_type == "LinearFutures":
        expiry = _to_expiry_string(native_symbol)
        if not expiry:
            expiry = ""
        symbol = f"{base_coin}{expiry.replace('-', '').upper()}FUT" if expiry else f"{base_coin}FUT"
        instrumenttype = "FUT"
        name = base_coin
    else:
        return None

    if not symbol:
        return None

    price_filter = item.get("priceFilter") or {}
    lot_filter = item.get("lotSizeFilter") or {}
    tick_size = _parse_decimal(price_filter.get("tickSize"), 0.0)
    lot_size = _parse_lot_size(lot_filter.get("qtyStep"))
    result = {
        "symbol": symbol,
        "brsymbol": native_symbol,
        "name": name,
        "exchange": "CRYPTO",
        "brexchange": "bybit",
        "token": native_symbol,
        "expiry": expiry,
        "strike": 0.0,
        "lotsize": lot_size,
        "instrumenttype": instrumenttype,
        "tick_size": tick_size,
        "contract_value": 1.0,
    }
    return result


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
    """Download the live Bybit linear perpetual/futures contract set.

    Scope for v1: Unified Account mainnet, linear perpetuals/futures only.
    This skips spot, inverse and options until a later phase uses them.
    """
    logger.info("Bybit master_contract_download requested")
    _emit_master_contract_status("pending")

    rows = []
    next_cursor = ""
    page_count = 0

    try:
        while page_count < 50:
            params = {"category": "linear", "limit": 1000}
            if next_cursor:
                params["cursor"] = next_cursor

            response = get_httpx_client().get(get_url("/v5/market/instruments-info"), params=params, timeout=60.0)
            if response.status_code != 200:
                raise ValueError(f"Bybit master contract fetch failed: HTTP {response.status_code}")

            payload = response.json() if response.content else {}
            if payload.get("retCode") != 0:
                raise ValueError(f"Bybit master contract fetch failed: {payload.get('retMsg', 'unknown error')}")

            result = payload.get("result") or {}
            items = result.get("list") or []
            for item in items:
                if (item.get("status") or "").lower() != "trading":
                    continue
                contract_type = item.get("contractType")
                if contract_type not in {"LinearPerpetual", "LinearFutures"}:
                    continue
                canonical = _build_linear_symbol(item)
                if canonical:
                    rows.append(canonical)

            next_cursor = result.get("nextPageCursor") or ""
            page_count += 1
            if not next_cursor:
                break

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
