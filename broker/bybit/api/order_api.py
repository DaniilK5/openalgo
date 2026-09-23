import json
import os

from broker.bybit.api.baseurl import get_auth_headers, get_url
from utils.httpx_client import get_httpx_client
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
        except Exception:
            return {}
    return {}


def _signed_request(endpoint, auth, method="GET", params=None, payload=None):
    params = params or {}
    api_secret = os.getenv("BROKER_API_SECRET", "").strip()
    body = json.dumps(payload, separators=(",", ":")) if payload else ""
    headers = get_auth_headers(
        method=method,
        path=endpoint,
        params=params,
        payload=body,
        api_key=auth,
        api_secret=api_secret,
    )

    client = get_httpx_client()
    method_upper = method.upper()
    if method_upper == "GET":
        response = client.get(get_url(endpoint), params=params, headers=headers, timeout=30.0)
    elif method_upper == "DELETE":
        response = client.delete(get_url(endpoint), params=params, headers=headers, timeout=30.0)
    else:
        response = client.post(get_url(endpoint), params=params, data=body, headers=headers, timeout=30.0)
    return response


def place_order_api(data, auth):
    payload = {
        "category": "linear",
        "symbol": data.get("symbol"),
        "side": (data.get("action") or data.get("side") or "BUY").upper(),
        "orderType": (data.get("pricetype") or data.get("ordertype") or "LIMIT").upper().replace("-M", "-M"),
        "qty": data.get("quantity"),
        "price": data.get("price"),
        "timeInForce": data.get("time_in_force") or "GTC",
        "reduceOnly": bool(data.get("reduce_only", False)),
        "positionIdx": 0,
    }
    if payload["orderType"] == "SL-M":
        payload["triggerPrice"] = data.get("trigger_price") or data.get("price")
    elif payload["orderType"] == "SL":
        payload["triggerPrice"] = data.get("trigger_price") or data.get("price")
    response = _signed_request("/v5/order/create", auth, method="POST", payload=payload)
    response_data = _as_dict(response.text if response.content else {})
    orderid = None
    result = response_data.get("result", {}) if isinstance(response_data, dict) else {}
    if isinstance(result, dict):
        orderid = result.get("orderId") or result.get("order_id")
    return response, response_data, orderid


def place_smartorder_api(data, auth):
    return place_order_api(data, auth)


def modify_order(data, auth):
    payload = {
        "category": "linear",
        "symbol": data.get("symbol"),
        "orderId": data.get("orderid") or data.get("order_id"),
        "qty": data.get("quantity"),
        "price": data.get("price"),
    }
    if data.get("trigger_price") is not None:
        payload["triggerPrice"] = data.get("trigger_price")
    response = _signed_request("/v5/order/amend", auth, method="POST", payload=payload)
    return _as_dict(response.text if response.content else {}), response.status_code


def cancel_order(orderid, auth):
    payload = {"category": "linear", "orderId": orderid}
    response = _signed_request("/v5/order/cancel", auth, method="POST", payload=payload)
    return _as_dict(response.text if response.content else {}), response.status_code


def cancel_all_orders_api(data, auth):
    payload = {"category": "linear", "symbol": data.get("symbol")}
    response = _signed_request("/v5/order/cancel-all", auth, method="POST", payload=payload)
    response_data = _as_dict(response.text if response.content else {})
    result = response_data.get("result", {})
    return result.get("cancelledOrderIds", []), result.get("failedOrders", [])


def close_all_positions(api_key, auth):
    payload = {"category": "linear", "settleCoin": "USDT"}
    response = _signed_request("/v5/position/close-pnl", auth, method="POST", payload=payload)
    return _as_dict(response.text if response.content else {}), response.status_code


def get_order_book(auth):
    response = _signed_request("/v5/order/history", auth, method="GET", params={"category": "linear"})
    return _as_dict(response.text if response.content else {})


def get_trade_book(auth):
    response = _signed_request("/v5/execution/list", auth, method="GET", params={"category": "linear"})
    return _as_dict(response.text if response.content else {})


def get_positions(auth):
    response = _signed_request("/v5/position/list", auth, method="GET", params={"category": "linear"})
    return _as_dict(response.text if response.content else {})


def get_holdings(auth):
    """Return the unified account positions as a holdings-like payload."""
    return get_positions(auth)


def get_open_position(symbol, exchange, product, auth):
    response = _signed_request("/v5/position/list", auth, method="GET", params={"category": "linear", "symbol": symbol})
    data = _as_dict(response.text if response.content else {})
    result = data.get("result", {})
    positions = result.get("list", []) if isinstance(result, dict) else []
    for pos in positions:
        if pos.get("symbol") == symbol:
            return str(pos.get("size", "0") or "0")
    return "0"
