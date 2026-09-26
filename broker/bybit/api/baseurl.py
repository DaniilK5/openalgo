import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

MAINNET_BASE_URL = "https://api.bybit.com"
TESTNET_BASE_URL = "https://api-testnet.bybit.com"
MAINNET_WS_HOST = "stream.bybit.com"
TESTNET_WS_HOST = "stream-testnet.bybit.com"


def is_testnet() -> bool:
    value = os.getenv("BYBIT_TESTNET", "false").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError("BYBIT_TESTNET must be set to true or false.")


def get_base_url() -> str:
    if is_testnet():
        return TESTNET_BASE_URL
    return os.getenv("BYBIT_BASE_URL", MAINNET_BASE_URL).strip().rstrip("/")


def get_url(endpoint: str) -> str:
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    return get_base_url() + endpoint


def get_public_ws_url(category: str = "linear") -> str:
    host = TESTNET_WS_HOST if is_testnet() else MAINNET_WS_HOST
    return f"wss://{host}/v5/public/{category}"


def get_private_ws_url() -> str:
    host = TESTNET_WS_HOST if is_testnet() else MAINNET_WS_HOST
    return f"wss://{host}/v5/private"


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
    signature = hmac.new(
        secret.encode("utf-8"), signed_payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-BAPI-API-KEY": key,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-SIGN": signature,
        "X-BAPI-RECV-WINDOW": str(recv_window),
    }
    return headers


def get_server_time_ms(client):
    """Return Bybit server time in milliseconds for clock-skew diagnostics."""
    response = client.get(get_url("/v5/market/time"), timeout=10.0)
    response.raise_for_status()
    data = response.json()
    if data.get("retCode") != 0:
        raise ValueError(data.get("retMsg") or "Bybit time endpoint returned an error")
    return int(data["time"])
