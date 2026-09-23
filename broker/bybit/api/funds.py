import os

from broker.bybit.api.baseurl import get_auth_headers, get_url
from utils.httpx_client import get_httpx_client
from utils.logging import get_logger

logger = get_logger(__name__)


def get_margin_data(auth):
    """Fetch the Unified Account wallet snapshot into the OpenAlgo margin contract."""
    api_key = auth or os.getenv("BROKER_API_KEY", "").strip()
    api_secret = os.getenv("BROKER_API_SECRET", "").strip()
    if not api_key or not api_secret:
        logger.error("Bybit margin request missing API credentials")
        return {"availablecash": "0.00", "collateral": "0.00", "m2mrealized": "0.00", "m2munrealized": "0.00", "utiliseddebits": "0.00"}

    params = {"accountType": "UNIFIED"}
    headers = get_auth_headers(
        method="GET",
        path="/v5/account/wallet-balance",
        params=params,
        payload="",
        api_key=api_key,
        api_secret=api_secret,
    )
    try:
        response = get_httpx_client().get(get_url("/v5/account/wallet-balance"), params=params, headers=headers, timeout=30.0)
        if response.status_code != 200:
            logger.warning("Bybit wallet-balance HTTP %s: %s", response.status_code, response.text[:200])
            return {"availablecash": "0.00", "collateral": "0.00", "m2mrealized": "0.00", "m2munrealized": "0.00", "utiliseddebits": "0.00"}
        data = response.json() if response.content else {}
        result = data.get("result") or {}
        list_rows = result.get("list") or []
        balance = 0.0
        for wallet in list_rows:
            coin_rows = wallet.get("coin") or []
            for coin in coin_rows:
                if coin.get("coin") in {"USDT", "USDC"}:
                    balance += float(coin.get("equity", 0) or 0.0)
        return {
            "availablecash": f"{balance:.2f}",
            "collateral": "0.00",
            "m2mrealized": "0.00",
            "m2munrealized": "0.00",
            "utiliseddebits": "0.00",
        }
    except Exception as exc:  # pragma: no cover - real account required
        logger.exception("Bybit wallet-balance fetch failed")
        return {"availablecash": "0.00", "collateral": "0.00", "m2mrealized": "0.00", "m2munrealized": "0.00", "utiliseddebits": "0.00"}
