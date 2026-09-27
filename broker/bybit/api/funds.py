import os
from decimal import Decimal, InvalidOperation

from broker.bybit.api.rest_client import request
from utils.logging import get_logger

logger = get_logger(__name__)

_WALLET_PATH = "/v5/account/wallet-balance"


def _decimal_field(value, field_name):
    """Parse a required Bybit numeric field without silently replacing bad data."""
    if isinstance(value, bool) or value is None or value == "":
        raise ValueError(f"Bybit wallet response has an invalid {field_name}")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Bybit wallet response has an invalid {field_name}") from exc
    if not amount.is_finite():
        raise ValueError(f"Bybit wallet response has a non-finite {field_name}")
    return amount


def _money(amount):
    return f"{amount:.2f}"


def _quantity(amount):
    return format(amount, "f")


def _map_wallet_response(data):
    """Validate and map a Bybit V5 Unified Account wallet response."""
    if not isinstance(data, dict) or type(data.get("retCode")) is not int:
        raise ValueError("Bybit wallet response is malformed")
    if data["retCode"] != 0:
        logger.warning("Bybit wallet endpoint returned retCode=%s", data["retCode"])
        raise ValueError("Bybit wallet request was rejected")

    result = data.get("result")
    wallets = result.get("list") if isinstance(result, dict) else None
    if not isinstance(wallets, list) or not wallets:
        raise ValueError("Bybit wallet response contains no Unified Account data")

    total_equity = Decimal("0")
    available_cash = Decimal("0")
    initial_margin = Decimal("0")
    unrealized_pnl = Decimal("0")
    coins = {}

    for wallet in wallets:
        if not isinstance(wallet, dict):
            raise ValueError("Bybit wallet response contains an invalid account")
        total_equity += _decimal_field(wallet.get("totalEquity"), "totalEquity")
        available_cash += _decimal_field(
            wallet.get("totalAvailableBalance"), "totalAvailableBalance"
        )
        initial_margin += _decimal_field(
            wallet.get("totalInitialMargin"), "totalInitialMargin"
        )
        unrealized_pnl += _decimal_field(wallet.get("totalPerpUPL"), "totalPerpUPL")

        coin_rows = wallet.get("coin")
        if not isinstance(coin_rows, list):
            raise ValueError("Bybit wallet response contains an invalid coin breakdown")
        for row in coin_rows:
            if not isinstance(row, dict) or not isinstance(row.get("coin"), str):
                raise ValueError("Bybit wallet response contains an invalid coin")
            coin = row["coin"]
            quantity = _decimal_field(row.get("equity"), f"{coin} equity")
            usd_value = _decimal_field(row.get("usdValue"), f"{coin} USD value")
            balance = coins.setdefault(
                coin, {"equity": Decimal("0"), "usd_value": Decimal("0")}
            )
            balance["equity"] += quantity
            balance["usd_value"] += usd_value

    return {
        "availablecash": _money(available_cash),
        "collateral": "0.00",
        "m2mrealized": "0.00",
        "m2munrealized": _money(unrealized_pnl),
        "utiliseddebits": _money(initial_margin),
        "account_equity_usd": _money(total_equity),
        "currency": "USD",
        "coin_balances": [
            {
                "coin": coin,
                "equity": _quantity(balance["equity"]),
                "usd_value": _quantity(balance["usd_value"]),
                "currency": "USD",
            }
            for coin, balance in sorted(coins.items())
        ],
    }


def get_margin_data(auth):
    """Fetch Bybit Unified Account funds into common and Bybit-specific fields.

    The common OpenAlgo funds fields remain available to generic API consumers.
    Bybit-specific fields expose total USD-equivalent account equity and keep
    each coin's quantity separate from its USD valuation.
    """
    api_key = (auth or os.getenv("BROKER_API_KEY", "")).strip()
    api_secret = os.getenv("BROKER_API_SECRET", "").strip()
    if not api_key or not api_secret:
        logger.error("Bybit wallet request cannot run because credentials are missing")
        raise ValueError("Bybit API credentials are not configured")

    data = request(
        _WALLET_PATH,
        params={"accountType": "UNIFIED"},
        api_key=api_key,
    )
    return _map_wallet_response(data)
