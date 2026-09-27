import os

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
