def _coerce_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_rows(raw):
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        if isinstance(raw.get("result"), list):
            return raw["result"]
        result = raw.get("result") or {}
        if isinstance(result, dict):
            for key in ("list", "rows", "data"):
                rows = result.get(key)
                if isinstance(rows, list):
                    return rows
        if isinstance(raw.get("data"), list):
            return raw["data"]
    return []


def _status_to_openalgo(status):
    status = str(status or "").lower()
    if status in {"new", "created", "open", "partiallyfilled", "partially_filled", "pending", "working"}:
        return "open"
    if status in {"filled", "complete", "completed", "closed"}:
        return "complete"
    if status in {"cancelled", "canceled", "cancel"}:
        return "cancelled"
    if status in {"rejected", "failed", "expired"}:
        return "rejected"
    return status or "open"


def _side_to_openalgo(side):
    side = str(side or "").upper()
    if side in {"BUY", "LONG"}:
        return "BUY"
    if side in {"SELL", "SHORT"}:
        return "SELL"
    return side


def map_order_data(raw):
    rows = _extract_rows(raw)
    mapped = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        mapped.append({
            "orderId": row.get("orderId") or row.get("order_id") or row.get("id"),
            "symbol": row.get("symbol", ""),
            "exchange": "CRYPTO",
            "action": _side_to_openalgo(row.get("side")),
            "quantity": row.get("qty") or row.get("orderQty") or row.get("order_qty") or 0,
            "price": row.get("price") or row.get("avgPrice") or row.get("cumExecValue") or 0,
            "trigger_price": row.get("triggerPrice") or 0,
            "pricetype": row.get("orderType") or row.get("order_type") or "LIMIT",
            "product": "NRML",
            "order_status": _status_to_openalgo(row.get("orderStatus") or row.get("status")),
            "timestamp": row.get("createdTime") or row.get("updateTime") or row.get("updatedTime") or 0,
        })
    return mapped


def map_trade_data(raw):
    rows = _extract_rows(raw)
    mapped = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        mapped.append({
            "symbol": row.get("symbol", ""),
            "exchange": "CRYPTO",
            "action": _side_to_openalgo(row.get("side")),
            "quantity": row.get("execQty") or row.get("qty") or 0,
            "price": row.get("execPrice") or row.get("price") or 0,
            "product": "NRML",
            "timestamp": row.get("tradeTime") or row.get("execTime") or row.get("createdTime") or 0,
        })
    return mapped


def map_position_data(raw):
    rows = _extract_rows(raw)
    mapped = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        size = _coerce_float(row.get("size"), 0.0)
        mapped.append({
            "symbol": row.get("symbol", ""),
            "exchange": "CRYPTO",
            "product": row.get("positionIdx") if row.get("positionIdx") is not None else "NRML",
            "quantity": size,
            "avg_price": _coerce_float(row.get("avgPrice"), 0.0),
            "ltp": _coerce_float(row.get("markPrice") or row.get("lastPrice"), 0.0),
            "pnl": _coerce_float(row.get("unrealisedPnl") or row.get("unrealizedPnl"), 0.0),
            "side": "BUY" if size > 0 else "SELL",
        })
    return mapped


def map_portfolio_data(raw):
    rows = _extract_rows(raw)
    mapped = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        size = _coerce_float(row.get("size") or row.get("qty") or row.get("positionValue"), 0.0)
        mapped.append({
            "symbol": row.get("symbol", ""),
            "exchange": "CRYPTO",
            "quantity": size,
            "avg_price": _coerce_float(row.get("avgPrice") or row.get("entryPrice"), 0.0),
            "ltp": _coerce_float(row.get("markPrice") or row.get("lastPrice"), 0.0),
            "pnl": _coerce_float(row.get("unrealisedPnl") or row.get("unrealizedPnl"), 0.0),
            "side": "BUY" if size > 0 else "SELL",
        })
    return mapped


def calculate_order_statistics(rows):
    total = len(rows or [])
    return {"total": total, "filled": 0, "pending": total}


def calculate_portfolio_statistics(rows):
    total_value = 0.0
    total_pnl = 0.0
    for row in rows or []:
        total_value += _coerce_float(row.get("quantity")) * _coerce_float(row.get("ltp"))
        total_pnl += _coerce_float(row.get("pnl"))
    return {"total_value": total_value, "total_pnl": total_pnl, "total_positions": len(rows or [])}


def transform_order_data(rows):
    return map_order_data(rows)


def transform_tradebook_data(rows):
    return map_trade_data(rows)


def transform_positions_data(rows):
    return map_position_data(rows)


def transform_holdings_data(rows):
    return map_portfolio_data(rows)
