import json
import os

from broker.bybit.api.rest_client import request
from utils.logging import get_logger

logger = get_logger(__name__)


def validate_public_contract(symbol: str = "BTCUSDT", limit: int = 1):
    """Probe the Bybit V5 public market contract for linear symbols.

    This is a live validation helper for phase 1: it confirms the endpoint is
    reachable, the value shape matches the documented V5 format, and the sample
    result contains the fields required for a later master-contract parser.
    """
    params = {"category": "linear", "symbol": symbol, "limit": limit}
    try:
        data = request("/v5/market/instruments-info", params=params)
    except ValueError as exc:
        return {
            "status_code": None,
            "ret_code": None,
            "ret_msg": str(exc),
            "endpoint": "/v5/market/instruments-info",
            "sample_symbol": None,
            "sample_keys": [],
            "has_category": False,
            "has_symbol": False,
            "has_status": False,
            "has_lot_size": False,
            "has_tick_size": False,
            "has_contract_type": False,
        }
    result = data.get("result", {}) if isinstance(data, dict) else {}
    items = result.get("list", []) if isinstance(result, dict) else []
    sample = items[0] if items else {}

    return {
        "status_code": 200,
        "ret_code": data.get("retCode"),
        "ret_msg": data.get("retMsg"),
        "endpoint": "/v5/market/instruments-info",
        "sample_symbol": sample.get("symbol"),
        "sample_keys": sorted(sample.keys())[:20],
        "has_category": "category" in sample,
        "has_symbol": "symbol" in sample,
        "has_status": "status" in sample,
        "has_lot_size": "lotSize" in sample,
        "has_tick_size": "tickSize" in sample,
        "has_contract_type": "contractType" in sample,
    }


def validate_signed_account_contract():
    """Probe a signed wallet-balance request if credentials are present.

    Returns a structured status so the integration can distinguish a missing env
    setup from a real API contract failure.
    """
    api_key = os.getenv("BROKER_API_KEY", "").strip()
    api_secret = os.getenv("BROKER_API_SECRET", "").strip()
    if not api_key or not api_secret:
        return {
            "status": "skipped",
            "reason": "BROKER_API_KEY/BROKER_API_SECRET are not configured",
        }

    path = "/v5/account/wallet-balance"
    params = {"accountType": "UNIFIED"}
    try:
        data = request(path, params=params, api_key=api_key)
    except ValueError as exc:
        return {
            "status": "failed",
            "status_code": None,
            "ret_code": None,
            "ret_msg": str(exc),
            "balances_count": 0,
            "endpoint": path,
        }

    result = data.get("result", {}) if isinstance(data, dict) else {}
    balances = result.get("list", []) if isinstance(result, dict) else []
    return {
        "status": "success" if data.get("retCode") == 0 else "failed",
        "status_code": 200,
        "ret_code": data.get("retCode"),
        "ret_msg": data.get("retMsg"),
        "balances_count": len(balances),
        "endpoint": path,
    }


def validate_bybit_contract():
    """Run the phase-1 live contract checks for Bybit Unified Account linear mode."""
    result = {
        "public_market_contract": validate_public_contract(),
        "signed_wallet_balance": validate_signed_account_contract(),
    }

    logger.info("Bybit contract validation result: %s", json.dumps(result, default=str, sort_keys=True))
    return result


if __name__ == "__main__":
    print(json.dumps(validate_bybit_contract(), indent=2, default=str))
