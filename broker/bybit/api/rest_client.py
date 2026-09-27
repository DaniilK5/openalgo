"""Shared pybit-backed REST client for Bybit V5 endpoints."""

import atexit
import hashlib
import json
import logging
import os
import sys
import threading

from pybit.exceptions import FailedRequestError, InvalidRequestError
from pybit.unified_trading import HTTP

from broker.bybit.api.baseurl import get_base_url, is_testnet
from utils.logging import get_logger

logger = get_logger(__name__)

_HTTP_METHODS = {
    ("GET", "/v5/account/wallet-balance"): "get_wallet_balance",
    ("GET", "/v5/execution/list"): "get_executions",
    ("GET", "/v5/market/instruments-info"): "get_instruments_info",
    ("GET", "/v5/market/kline"): "get_kline",
    ("GET", "/v5/market/orderbook"): "get_orderbook",
    ("GET", "/v5/market/tickers"): "get_tickers",
    ("GET", "/v5/market/time"): "get_server_time",
    ("GET", "/v5/order/history"): "get_order_history",
    ("GET", "/v5/order/realtime"): "get_open_orders",
    ("GET", "/v5/position/list"): "get_positions",
    ("POST", "/v5/order/amend"): "amend_order",
    ("POST", "/v5/order/cancel"): "cancel_order",
    ("POST", "/v5/order/create"): "place_order",
}

_client = None
_client_fingerprint = None
_client_lock = threading.Lock()
_request_lock = threading.Lock()


class BybitSDKResponse:
    """Response facade for existing Bybit broker code consuming SDK results."""

    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.http_version = "pybit"
        self.headers = {}
        self.text = json.dumps(payload, separators=(",", ":"))
        self.content = self.text.encode("utf-8")

    def json(self):
        return self._payload


def _credentials(api_key=None):
    key = (api_key or os.getenv("BROKER_API_KEY", "")).strip()
    secret = os.getenv("BROKER_API_SECRET", "").strip()
    return key, secret


def _request_logging_enabled():
    return os.getenv("BYBIT_LOG_REQUESTS", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _safe_ret_msg(message):
    if "origin_string[" in message.lower():
        return "signature validation failed (signed details redacted)"
    return message


def get_client(api_key=None):
    """Return one reusable pybit session, replacing it when its config changes."""
    global _client, _client_fingerprint

    key, secret = _credentials(api_key)
    endpoint = get_base_url()
    fingerprint = hashlib.sha256(f"{key}\0{secret}\0{endpoint}".encode()).digest()

    with _client_lock:
        if _client is not None and fingerprint == _client_fingerprint:
            return _client

        previous_client = _client
        client = HTTP(
            testnet=is_testnet(),
            api_key=key or None,
            api_secret=secret or None,
            timeout=30,
            recv_window=10000,
            max_retries=3,
            force_retry=True,
            log_requests=False,
            logging_level=logging.WARNING,
        )
        client.endpoint = endpoint
        _client = client
        _client_fingerprint = fingerprint

    if previous_client is not None:
        previous_client.client.close()
    return client


def close_client():
    """Close the shared requests session during application shutdown."""
    global _client, _client_fingerprint

    with _client_lock:
        client = _client
        _client = None
        _client_fingerprint = None

    if client is not None:
        client.client.close()


atexit.register(close_client)


def request(endpoint, method="GET", params=None, payload=None, api_key=None):
    """Call a typed pybit V5 method and normalize SDK errors for broker callers."""
    method = method.upper()
    sdk_method = _HTTP_METHODS.get((method, endpoint))
    if sdk_method is None:
        raise ValueError(f"Unsupported Bybit API operation: {method} {endpoint}")

    arguments = params if method == "GET" else payload
    arguments = arguments or {}

    if _request_logging_enabled():
        logger.info(
            "Bybit SDK request: method=%s endpoint=%s parameter_names=%s",
            method,
            endpoint,
            ",".join(sorted(arguments)),
        )

    try:
        client = get_client(api_key)
        if "eventlet" in sys.modules:
            import eventlet

            with _request_lock:
                result = eventlet.tpool.execute(
                    getattr(client, sdk_method),
                    **arguments,
                )
        else:
            result = getattr(client, sdk_method)(**arguments)
    except InvalidRequestError as exc:
        logger.warning(
            "Bybit request rejected for %s (retCode=%s retMsg=%s)",
            endpoint,
            exc.status_code,
            _safe_ret_msg(exc.message),
        )
        raise ValueError(
            "Bybit rejected the account request. Check the account settings and try again."
        ) from exc
    except FailedRequestError as exc:
        logger.warning(
            "Bybit request failed for %s (HTTP status=%s)",
            endpoint,
            exc.status_code,
        )
        raise ValueError(
            "Bybit could not provide the requested data. Check the API connection and try again."
        ) from exc

    if not isinstance(result, dict):
        raise ValueError("Bybit returned an invalid API response. Try again later.")
    if _request_logging_enabled():
        logger.info(
            "Bybit SDK response: endpoint=%s retCode=%s",
            endpoint,
            result.get("retCode"),
        )
    return result


def request_response(endpoint, method="GET", params=None, payload=None, api_key=None):
    """Return a response facade where existing broker code expects HTTP metadata."""
    return BybitSDKResponse(
        request(
            endpoint,
            method=method,
            params=params,
            payload=payload,
            api_key=api_key,
        )
    )


def get_server_time_ms(api_key=None):
    data = request("/v5/market/time", api_key=api_key)
    if "time" in data:
        return int(data["time"])

    result = data.get("result")
    time_nano = result.get("timeNano") if isinstance(result, dict) else None
    if time_nano is not None:
        return int(time_nano) // 1_000_000
    raise ValueError("Bybit server-time response is malformed")
