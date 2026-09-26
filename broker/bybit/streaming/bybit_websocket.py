"""Thin wrapper around Bybit's official pybit V5 WebSocket client."""

from pybit.unified_trading import WebSocket

from broker.bybit.api.baseurl import is_testnet


class BybitWebSocket:
    """Expose the pybit stream methods used by the Bybit adapters."""

    def __init__(
        self,
        channel_type: str,
        on_message,
        api_key: str | None = None,
        api_secret: str | None = None,
    ):
        self.channel_type = channel_type
        self.on_message = on_message
        private = channel_type == "private"
        self.client = WebSocket(
            channel_type=channel_type,
            testnet=is_testnet(),
            api_key=api_key if private else None,
            api_secret=api_secret if private else None,
            retries=10,
            restart_on_error=True,
            ping_interval=20,
            ping_timeout=10,
            trace_logging=False,
        )

    def is_connected(self) -> bool:
        return self.client.is_connected()

    def is_running(self) -> bool:
        thread = getattr(self.client, "wst", None)
        return bool(thread and thread.is_alive())

    def subscribe_ticker(self, symbol: str) -> None:
        self.client.ticker_stream(symbol, self.on_message)

    def subscribe_orderbook(self, depth: int, symbol: str) -> None:
        self.client.orderbook_stream(depth, symbol, self.on_message)

    def subscribe_private(self) -> None:
        if self.channel_type != "private":
            raise ValueError("Private Bybit topics require a private WebSocket.")
        self.client.order_stream(self.on_message)
        self.client.execution_stream(self.on_message)
        self.client.position_stream(self.on_message)
        self.client.wallet_stream(self.on_message)

    def unsubscribe_ticker(self, symbol: str) -> None:
        self.client.unsubscribe(f"tickers.{symbol}")

    def unsubscribe_orderbook(self, depth: int, symbol: str) -> None:
        self.client.unsubscribe(f"orderbook.{depth}.{symbol}")

    def close_connection(self) -> None:
        self.client.exit()
