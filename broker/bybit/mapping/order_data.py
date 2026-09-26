import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from database.token_db import get_oa_symbol
from utils.timezones import APP_TIMEZONE


def _coerce_float(value, default=0.0):
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _required_float(value):
    amount = _coerce_float(value, None)
    if amount is None or not math.isfinite(amount):
        raise ValueError("Bybit account data could not be read. Try again later.")
    return amount


def _extract_rows(raw):
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, dict):
        return []
    result = raw.get("result")
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        rows = result.get("list")
        return rows if isinstance(rows, list) else []
    rows = raw.get("data")
    return rows if isinstance(rows, list) else []


def _canonical_symbol(row):
    native_symbol = row.get("symbol")
    category = row.get("category")
    if not isinstance(native_symbol, str) or not native_symbol or not category:
        raise ValueError("Bybit account data could not be read. Try again later.")
    symbol = get_oa_symbol(native_symbol, "CRYPTO", category)
    if not symbol:
        raise ValueError(
            "This Bybit symbol is not in the local symbol list. Update the symbol list and retry."
        )
    return symbol


def _timestamp(value, include_date=False):
    try:
        stamp = datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).astimezone(
            ZoneInfo(APP_TIMEZONE)
        )
    except (TypeError, ValueError, OSError) as exc:
        raise ValueError("Bybit account data could not be read. Try again later.") from exc
    format_string = "%d-%b-%Y %H:%M:%S" if include_date else "%H:%M:%S"
    return stamp.strftime(format_string)


def _status_to_openalgo(status):
    normalized = str(status or "").lower()
    statuses = {
        "new": "open",
        "partiallyfilled": "open",
        "untriggered": "pending",
        "triggered": "open",
        "pendingcancel": "open",
        "filled": "complete",
        "cancelled": "cancelled",
        "partiallyfilledcanceled": "cancelled",
        "deactivated": "cancelled",
        "rejected": "rejected",
    }
    if normalized not in statuses:
        raise ValueError("An order has a status that this OpenAlgo version cannot display yet.")
    return statuses[normalized]


def _order_type(row):
    order_type = str(row.get("orderType") or "").upper()
    if order_type not in {"MARKET", "LIMIT"}:
        raise ValueError("An order type in the Bybit account history is not supported yet.")
    if row.get("triggerPrice") not in (None, "", "0", 0, 0.0):
        return "SL-M" if order_type == "MARKET" else "SL"
    return order_type


def map_order_data(order_data):
    mapped = []
    for row in _extract_rows(order_data):
        if not isinstance(row, dict):
            continue
        quantity = _coerce_float(row.get("qty"), None)
        filled = _coerce_float(row.get("cumExecQty"), 0.0)
        if quantity is None:
            raise ValueError("Bybit account data could not be read. Try again later.")
        order_id = row.get("orderId")
        if not order_id:
            raise ValueError("Bybit account data could not be read. Try again later.")
        mapped.append(
            {
                "orderid": str(order_id),
                "symbol": _canonical_symbol(row),
                "exchange": "CRYPTO",
                "category": row["category"],
                "action": str(row.get("side") or "").upper(),
                "quantity": quantity,
                "filledqty": filled,
                "pendingqty": max(quantity - filled, 0.0),
                "price": _coerce_float(row.get("price"), 0.0),
                "trigger_price": _coerce_float(row.get("triggerPrice"), 0.0),
                "pricetype": _order_type(row),
                "product": "NRML",
                "order_status": _status_to_openalgo(row.get("orderStatus")),
                "timestamp": _timestamp(row.get("createdTime"), include_date=True),
                "reduce_only": bool(row.get("reduceOnly", False)),
            }
        )
    return mapped


def map_trade_data(trade_data):
    mapped = []
    for row in _extract_rows(trade_data):
        if not isinstance(row, dict):
            continue
        quantity = _coerce_float(row.get("execQty"), None)
        price = _coerce_float(row.get("execPrice"), None)
        if quantity is None or price is None:
            raise ValueError("Bybit account data could not be read. Try again later.")
        mapped.append(
            {
                "tradeid": str(row.get("execId") or ""),
                "orderid": str(row.get("orderId") or ""),
                "symbol": _canonical_symbol(row),
                "exchange": "CRYPTO",
                "category": row["category"],
                "action": str(row.get("side") or "").upper(),
                "quantity": quantity,
                "average_price": price,
                "product": "NRML",
                "timestamp": _timestamp(row.get("execTime")),
                "trade_value": _coerce_float(row.get("execValue"), None),
            }
        )
    return mapped


def map_position_data(raw):
    mapped = []
    for row in _extract_rows(raw):
        if not isinstance(row, dict):
            continue
        size = _coerce_float(row.get("size"), 0.0)
        if not size:
            continue
        side = str(row.get("side") or "").upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("Bybit account data could not be read. Try again later.")
        mapped.append(
            {
                "symbol": _canonical_symbol(row),
                "exchange": "CRYPTO",
                "category": row["category"],
                "product": "NRML",
                "quantity": size if side == "BUY" else -size,
                "average_price": _coerce_float(row.get("avgPrice"), 0.0),
                "ltp": _coerce_float(row.get("markPrice"), 0.0),
                "pnl": _coerce_float(row.get("unrealisedPnl"), 0.0),
                "side": side,
                "position_idx": row.get("positionIdx", 0),
            }
        )
    return mapped


def map_portfolio_data(raw):
    mapped = []
    for wallet in _extract_rows(raw):
        if not isinstance(wallet, dict):
            continue
        coins = wallet.get("coin")
        if not isinstance(coins, list):
            raise ValueError("Bybit account data could not be read. Try again later.")
        for row in coins:
            if not isinstance(row, dict):
                raise ValueError("Bybit account data could not be read. Try again later.")
            coin = row.get("coin")
            if not isinstance(coin, str) or not coin:
                raise ValueError("Bybit account data could not be read. Try again later.")
            mapped.append(
                {
                    "symbol": coin,
                    "exchange": "CRYPTO",
                    "asset_type": "account_coin_balance",
                    "quantity": _required_float(row.get("equity")),
                    "usd_value": _required_float(row.get("usdValue")),
                    "currency": "USD",
                }
            )
    return mapped


def calculate_order_statistics(rows):
    rows = rows or []
    return {
        "total_buy_orders": sum(row.get("action") == "BUY" for row in rows),
        "total_sell_orders": sum(row.get("action") == "SELL" for row in rows),
        "total_completed_orders": sum(row.get("order_status") == "complete" for row in rows),
        "total_open_orders": sum(row.get("order_status") in {"open", "pending"} for row in rows),
        "total_rejected_orders": sum(row.get("order_status") == "rejected" for row in rows),
    }


def calculate_portfolio_statistics(rows):
    rows = rows or []
    return {
        "total_value": sum(_coerce_float(row.get("usd_value"), 0.0) for row in rows),
        "total_positions": len(rows),
        "currency": "USD",
    }


def transform_order_data(rows):
    return rows


def transform_tradebook_data(rows):
    return rows


def transform_positions_data(rows):
    return rows


def transform_holdings_data(rows):
    return rows
