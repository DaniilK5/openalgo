import json
from types import SimpleNamespace

import pytest

from broker.bybit.api import order_api
from broker.bybit.mapping import order_data
from services import orderbook_service, tradebook_service


def _response(result, status_code=200):
    body = json.dumps({"retCode": 0, "retMsg": "OK", "result": result})
    return SimpleNamespace(status_code=status_code, content=body.encode(), text=body)


def test_account_pagination_follows_cursor_and_rejects_repeated_cursor(monkeypatch):
    requests = []

    def fake_request(endpoint, auth, method="GET", params=None, payload=None):
        requests.append(params.copy())
        if len(requests) == 1:
            return _response({"list": [{"orderId": "1"}], "nextPageCursor": "page-2"})
        return _response({"list": [{"orderId": "2"}]})

    monkeypatch.setattr(order_api, "_signed_request", fake_request)

    rows = order_api._fetch_pages(
        "/v5/order/history", "token", {"category": "spot", "startTime": 1, "endTime": 2}
    )

    assert [row["orderId"] for row in rows] == ["1", "2"]
    assert requests[1]["cursor"] == "page-2"
    assert all(request["limit"] == 50 for request in requests)

    monkeypatch.setattr(
        order_api,
        "_signed_request",
        lambda *args, **kwargs: _response({"list": [], "nextPageCursor": "same-cursor"}),
    )
    with pytest.raises(ValueError, match="history could not be fully retrieved"):
        order_api._fetch_pages("/v5/order/history", "token", {"category": "spot"})


def test_orderbook_combines_current_day_orders_from_all_categories(monkeypatch):
    monkeypatch.setattr(order_api, "_utc_day_bounds", lambda: (100, 200))
    requests = []

    def fake_fetch(endpoint, auth, params, page_size=50, max_pages=100):
        requests.append((endpoint, params))
        return [
            {
                "category": params["category"],
                "orderId": params["category"],
                "createdTime": "150",
            }
        ]

    monkeypatch.setattr(order_api, "_fetch_pages", fake_fetch)

    result = order_api.get_order_book("token")

    assert len(result["result"]) == 4
    assert {row["category"] for row in result["result"]} == {
        "spot",
        "linear",
        "inverse",
        "option",
    }
    assert len(requests) == 8
    assert all(item[1]["startTime"] == 100 for item in requests if item[0].endswith("history"))


def test_tradebook_filters_execution_time_and_fetches_each_category(monkeypatch):
    monkeypatch.setattr(order_api, "_utc_day_bounds", lambda: (100, 200))
    categories = []

    def fake_fetch(endpoint, auth, params, page_size=50, max_pages=100):
        categories.append(params["category"])
        return [
            {"category": params["category"], "execTime": "150"},
            {"category": params["category"], "execTime": "201"},
        ]

    monkeypatch.setattr(order_api, "_fetch_pages", fake_fetch)

    result = order_api.get_trade_book("token")

    assert categories == ["spot", "linear", "inverse", "option"]
    assert len(result["result"]) == 4
    assert all(row["execTime"] == "150" for row in result["result"])


def test_realtime_orders_retry_with_required_filters_when_category_only_is_rejected(monkeypatch):
    requests = []

    def fake_fetch(endpoint, auth, params, page_size=50, max_pages=100):
        requests.append(params.copy())
        if params["category"] == "linear" and "settleCoin" not in params:
            raise ValueError("Bybit rejected the account request.")
        return [
            {
                "category": params["category"],
                "orderId": f"linear-{params.get('settleCoin', 'all')}",
                "createdTime": "150",
            }
        ]

    monkeypatch.setattr(order_api, "_fetch_pages", fake_fetch)

    rows = order_api._fetch_realtime_orders("token", "linear")

    assert requests[0] == {"category": "linear"}
    assert any(request.get("settleCoin") == "USDT" for request in requests)
    assert {row["orderId"] for row in rows} == {"linear-USDT", "linear-USDC"}


def test_realtime_orders_accept_empty_successful_filtered_responses(monkeypatch):
    requests = []

    def fake_fetch(endpoint, auth, params, page_size=50, max_pages=100):
        requests.append(params.copy())
        if "settleCoin" not in params:
            raise ValueError("Bybit requires a symbol, settle coin, or base coin.")
        return []

    monkeypatch.setattr(order_api, "_fetch_pages", fake_fetch)

    rows = order_api._fetch_realtime_orders("token", "linear")

    assert rows == []
    assert requests == [
        {"category": "linear"},
        {"category": "linear", "settleCoin": "USDT"},
        {"category": "linear", "settleCoin": "USDC"},
    ]


def test_orderbook_returns_empty_result_when_filtered_realtime_queries_are_empty(monkeypatch):
    monkeypatch.setattr(order_api, "_utc_day_bounds", lambda: (100, 200))

    def fake_fetch(endpoint, auth, params, page_size=50, max_pages=100):
        if endpoint.endswith("/history"):
            return []
        category = params["category"]
        if category == "linear" and "settleCoin" not in params:
            raise ValueError("Bybit requires a settle coin.")
        if category == "inverse" and "settleCoin" not in params:
            raise ValueError("Bybit requires a settle coin.")
        if category == "option" and "baseCoin" not in params:
            raise ValueError("Bybit requires a base coin.")
        return []

    monkeypatch.setattr(order_api, "_fetch_pages", fake_fetch)

    assert order_api.get_order_book("token") == {"result": []}


def test_cancel_all_scans_categories_via_realtime_helper(monkeypatch):
    calls = []
    monkeypatch.setattr(
        order_api,
        "_fetch_realtime_orders",
        lambda auth, category, symbol=None: calls.append((category, symbol)) or [],
    )

    cancelled, failed = order_api.cancel_all_orders_api({}, "token")

    assert calls == [
        ("spot", None),
        ("linear", None),
        ("inverse", None),
        ("option", None),
    ]
    assert cancelled == []
    assert failed == []


def test_positionbook_requests_supported_settlement_groups_and_excludes_zero_rows(monkeypatch):
    requests = []

    def fake_fetch(endpoint, auth, params, page_size=50, max_pages=100):
        requests.append((params, page_size))
        return [
            {
                "category": params["category"],
                "symbol": f"{params['category']}-{params.get('settleCoin', 'ALL')}",
                "size": "0.1" if params["category"] != "inverse" else "0",
            }
        ]

    monkeypatch.setattr(order_api, "_fetch_pages", fake_fetch)

    result = order_api.get_positions("token")

    assert len(requests) == 4
    assert [params for params, _ in requests] == [
        {"category": "linear", "settleCoin": "USDT"},
        {"category": "linear", "settleCoin": "USDC"},
        {"category": "inverse"},
        {"category": "option"},
    ]
    assert all(page_size == 200 for _, page_size in requests)
    assert [row["category"] for row in result["result"]] == [
        "linear",
        "linear",
        "option",
    ]


def test_signed_request_uses_pybit_response_adapter(monkeypatch):
    request = {}
    response = order_api._order_response(_response({"orderId": "test-order"}))
    monkeypatch.setattr(
        order_api,
        "request_response",
        lambda endpoint, **kwargs: request.update(endpoint=endpoint, **kwargs) or response,
    )

    result = order_api._signed_request(
        "/v5/order/create",
        "test-key",
        method="POST",
        payload={"category": "spot", "symbol": "BTCUSDT"},
    )

    assert result is response
    assert request == {
        "endpoint": "/v5/order/create",
        "method": "POST",
        "params": None,
        "payload": {"category": "spot", "symbol": "BTCUSDT"},
        "api_key": "test-key",
    }


def test_spot_order_uses_native_symbol_and_spot_category(monkeypatch):
    instrument = SimpleNamespace(
        category="spot",
        qty_step=None,
        base_precision=0.01,
        min_qty=0.01,
        max_qty=10,
        max_market_qty=10,
        max_limit_qty=10,
        min_order_amt=5,
        tick_size=0.01,
    )
    monkeypatch.setattr(order_api, "get_symbol_info", lambda symbol, exchange: instrument)
    monkeypatch.setattr(order_api, "get_br_symbol", lambda symbol, exchange: "BTCUSDT")
    requests = []

    def fake_request(endpoint, auth, method="GET", params=None, payload=None):
        requests.append((endpoint, payload))
        return _response({"orderId": "spot-order"})

    monkeypatch.setattr(order_api, "_signed_request", fake_request)

    response, response_data, order_id = order_api.place_order_api(
        {
            "symbol": "BTCUSDT",
            "exchange": "CRYPTO",
            "action": "BUY",
            "quantity": "0.5",
            "price": "100",
            "pricetype": "LIMIT",
            "product": "CNC",
        },
        "token",
    )

    assert response.status == 200
    assert response_data["result"]["orderId"] == "spot-order"
    assert order_id == "spot-order"
    assert requests == [
        (
            "/v5/order/create",
            {
                "category": "spot",
                "symbol": "BTCUSDT",
                "side": "Buy",
                "orderType": "Limit",
                "qty": "0.5",
                "price": "100",
                "timeInForce": "GTC",
            },
        )
    ]


def test_order_quantity_precision_is_checked_before_broker_request(monkeypatch):
    instrument = SimpleNamespace(
        category="linear",
        qty_step=0.001,
        base_precision=None,
        min_qty=0.001,
        max_qty=100,
        max_market_qty=100,
        max_limit_qty=100,
        min_order_amt=None,
        tick_size=0.1,
    )
    monkeypatch.setattr(order_api, "get_symbol_info", lambda symbol, exchange: instrument)
    monkeypatch.setattr(order_api, "get_br_symbol", lambda symbol, exchange: "BTCUSDT")
    monkeypatch.setattr(
        order_api,
        "_signed_request",
        lambda *args, **kwargs: pytest.fail("Invalid quantity must be rejected locally"),
    )

    with pytest.raises(ValueError, match="quantity precision"):
        order_api.place_order_api(
            {
                "symbol": "BTCUSDT",
                "exchange": "CRYPTO",
                "action": "BUY",
                "quantity": "0.0005",
                "pricetype": "MARKET",
                "product": "NRML",
            },
            "token",
        )


def test_modify_limit_price_does_not_require_resubmitting_quantity(monkeypatch):
    instrument = SimpleNamespace(
        category="linear",
        qty_step=0.001,
        base_precision=None,
        min_qty=0.001,
        max_qty=100,
        max_market_qty=100,
        max_limit_qty=100,
        min_order_amt=None,
        tick_size=0.1,
    )
    monkeypatch.setattr(order_api, "get_symbol_info", lambda symbol, exchange: instrument)
    monkeypatch.setattr(order_api, "get_br_symbol", lambda symbol, exchange: "BTCUSDT")
    requests = []
    monkeypatch.setattr(
        order_api,
        "_signed_request",
        lambda endpoint, auth, method="GET", params=None, payload=None: (
            requests.append((endpoint, payload)) or _response({})
        ),
    )

    result, status = order_api.modify_order(
        {
            "symbol": "BTCUSDT",
            "exchange": "CRYPTO",
            "orderid": "order-1",
            "price": "100.1",
        },
        "token",
    )

    assert status == 200
    assert result["retCode"] == 0
    assert requests[0][1] == {
        "category": "linear",
        "symbol": "BTCUSDT",
        "orderId": "order-1",
        "price": "100.1",
    }


def test_cancel_finds_order_category_before_sending_cancel(monkeypatch):
    calls = []

    def fake_request(endpoint, auth, method="GET", params=None, payload=None):
        calls.append((endpoint, method, params, payload))
        if endpoint == "/v5/order/realtime" and params["category"] == "linear":
            return _response(
                {
                    "list": [
                        {"orderId": "order-1", "symbol": "BTCUSDT"},
                    ]
                }
            )
        return _response({"list": []} if method == "GET" else {"orderId": "order-1"})

    monkeypatch.setattr(order_api, "_signed_request", fake_request)

    result, status = order_api.cancel_order("order-1", "token")

    assert status == 200
    assert result["retCode"] == 0
    assert calls[-1] == (
        "/v5/order/cancel",
        "POST",
        None,
        {"category": "linear", "symbol": "BTCUSDT", "orderId": "order-1"},
    )


def test_open_position_uses_symbol_category_and_signed_net_size(monkeypatch):
    instrument = SimpleNamespace(category="inverse")
    monkeypatch.setattr(order_api, "get_symbol_info", lambda symbol, exchange: instrument)
    monkeypatch.setattr(order_api, "get_br_symbol", lambda symbol, exchange: "BTCUSD")
    requests = []
    monkeypatch.setattr(
        order_api,
        "_signed_request",
        lambda endpoint, auth, method="GET", params=None, payload=None: (
            requests.append(params)
            or _response(
                {
                    "list": [
                        {"symbol": "BTCUSD", "side": "Buy", "size": "0.5"},
                        {"symbol": "BTCUSD", "side": "Sell", "size": "0.2"},
                    ]
                }
            )
        ),
    )

    position = order_api.get_open_position("BTC28FEB25FUT", "CRYPTO", "NRML", "token")

    assert position == "0.3"
    assert requests == [{"category": "inverse", "symbol": "BTCUSD"}]


def test_close_all_refuses_before_sending_when_options_positions_are_present(monkeypatch):
    monkeypatch.setattr(
        order_api,
        "get_positions",
        lambda auth: {
            "result": [
                {"category": "linear", "symbol": "BTCUSDT", "side": "Buy", "size": "0.1"},
                {"category": "option", "symbol": "BTC-25JUN27-100000-C-USDT", "side": "Buy", "size": "1"},
            ]
        },
    )
    monkeypatch.setattr(
        order_api,
        "_signed_request",
        lambda *args, **kwargs: pytest.fail("Preflight must refuse before sending any close order"),
    )

    result, status = order_api.close_all_positions("key", "token")

    assert status == 400
    assert result["status"] == "error"
    assert result["closed_positions"] == []
    assert result["failed_positions"] == ["BTC-25JUN27-100000-C-USDT"]


@pytest.mark.parametrize(
    ("trigger", "expected_direction"),
    [("101", 1), ("99", 2)],
)
def test_trigger_direction_compares_trigger_to_current_price(
    monkeypatch, trigger, expected_direction
):
    monkeypatch.setattr(
        order_api,
        "request",
        lambda endpoint, params: {
            "result": {"list": [{"lastPrice": "100"}]}
        },
    )
    assert order_api._trigger_direction("linear", "BTCUSDT", trigger) == expected_direction


def test_order_mapping_normalizes_status_and_category_specific_symbols(monkeypatch):
    monkeypatch.setattr(
        order_data,
        "get_oa_symbol",
        lambda native, exchange, category: f"{category.upper()}-{native}",
    )
    raw = {
        "result": [
            {
                "orderId": "abc",
                "symbol": "BTCUSDT",
                "category": "spot",
                "side": "Buy",
                "qty": "0.5",
                "cumExecQty": "0.25",
                "price": "100",
                "orderType": "Limit",
                "orderStatus": "PartiallyFilled",
                "createdTime": "1790430181000",
                "triggerPrice": "0",
            }
        ]
    }

    mapped = order_data.map_order_data(order_data=raw)

    assert mapped[0]["symbol"] == "SPOT-BTCUSDT"
    assert mapped[0]["order_status"] == "open"
    assert mapped[0]["pendingqty"] == 0.25
    assert mapped[0]["pricetype"] == "LIMIT"
    assert order_data.calculate_order_statistics(mapped)["total_open_orders"] == 1


def test_positions_and_wallet_coin_balances_are_distinct(monkeypatch):
    monkeypatch.setattr(
        order_data,
        "get_oa_symbol",
        lambda native, exchange, category: f"{category.upper()}-{native}",
    )
    positions = order_data.map_position_data(
        {
            "result": [
                {
                    "symbol": "BTCUSDT",
                    "category": "linear",
                    "size": "0.1",
                    "side": "Buy",
                    "avgPrice": "100",
                    "markPrice": "110",
                    "unrealisedPnl": "1",
                }
            ]
        }
    )
    holdings = order_data.map_portfolio_data(
        {"result": [{"coin": [{"coin": "BTC", "equity": "0.5", "usdValue": "50000"}]}]}
    )

    assert positions[0]["symbol"] == "LINEAR-BTCUSDT"
    assert positions[0]["side"] == "BUY"
    assert holdings[0]["symbol"] == "BTC"
    assert holdings[0]["asset_type"] == "account_coin_balance"
    assert holdings[0]["usd_value"] == 50000


def test_orderbook_and_tradebook_services_accept_bybit_mapping_contract(monkeypatch):
    from database import settings_db

    monkeypatch.setattr(settings_db, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(
        order_data,
        "get_oa_symbol",
        lambda native, exchange, category: f"{category.upper()}-{native}",
    )
    order_payload = {
        "result": [
            {
                "orderId": "order-1",
                "symbol": "BTCUSDT",
                "category": "spot",
                "side": "Buy",
                "qty": "1",
                "cumExecQty": "0",
                "price": "100",
                "orderType": "Limit",
                "orderStatus": "New",
                "createdTime": "1790430181000",
            }
        ]
    }
    trade_payload = {
        "result": [
            {
                "orderId": "order-1",
                "execId": "fill-1",
                "symbol": "BTCUSDT",
                "category": "spot",
                "side": "Buy",
                "execQty": "1",
                "execPrice": "100",
                "execValue": "100",
                "execTime": "1790430181000",
            }
        ]
    }
    monkeypatch.setattr(
        orderbook_service,
        "import_broker_module",
        lambda broker: {
            "get_order_book": lambda auth: order_payload,
            "map_order_data": order_data.map_order_data,
            "calculate_order_statistics": order_data.calculate_order_statistics,
            "transform_order_data": order_data.transform_order_data,
        },
    )
    monkeypatch.setattr(
        tradebook_service,
        "import_broker_module",
        lambda broker: {
            "get_trade_book": lambda auth: trade_payload,
            "map_trade_data": order_data.map_trade_data,
            "transform_tradebook_data": order_data.transform_tradebook_data,
        },
    )

    order_result = orderbook_service.get_orderbook_with_auth("token", "bybit", original_data=None)
    trade_result = tradebook_service.get_tradebook_with_auth("token", "bybit", original_data=None)

    assert order_result[0] is True
    assert order_result[1]["data"]["orders"][0]["symbol"] == "SPOT-BTCUSDT"
    assert trade_result[0] is True
    assert trade_result[1]["data"][0]["tradeid"] == "fill-1"
