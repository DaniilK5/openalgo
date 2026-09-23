import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

BASE_URL = os.getenv("BYBIT_BASE_URL", "https://api.bybit.com")


def get_url(endpoint: str) -> str:
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    return BASE_URL + endpoint


def _encode_params(params):
    if not params:
        return ""
    if isinstance(params, str):
        return params.lstrip("?")
    if isinstance(params, dict):
        return urlencode(sorted(params.items()), doseq=True)
    return urlencode(params, doseq=True)


def get_auth_headers(
    method: str,
    path: str,
    params=None,
    payload: str = "",
    api_key: str | None = None,
    api_secret: str | None = None,
    recv_window: int = 5000,
):
    key = (api_key or os.getenv("BROKER_API_KEY", "")).strip()
    secret = (api_secret or os.getenv("BROKER_API_SECRET", "")).strip()
    timestamp = str(int(time.time() * 1000))
    query_string = _encode_params(params)
    signed_payload = f"{timestamp}{key}{recv_window}{query_string}{payload}"
    signature = hmac.new(secret.encode("utf-8"), signed_payload.encode("utf-8"), hashlib.sha256).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-BAPI-APIKEY": key,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-SIGN": signature,
        "X-BAPI-RECV-WINDOW": str(recv_window),
    }
    return headers
