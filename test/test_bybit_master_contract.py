from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from broker.bybit.database import master_contract_db as master
from database import token_db_enhanced


def _instrument(category, **overrides):
    item = {
        "symbolId": 42,
        "symbol": "BTCUSDT",
        "status": "Trading",
        "baseCoin": "BTC",
        "quoteCoin": "USDT",
        "settleCoin": "USDT",
        "priceFilter": {"tickSize": "0.1"},
        "lotSizeFilter": {
            "qtyStep": "0.01",
            "minOrderQty": "0.01",
            "maxOrderQty": "100",
            "maxMktOrderQty": "50",
            "minNotionalValue": "5",
        },
    }
    item.update(overrides)
    return master._build_instrument(item, category)


def test_builds_spot_instrument_with_spot_precision_metadata():
    result = _instrument(
        "spot",
        lotSizeFilter={
            "basePrecision": "0.000001",
            "quotePrecision": "0.01",
            "minOrderQty": "0.000001",
            "maxOrderQty": "230",
            "minOrderAmt": "5",
            "maxMarketOrderQty": "10",
            "maxLimitOrderQty": "20",
        },
    )

    assert result["symbol"] == "BTCUSDT"
    assert result["token"] == "spot:42"
    assert result["instrumenttype"] == "SPOT"
    assert result["qty_step"] is None
    assert result["min_qty"] == 0.000001
    assert result["max_qty"] == 230
    assert result["base_precision"] == 0.000001
    assert result["quote_precision"] == 0.01
    assert result["min_order_amt"] == 5


def test_builds_linear_perpetual_and_inverse_dated_future():
    linear = _instrument("linear", contractType="LinearPerpetual")
    delivery_time = int(datetime(2027, 6, 25, tzinfo=timezone.utc).timestamp() * 1000)
    inverse = _instrument(
        "inverse",
        symbolId=84,
        symbol="ETHUSD",
        baseCoin="ETH",
        quoteCoin="USD",
        settleCoin="USD",
        contractType="InverseFutures",
        deliveryTime=str(delivery_time),
    )

    assert linear["symbol"] == "BTCUSDTFUT"
    assert linear["category"] == "linear"
    assert linear["qty_step"] == 0.01
    assert inverse["symbol"] == "ETHUSD25JUN27FUT"
    assert inverse["expiry"] == "25-JUN-27"
    assert inverse["settle_coin"] == "USD"


def test_builds_option_symbol_and_checks_native_expiry_and_side():
    delivery_time = int(datetime(2027, 6, 25, tzinfo=timezone.utc).timestamp() * 1000)
    result = _instrument(
        "option",
        symbolId=99,
        symbol="BTC-25JUN27-106000-P-USDT",
        optionsType="Put",
        deliveryTime=str(delivery_time),
    )

    assert result["symbol"] == "BTCUSDT25JUN27106000PE"
    assert result["strike"] == 106000
    assert result["instrumenttype"] == "PE"
    assert result["category"] == "option"

    mismatch = _instrument(
        "option",
        symbol="BTC-25JUN27-106000-C-USDT",
        optionsType="Put",
        deliveryTime=str(delivery_time),
    )
    assert mismatch is None


def test_fetch_category_items_paginates_derivatives_but_not_spot(monkeypatch):
    calls = []

    def fake_request(endpoint, params):
        calls.append((endpoint, params.copy()))
        page = len(calls)
        result = {"list": [{"page": page}]}
        if page == 1 and params["category"] != "spot":
            result["nextPageCursor"] = "cursor-2"
        return {"retCode": 0, "result": result}

    monkeypatch.setattr(master, "request", fake_request)
    assert master._fetch_category_items("linear") == [{"page": 1}, {"page": 2}]
    assert calls[0][1]["limit"] == 1000
    assert calls[1][1]["cursor"] == "cursor-2"

    calls.clear()
    assert master._fetch_category_items("spot") == [{"page": 1}]
    assert "limit" not in calls[0][1]
    assert "cursor" not in calls[0][1]


def test_master_download_keeps_existing_rows_if_a_category_fails(monkeypatch):
    delete_calls = []
    fetched = []

    def fetch_category(category):
        fetched.append(category)
        if category == "inverse":
            raise ValueError("upstream unavailable")
        return [{"symbol": category}]

    monkeypatch.setattr(master, "_fetch_category_items", fetch_category)
    monkeypatch.setattr(master, "_status_supported", lambda category, item: True)
    monkeypatch.setattr(
        master,
        "_build_instrument",
        lambda item, category: {"symbol": item["symbol"], "exchange": "CRYPTO"},
    )
    monkeypatch.setattr(master, "delete_symtoken_table", lambda: delete_calls.append(True))
    monkeypatch.setattr(master, "copy_from_dataframe", lambda _df: pytest.fail("must not replace"))
    monkeypatch.setattr(master, "_emit_master_contract_status", lambda *args, **kwargs: None)

    assert master.master_contract_download() is False
    assert fetched == ["spot", "linear", "inverse"]
    assert delete_calls == []


def test_symbol_cache_preserves_category_metadata_and_native_symbol_mapping(monkeypatch):
    from database.symbol import SymToken

    row = SimpleNamespace(
        symbol="BTCUSDT",
        brsymbol="BTCUSDT",
        name="BTC",
        exchange="CRYPTO",
        brexchange="bybit",
        token="spot:42",
        expiry="",
        strike=0.0,
        lotsize=1,
        instrumenttype="SPOT",
        tick_size=0.01,
        contract_value=1.0,
        category="spot",
        qty_step=None,
        min_qty=None,
        max_qty=None,
        base_precision=0.000001,
        quote_precision=0.01,
        min_order_amt=5.0,
        max_market_qty=None,
        max_limit_qty=None,
        base_coin="BTC",
        quote_coin="USDT",
        settle_coin="USDT",
    )
    monkeypatch.setattr(SymToken, "query", SimpleNamespace(all=lambda: [row]))

    cache = token_db_enhanced.BrokerSymbolCache()

    assert cache.load_all_symbols("bybit") is True
    assert cache.get_symbol_info("BTCUSDT", "CRYPTO").category == "spot"
    assert cache.get_oa_symbol("BTCUSDT", "CRYPTO", "spot") == "BTCUSDT"


def test_database_reverse_symbol_lookup_filters_by_category(monkeypatch):
    from database.symbol import SymToken

    class Query:
        def __init__(self):
            self.filters = []

        def filter_by(self, **kwargs):
            self.filters.append(kwargs)
            return self

        def first(self):
            return SimpleNamespace(symbol="BTCUSDT")

    query = Query()
    monkeypatch.setattr(SymToken, "query", query)

    assert token_db_enhanced.get_oa_symbol_dbquery("BTCUSDT", "CRYPTO", "spot") == "BTCUSDT"
    assert query.filters == [
        {"brsymbol": "BTCUSDT", "exchange": "CRYPTO"},
        {"category": "spot"},
    ]
