import time
from datetime import datetime

import pandas as pd

from broker.bybit.api.baseurl import get_url
from utils.httpx_client import get_httpx_client
from utils.logging import get_logger

logger = get_logger(__name__)


def _safe_float(value, default=0.0):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default=0):
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


class BrokerData:
    """Bybit V5 data adapter for linear crypto contracts."""

    TIMEFRAME_MAP = {
        "1m": "1",
        "3m": "3",
        "5m": "5",
        "15m": "15",
        "30m": "30",
        "1h": "60",
        "2h": "120",
        "4h": "240",
        "6h": "360",
        "1d": "D",
        "D": "D",
        "1w": "W",
        "W": "W",
    }

    def __init__(self, auth_token: str):
        self.auth_token = auth_token
        self.timeframe_map = self.TIMEFRAME_MAP

    def _public_get(self, path: str, params=None):
        params = params or {}
        response = get_httpx_client().get(get_url(path), params=params, timeout=30.0)
        if response.status_code == 429:
            time.sleep(0.5)
            response = get_httpx_client().get(get_url(path), params=params, timeout=30.0)
        if response.status_code != 200:
            raise ValueError(f"Bybit HTTP {response.status_code} for {path}: {response.text[:200]}")
        data = response.json()
        if data.get("retCode") != 0:
            raise ValueError(f"Bybit API error for {path}: {data.get('retMsg')}")
        return data.get("result", {})

    def _ticker_for(self, symbol):
        params = {"category": "linear", "symbol": symbol}
        result = self._public_get("/v5/market/tickers", params=params)
        items = result.get("list") or []
        if not items:
            return {}
        return items[0]

    def get_quotes(self, symbol, exchange):
        if exchange != "CRYPTO":
            raise ValueError(f"Bybit integration only supports CRYPTO exchange, got {exchange}")
        item = self._ticker_for(symbol)
        if not item:
            raise ValueError(f"No ticker data returned for {symbol}")
        return {
            "symbol": symbol,
            "exchange": exchange,
            "ask": _safe_float(item.get("ask1Price"), 0.0),
            "bid": _safe_float(item.get("bid1Price"), 0.0),
            "ltp": _safe_float(item.get("lastPrice"), 0.0),
            "open": _safe_float(item.get("openPrice"), 0.0),
            "high": _safe_float(item.get("highPrice24h"), 0.0),
            "low": _safe_float(item.get("lowPrice24h"), 0.0),
            "prev_close": _safe_float(item.get("prevPrice24h"), 0.0),
            "volume": _safe_float(item.get("volume24h"), 0.0),
            "oi": _safe_float(item.get("openInterest"), 0.0),
            "change": _safe_float(item.get("price24hPcnt"), 0.0),
            "change_percent": _safe_float(item.get("price24hPcnt"), 0.0) * 100.0,
        }

    def get_depth(self, symbol, exchange):
        if exchange != "CRYPTO":
            raise ValueError(f"Bybit integration only supports CRYPTO exchange, got {exchange}")

        tick = self._ticker_for(symbol)
        params = {"category": "linear", "symbol": symbol, "limit": 5}
        result = self._public_get("/v5/market/orderbook", params=params)
        bids = result.get("b", []) or []
        asks = result.get("a", []) or []

        def _normalize_levels(rows):
            levels = [{"price": _safe_float(row[0]), "quantity": _safe_float(row[1])} for row in rows[:5]]
            while len(levels) < 5:
                levels.append({"price": 0.0, "quantity": 0.0})
            return levels

        bid_levels = _normalize_levels(bids)
        ask_levels = _normalize_levels(asks)
        total_buy_qty = sum(level["quantity"] for level in bid_levels)
        total_sell_qty = sum(level["quantity"] for level in ask_levels)

        return {
            "symbol": symbol,
            "exchange": exchange,
            "bids": bid_levels,
            "asks": ask_levels,
            "ltp": _safe_float(tick.get("lastPrice"), 0.0),
            "ltq": 0.0,
            "open": _safe_float(tick.get("openPrice"), 0.0),
            "prev_close": _safe_float(tick.get("prevPrice24h"), 0.0),
            "high": _safe_float(tick.get("highPrice24h"), 0.0),
            "low": _safe_float(tick.get("lowPrice24h"), 0.0),
            "oi": _safe_float(tick.get("openInterest"), 0.0),
            "volume": _safe_float(tick.get("volume24h"), 0.0),
            "totalbuyqty": total_buy_qty,
            "totalsellqty": total_sell_qty,
        }

    def get_multiquotes(self, symbols):
        response = []
        if not symbols:
            return response

        for symbol in symbols:
            symbol_name = symbol.get("symbol") if isinstance(symbol, dict) else symbol
            if not symbol_name:
                continue
            try:
                response.append(self.get_quotes(symbol_name, "CRYPTO"))
            except Exception as exc:
                logger.warning("Bybit multiquote failed for %s: %s", symbol_name, exc)
        return response

    def get_history(self, symbol, exchange, interval, start, end):
        if exchange != "CRYPTO":
            raise ValueError(f"Bybit integration only supports CRYPTO exchange, got {exchange}")

        interval_key = self.TIMEFRAME_MAP.get(interval, "1")
        start_ms = int(start.timestamp() * 1000) if hasattr(start, "timestamp") else int(start)
        end_ms = int(end.timestamp() * 1000) if hasattr(end, "timestamp") else int(end)
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": interval_key,
            "start": start_ms,
            "end": end_ms,
            "limit": 200,
        }
        result = self._public_get("/v5/market/kline", params=params)
        rows = []
        for candle in result.get("list", []):
            rows.append({
                "timestamp": int(int(candle[0]) / 1000),
                "open": _safe_float(candle[1]),
                "high": _safe_float(candle[2]),
                "low": _safe_float(candle[3]),
                "close": _safe_float(candle[4]),
                "volume": _safe_float(candle[5]),
                "oi": _safe_float(candle[6], 0.0),
            })
        return pd.DataFrame(rows)
