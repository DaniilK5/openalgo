"""Bybit V5 public market-data adapter."""

import queue
import threading
import time

from broker.bybit.streaming.bybit_mapping import (
    BybitCapabilityRegistry,
    BybitMapper,
    BybitModeMapper,
)
from broker.bybit.streaming.bybit_websocket import BybitWebSocket
from database.token_db import get_br_symbol, get_symbol_info
from utils.logging import get_logger
from websocket_proxy.base_adapter import BaseBrokerWebSocketAdapter

logger = get_logger(__name__)

_RECONNECT_DELAYS = (1, 2, 5, 10, 30)
_MAX_PUBLISH_QUEUE = 10000


def _float(value, default=0.0):
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else default
    except (TypeError, ValueError):
        return default


def _book_rows(rows):
    normalized = []
    if not isinstance(rows, list):
        return normalized
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        price, quantity = _float(row[0], None), _float(row[1], None)
        if price is None or quantity is None:
            continue
        normalized.append((price, quantity))
    return normalized


class BybitWebSocketAdapter(BaseBrokerWebSocketAdapter):
    """Stream Bybit Spot, Linear, Inverse, and Options market data."""

    def __init__(self):
        super().__init__()
        self.logger = logger
        self.broker_name = "bybit"
        self.user_id = None
        self.running = False
        self.public_ws = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._closed = False
        self._public_threads = {}
        self._worker_threads = set()
        self._subscriptions = {}
        self._symbols_by_topic = {}
        self._publish_queue = queue.Queue(maxsize=_MAX_PUBLISH_QUEUE)
        self._publisher_thread = None

    def initialize(self, broker_name: str, user_id: str, auth_data: dict | None = None):
        self.broker_name = broker_name
        self.user_id = user_id

    def connect(self):
        with self._lock:
            if self.running:
                return self._create_success_response("Bybit adapter is already running")
            self._worker_threads = {
                thread for thread in self._worker_threads if thread.is_alive()
            }
            if self._worker_threads:
                return self._create_error_response(
                    "DISCONNECT_IN_PROGRESS",
                    "The previous Bybit connection is still shutting down. Try again shortly.",
                )
            if self._closed or getattr(self, "_zmq_cleaned_up", False):
                return self._create_error_response(
                    "ADAPTER_CLOSED",
                    "This Bybit adapter has been shut down. Create a new connection to restart it.",
                )
            self.running = True
            self._stop_event.clear()
            self._publisher_thread = self._start_worker(
                self._publish_loop,
                name=f"bybit-zmq-publisher-{self.user_id or 'unknown'}",
            )
            self.connected = True
        return self._create_success_response("Bybit market-data adapter started")

    def disconnect(self):
        with self._lock:
            self.running = False
            self.connected = False
            self._closed = True
            self._stop_event.set()
            clients = list(self.public_ws.values())
            self.public_ws.clear()
            self._subscriptions.clear()
            self._symbols_by_topic.clear()
            self._public_threads.clear()
            workers = list(self._worker_threads)

        for client in clients:
            try:
                client.close_connection()
            except Exception:
                self.logger.warning("Bybit WebSocket close failed")

        current_thread = threading.current_thread()
        for worker in workers:
            if worker is not current_thread and worker.is_alive():
                worker.join(timeout=5)

        with self._lock:
            active_workers = {thread for thread in self._worker_threads if thread.is_alive()}
            if not active_workers:
                self._publisher_thread = None
        if active_workers:
            self.logger.error("Bybit adapter workers did not stop before the shutdown deadline")
            return

        while True:
            try:
                self._publish_queue.get_nowait()
            except queue.Empty:
                break
        self.cleanup_zmq()

    def subscribe(self, symbol: str, exchange: str, mode: int = 2, depth_level: int = 5):
        exchange = str(exchange or "").upper()
        try:
            mode = int(mode)
        except (TypeError, ValueError):
            mode = -1
        if not BybitCapabilityRegistry.supports_mode(mode):
            return self._create_error_response(
                "INVALID_MODE", "Bybit supports LTP, Quote, and Depth modes."
            )
        if exchange != "CRYPTO":
            return self._create_error_response(
                "UNSUPPORTED_EXCHANGE", "Bybit streaming supports CRYPTO symbols."
            )

        symbol_info = get_symbol_info(symbol, exchange)
        category = str(getattr(symbol_info, "category", "") or "").lower()
        segment = BybitMapper.get_segment(exchange, category)
        br_symbol = (
            getattr(symbol_info, "brsymbol", None) or get_br_symbol(symbol, exchange)
            if symbol_info
            else None
        )
        if not segment or not br_symbol:
            return self._create_error_response(
                "SYMBOL_NOT_FOUND",
                "Bybit symbol metadata is unavailable. Update the symbol list and try again.",
            )

        if mode == 3:
            try:
                depth_level = int(depth_level)
            except (TypeError, ValueError):
                depth_level = -1
            if not BybitCapabilityRegistry.is_depth_level_supported(exchange, depth_level):
                return self._create_error_response(
                    "UNSUPPORTED_DEPTH_LEVEL",
                    "Bybit supports depth levels 5, 20, 30, and 50.",
                )

        key = (symbol, exchange)
        native_symbol = BybitMapper.get_channel_symbol(br_symbol)
        with self._lock:
            record = self._subscriptions.get(key)
            if record is None:
                record = {
                    "symbol": symbol,
                    "exchange": exchange,
                    "category": segment,
                    "br_symbol": native_symbol,
                    "modes": set(),
                    "depth_level": depth_level if mode == 3 else 5,
                    "book": None,
                    "ticker": None,
                    "ticker_subscribed": False,
                    "book_subscribed": False,
                }
                self._subscriptions[key] = record
                self._symbols_by_topic[(segment, native_symbol)] = key
            elif record["category"] != segment or record["br_symbol"] != native_symbol:
                return self._create_error_response(
                    "SYMBOL_METADATA_CHANGED",
                    "Bybit symbol metadata changed. Refresh the symbol list and subscribe again.",
                )

            record["modes"].add(mode)
            if mode == 3:
                record["depth_level"] = max(record["depth_level"], depth_level)
            client = self.public_ws.get(segment)
            if client:
                self._subscribe_record(client, record)
            else:
                self._start_public_connection(segment)

        response = self._create_success_response(
            "Bybit subscription queued", symbol=symbol, exchange=exchange, mode=mode
        )
        if mode == 3:
            response["actual_depth"] = depth_level
        return response

    def unsubscribe(self, symbol: str, exchange: str, mode: int = 2):
        exchange = str(exchange or "").upper()
        try:
            mode = int(mode)
        except (TypeError, ValueError):
            return self._create_error_response(
                "INVALID_MODE", "The requested mode is not supported."
            )

        key = (symbol, exchange)
        with self._lock:
            record = self._subscriptions.get(key)
            if not record or mode not in record["modes"]:
                return self._create_error_response(
                    "SUBSCRIPTION_NOT_FOUND", "The Bybit subscription was not found."
                )
            record["modes"].discard(mode)
            client = self.public_ws.get(record["category"])

            if not record["modes"]:
                if client:
                    if record["ticker_subscribed"]:
                        client.unsubscribe_ticker(record["br_symbol"])
                    if record["book_subscribed"]:
                        client.unsubscribe_orderbook(
                            BybitCapabilityRegistry.native_depth(record["category"]),
                            record["br_symbol"],
                        )
                self._subscriptions.pop(key, None)
                self._symbols_by_topic.pop((record["category"], record["br_symbol"]), None)
            elif mode == 3 and not any(active_mode == 3 for active_mode in record["modes"]):
                if client and record["book_subscribed"]:
                    client.unsubscribe_orderbook(
                        BybitCapabilityRegistry.native_depth(record["category"]),
                        record["br_symbol"],
                    )
                record["book_subscribed"] = False
                record["book"] = None

        return self._create_success_response(
            "Bybit unsubscribe queued", symbol=symbol, exchange=exchange, mode=mode
        )

    def get_supported_depth_levels(self, exchange: str):
        return BybitCapabilityRegistry.get_supported_depth_levels(exchange)

    def _start_public_connection(self, category: str):
        thread = self._public_threads.get(category)
        if thread and thread.is_alive():
            return
        thread = self._start_worker(
            lambda: self._run_public_connection(category),
            name=f"bybit-public-{category}-{self.user_id or 'unknown'}",
        )
        self._public_threads[category] = thread

    def _start_worker(self, target, name: str):
        thread = threading.Thread(
            target=self._run_worker,
            args=(target,),
            daemon=True,
            name=name,
        )
        self._worker_threads.add(thread)
        thread.start()
        return thread

    def _run_worker(self, target):
        try:
            target()
        finally:
            with self._lock:
                self._worker_threads.discard(threading.current_thread())

    def _run_public_connection(self, category: str):
        attempt = 0
        while self.running and not self._stop_event.is_set():
            client = None
            try:
                client = BybitWebSocket(
                    channel_type=category,
                    on_message=lambda payload, ws_category=category: self._on_public_message(
                        ws_category, payload
                    ),
                )
                with self._lock:
                    if not self.running:
                        client.close_connection()
                        return
                    self.public_ws[category] = client
                    for record in self._category_subscriptions(category):
                        record["ticker_subscribed"] = False
                        record["book_subscribed"] = False
                        self._subscribe_record(client, record)
                attempt = 0
                while self.running and not self._stop_event.is_set() and client.is_running():
                    self._stop_event.wait(0.25)
            except Exception:
                self.logger.warning("Bybit %s public WebSocket disconnected", category)
            finally:
                with self._lock:
                    if self.public_ws.get(category) is client:
                        self.public_ws.pop(category, None)
                    for record in self._category_subscriptions(category):
                        record["ticker_subscribed"] = False
                        record["book_subscribed"] = False
                if client:
                    try:
                        client.close_connection()
                    except Exception:
                        self.logger.debug("Bybit public WebSocket cleanup failed")

            if not self.running or self._stop_event.is_set():
                break
            delay = _RECONNECT_DELAYS[min(attempt, len(_RECONNECT_DELAYS) - 1)]
            attempt += 1
            self._stop_event.wait(delay)

    def _category_subscriptions(self, category: str):
        return [
            record
            for record in self._subscriptions.values()
            if record["category"] == category and record["modes"]
        ]

    def _subscribe_record(self, client: BybitWebSocket, record: dict):
        if not record["ticker_subscribed"]:
            client.subscribe_ticker(record["br_symbol"])
            record["ticker_subscribed"] = True
        if 3 in record["modes"] and not record["book_subscribed"]:
            client.subscribe_orderbook(
                BybitCapabilityRegistry.native_depth(record["category"]),
                record["br_symbol"],
            )
            record["book_subscribed"] = True

    def _on_public_message(self, category: str, payload):
        if not self.running or not isinstance(payload, dict):
            return
        topic = str(payload.get("topic") or "")
        native_symbol = topic.rsplit(".", 1)[-1]
        with self._lock:
            key = self._symbols_by_topic.get((category, native_symbol))
            record = self._subscriptions.get(key) if key else None
            if not record:
                return
            try:
                if topic.startswith("tickers."):
                    ticker = self._ticker_data(payload.get("data"), native_symbol)
                    if ticker is None:
                        return
                    record["ticker"] = self._normalize_ticker(record, ticker, payload)
                    updates = self._ticker_updates(record)
                elif topic.startswith("orderbook."):
                    record["book"] = self._merge_orderbook(
                        record.get("book"),
                        payload.get("data") or {},
                        payload.get("type") or "snapshot",
                    )
                    updates = self._depth_update(record, payload)
                else:
                    return
            except Exception:
                self.logger.exception("Bybit public market-data normalization failed")
                return

        for internal_topic, data in updates:
            self._enqueue_publish(internal_topic, data)

    @staticmethod
    def _ticker_data(data, native_symbol):
        if isinstance(data, list):
            return next(
                (
                    row
                    for row in data
                    if isinstance(row, dict) and row.get("symbol") == native_symbol
                ),
                None,
            )
        return data if isinstance(data, dict) else None

    @staticmethod
    def _normalize_ticker(record: dict, ticker: dict, message: dict):
        last_price = _float(ticker.get("lastPrice") or ticker.get("last_price"))
        previous_price = _float(ticker.get("prevPrice24h"))
        change = last_price - previous_price if previous_price else 0.0
        timestamp = message.get("ts") or ticker.get("ts") or int(time.time() * 1000)
        return {
            "symbol": record["symbol"],
            "exchange": record["exchange"],
            "ltp": last_price,
            "last_price": last_price,
            "open": previous_price,
            "high": _float(ticker.get("highPrice24h")),
            "low": _float(ticker.get("lowPrice24h")),
            "close": previous_price,
            "change": change,
            "change_percent": _float(ticker.get("price24hPcnt")) * 100,
            "volume": _float(ticker.get("volume24h")),
            "average_price": _float(ticker.get("averagePrice")),
            "last_quantity": _float(ticker.get("lastTradeQty")),
            "total_buy_quantity": 0.0,
            "total_sell_quantity": 0.0,
            "oi": _float(ticker.get("openInterest")),
            "bid": _float(ticker.get("bid1Price")),
            "ask": _float(ticker.get("ask1Price")),
            "bid_qty": _float(ticker.get("bid1Size")),
            "ask_qty": _float(ticker.get("ask1Size")),
            "timestamp": timestamp,
        }

    @staticmethod
    def _merge_orderbook(book, data: dict, message_type: str):
        """Apply a V5 snapshot or delta, including Bybit's update-id reset."""
        if not isinstance(data, dict):
            return book
        update_id = data.get("u")
        snapshot = book is None or str(message_type).lower() == "snapshot" or str(update_id) == "1"
        next_book = (
            {"b": {}, "a": {}}
            if snapshot
            else {
                "b": dict(book["b"]),
                "a": dict(book["a"]),
            }
        )
        for source_key, target_key in (("b", "b"), ("a", "a")):
            rows = _book_rows(data.get(source_key))
            if snapshot:
                next_book[target_key] = {
                    price: quantity for price, quantity in rows if quantity > 0
                }
                continue
            for price, quantity in rows:
                if quantity <= 0:
                    next_book[target_key].pop(price, None)
                else:
                    next_book[target_key][price] = quantity
        next_book["u"] = update_id
        next_book["seq"] = data.get("seq")
        return next_book

    @staticmethod
    def _ticker_updates(record: dict):
        ticker = dict(record["ticker"])
        modes = set(record["modes"])
        updates = []
        if 1 in modes:
            ltp_data = {key: ticker[key] for key in ("symbol", "exchange", "ltp", "timestamp")}
            ltp_data["mode"] = "ltp"
            updates.append((f"{record['exchange']}_{record['symbol']}_LTP", ltp_data))
        if 2 in modes:
            quote_data = dict(ticker, mode="quote")
            updates.append((f"{record['exchange']}_{record['symbol']}_QUOTE", quote_data))
        if 3 in modes:
            depth_data = BybitWebSocketAdapter._format_depth(record, ticker)
            updates.append((f"{record['exchange']}_{record['symbol']}_DEPTH", depth_data))
        return updates

    @staticmethod
    def _depth_update(record: dict, message: dict):
        if 3 not in record["modes"]:
            return []
        ticker = dict(record["ticker"] or {})
        timestamp = message.get("ts") or ticker.get("timestamp") or int(time.time() * 1000)
        ticker["timestamp"] = timestamp
        data = BybitWebSocketAdapter._format_depth(record, ticker)
        return [(f"{record['exchange']}_{record['symbol']}_DEPTH", data)]

    @staticmethod
    def _format_depth(record: dict, ticker: dict):
        book = record.get("book") or {"b": {}, "a": {}}
        requested_depth = record.get("depth_level", 5)
        bids = sorted(book["b"].items(), key=lambda item: item[0], reverse=True)[:requested_depth]
        asks = sorted(book["a"].items(), key=lambda item: item[0])[:requested_depth]
        buy = [{"price": price, "quantity": quantity, "orders": 0} for price, quantity in bids]
        sell = [{"price": price, "quantity": quantity, "orders": 0} for price, quantity in asks]
        data = dict(ticker)
        data.update(
            {
                "symbol": record["symbol"],
                "exchange": record["exchange"],
                "ltp": ticker.get("ltp", 0.0),
                "timestamp": ticker.get("timestamp", int(time.time() * 1000)),
                "mode": "depth",
                "total_buy_quantity": sum(row["quantity"] for row in buy),
                "total_sell_quantity": sum(row["quantity"] for row in sell),
                "depth": {"buy": buy, "sell": sell},
            }
        )
        return data

    def _enqueue_publish(self, topic: str, data: dict):
        if not self.running:
            return
        try:
            self._publish_queue.put_nowait((topic, data))
        except queue.Full:
            try:
                self._publish_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._publish_queue.put_nowait((topic, data))
            except queue.Full:
                self.logger.debug("Bybit market-data queue is full; dropping an update")

    def _publish_loop(self):
        while not self._stop_event.is_set():
            try:
                topic, data = self._publish_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            self.publish_market_data(topic, data)
