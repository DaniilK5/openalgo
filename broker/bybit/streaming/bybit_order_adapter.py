"""Bybit V5 private order-update adapter."""

import json
import math
import os
import threading
import time
from decimal import Decimal, InvalidOperation
from queue import Full

from broker.bybit.api.baseurl import get_private_ws_url
from broker.bybit.streaming.bybit_websocket import BybitWebSocket
from database.auth_db import get_auth_token
from database.token_db_enhanced import get_cache
from events import ExecutionUpdateEvent
from utils import real_threading
from utils.event_bus import bus
from utils.logging import get_logger
from websocket_proxy.order_adapter import BaseOrderUpdateAdapter

logger = get_logger(__name__)

_EVENT_QUEUE_MAX = 10000
_EVENT_DISPATCH_POLL = 0.005

_STATUS_MAP = {
    "NEW": "open",
    "PARTIALLYFILLED": "open",
    "UNTRIGGERED": "trigger pending",
    "TRIGGERED": "open",
    "PENDINGCANCEL": "open",
    "FILLED": "complete",
    "CANCELLED": "cancelled",
    "PARTIALLYFILLEDCANCELED": "cancelled",
    "DEACTIVATED": "cancelled",
    "REJECTED": "rejected",
}


def _float(value):
    try:
        number = float(value or 0)
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _quantity(value):
    """Keep Bybit Spot's fractional base-asset quantities intact."""
    number = _float(value)
    return int(number) if number.is_integer() else number


def _decimal(value):
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _cached_openalgo_symbol(native_symbol: str, category: str):
    """Resolve an order symbol without falling back to a database query in the callback."""
    try:
        cache = get_cache()
        if cache.cache_loaded and cache.is_cache_valid():
            return cache.get_oa_symbol(native_symbol, "CRYPTO", category)
    except Exception:
        return None
    return None


class BybitOrderUpdateAdapter(BaseOrderUpdateAdapter):
    """Subscribe to Bybit order, execution, position, and wallet private topics."""

    def __init__(self, user_id: str, api_key: str, api_secret: str):
        super().__init__(broker_name="bybit", user_id=user_id)
        self.api_key = api_key
        self.api_secret = api_secret
        self._sdk_ws = None
        self._stop_wait = threading.Event()
        self._event_queue = real_threading.Queue(maxsize=_EVENT_QUEUE_MAX)
        self._event_queue_lock = real_threading.Lock()
        self._event_dispatching = False
        self._event_dispatch_thread = None
        self._event_dispatch_generation = 0

    def get_ws_url(self) -> str:
        return get_private_ws_url()

    def get_headers(self):
        return None

    def connect(self):
        """Start pybit's private stream manager without blocking the service."""
        with self._lock:
            if self._running:
                return
            previous_threads = (self._thread, self._event_dispatch_thread)
            if any(thread and thread.is_alive() for thread in previous_threads):
                raise RuntimeError("The previous Bybit private stream is still shutting down")
            self._shutting_down = False
            self._auth_rejected = False
            self._stop_wait.clear()
            self._running = True
            with self._event_queue_lock:
                self._event_dispatching = True
                self._event_dispatch_generation += 1
                generation = self._event_dispatch_generation
            self._event_dispatch_thread = threading.Thread(
                target=self._run_event_dispatcher,
                args=(generation,),
                daemon=True,
                name=f"bybit-event-dispatch-{self.user_id}",
            )
            self._event_dispatch_thread.start()
            self._thread = threading.Thread(
                target=self._run_pybit,
                daemon=True,
                name=f"order-adapter-bybit-{self.user_id}",
            )
            self._thread.start()

    def disconnect(self):
        with self._lock:
            self._shutting_down = True
            self._running = False
            self._stop_wait.set()
            client = self._sdk_ws
            self._sdk_ws = None
            threads = (self._thread, self._event_dispatch_thread)
        if client:
            try:
                client.close_connection()
            except Exception:
                self.logger.warning("Bybit private WebSocket close failed")
        with self._event_queue_lock:
            self._event_dispatching = False
        current_thread = threading.current_thread()
        results = [
            real_threading.join(thread, timeout=5)
            for thread in threads
            if thread and thread is not current_thread
        ]
        stopped = all(results)
        if not stopped:
            self.logger.error("Bybit private stream workers did not stop before the shutdown deadline")
        return stopped

    @property
    def connected(self) -> bool:
        client = self._sdk_ws
        return bool(self._running and client and client.is_connected())

    def _run_pybit(self):
        attempt = 0
        delays = (1, 2, 5, 10, 30)
        while not self._shutting_down:
            client = None
            try:
                client = BybitWebSocket(
                    channel_type="private",
                    api_key=self.api_key,
                    api_secret=self.api_secret,
                    on_message=self._on_private_message,
                )
                with self._lock:
                    if self._shutting_down:
                        client.close_connection()
                        return
                    self._sdk_ws = client
                client.subscribe_private()
                attempt = 0

                while not self._shutting_down and client.is_running():
                    self._stop_wait.wait(0.25)
            except Exception:
                self.logger.warning("Bybit private WebSocket disconnected")
            finally:
                with self._lock:
                    if self._sdk_ws is client:
                        self._sdk_ws = None
                if client:
                    try:
                        client.close_connection()
                    except Exception:
                        self.logger.debug("Bybit private WebSocket cleanup failed")

            if self._shutting_down:
                break
            delay = delays[min(attempt, len(delays) - 1)]
            attempt += 1
            self.logger.info("Bybit private WebSocket reconnecting")
            self._wait_for_stop(delay)

    def _wait_for_stop(self, delay: float):
        self._stop_wait.wait(delay)

    def _on_private_message(self, payload):
        if not isinstance(payload, dict):
            return
        topic = payload.get("topic")
        if topic not in {"order", "execution"}:
            return
        if not self._running:
            return
        rows = payload.get("data")
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            return
        for row in rows:
            if topic == "order":
                fields = self.normalize({"topic": "order", "data": [row]})
                if fields:
                    self._enqueue_event("order", fields)
            else:
                fields = self.normalize_execution(row)
                if fields:
                    self._enqueue_event("execution", ExecutionUpdateEvent(**fields))

    def _enqueue_event(self, event_type: str, event):
        try:
            with self._event_queue_lock:
                if not self._running or not self._event_dispatching:
                    return
                self._event_queue.put_nowait((event_type, event))
        except Full:
            self.logger.warning("Bybit private event queue is full; an update was dropped")

    def _run_event_dispatcher(self, generation=None):
        """Publish private events on the hub, not from a foreign socket thread."""
        if generation is None:
            generation = self._event_dispatch_generation
        while True:
            with self._event_queue_lock:
                if generation != self._event_dispatch_generation:
                    return
                if not self._event_dispatching and self._event_queue.empty():
                    return
            try:
                event_type, event = self._event_queue.get_nowait()
            except real_threading.Empty:
                time.sleep(_EVENT_DISPATCH_POLL)
                continue
            try:
                if event_type == "order":
                    self._publish_event_fields(event)
                elif event_type == "execution":
                    bus.publish(event)
            except Exception:
                self.logger.exception("Failed to publish Bybit private update")

    def normalize_execution(self, row):
        """Normalize one Bybit execution without database or raw-frame logging."""
        if not isinstance(row, dict):
            return None
        exec_id = row.get("execId")
        order_id = row.get("orderId")
        category = str(row.get("category") or "").lower()
        native_symbol = str(row.get("symbol") or "")
        side = str(row.get("side") or "").upper()
        quantity = _decimal(row.get("execQty"))
        price = _decimal(row.get("execPrice"))
        fee_amount = _decimal(row.get("execFee"))
        fee_currency = str(row.get("feeCurrency") or "").upper()
        if (
            not exec_id
            or not order_id
            or not category
            or not native_symbol
            or side not in {"BUY", "SELL"}
            or quantity is None
            or quantity <= 0
            or price is None
            or price <= 0
            or fee_amount is None
        ):
            return None

        symbol = _cached_openalgo_symbol(native_symbol, category)
        if not symbol:
            self.logger.debug("Ignoring Bybit execution because symbol metadata is unavailable")
            return None
        return {
            "exec_id": str(exec_id),
            "order_id": str(order_id),
            "user_id": str(self.user_id or ""),
            "symbol": symbol,
            "exchange": "CRYPTO",
            "side": side,
            "quantity": quantity,
            "price": price,
            "fee_amount": fee_amount,
            "fee_currency": fee_currency,
            "broker": "bybit",
            "order_link_id": str(row.get("orderLinkId") or ""),
        }

    def normalize(self, raw_message):
        if isinstance(raw_message, (str, bytes, bytearray)):
            try:
                raw_message = json.loads(raw_message)
            except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                return None
        if not isinstance(raw_message, dict) or raw_message.get("topic") != "order":
            return None
        rows = raw_message.get("data")
        row = rows[0] if isinstance(rows, list) and rows else rows
        if not isinstance(row, dict) or not row.get("orderId"):
            return None

        category = str(row.get("category") or "").lower()
        native_symbol = str(row.get("symbol") or "")
        symbol = _cached_openalgo_symbol(native_symbol, category) if category else None
        raw_status = str(row.get("orderStatus") or "")
        status = _STATUS_MAP.get(raw_status.replace(" ", "").upper(), raw_status.lower() or "open")
        trigger_price = _float(row.get("triggerPrice"))
        order_type = str(row.get("orderType") or "").upper()
        if trigger_price:
            pricetype = "SL-M" if order_type == "MARKET" else "SL"
        else:
            pricetype = order_type
        quantity = _quantity(row.get("qty"))
        filled_quantity = _quantity(row.get("cumExecQty"))
        if "leavesQty" in row:
            pending_quantity = _quantity(row.get("leavesQty"))
        else:
            pending_quantity = max(quantity - filled_quantity, 0)

        return {
            "orderid": str(row["orderId"]),
            "symbol": symbol or native_symbol,
            "exchange": "CRYPTO",
            "action": str(row.get("side") or "").upper(),
            "quantity": quantity,
            "price": _float(row.get("price")),
            "trigger_price": trigger_price,
            "pricetype": pricetype,
            "product": "NRML",
            "order_status": status,
            "filled_quantity": filled_quantity,
            "pending_quantity": pending_quantity,
            "average_price": _float(row.get("avgPrice")),
            "rejection_reason": str(row.get("rejectReason") or "") if status == "rejected" else "",
            "order_link_id": str(row.get("orderLinkId") or ""),
        }


def create_bybit_order_adapter(user_id: str) -> BybitOrderUpdateAdapter | None:
    """Build a private adapter from the stored API key and configured secret."""
    api_key = get_auth_token(user_id, bypass_cache=True) or ""
    api_secret = os.getenv("BROKER_API_SECRET", "").strip()
    if not api_key or not api_secret:
        logger.info("Bybit order updates are unavailable because credentials are missing")
        return None
    return BybitOrderUpdateAdapter(user_id, api_key, api_secret)
