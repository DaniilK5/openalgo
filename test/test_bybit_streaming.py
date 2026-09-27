import os
import threading
from decimal import Decimal
from types import SimpleNamespace

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("API_KEY_PEPPER", "a" * 64)

import websocket_proxy  # noqa: E402, F401
from broker.bybit.streaming import bybit_adapter, bybit_order_adapter, bybit_websocket
from broker.bybit.streaming.bybit_adapter import BybitWebSocketAdapter
from broker.bybit.streaming.bybit_mapping import (
    BybitCapabilityRegistry,
    BybitMapper,
)
from broker.bybit.streaming.bybit_order_adapter import BybitOrderUpdateAdapter
from broker.bybit.streaming.bybit_websocket import BybitWebSocket
from events import ExecutionUpdateEvent
from services import order_update_service
from websocket_proxy.base_adapter import BaseBrokerWebSocketAdapter


def _adapter(monkeypatch):
    def initialize_base(adapter):
        adapter.connected = False

    monkeypatch.setattr(BaseBrokerWebSocketAdapter, "__init__", initialize_base)
    adapter = BybitWebSocketAdapter()
    adapter.initialize("bybit", "test-user")
    adapter.running = True
    return adapter


def test_bybit_categories_are_selected_from_persisted_symbol_metadata(monkeypatch):
    adapter = _adapter(monkeypatch)
    starts = []
    monkeypatch.setattr(
        bybit_adapter,
        "get_symbol_info",
        lambda symbol, exchange: SimpleNamespace(category="inverse", brsymbol="BTCUSD"),
    )
    monkeypatch.setattr(bybit_adapter, "get_br_symbol", lambda *_: "WRONG")
    monkeypatch.setattr(adapter, "_start_public_connection", starts.append)

    result = adapter.subscribe("BTCUSD", "CRYPTO", mode=3, depth_level=20)

    assert result["status"] == "success"
    assert result["actual_depth"] == 20
    assert starts == ["inverse"]
    record = adapter._subscriptions[("BTCUSD", "CRYPTO")]
    assert record["category"] == "inverse"
    assert record["br_symbol"] == "BTCUSD"


def test_bybit_rejects_missing_category_instead_of_guessing_linear(monkeypatch):
    adapter = _adapter(monkeypatch)
    monkeypatch.setattr(
        bybit_adapter,
        "get_symbol_info",
        lambda *_: SimpleNamespace(category=None, brsymbol="BTCUSDT"),
    )
    monkeypatch.setattr(
        adapter, "_start_public_connection", lambda *_: pytest.fail("must not connect")
    )

    result = adapter.subscribe("BTCUSDT", "CRYPTO", mode=1)

    assert result["status"] == "error"
    assert result["code"] == "SYMBOL_NOT_FOUND"


def test_bybit_public_payloads_are_normalized_for_all_requested_modes(monkeypatch):
    adapter = _adapter(monkeypatch)
    monkeypatch.setattr(
        bybit_adapter,
        "get_symbol_info",
        lambda *_: SimpleNamespace(category="spot", brsymbol="BTCUSDT"),
    )
    monkeypatch.setattr(adapter, "_start_public_connection", lambda *_: None)
    adapter.subscribe("BTCUSDT", "CRYPTO", mode=1)
    adapter.subscribe("BTCUSDT", "CRYPTO", mode=2)
    adapter.subscribe("BTCUSDT", "CRYPTO", mode=3, depth_level=5)

    adapter._on_public_message(
        "spot",
        {
            "topic": "tickers.BTCUSDT",
            "ts": 1234,
            "data": {
                "symbol": "BTCUSDT",
                "lastPrice": "101",
                "prevPrice24h": "100",
                "price24hPcnt": "0.01",
                "highPrice24h": "105",
                "lowPrice24h": "95",
                "volume24h": "12",
                "bid1Price": "100.5",
                "ask1Price": "101.5",
            },
        },
    )
    adapter._on_public_message(
        "spot",
        {
            "topic": "orderbook.50.BTCUSDT",
            "type": "snapshot",
            "data": {"b": [["100", "2"]], "a": [["102", "3"]], "u": 2},
        },
    )

    outputs = []
    while not adapter._publish_queue.empty():
        outputs.append(adapter._publish_queue.get_nowait())
    by_topic = dict(outputs)
    assert set(by_topic) == {
        "CRYPTO_BTCUSDT_LTP",
        "CRYPTO_BTCUSDT_QUOTE",
        "CRYPTO_BTCUSDT_DEPTH",
    }
    assert by_topic["CRYPTO_BTCUSDT_LTP"]["ltp"] == 101
    assert by_topic["CRYPTO_BTCUSDT_QUOTE"]["change_percent"] == 1
    depth = by_topic["CRYPTO_BTCUSDT_DEPTH"]
    assert depth["depth"]["buy"] == [{"price": 100.0, "quantity": 2.0, "orders": 0}]
    assert depth["depth"]["sell"] == [{"price": 102.0, "quantity": 3.0, "orders": 0}]


def test_public_reconnect_replays_ticker_and_orderbook_subscriptions(monkeypatch):
    adapter = _adapter(monkeypatch)
    monkeypatch.setattr(
        bybit_adapter,
        "get_symbol_info",
        lambda *_: SimpleNamespace(category="option", brsymbol="BTC-OPTION"),
    )
    monkeypatch.setattr(adapter, "_start_public_connection", lambda *_: None)
    adapter.subscribe("BTC-OPTION", "CRYPTO", mode=3, depth_level=50)
    calls = []

    class FakeClient:
        def __init__(self, channel_type, on_message):
            calls.append(("connect", channel_type))
            self.on_message = on_message
            adapter._stop_event.set()

        def subscribe_ticker(self, symbol):
            calls.append(("ticker", symbol))

        def subscribe_orderbook(self, depth, symbol):
            calls.append(("orderbook", depth, symbol))

        def is_running(self):
            return False

        def close_connection(self):
            calls.append(("close",))

    monkeypatch.setattr(bybit_adapter, "BybitWebSocket", FakeClient)

    adapter._run_public_connection("option")

    assert calls == [
        ("connect", "option"),
        ("ticker", "BTC-OPTION"),
        ("orderbook", 100, "BTC-OPTION"),
        ("close",),
    ]


def test_public_adapter_does_not_restart_while_previous_worker_is_alive(monkeypatch):
    adapter = _adapter(monkeypatch)
    adapter.running = False
    adapter._stop_event.set()

    class ActiveWorker:
        def is_alive(self):
            return True

    adapter._worker_threads.add(ActiveWorker())

    result = adapter.connect()

    assert result["status"] == "error"
    assert result["code"] == "DISCONNECT_IN_PROGRESS"
    assert adapter._stop_event.is_set()
    assert not adapter.running


def test_public_disconnect_signals_and_joins_workers_before_cleanup(monkeypatch):
    adapter = _adapter(monkeypatch)
    adapter._worker_threads.clear()
    adapter.running = True
    cleaned = []
    monkeypatch.setattr(adapter, "cleanup_zmq", lambda: cleaned.append(True))
    worker = adapter._start_worker(
        adapter._stop_event.wait,
        name="bybit-lifecycle-test-worker",
    )

    adapter.disconnect()

    assert not worker.is_alive()
    assert not adapter._worker_threads
    assert cleaned == [True]
    result = adapter.connect()
    assert result["status"] == "error"
    assert result["code"] == "ADAPTER_CLOSED"


def test_bybit_orderbook_snapshot_delta_and_update_id_reset():
    book = BybitWebSocketAdapter._merge_orderbook(
        None,
        {"b": [["100", "2"], ["99", "1"]], "a": [["101", "3"]], "u": 7},
        "snapshot",
    )
    book = BybitWebSocketAdapter._merge_orderbook(
        book,
        {"b": [["100", "4"], ["99", "0"], ["98", "2"]], "a": [], "u": 8},
        "delta",
    )

    assert book["b"] == {100.0: 4.0, 98.0: 2.0}
    assert book["a"] == {101.0: 3.0}

    reset = BybitWebSocketAdapter._merge_orderbook(
        book,
        {"b": [["97", "5"]], "a": [["103", "6"]], "u": 1},
        "delta",
    )
    assert reset["b"] == {97.0: 5.0}
    assert reset["a"] == {103.0: 6.0}


def test_bybit_depth_levels_cover_native_category_limits():
    assert BybitMapper.get_segment("CRYPTO", "spot") == "spot"
    assert BybitMapper.get_segment("CRYPTO", "linear") == "linear"
    assert BybitMapper.get_segment("CRYPTO", "inverse") == "inverse"
    assert BybitMapper.get_segment("CRYPTO", "option") == "option"
    assert BybitCapabilityRegistry.get_supported_depth_levels("CRYPTO") == [5, 20, 30, 50]
    assert BybitCapabilityRegistry.native_depth("spot") == 50
    assert BybitCapabilityRegistry.native_depth("option") == 100


def test_pybit_websocket_uses_environment_testnet_and_official_topics(monkeypatch):
    calls = []

    class FakePybitWebSocket:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        def ticker_stream(self, symbol, callback):
            calls.append(("ticker", symbol, callback))

        def orderbook_stream(self, depth, symbol, callback):
            calls.append(("orderbook", depth, symbol, callback))

        def order_stream(self, callback):
            calls.append(("order", callback))

        def execution_stream(self, callback):
            calls.append(("execution", callback))

        def position_stream(self, callback):
            calls.append(("position", callback))

        def wallet_stream(self, callback):
            calls.append(("wallet", callback))

    monkeypatch.setattr(bybit_websocket, "WebSocket", FakePybitWebSocket)
    monkeypatch.setattr("broker.bybit.streaming.bybit_websocket.is_testnet", lambda: True)

    def callback(_payload):
        return None

    client = BybitWebSocket("option", callback)
    client.subscribe_ticker("BTC-OPTION")
    client.subscribe_orderbook(100, "BTC-OPTION")

    init = calls[0][1]
    assert init["channel_type"] == "option"
    assert init["testnet"] is True
    assert init["trace_logging"] is False
    assert calls[1][0:2] == ("ticker", "BTC-OPTION")
    assert calls[2][0:3] == ("orderbook", 100, "BTC-OPTION")

    private = BybitWebSocket("private", callback, api_key="key", api_secret="secret")
    private.subscribe_private()
    assert [call[0] for call in calls[-4:]] == [
        "order",
        "execution",
        "position",
        "wallet",
    ]
    private_init = calls[-5][1]
    assert private_init["api_key"] == "key"
    assert private_init["api_secret"] == "secret"


def test_bybit_order_updates_normalize_orders_and_preserve_spot_fraction(monkeypatch):
    adapter = BybitOrderUpdateAdapter("user", "key", "secret")
    monkeypatch.setattr(
        bybit_order_adapter, "_cached_openalgo_symbol", lambda native_symbol, category: "BTCUSDT"
    )

    fields = adapter.normalize(
        {
            "topic": "order",
            "data": [
                {
                    "orderId": "123",
                    "category": "spot",
                    "symbol": "BTCUSDT",
                    "side": "Buy",
                    "qty": "0.005",
                    "cumExecQty": "0.002",
                    "leavesQty": "0.003",
                    "price": "60000",
                    "avgPrice": "59950",
                    "orderType": "Limit",
                    "orderStatus": "PartiallyFilled",
                }
            ],
        }
    )

    assert fields["orderid"] == "123"
    assert fields["symbol"] == "BTCUSDT"
    assert fields["exchange"] == "CRYPTO"
    assert fields["quantity"] == 0.005
    assert fields["filled_quantity"] == 0.002
    assert fields["pending_quantity"] == 0.003
    assert fields["order_status"] == "open"
    assert fields["pricetype"] == "LIMIT"
    assert adapter.normalize({"topic": "wallet", "data": []}) is None


def test_bybit_execution_events_preserve_exact_decimals_and_identity(monkeypatch):
    adapter = BybitOrderUpdateAdapter("ledger-user", "key", "secret")
    monkeypatch.setattr(
        bybit_order_adapter,
        "_cached_openalgo_symbol",
        lambda native_symbol, category: "BTCUSDT",
    )
    event = adapter.normalize_execution(
        {
            "execId": "execution-1",
            "orderId": "order-1",
            "category": "spot",
            "symbol": "BTCUSDT",
            "side": "Buy",
            "execQty": "0.00012345",
            "execPrice": "67234.12345678",
            "execFee": "0.00000012",
            "feeCurrency": "BTC",
            "orderLinkId": "scalping-link",
        }
    )

    assert event == {
        "exec_id": "execution-1",
        "order_id": "order-1",
        "user_id": "ledger-user",
        "symbol": "BTCUSDT",
        "exchange": "CRYPTO",
        "side": "BUY",
        "quantity": Decimal("0.00012345"),
        "price": Decimal("67234.12345678"),
        "fee_amount": Decimal("0.00000012"),
        "fee_currency": "BTC",
        "broker": "bybit",
        "order_link_id": "scalping-link",
    }


def test_bybit_execution_callback_publishes_typed_events_from_dispatcher(monkeypatch):
    adapter = BybitOrderUpdateAdapter("ledger-user", "key", "secret")
    adapter._running = True
    adapter._event_dispatching = True
    monkeypatch.setattr(
        bybit_order_adapter,
        "_cached_openalgo_symbol",
        lambda native_symbol, category: "BTCUSDT",
    )
    published = []
    monkeypatch.setattr(bybit_order_adapter.bus, "publish", published.append)
    message = {
        "topic": "execution",
        "data": [
            {
                "execId": "execution-1",
                "orderId": "order-1",
                "category": "spot",
                "symbol": "BTCUSDT",
                "side": "Sell",
                "execQty": "0.25",
                "execPrice": "60000.10",
                "execFee": "0.0001",
                "feeCurrency": "BTC",
            }
        ],
    }

    adapter._on_private_message(message)

    assert published == []
    adapter._event_dispatching = False
    adapter._run_event_dispatcher()

    assert len(published) == 1
    event = published[0]
    assert isinstance(event, ExecutionUpdateEvent)
    assert event.topic == "execution.update"
    assert event.exec_id == "execution-1"
    assert event.order_id == "order-1"
    assert event.user_id == "ledger-user"
    assert event.quantity == Decimal("0.25")
    assert event.price == Decimal("60000.10")
    assert event.fee_amount == Decimal("0.0001")
    assert event.fee_currency == "BTC"


def test_bybit_execution_callback_ignores_invalid_or_unmapped_events(monkeypatch):
    adapter = BybitOrderUpdateAdapter("ledger-user", "key", "secret")
    adapter._running = True
    monkeypatch.setattr(bybit_order_adapter, "_cached_openalgo_symbol", lambda *_: None)

    assert adapter.normalize_execution({"execId": "missing-fields"}) is None
    assert (
        adapter.normalize_execution(
            {
                "execId": "execution-1",
                "orderId": "order-1",
                "category": "spot",
                "symbol": "UNKNOWN",
                "side": "Buy",
                "execQty": "0.1",
                "execPrice": "1",
                "execFee": "0",
                "feeCurrency": "USDT",
            }
        )
        is None
    )
    adapter._on_private_message({"topic": "wallet", "data": [{"coin": "BTC"}]})
    assert adapter._event_queue.empty()


def test_private_adapter_waits_for_old_workers_before_restart(monkeypatch):
    adapter = BybitOrderUpdateAdapter("user", "key", "secret")

    class ActiveWorker:
        def is_alive(self):
            return True

    adapter._thread = ActiveWorker()
    adapter._event_dispatch_thread = ActiveWorker()
    joins = []
    monkeypatch.setattr(
        bybit_order_adapter.real_threading,
        "join",
        lambda thread, timeout: joins.append((thread, timeout)) or False,
    )

    assert adapter.disconnect() is False
    assert len(joins) == 2
    with pytest.raises(RuntimeError, match="still shutting down"):
        adapter.connect()


def test_bybit_is_registered_with_order_update_service():
    assert order_update_service._BROKER_FACTORIES["bybit"] == (
        "broker.bybit.streaming.bybit_order_adapter",
        "create_bybit_order_adapter",
    )


def test_order_update_service_does_not_replace_adapter_until_shutdown_finishes(monkeypatch):
    class PreviousAdapter:
        def disconnect(self):
            return False

    monkeypatch.setattr(order_update_service, "_ADAPTERS", {"user": PreviousAdapter()})
    monkeypatch.setattr(order_update_service, "_order_updates_enabled", lambda: True)

    def build(*_args):
        pytest.fail("must not build a replacement while the old socket is alive")

    monkeypatch.setattr(order_update_service, "_build_adapter", build)

    assert not order_update_service.start_order_update_adapter("user", "bybit")
