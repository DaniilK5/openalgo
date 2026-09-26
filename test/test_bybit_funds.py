from types import SimpleNamespace

import pytest

import broker.bybit.api.funds as funds
from services import funds_service

WALLET_RESPONSE = {
    "retCode": 0,
    "retMsg": "OK",
    "result": {
        "list": [
            {
                "totalEquity": "1100.50",
                "totalAvailableBalance": "900.25",
                "totalInitialMargin": "100.25",
                "totalPerpUPL": "12.50",
                "coin": [
                    {"coin": "BTC", "equity": "0.0123", "usdValue": "750.20"},
                    {"coin": "USDT", "equity": "350.30", "usdValue": "350.30"},
                ],
            }
        ]
    },
}


def test_unified_wallet_maps_common_fields_and_usd_coin_balances(monkeypatch):
    request = {}

    monkeypatch.setenv("BROKER_API_SECRET", "test-secret")
    monkeypatch.setattr(
        funds,
        "request",
        lambda endpoint, **kwargs: request.update(endpoint=endpoint, **kwargs) or WALLET_RESPONSE,
    )

    result = funds.get_margin_data("test-key")

    assert result == {
        "availablecash": "900.25",
        "collateral": "0.00",
        "m2mrealized": "0.00",
        "m2munrealized": "12.50",
        "utiliseddebits": "100.25",
        "account_equity_usd": "1100.50",
        "currency": "USD",
        "coin_balances": [
            {
                "coin": "BTC",
                "equity": "0.0123",
                "usd_value": "750.20",
                "currency": "USD",
            },
            {
                "coin": "USDT",
                "equity": "350.30",
                "usd_value": "350.30",
                "currency": "USD",
            },
        ],
    }
    assert request == {
        "endpoint": "/v5/account/wallet-balance",
        "params": {"accountType": "UNIFIED"},
        "api_key": "test-key",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"retCode": 10001, "retMsg": "account error", "result": {"list": []}},
        {"retCode": 0, "result": {"list": []}},
        {"retCode": 0, "result": {"list": [{}]}},
        {"retCode": 0, "result": {"list": [{"totalEquity": "bad"}]}},
    ],
)
def test_invalid_or_empty_wallet_payloads_raise_instead_of_returning_zero(payload):
    with pytest.raises(ValueError):
        funds._map_wallet_response(payload)


def test_sdk_errors_raise(monkeypatch):
    monkeypatch.setenv("BROKER_API_SECRET", "test-secret")
    monkeypatch.setattr(
        funds,
        "request",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("connection failed")),
    )

    with pytest.raises(ValueError, match="connection failed"):
        funds.get_margin_data("test-key")


def test_funds_service_returns_explicit_safe_bybit_error(monkeypatch):
    import database.settings_db

    monkeypatch.setattr(database.settings_db, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(
        funds_service,
        "import_broker_module",
        lambda broker: SimpleNamespace(
            get_margin_data=lambda token: (_ for _ in ()).throw(
                ValueError("sensitive response details")
            )
        ),
    )

    success, response, status = funds_service.get_funds_with_auth("token", "bybit")

    assert success is False
    assert status == 502
    assert response["status"] == "error"
    assert "Bybit account balance" in response["message"]
    assert "sensitive response details" not in response["message"]


def test_funds_service_rejects_bybit_error_payload(monkeypatch):
    import database.settings_db

    monkeypatch.setattr(database.settings_db, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(
        funds_service,
        "import_broker_module",
        lambda broker: SimpleNamespace(
            get_margin_data=lambda token: {
                "status": "error",
                "message": "wallet unavailable",
            }
        ),
    )

    success, response, status = funds_service.get_funds_with_auth("token", "bybit")

    assert success is False
    assert status == 502
    assert response["status"] == "error"
    assert "Bybit account balance" in response["message"]


def test_unrelated_broker_funds_shape_is_unchanged(monkeypatch):
    import database.settings_db

    monkeypatch.setattr(database.settings_db, "get_analyze_mode", lambda: False)
    expected = {"availablecash": "125.00", "collateral": "0.00"}
    monkeypatch.setattr(
        funds_service,
        "import_broker_module",
        lambda broker: SimpleNamespace(get_margin_data=lambda token: expected),
    )

    assert funds_service.get_funds_with_auth("token", "zerodha") == (
        True,
        {"status": "success", "data": expected},
        200,
    )
