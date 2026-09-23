import json
import threading

from broker.bybit.streaming.bybit_mapping import BybitCapabilityRegistry, BybitMapper, BybitModeMapper
from broker.bybit.streaming.bybit_websocket import BybitWebSocket
from database.auth_db import get_auth_token
from utils.logging import get_logger
from websocket_proxy.base_adapter import BaseBrokerWebSocketAdapter

logger = get_logger("bybit_websocket_adapter")


class BybitWebSocketAdapter(BaseBrokerWebSocketAdapter):
    """Bybit public/private WebSocket adapter for linear crypto contracts."""

    def __init__(self):
        super().__init__()
        self.user_id = None
        self.broker_name = "bybit"
        self.public_ws = None
        self.private_ws = None
        self.api_key = None
        self.api_secret = None
        self._lock = threading.Lock()

    def initialize(self, broker_name: str, user_id: str, auth_data: dict | None = None) -> None:
        self.broker_name = broker_name
        self.user_id = user_id

        auth = auth_data or {}
        if auth:
            self.api_key = auth.get("api_key") or auth.get("token") or auth.get("access_token")
            self.api_secret = auth.get("api_secret") or auth.get("secret")
        if not self.api_key:
            self.api_key = get_auth_token(user_id, bypass_cache=True) or ""
        if not self.api_secret:
            import os
            self.api_secret = os.getenv("BROKER_API_SECRET", "")

    def _on_public_message(self, ws, payload):
        if not isinstance(payload, dict):
            return
        topic = payload.get("topic") or ""
        if not topic:
            return
        data = payload.get("data")
        if not isinstance(data, dict):
            return
        symbol = (topic.split(".")[-1] if "." in topic else data.get("symbol") or "")
        normalized = {"symbol": symbol.upper(), "broker": "bybit", "topic": topic}
        if "tickers" in topic:
            normalized.update({
                "ltp": data.get("lastPrice") or data.get("last_price") or 0,
                "last_price": data.get("lastPrice") or data.get("last_price") or 0,
                "bid": data.get("bid1Price") or data.get("bid_price") or 0,
                "ask": data.get("ask1Price") or data.get("ask_price") or 0,
                "bid_qty": data.get("bid1Size") or data.get("bid_size") or 0,
                "ask_qty": data.get("ask1Size") or data.get("ask_size") or 0,
                "volume_24h": data.get("volume24h") or data.get("volume_24h") or 0,
            })
        elif "orderbook" in topic:
            normalized.update({
                "bids": data.get("bids") or [],
                "asks": data.get("asks") or [],
                "depth": data.get("depth") or 0,
            })
        self.publish_market_data(topic, normalized)

    def _on_private_message(self, ws, payload):
        if not isinstance(payload, dict):
            return
        logger.debug("Bybit private message: %s", payload)

    def connect(self) -> None:
        if not self.api_key and not self.api_secret:
            logger.warning("Bybit adapter started without credentials; public streams will still connect")
        self.public_ws = BybitWebSocket(
            api_key=self.api_key,
            api_secret=self.api_secret,
            url=BybitWebSocket.PUBLIC_WS_URL,
            authenticate=False,
            name="public",
            on_message=self._on_public_message,
            on_error=lambda ws, err: logger.warning("Bybit public ws error: %s", err),
            on_close=lambda ws, code, reason: logger.info("Bybit public ws closed: %s %s", code, reason),
        )
        self.private_ws = BybitWebSocket(
            api_key=self.api_key,
            api_secret=self.api_secret,
            url=BybitWebSocket.PRIVATE_WS_URL,
            authenticate=True,
            name="private",
            on_message=self._on_private_message,
            on_error=lambda ws, err: logger.warning("Bybit private ws error: %s", err),
            on_close=lambda ws, code, reason: logger.info("Bybit private ws closed: %s %s", code, reason),
        )

        threading.Thread(target=self.public_ws.connect, daemon=True).start()
        if self.api_key and self.api_secret:
            threading.Thread(target=self.private_ws.connect, daemon=True).start()

    def disconnect(self) -> None:
        for client in (self.public_ws, self.private_ws):
            if client:
                try:
                    client.close_connection()
                except Exception:
                    logger.exception("Bybit websocket close failed")
        self.cleanup_zmq()

    def subscribe(self, symbol: str, exchange: str, mode: int = 2, depth_level: int = 5):
        if not BybitCapabilityRegistry.supports_mode(mode):
            return self._create_error_response("INVALID_MODE", f"Mode {mode} unsupported for Bybit")

        segment = BybitMapper.get_segment(exchange)
        if segment != "linear":
            return self._create_error_response("UNSUPPORTED_EXCHANGE", f"Exchange {exchange} not supported by Bybit linear v1")

        channels = BybitModeMapper.get_channels(mode)
        subscriptions = []
        for channel in channels:
            if channel == "tickers":
                subscriptions.append(f"tickers.{symbol}")
            elif channel == "orderbook":
                level = str(min(depth_level if depth_level in {1, 5, 50} else 50, 50))
                subscriptions.append(f"orderbook.{level}.{symbol}")

        if self.public_ws:
            for topic in subscriptions:
                self.public_ws.subscribe(topic.split(".")[0], symbol)
        return self._create_success_response("Bybit subscription queued", symbol=symbol, exchange=exchange, mode=mode, channels=subscriptions)

    def unsubscribe(self, symbol: str, exchange: str, mode: int = 2):
        if self.public_ws:
            for channel in BybitModeMapper.get_channels(mode):
                if channel == "tickers":
                    self.public_ws.unsubscribe("tickers", symbol)
                elif channel == "orderbook":
                    self.public_ws.unsubscribe("orderbook", symbol)
        return self._create_success_response("Bybit unsubscribe queued", symbol=symbol, exchange=exchange, mode=mode)
