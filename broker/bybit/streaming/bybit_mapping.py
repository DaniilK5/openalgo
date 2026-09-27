"""Bybit V5 category, topic, and depth mappings."""


class BybitMapper:
    CATEGORIES = {"spot", "linear", "inverse", "option"}

    @staticmethod
    def get_segment(exchange: str, category: str | None = None) -> str | None:
        if str(exchange or "").upper() != "CRYPTO":
            return None
        normalized = str(category or "").lower()
        if normalized in BybitMapper.CATEGORIES:
            return normalized
        return "linear" if category is None else None

    @staticmethod
    def get_channel_symbol(br_symbol: str) -> str:
        return str(br_symbol or "").upper()


class BybitModeMapper:
    MODE_CHANNELS = {
        1: ("tickers",),
        2: ("tickers",),
        3: ("tickers", "orderbook"),
    }

    @staticmethod
    def get_channels(mode: int):
        try:
            return BybitModeMapper.MODE_CHANNELS.get(int(mode), ())
        except (TypeError, ValueError):
            return ()

    @staticmethod
    def get_mode_str(mode: int) -> str:
        return {1: "LTP", 2: "QUOTE", 3: "DEPTH"}.get(int(mode), "LTP")


class BybitCapabilityRegistry:
    exchanges = ["CRYPTO"]
    categories = ["spot", "linear", "inverse", "option"]
    subscription_modes = [1, 2, 3]
    # The adapter trims Bybit's 50/100-level book to the requested common depth.
    depth_support = {"CRYPTO": [5, 20, 30, 50]}

    @classmethod
    def get_supported_depth_levels(cls, exchange: str):
        return cls.depth_support.get(str(exchange or "").upper(), [])

    @classmethod
    def is_depth_level_supported(cls, exchange: str, depth_level: int) -> bool:
        try:
            return int(depth_level) in cls.get_supported_depth_levels(exchange)
        except (TypeError, ValueError):
            return False

    @classmethod
    def supports_mode(cls, mode: int) -> bool:
        try:
            return int(mode) in cls.subscription_modes
        except (TypeError, ValueError):
            return False

    @classmethod
    def native_depth(cls, category: str) -> int:
        return 100 if category == "option" else 50
