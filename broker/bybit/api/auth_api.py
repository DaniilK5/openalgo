import os
import time

from broker.bybit.api.baseurl import is_testnet
from broker.bybit.api.rest_client import get_server_time_ms, request
from utils.logging import get_logger

logger = get_logger(__name__)


def authenticate_broker(code):
    """Authenticate by verifying the configured Bybit key with the pybit SDK."""
    api_key = os.getenv("BROKER_API_KEY", "").strip()
    api_secret = os.getenv("BROKER_API_SECRET", "").strip()

    if not api_key:
        return None, "BROKER_API_KEY is not set in environment variables"
    if not api_secret:
        return None, "BROKER_API_SECRET is not set in environment variables"

    logger.info(
        "Bybit authentication environment: %s",
        "testnet" if is_testnet() else "mainnet",
    )
    try:
        server_time_ms = get_server_time_ms(api_key=api_key)
        local_time_ms = int(time.time() * 1000)
        logger.info("Bybit clock offset: %d ms", server_time_ms - local_time_ms)
    except ValueError:
        logger.exception("Unable to read Bybit server time")

    try:
        data = request(
            "/v5/account/wallet-balance",
            params={"accountType": "UNIFIED"},
            api_key=api_key,
        )
    except ValueError as exc:
        logger.error("Bybit authentication rejected: %s", exc)
        return None, str(exc)

    if data.get("retCode") == 0:
        logger.info("Bybit authentication successful")
        return api_key, None

    ret_code = data.get("retCode")
    ret_msg = data.get("retMsg")
    message = (
        f"{ret_msg} (retCode={ret_code})" if ret_msg else "Bybit account data could not be verified"
    )
    logger.error("Bybit authentication rejected: %s", message)
    return None, message
