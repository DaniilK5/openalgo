import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from broker.bybit.api.rest_client import request, request_response
from database.token_db import get_br_symbol, get_symbol_info
from utils.logging import get_logger

logger = get_logger(__name__)


def _as_dict(payload):
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError("Bybit account data could not be read. Try again later.") from exc
    return {}


def _signed_request(endpoint, auth, method="GET", params=None, payload=None):
    return request_response(
        endpoint,
        method=method,
        params=params,
        payload=payload,
        api_key=auth,
    )


def _utc_day_bounds():
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _fetch_pages(endpoint, auth, params, page_size=50, max_pages=100):
    rows = []
    cursor = None
    seen_cursors = set()
    for _ in range(max_pages):
        page_params = dict(params)
        page_params["limit"] = page_size
        if cursor:
            page_params["cursor"] = cursor
        response = _signed_request(endpoint, auth, method="GET", params=page_params)
        payload = _as_dict(response.text if response.content else {})
        result = payload.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("list"), list):
            raise ValueError("Bybit account data could not be read. Try again later.")
        rows.extend(
            dict(row, category=params["category"])
            for row in result["list"]
            if isinstance(row, dict)
        )
        next_cursor = result.get("nextPageCursor") or None
        if not next_cursor:
            return rows
        if next_cursor in seen_cursors:
            raise ValueError("Bybit account history could not be fully retrieved. Try again later.")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise ValueError("Bybit account history could not be fully retrieved. Try again later.")


def _filter_to_current_utc_day(rows, start_ms, end_ms, time_field="createdTime"):
    filtered = []
    for row in rows:
        try:
            created_time = int(row[time_field])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Bybit account data could not be read. Try again later.") from exc
        if start_ms <= created_time <= end_ms:
            filtered.append(row)
    return filtered


def _merge_by_order_id(*groups):
    merged = {}
    for rows in groups:
        for row in rows:
            order_id = row.get("orderId")
            if order_id:
                merged[str(order_id)] = row
    return list(merged.values())


def _realtime_param_candidates(category, symbol=None):
    if symbol:
        return [{"category": category, "symbol": symbol}]

    candidates = [{"category": category}]
    if category == "linear":
        for settle_coin in ("USDT", "USDC"):
            candidates.append({"category": category, "settleCoin": settle_coin})
    elif category == "inverse":
        for settle_coin in ("BTC", "ETH", "USD"):
            candidates.append({"category": category, "settleCoin": settle_coin})
    elif category == "option":
        for base_coin in ("BTC", "ETH"):
            candidates.append({"category": category, "baseCoin": base_coin})
    return candidates


def _fetch_realtime_orders(auth, category, symbol=None):
    rows = []
    last_error = None
    request_succeeded = False
    for index, params in enumerate(_realtime_param_candidates(category, symbol=symbol)):
        try:
            fetched_rows = _fetch_pages("/v5/order/realtime", auth, params, page_size=50)
        except ValueError as exc:
            last_error = exc
            continue
        request_succeeded = True
        if symbol or index == 0:
            rows = fetched_rows
            break
        rows.extend(fetched_rows)

    if not request_succeeded and last_error is not None:
        raise last_error

    deduplicated = {}
    for row in rows:
        order_id = row.get("orderId")
        key = (
            str(order_id)
            if order_id
            else f"{row.get('category')}:{row.get('symbol')}:{row.get('createdTime')}"
        )
        deduplicated[key] = row
    return list(deduplicated.values())


def _order_instrument(data):
    symbol = data.get("symbol")
    exchange = data.get("exchange")
    if exchange != "CRYPTO" or not symbol:
        raise ValueError("Bybit orders require a valid CRYPTO symbol.")
    instrument = get_symbol_info(symbol, exchange)
    category = getattr(instrument, "category", None) if instrument else None
    broker_symbol = get_br_symbol(symbol, exchange)
    if not instrument or category not in {"spot", "linear", "inverse", "option"}:
        raise ValueError("Bybit instrument metadata is missing. Update the symbol list and retry.")
    if not broker_symbol:
        raise ValueError("Bybit symbol mapping is missing. Update the symbol list and retry.")
    return instrument, category, broker_symbol


def _decimal(value, field):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Bybit {field} must be a valid number.") from exc
    if not number.is_finite():
        raise ValueError(f"Bybit {field} must be a valid number.")
    return number


def _decimal_text(value):
    text_value = format(value, "f")
    return text_value.rstrip("0").rstrip(".") if "." in text_value else text_value


def _validate_order_price(instrument, price):
    normalized_price = _decimal(price, "price")
    tick_value = getattr(instrument, "tick_size", None)
    if tick_value is None:
        raise ValueError("Bybit price precision is unavailable for this symbol.")
    tick = _decimal(tick_value, "price tick")
    if normalized_price <= 0 or tick <= 0 or normalized_price % tick:
        raise ValueError("Bybit order price does not match the symbol's price precision.")
    return normalized_price


def _validate_order_amount(instrument, quantity, price, category, order_type):
    qty = _decimal(quantity, "quantity")
    if qty <= 0:
        raise ValueError("Bybit order quantity must be greater than zero.")

    step_value = (
        getattr(instrument, "qty_step", None)
        or getattr(instrument, "base_precision", None)
    )
    if step_value is None:
        raise ValueError("Bybit quantity precision is unavailable for this symbol.")
    step = _decimal(step_value, "quantity step")
    if step <= 0 or qty % step:
        raise ValueError("Bybit order quantity does not match the symbol's quantity precision.")

    minimum = getattr(instrument, "min_qty", None)
    maximum = getattr(instrument, "max_qty", None)
    if minimum is not None and qty < _decimal(minimum, "minimum quantity"):
        raise ValueError("Bybit order quantity is below the symbol minimum.")
    if maximum is not None and qty > _decimal(maximum, "maximum quantity"):
        raise ValueError("Bybit order quantity is above the symbol maximum.")

    category_limit = (
        getattr(instrument, "max_market_qty", None)
        if order_type == "Market"
        else getattr(instrument, "max_limit_qty", None)
    )
    if category_limit is not None and qty > _decimal(category_limit, "maximum order quantity"):
        raise ValueError("Bybit order quantity exceeds the symbol limit.")

    normalized_price = None
    if price not in (None, ""):
        normalized_price = _validate_order_price(instrument, price)

    if category == "spot" and order_type == "Limit" and normalized_price is not None:
        minimum_amount = getattr(instrument, "min_order_amt", None)
        if minimum_amount is not None and qty * normalized_price < _decimal(
            minimum_amount, "minimum order amount"
        ):
            raise ValueError("Bybit spot order is below the symbol's minimum order value.")

    return qty, normalized_price


def _validate_spot_quote_amount(instrument, amount):
    quote_amount = _decimal(amount, "quote amount")
    if quote_amount <= 0:
        raise ValueError("Bybit quote amount must be greater than zero.")

    precision_value = getattr(instrument, "quote_precision", None)
    if precision_value is None:
        raise ValueError("Bybit quote precision is unavailable for this symbol.")
    precision = _decimal(precision_value, "quote precision")
    if precision <= 0 or quote_amount % precision:
        raise ValueError("Bybit quote amount does not match the symbol's quote precision.")

    minimum = getattr(instrument, "min_order_amt", None)
    if minimum is None:
        raise ValueError("Bybit minimum order value is unavailable for this symbol.")
    if quote_amount < _decimal(minimum, "minimum order amount"):
        raise ValueError("Bybit spot order is below the symbol's minimum order value.")

    return quote_amount


def _market_unit(data):
    supplied = [
        data[key] for key in ("market_unit", "marketUnit") if key in data and data[key] is not None
    ]
    if not supplied:
        return None
    if any(value != supplied[0] for value in supplied[1:]):
        raise ValueError("Bybit market unit fields must match.")

    market_unit = supplied[0]
    if not isinstance(market_unit, str) or market_unit not in {"baseCoin", "quoteCoin"}:
        raise ValueError("Bybit market unit must be baseCoin or quoteCoin.")
    return market_unit


def _trigger_direction(category, broker_symbol, trigger_price):
    data = request(
        "/v5/market/tickers",
        params={"category": category, "symbol": broker_symbol},
    )
    result = data.get("result")
    rows = result.get("list") if isinstance(result, dict) else None
    if not rows or not isinstance(rows[0], dict):
        raise ValueError("Bybit could not verify the stop trigger. Try again later.")
    last_price = _decimal(rows[0].get("lastPrice"), "last traded price")
    trigger = _decimal(trigger_price, "trigger price")
    if trigger == last_price:
        raise ValueError("Bybit stop trigger must differ from the current market price.")
    return 1 if trigger > last_price else 2


def _order_response(response):
    return SimpleNamespace(
        status=response.status_code,
        status_code=response.status_code,
        text=response.text,
        content=response.content,
    )


def place_order_api(data, auth):
    instrument, category, broker_symbol = _order_instrument(data)
    requested_category = data.get("category")
    if requested_category is not None and requested_category != category:
        raise ValueError("Bybit order category does not match the symbol's instrument category.")

    action = str(data.get("action") or data.get("side") or "").upper()
    if action not in {"BUY", "SELL"}:
        raise ValueError("Bybit order action must be BUY or SELL.")

    requested_type = str(data.get("pricetype") or data.get("ordertype") or "").upper()
    order_type_map = {
        "MARKET": "Market",
        "LIMIT": "Limit",
        "SL": "Limit",
        "SL-M": "Market",
    }
    if requested_type not in order_type_map:
        raise ValueError("This Bybit order type is not supported.")
    order_type = order_type_map[requested_type]
    if order_type == "Limit" and data.get("price") in (None, ""):
        raise ValueError("Bybit limit orders require a price.")

    if requested_type in {"SL", "SL-M"} and category not in {"linear", "inverse"}:
        raise ValueError("Bybit stop orders are currently supported for derivatives only.")
    if category == "spot" and str(data.get("product") or "CNC").upper() not in {"CNC", "NRML"}:
        raise ValueError("Bybit spot orders do not support this product type.")
    if category != "spot" and str(data.get("product") or "NRML").upper() not in {"NRML", "MIS"}:
        raise ValueError("Bybit derivative orders do not support this product type.")

    market_unit = _market_unit(data)
    if market_unit is not None and (category != "spot" or order_type != "Market"):
        raise ValueError("Bybit market units are supported for spot market orders only.")
    if market_unit == "quoteCoin" and action != "BUY":
        raise ValueError("Bybit spot market sells must use base coin quantity.")

    if market_unit == "quoteCoin":
        quote_coin = str(getattr(instrument, "quote_coin", "") or "").upper()
        if quote_coin not in {"USDT", "USDC"}:
            raise ValueError("Bybit quote-budget market buys require a USDT or USDC quote coin.")
        qty = _validate_spot_quote_amount(instrument, data.get("quantity"))
        price = None
    else:
        qty, price = _validate_order_amount(
            instrument,
            data.get("quantity"),
            data.get("price") if order_type == "Limit" else None,
            category,
            order_type,
        )

    payload = {
        "category": category,
        "symbol": broker_symbol,
        "side": action.title(),
        "orderType": order_type,
        "qty": _decimal_text(qty),
    }
    order_link_id = data.get("order_link_id")
    if order_link_id is not None:
        if not isinstance(order_link_id, str) or not 1 <= len(order_link_id) <= 36:
            raise ValueError("Bybit order link ID must contain 1 to 36 characters.")
        payload["orderLinkId"] = order_link_id
    if market_unit is not None:
        payload["marketUnit"] = market_unit
    if price is not None:
        payload["price"] = _decimal_text(price)

    if order_type == "Limit":
        time_in_force = str(data.get("time_in_force") or data.get("validity") or "GTC").upper()
        if time_in_force not in {"GTC", "IOC", "FOK"}:
            raise ValueError("Bybit supports GTC, IOC, or FOK order validity.")
        if data.get("post_only") is True:
            if time_in_force != "GTC":
                raise ValueError("Bybit post-only orders require GTC validity.")
            time_in_force = "PostOnly"
        payload["timeInForce"] = time_in_force

    reduce_only = data.get("reduce_only") is True or data.get("reduceOnly") is True
    if reduce_only:
        if category not in {"linear", "inverse"}:
            raise ValueError("Bybit reduce-only orders are supported for linear and inverse only.")
        payload["reduceOnly"] = True
    if category in {"linear", "inverse"}:
        position_idx = data.get("position_idx", 0)
        if position_idx not in (0, 1, 2, "0", "1", "2"):
            raise ValueError("Bybit position index must be 0, 1, or 2.")
        payload["positionIdx"] = int(position_idx)

    if requested_type in {"SL", "SL-M"}:
        trigger_value = data.get("trigger_price")
        if trigger_value in (None, ""):
            trigger_value = data.get("price")
        if trigger_value in (None, ""):
            raise ValueError("Bybit stop orders require a trigger price.")
        trigger = _decimal(trigger_value, "trigger price")
        tick = _decimal(instrument.tick_size, "price tick")
        if trigger <= 0 or tick <= 0 or trigger % tick:
            raise ValueError("Bybit trigger price does not match the symbol's price precision.")
        payload["triggerPrice"] = _decimal_text(trigger)
        payload["triggerDirection"] = _trigger_direction(category, broker_symbol, trigger)
        payload["triggerBy"] = "LastPrice"

    response = _signed_request("/v5/order/create", auth, method="POST", payload=payload)
    response_data = _as_dict(response.text if response.content else {})
    orderid = None
    result = response_data.get("result", {}) if isinstance(response_data, dict) else {}
    if isinstance(result, dict):
        orderid = result.get("orderId") or result.get("order_id")
    if not orderid:
        raise ValueError("Bybit accepted no order ID. Check the account order book before retrying.")
    return _order_response(response), response_data, orderid


def place_smartorder_api(data, auth):
    return place_order_api(data, auth)


def modify_order(data, auth):
    instrument, category, broker_symbol = _order_instrument(data)
    order_id = data.get("orderid") or data.get("order_id")
    if not order_id:
        raise ValueError("Bybit order ID is required to modify an order.")
    payload = {
        "category": category,
        "symbol": broker_symbol,
        "orderId": order_id,
    }
    if data.get("quantity") not in (None, ""):
        qty, _ = _validate_order_amount(
            instrument,
            data["quantity"],
            None,
            category,
            "Limit",
        )
        payload["qty"] = _decimal_text(qty)
    if data.get("price") not in (None, "", 0, 0.0, "0"):
        price = _validate_order_price(instrument, data["price"])
        payload["price"] = _decimal_text(price)
    if data.get("trigger_price") not in (None, "", 0, 0.0, "0"):
        if category not in {"linear", "inverse"}:
            raise ValueError("Bybit stop orders are currently supported for derivatives only.")
        trigger = _decimal(data["trigger_price"], "trigger price")
        tick = _decimal(instrument.tick_size, "price tick")
        if trigger <= 0 or tick <= 0 or trigger % tick:
            raise ValueError("Bybit trigger price does not match the symbol's price precision.")
        payload["triggerPrice"] = _decimal_text(trigger)
        payload["triggerDirection"] = _trigger_direction(category, broker_symbol, trigger)
        payload["triggerBy"] = "LastPrice"
    if len(payload) == 3:
        raise ValueError("Provide a new Bybit order quantity, price, or trigger price.")
    response = _signed_request("/v5/order/amend", auth, method="POST", payload=payload)
    return _as_dict(response.text if response.content else {}), response.status_code


def cancel_order(orderid, auth):
    for category in ("spot", "linear", "inverse", "option"):
        response = _signed_request(
            "/v5/order/realtime",
            auth,
            method="GET",
            params={"category": category, "orderId": orderid},
        )
        data = _as_dict(response.text if response.content else {})
        result = data.get("result")
        rows = result.get("list") if isinstance(result, dict) else None
        matching = next(
            (
                row
                for row in rows or []
                if isinstance(row, dict) and str(row.get("orderId")) == str(orderid)
            ),
            None,
        )
        if matching:
            payload = {
                "category": category,
                "symbol": matching["symbol"],
                "orderId": orderid,
            }
            cancel_response = _signed_request(
                "/v5/order/cancel", auth, method="POST", payload=payload
            )
            return _as_dict(cancel_response.text if cancel_response.content else {}), cancel_response.status_code
    raise ValueError("Bybit could not find this order among active orders.")


def cancel_all_orders_api(data, auth):
    requested_symbol = data.get("symbol")
    if requested_symbol:
        _, selected_category, broker_symbol = _order_instrument(data)
        categories = (selected_category,)
    else:
        broker_symbol = None
        categories = ("spot", "linear", "inverse", "option")

    cancelled = []
    failed = []
    for category in categories:
        rows = _fetch_realtime_orders(auth, category, symbol=broker_symbol)
        for row in rows:
            order_id = row.get("orderId")
            symbol = row.get("symbol")
            if not order_id or not symbol:
                failed.append(str(order_id or "unknown"))
                continue
            try:
                _signed_request(
                    "/v5/order/cancel",
                    auth,
                    method="POST",
                    payload={
                        "category": category,
                        "symbol": symbol,
                        "orderId": order_id,
                    },
                )
            except ValueError:
                failed.append(str(order_id))
            else:
                cancelled.append(str(order_id))
    return cancelled, failed


def close_all_positions(api_key, auth):
    rows = get_positions(auth).get("result", [])
    closed = []
    failed = []
    prepared = []
    for row in rows:
        category = row.get("category")
        if category not in {"linear", "inverse"}:
            failed.append(str(row.get("symbol") or "unknown"))
            continue
        symbol = row.get("symbol")
        side = str(row.get("side") or "").lower()
        size = _decimal(row.get("size"), "position size")
        if not symbol or size <= 0 or side not in {"buy", "sell"}:
            failed.append(str(symbol or "unknown"))
            continue
        try:
            position_idx = int(row.get("positionIdx", 0))
        except (TypeError, ValueError):
            failed.append(str(symbol))
            continue
        if position_idx not in {0, 1, 2}:
            failed.append(str(symbol))
            continue
        prepared.append(
            {
                "category": category,
                "symbol": symbol,
                "side": "Sell" if side == "buy" else "Buy",
                "orderType": "Market",
                "qty": _decimal_text(size),
                "reduceOnly": True,
                "positionIdx": position_idx,
            }
        )

    if failed:
        return {
            "status": "error",
            "message": (
                "Bybit close-all supports linear and inverse positions only. "
                "No closing orders were sent."
            ),
            "closed_positions": [],
            "failed_positions": failed,
        }, 400

    for payload in prepared:
        try:
            _signed_request("/v5/order/create", auth, method="POST", payload=payload)
        except ValueError:
            failed.append(str(payload["symbol"]))
        else:
            closed.append(str(payload["symbol"]))
    status_code = 200 if not failed else 400
    return {
        "status": "success" if not failed else "error",
        "message": (
            "Closing orders were accepted."
            if not failed
            else "Some Bybit positions could not be closed."
        ),
        "closed_positions": closed,
        "failed_positions": failed,
    }, status_code


def get_order_book(auth):
    start_ms, end_ms = _utc_day_bounds()
    rows = []
    for category in ("spot", "linear", "inverse", "option"):
        params = {"category": category, "startTime": start_ms, "endTime": end_ms}
        history = _fetch_pages("/v5/order/history", auth, params)
        realtime = _fetch_realtime_orders(auth, category)
        rows.extend(
            _merge_by_order_id(
                _filter_to_current_utc_day(history, start_ms, end_ms),
                _filter_to_current_utc_day(realtime, start_ms, end_ms),
            )
        )
    return {"result": rows}


def get_trade_book(auth):
    start_ms, end_ms = _utc_day_bounds()
    rows = []
    for category in ("spot", "linear", "inverse", "option"):
        params = {"category": category, "startTime": start_ms, "endTime": end_ms}
        rows.extend(_fetch_pages("/v5/execution/list", auth, params))
    return {"result": _filter_to_current_utc_day(rows, start_ms, end_ms, "execTime")}


def get_positions(auth):
    rows = []
    for settle_coin in ("USDT", "USDC"):
        rows.extend(
            _fetch_pages(
                "/v5/position/list",
                auth,
                {"category": "linear", "settleCoin": settle_coin},
                page_size=200,
            )
        )
    for category in ("inverse", "option"):
        rows.extend(
            _fetch_pages(
                "/v5/position/list",
                auth,
                {"category": category},
                page_size=200,
            )
        )
    deduplicated = {}
    for row in rows:
        key = (row.get("category"), row.get("symbol"), row.get("positionIdx"))
        if row.get("size") not in (None, "", "0", 0, 0.0):
            deduplicated[key] = row
    return {"result": list(deduplicated.values())}


def get_holdings(auth):
    """Return Unified Account coin balances, distinct from derivative positions."""
    response = _signed_request(
        "/v5/account/wallet-balance",
        auth,
        method="GET",
        params={"accountType": "UNIFIED"},
    )
    data = _as_dict(response.text if response.content else {})
    result = data.get("result")
    wallets = result.get("list") if isinstance(result, dict) else None
    if not isinstance(wallets, list) or not wallets:
        raise ValueError(
            "Bybit did not return Unified Account coin balances. "
            "Check that the account is using Unified Account mode."
        )
    return {"result": wallets}


def get_open_position(symbol, exchange, product, auth):
    if exchange != "CRYPTO":
        raise ValueError("Bybit position lookup requires a CRYPTO symbol.")
    instrument = get_symbol_info(symbol, exchange)
    category = getattr(instrument, "category", None) if instrument else None
    broker_symbol = get_br_symbol(symbol, exchange)
    if category == "spot":
        raise ValueError("Bybit smart orders do not support spot coin balances as positions.")
    if category not in {"linear", "inverse", "option"} or not broker_symbol:
        raise ValueError("Bybit position metadata is missing. Update the symbol list and retry.")
    response = _signed_request(
        "/v5/position/list",
        auth,
        method="GET",
        params={"category": category, "symbol": broker_symbol},
    )
    data = _as_dict(response.text if response.content else {})
    result = data.get("result", {})
    positions = result.get("list", []) if isinstance(result, dict) else []
    net_size = Decimal("0")
    for position in positions:
        if not isinstance(position, dict) or position.get("symbol") != broker_symbol:
            continue
        size = _decimal(position.get("size", "0") or "0", "position size")
        side = str(position.get("side") or "").lower()
        if side == "buy":
            net_size += size
        elif side == "sell":
            net_size -= size
        elif size:
            raise ValueError("Bybit position data could not be read. Try again later.")
    return _decimal_text(net_size)
