class BybitMapper:
    EXCHANGE_SEGMENTS = {"CRYPTO": "linear"}

    @staticmethod
    def get_segment(exchange: str) -> str:
        return BybitMapper.EXCHANGE_SEGMENTS.get(str(exchange or "").upper(), "linear")

    @staticmethod
    def get_channel_symbol(br_symbol: str) -> str:
        return str(br_symbol or "").upper()


class BybitModeMapper:
    MODE_CHANNELS = {
        1: ("tickers",),
        2: ("tickers",),
        3: ("orderbook", "tickers"),
    }

    @staticmethod
    def get_channels(mode: int):
        return BybitModeMapper.MODE_CHANNELS.get(mode, ("tickers",))

    @staticmethod
    def get_mode_str(mode: int) -> str:
        return {1: "LTP", 2: "QUOTE", 3: "DEPTH"}.get(mode, "LTP")


class BybitCapabilityRegistry:
    exchanges = ["CRYPTO"]
    subscription_modes = [1, 2, 3]
    depth_support = {"CRYPTO": [1, 5, 50]}

    @classmethod
    def get_supported_depth_levels(cls, exchange: str):
        return cls.depth_support.get(str(exchange or "").upper(), [1])

    @classmethod
    def is_depth_level_supported(cls, exchange: str, depth_level: int) -> bool:
        return int(depth_level) in cls.get_supported_depth_levels(exchange)

    @classmethod
    def supports_mode(cls, mode: int) -> bool:
        return int(mode) in cls.subscription_modes
