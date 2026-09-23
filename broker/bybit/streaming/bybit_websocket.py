import hashlib
import hmac
import json
import ssl
import threading
import time

import websocket

from utils.logging import get_logger

logger = get_logger("bybit_websocket")


class BybitWebSocket:
    """Minimal but real Bybit V5 WebSocket client for linear public/private streams."""

    PUBLIC_WS_URL = "wss://stream.bybit.com/v5/public/linear"
    PRIVATE_WS_URL = "wss://stream.bybit.com/v5/private"
    HEARTBEAT_INTERVAL = 20

    def __init__(
        self,
        api_key=None,
        api_secret=None,
        url=None,
        authenticate=False,
        name=None,
        on_open=None,
        on_message=None,
        on_error=None,
        on_close=None,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.url = url or self.PUBLIC_WS_URL
        self.authenticate = authenticate
        self.name = name or ("private" if authenticate else "public")
        self.on_open = on_open or (lambda ws: None)
        self.on_message = on_message or (lambda ws, msg: None)
        self.on_error = on_error or (lambda ws, err: None)
        self.on_close = on_close or (lambda ws, code, reason: None)

        self.wsapp = None
        self.connected = False
        self._lock = threading.Lock()
        self._subscribed = set()

    @property
    def is_connected(self):
        return self.connected

    def _build_auth_message(self):
        if not self.api_key or not self.api_secret:
            return None
        expires = int(time.time() * 1000) + 60_000
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            f"{self.api_key}{expires}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {"op": "auth", "args": [self.api_key, str(expires), signature]}

    def _build_subscribe_message(self, channel, symbol):
        if channel == "tickers":
            args = [f"tickers.{symbol}"]
        elif channel == "orderbook":
            depth = "50" if symbol and symbol.startswith("orderbook") else "50"
            args = [f"orderbook.{depth}.{symbol}"]
        elif channel == "position":
            args = ["position"]
        elif channel == "execution":
            args = ["execution"]
        elif channel == "order":
            args = ["order"]
        else:
            args = [channel]
        return {"op": "subscribe", "args": args}

    def _ws_on_open(self, ws):
        self.connected = True
        with self._lock:
            if self.authenticate:
                auth_msg = self._build_auth_message()
                if auth_msg:
                    ws.send(json.dumps(auth_msg))
            for topic in sorted(self._subscribed):
                try:
                    ws.send(json.dumps({"op": "subscribe", "args": [topic]}))
                except Exception:
                    logger.exception("Bybit WS resubscribe failed for %s", topic)
        self.on_open(ws)

    def _ws_on_message(self, ws, message):
        try:
            payload = json.loads(message)
            if payload.get("success") is False:
                logger.warning("Bybit WS warning: %s", payload)
                return
            self.on_message(ws, payload)
        except Exception:
            logger.exception("Bybit WS message decode failed: %s", message)

    def _ws_on_error(self, ws, error):
        self.connected = False
        logger.warning("Bybit WS %s error: %s", self.name, error)
        self.on_error(ws, error)

    def _ws_on_close(self, ws, close_status_code, close_msg):
        self.connected = False
        logger.info("Bybit WS %s closed: %s %s", self.name, close_status_code, close_msg)
        self.on_close(ws, close_status_code, close_msg)

    def connect(self):
        self.wsapp = websocket.WebSocketApp(
            self.url,
            on_open=self._ws_on_open,
            on_message=self._ws_on_message,
            on_error=self._ws_on_error,
            on_close=self._ws_on_close,
        )
        try:
            self.wsapp.run_forever(
                ping_interval=self.HEARTBEAT_INTERVAL,
                ping_timeout=10,
                sslopt={"cert_reqs": ssl.CERT_NONE},
                skip_utf8_validation=True,
            )
        except Exception:
            logger.exception("Bybit WS %s connection failed", self.name)
            self.connected = False

    def close_connection(self):
        try:
            if self.wsapp:
                self.wsapp.close()
        except Exception:
            logger.exception("Bybit WS %s close failed", self.name)
        self.connected = False

    def subscribe(self, channel, symbol):
        topic = None
        if channel == "tickers":
            topic = f"tickers.{symbol}"
        elif channel == "orderbook":
            topic = f"orderbook.50.{symbol}"
        elif channel in {"position", "execution", "order"}:
            topic = channel
        else:
            topic = str(channel)

        with self._lock:
            self._subscribed.add(topic)

        if self.wsapp and self.connected:
            try:
                self.wsapp.send(json.dumps({"op": "subscribe", "args": [topic]}))
            except Exception:
                logger.exception("Bybit WS subscribe failed for %s", topic)
        return {"status": "queued", "channel": channel, "symbol": symbol, "topic": topic}

    def unsubscribe(self, channel, symbol):
        topic = None
        if channel == "tickers":
            topic = f"tickers.{symbol}"
        elif channel == "orderbook":
            topic = f"orderbook.50.{symbol}"
        elif channel in {"position", "execution", "order"}:
            topic = channel
        else:
            topic = str(channel)

        with self._lock:
            self._subscribed.discard(topic)

        if self.wsapp and self.connected:
            try:
                self.wsapp.send(json.dumps({"op": "unsubscribe", "args": [topic]}))
            except Exception:
                logger.exception("Bybit WS unsubscribe failed for %s", topic)
        return {"status": "queued", "channel": channel, "symbol": symbol, "topic": topic}

    def forget_subscriptions(self):
        with self._lock:
            self._subscribed.clear()
        return None
