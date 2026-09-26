import os
import time

<<<<<<< HEAD
from broker.bybit.api.baseurl import get_auth_headers, get_server_time_ms, get_url, is_testnet
=======
from broker.bybit.api.baseurl import get_auth_headers, get_server_time_ms, get_url
>>>>>>> bybit-add
from utils.httpx_client import get_httpx_client
from utils.logging import get_logger

logger = get_logger(__name__)


def _request_logging_enabled() -> bool:
<<<<<<< HEAD
    return os.getenv("BYBIT_LOG_REQUESTS", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
=======
    return os.getenv("BYBIT_LOG_REQUESTS", "").strip().lower() in {"1", "true", "yes", "on"}
>>>>>>> bybit-add


def authenticate_broker(code):
    """Authenticate with Bybit Unified Account via API key + secret.

    The broker-specific callback code is ignored; the actual credential test is the
    wallet-balance request signed with the API key and secret.
    """
    api_key = os.getenv("BROKER_API_KEY", "").strip()
    api_secret = os.getenv("BROKER_API_SECRET", "").strip()

    if not api_key:
        return None, "BROKER_API_KEY is not set in environment variables"
    if not api_secret:
        return None, "BROKER_API_SECRET is not set in environment variables"

    path = "/v5/account/wallet-balance"
    params = {"accountType": "UNIFIED"}
<<<<<<< HEAD
    logger.info(
        "Bybit authentication environment: %s",
        "testnet" if is_testnet() else "mainnet",
    )
=======
>>>>>>> bybit-add
    client = get_httpx_client()
    try:
        server_time_ms = get_server_time_ms(client)
        local_time_ms = int(time.time() * 1000)
        logger.info("Bybit clock offset: %d ms", server_time_ms - local_time_ms)
    except Exception:
        logger.exception("Unable to read Bybit server time")

    headers = get_auth_headers(
        method="GET",
        path=path,
        params=params,
        payload="",
        api_key=api_key,
        api_secret=api_secret,
    )

    if _request_logging_enabled():
        logger.info(
            "Bybit request: method=GET url=%s params=%s timestamp=%s recv_window=%s",
            get_url(path),
            params,
            headers["X-BAPI-TIMESTAMP"],
            headers["X-BAPI-RECV-WINDOW"],
        )

    try:
        response = client.get(
            get_url(path),
            params=params,
            headers=headers,
            timeout=30.0,
        )
    except Exception as exc:  # pragma: no cover - network dependent
        logger.exception("Bybit authentication request failed")
        return None, f"Authentication request failed: {exc}"

    response_text = response.text.strip()
    if _request_logging_enabled():
        logger.info(
            "Bybit response: status=%s http_version=%s content_type=%s body_bytes=%s",
            response.status_code,
            response.http_version,
            response.headers.get("content-type", "unknown"),
            len(response.content),
        )
    try:
        data = response.json() if response_text else {}
    except ValueError:
        data = {}

    if response.status_code == 200 and data.get("retCode") == 0:
        logger.info("Bybit authentication successful")
        return api_key, None

    ret_code = data.get("retCode")
    ret_msg = data.get("retMsg")
    if ret_msg:
        msg = f"{ret_msg} (retCode={ret_code})" if ret_code is not None else ret_msg
    else:
        body = response_text[:300] if response_text else "empty response body"
        content_type = response.headers.get("content-type", "unknown")
        msg = f"HTTP {response.status_code} from Bybit ({content_type}): {body}"
    logger.error("Bybit authentication rejected: %s", msg)
    return None, msg
