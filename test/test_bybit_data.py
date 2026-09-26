from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest
from marshmallow import ValidationError

from broker.bybit.api import data as bybit_data
from broker.bybit.api.data import BrokerData
from restx_api.data_schemas import (
    DepthSchema,
    HistorySchema,
    IntervalsSchema,
    MultiQuotesSchema,
    QuotesSchema,
)
from services import intervals_service, quotes_service


@pytest.fixture(autouse=True)
def no_symbol_metadata_lookup(monkeypatch):
    monkeypatch.setattr(bybit_data, "get_symbol_info", lambda symbol, exchange: None)


def test_quotes_and_depth_use_requested_category(monkeypatch):
    data = BrokerData("test-token")
    requests = []

    def fake_public_get(path, params=None):
        requests.append((path, params))
        if path.endswith("tickers"):
            return {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "lastPrice": "100",
                        "ask1Price": "101",
                        "bid1Price": "99",
                    }
                ]
            }
        return {"b": [["99", "2"]], "a": [["101", "3"]]}

    monkeypatch.setattr(data, "_public_get", fake_public_get)
    monkeypatch.setattr(data, "_broker_symbol", lambda symbol, exchange: symbol)

    quote = data.get_quotes("BTCUSDT", "CRYPTO", category="spot")
    depth = data.get_depth("BTCUSDT", "CRYPTO", category="inverse")

    assert quote["ltp"] == 100
    assert depth["symbol"] == "BTCUSDT"
    assert requests[0][1]["category"] == "spot"
    assert requests[1][1]["category"] == "inverse"
    assert requests[2][1]["category"] == "inverse"
    assert requests[2][1]["limit"] == 50


def test_quotes_resolve_category_from_symbol_metadata(monkeypatch):
    data = BrokerData("test-token")
    captured = {}
    monkeypatch.setattr(
        bybit_data,
        "get_symbol_info",
        lambda symbol, exchange: SimpleNamespace(category="spot"),
    )
    monkeypatch.setattr(data, "_broker_symbol", lambda symbol, exchange: symbol)
    monkeypatch.setattr(
        data,
        "_public_get",
        lambda path, params=None: captured.update(params) or {"list": [{"lastPrice": "100"}]},
    )

    quote = data.get_quotes("BTCUSDT", "CRYPTO")

    assert quote["category"] == "spot"
    assert captured["category"] == "spot"


def test_option_ticker_change_uses_bybit_option_field(monkeypatch):
    data = BrokerData("test-token")
    monkeypatch.setattr(
        bybit_data,
        "get_symbol_info",
        lambda symbol, exchange: SimpleNamespace(category="option"),
    )
    monkeypatch.setattr(data, "_broker_symbol", lambda symbol, exchange: symbol)
    monkeypatch.setattr(
        data,
        "_public_get",
        lambda path, params=None: {"list": [{"lastPrice": "10", "change24h": "0.25"}]},
    )

    quote = data.get_quotes("BTCUSDT25JUN27106000PE", "CRYPTO")

    assert quote["change"] == 0.25
    assert quote["change_percent"] == 25


def test_quotes_reject_category_conflicting_with_symbol_metadata(monkeypatch):
    data = BrokerData("test-token")
    monkeypatch.setattr(
        bybit_data,
        "get_symbol_info",
        lambda symbol, exchange: SimpleNamespace(category="spot"),
    )
    monkeypatch.setattr(
        data, "_public_get", lambda *args, **kwargs: pytest.fail("must reject before request")
    )

    with pytest.raises(ValueError, match="belongs to spot, not linear"):
        data.get_quotes("BTCUSDT", "CRYPTO", category="linear")


def test_quotes_translate_openalgo_symbol_to_bybit_symbol(monkeypatch):
    data = BrokerData("test-token")
    captured = {}
    monkeypatch.setattr(data, "_broker_symbol", lambda symbol, exchange: "BTCUSDT")
    monkeypatch.setattr(
        data,
        "_public_get",
        lambda path, params=None: captured.update(params) or {"list": [{"lastPrice": "100"}]},
    )

    data.get_quotes("BTCUSDTFUT", "CRYPTO", category="linear")

    assert captured["symbol"] == "BTCUSDT"
    assert captured["category"] == "linear"


def test_history_category_sorting_and_turnover_not_reported_as_open_interest(monkeypatch):
    data = BrokerData("test-token")
    captured = {}

    def fake_public_get(path, params=None):
        captured.update(params)
        return {
            "list": [
                ["2000", "2", "3", "1", "2", "10", "999"],
                ["1000", "1", "2", "0", "1", "5", "888"],
            ]
        }

    monkeypatch.setattr(data, "_public_get", fake_public_get)
    monkeypatch.setattr(data, "_broker_symbol", lambda symbol, exchange: symbol)

    candles = data.get_history("BTCUSDT", "CRYPTO", "1m", 1000, 2000, category="spot")

    assert isinstance(candles, pd.DataFrame)
    assert captured["category"] == "spot"
    assert candles["timestamp"].tolist() == [1, 2]
    assert candles["oi"].tolist() == [0.0, 0.0]


def test_history_date_bounds_use_ist_calendar_days_and_are_inclusive(monkeypatch):
    data = BrokerData("test-token")
    captured = {}

    def fake_public_get(path, params=None):
        captured.update(params)
        return {"list": []}

    monkeypatch.setattr(data, "_public_get", fake_public_get)
    monkeypatch.setattr(data, "_broker_symbol", lambda symbol, exchange: symbol)

    data.get_history(
        "BTCUSDT",
        "CRYPTO",
        "1m",
        date(2026, 1, 1),
        date(2026, 1, 1),
        category="linear",
    )

    assert captured["start"] == 1_767_205_800_000
    assert captured["end"] == 1_767_292_199_999


def test_history_fetches_older_pages_and_returns_chronological_candles(monkeypatch):
    data = BrokerData("test-token")
    requests = []

    def fake_public_get(path, params=None):
        requests.append(params)
        if params["end"] == 3000:
            return {
                "list": [
                    ["3000", "3", "3", "3", "3", "3", "30"],
                    ["2000", "2", "2", "2", "2", "2", "20"],
                ]
            }
        return {"list": [["1000", "1", "1", "1", "1", "1", "10"]]}

    monkeypatch.setattr(data, "_public_get", fake_public_get)
    monkeypatch.setattr(data, "_broker_symbol", lambda symbol, exchange: symbol)

    candles = data.get_history("BTCUSDT", "CRYPTO", "1m", 1000, 3000)

    assert len(requests) == 2
    assert requests[1]["end"] == 1999
    assert candles["timestamp"].tolist() == [1, 2, 3]


def test_multiquotes_preserve_category_and_normalized_result_shape(monkeypatch):
    data = BrokerData("test-token")
    monkeypatch.setattr(
        data,
        "_public_get",
        lambda path, params=None: {"list": [{"symbol": "BTCUSDT", "lastPrice": "100"}]},
    )
    monkeypatch.setattr(data, "_broker_symbol", lambda symbol, exchange: symbol)

    result = data.get_multiquotes([{"symbol": "BTCUSDT", "category": "spot"}])

    assert result[0]["category"] == "spot"
    assert result[0]["data"]["category"] == "spot"
    assert result[0]["data"]["ltp"] == 100


def test_multiquotes_batches_symbols_by_category_and_preserves_order(monkeypatch):
    data = BrokerData("test-token")
    requests = []

    def fake_public_get(path, params=None):
        requests.append(params)
        return {
            "list": [
                {"symbol": "BTCUSDT", "lastPrice": "100"},
                {"symbol": "ETHUSDT", "lastPrice": "50"},
            ]
        }

    monkeypatch.setattr(data, "_public_get", fake_public_get)
    monkeypatch.setattr(data, "_broker_symbol", lambda symbol, exchange: symbol)

    result = data.get_multiquotes(
        [
            {"symbol": "BTCUSDT", "category": "spot"},
            {"symbol": "ETHUSDT", "category": "spot"},
        ]
    )

    assert len(requests) == 1
    assert requests[0] == {"category": "spot"}
    assert [item["symbol"] for item in result] == ["BTCUSDT", "ETHUSDT"]
    assert [item["data"]["ltp"] for item in result] == [100, 50]


def test_option_depth_uses_supported_bybit_orderbook_limit(monkeypatch):
    data = BrokerData("test-token")
    requests = []

    def fake_public_get(path, params=None):
        requests.append((path, params))
        if path.endswith("tickers"):
            return {"list": [{"lastPrice": "100"}]}
        return {"b": [], "a": []}

    monkeypatch.setattr(data, "_public_get", fake_public_get)
    monkeypatch.setattr(data, "_broker_symbol", lambda symbol, exchange: symbol)

    data.get_depth("BTCUSDT25JUN27106000PE", "CRYPTO", category="option")

    assert requests[-1][1]["limit"] == 25


def test_quotes_service_forwards_category_to_bybit(monkeypatch):
    received = []

    class FakeBrokerData:
        def __init__(self, auth_token):
            pass

        def get_quotes(self, symbol, exchange, category=None):
            received.append(category)
            return {"category": category}

    monkeypatch.setattr(
        quotes_service, "validate_symbol_exchange", lambda symbol, exchange: (True, None)
    )
    monkeypatch.setattr(
        quotes_service,
        "import_broker_module",
        lambda broker: SimpleNamespace(BrokerData=FakeBrokerData),
    )

    result = quotes_service.get_quotes_with_auth(
        "test-token", None, "bybit", "BTCUSDT", "CRYPTO", "spot"
    )

    assert result == (True, {"status": "success", "data": {"category": "spot"}}, 200)
    assert received == ["spot"]


def test_quotes_service_rejects_category_for_other_brokers(monkeypatch):
    monkeypatch.setattr(
        quotes_service,
        "import_broker_module",
        lambda broker: pytest.fail("broker data module should not be imported"),
    )

    result = quotes_service.get_quotes_with_auth(
        "test-token", None, "zerodha", "BTCUSDT", "CRYPTO", "spot"
    )

    assert result == (
        False,
        {"status": "error", "message": "category is only supported for Bybit"},
        400,
    )


def test_intervals_service_handles_bybit_category_and_rejects_options(monkeypatch):
    class FakeBrokerData:
        timeframe_map = BrokerData.TIMEFRAME_MAP

        def __init__(self, auth_token):
            pass

        def get_timeframe_map(self, category=None):
            return BrokerData.TIMEFRAME_MAP

    monkeypatch.setattr(
        intervals_service,
        "import_broker_module",
        lambda broker: SimpleNamespace(BrokerData=FakeBrokerData),
    )

    success, response, status = intervals_service.get_intervals_with_auth(
        "test-token", "bybit", "spot"
    )
    option_result = intervals_service.get_intervals_with_auth("test-token", "bybit", "option")

    assert success is True
    assert status == 200
    assert response["data"]["minutes"] == ["1m", "3m", "5m", "15m", "30m"]
    assert option_result[0] is False
    assert option_result[2] == 400


def test_options_are_not_advertised_as_historical_klines():
    data = BrokerData("test-token")

    with pytest.raises(ValueError, match="do not provide historical klines"):
        data.get_timeframe_map("option")

    with pytest.raises(ValueError, match="do not provide historical klines"):
        data.get_history("BTCUSDT", "CRYPTO", "1m", 1000, 2000, category="option")


@pytest.mark.parametrize(
    ("schema", "payload", "expected"),
    [
        (
            QuotesSchema(),
            {"apikey": "key", "symbol": "BTCUSDT", "exchange": "CRYPTO", "category": "inverse"},
            True,
        ),
        (
            DepthSchema(),
            {"apikey": "key", "symbol": "BTCUSDT", "exchange": "CRYPTO", "category": "option"},
            True,
        ),
        (
            HistorySchema(),
            {
                "apikey": "key",
                "symbol": "BTCUSDT",
                "exchange": "CRYPTO",
                "interval": "1m",
                "start_date": "2026-01-01",
                "end_date": "2026-01-02",
                "category": "spot",
            },
            True,
        ),
        (IntervalsSchema(), {"apikey": "key", "category": "linear"}, True),
        (
            MultiQuotesSchema(),
            {
                "apikey": "key",
                "symbols": [{"symbol": "BTCUSDT", "exchange": "CRYPTO", "category": "spot"}],
            },
            True,
        ),
        (
            QuotesSchema(),
            {"apikey": "key", "symbol": "BTCUSDT", "exchange": "CRYPTO", "category": "future"},
            False,
        ),
    ],
)
def test_category_schema_validation(schema, payload, expected):
    if expected:
        assert schema.load(payload)
    else:
        with pytest.raises(ValidationError):
            schema.load(payload)
