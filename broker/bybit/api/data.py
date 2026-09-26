from datetime import date, datetime, timezone
from datetime import time as day_time
from zoneinfo import ZoneInfo

import pandas as pd

from broker.bybit.api.rest_client import request
from database.token_db import get_br_symbol, get_symbol_info

KZ_TZ = ZoneInfo("Asia/Almaty")


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


def _timestamp_ms(value, end_of_day=False):
    if isinstance(value, datetime):
        timestamp = value
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=KZ_TZ)
        else:
            timestamp = timestamp.astimezone(timezone.utc)
        return int(timestamp.timestamp() * 1000)

    if isinstance(value, date):
        boundary = day_time.max if end_of_day else day_time.min
        timestamp = datetime.combine(value, boundary, tzinfo=KZ_TZ)
        return int(timestamp.timestamp() * 1000)

    if isinstance(value, str):
        try:
            date_value = date.fromisoformat(value)
        except ValueError:
            return int(value)
        return _timestamp_ms(date_value, end_of_day=end_of_day)

    return int(value)


class BrokerData:
    """Bybit V5 market-data adapter."""

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

    def _category(self, category: str | None, symbol: str | None = None) -> str:
        if category is not None and not isinstance(category, str):
            raise ValueError(f"Unsupported Bybit category: {category}")
        symbol_info = get_symbol_info(symbol, "CRYPTO") if symbol else None
        master_category = getattr(symbol_info, "category", None) if symbol_info else None
        normalized = (category or master_category or "linear").lower()
        if normalized not in {"spot", "linear", "inverse", "option"}:
            raise ValueError(f"Unsupported Bybit category: {category}")
        if category is not None and master_category and normalized != master_category.lower():
            raise ValueError(
                f"Bybit symbol {symbol} belongs to {master_category}, not {normalized}"
            )
        return normalized

    def get_timeframe_map(self, category: str | None = None):
        category = self._category(category)
        if category == "option":
            raise ValueError("Bybit options do not provide historical klines")
        return self.TIMEFRAME_MAP

    def _public_get(self, path: str, params=None):
        data = request(path, params=params)
        return data.get("result", {})

    def _ticker_for(self, symbol, category):
        params = {"category": category, "symbol": symbol}
        result = self._public_get("/v5/market/tickers", params=params)
        items = result.get("list") or []
        if not items:
            return {}
        return items[0]

    def _quote_from_ticker(self, symbol, exchange, category, item):
        change = _safe_float(
            item.get("price24hPcnt", item.get("change24h")), 0.0
        )
        return {
            "symbol": symbol,
            "exchange": exchange,
            "category": category,
            "ask": _safe_float(item.get("ask1Price"), 0.0),
            "bid": _safe_float(item.get("bid1Price"), 0.0),
            "ltp": _safe_float(item.get("lastPrice"), 0.0),
            "open": _safe_float(item.get("openPrice"), 0.0),
            "high": _safe_float(item.get("highPrice24h"), 0.0),
            "low": _safe_float(item.get("lowPrice24h"), 0.0),
            "prev_close": _safe_float(item.get("prevPrice24h"), 0.0),
            "volume": _safe_float(item.get("volume24h"), 0.0),
            "oi": _safe_float(item.get("openInterest"), 0.0),
            "change": change,
            "change_percent": change * 100.0,
        }

    @staticmethod
    def _broker_symbol(symbol, exchange):
        broker_symbol = get_br_symbol(symbol, exchange)
        if not broker_symbol:
            raise ValueError(f"Bybit symbol mapping not found for {symbol}:{exchange}")
        return broker_symbol

    def get_quotes(self, symbol, exchange, category=None):
        if exchange != "CRYPTO":
            raise ValueError(f"Bybit integration only supports CRYPTO exchange, got {exchange}")
        category = self._category(category, symbol)
        item = self._ticker_for(self._broker_symbol(symbol, exchange), category)
        if not item:
            raise ValueError(f"No ticker data returned for {symbol}")
        return self._quote_from_ticker(symbol, exchange, category, item)

    def get_depth(self, symbol, exchange, category=None):
        if exchange != "CRYPTO":
            raise ValueError(f"Bybit integration only supports CRYPTO exchange, got {exchange}")

        category = self._category(category, symbol)
        broker_symbol = self._broker_symbol(symbol, exchange)
        tick = self._ticker_for(broker_symbol, category)
        orderbook_limit = 25 if category == "option" else 50
        params = {
            "category": category,
            "symbol": broker_symbol,
            "limit": orderbook_limit,
        }
        result = self._public_get("/v5/market/orderbook", params=params)
        bids = result.get("b", []) or []
        asks = result.get("a", []) or []

        def _normalize_levels(rows):
            levels = [
                {"price": _safe_float(row[0]), "quantity": _safe_float(row[1])} for row in rows[:5]
            ]
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
            "category": category,
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

        grouped_symbols = {}
        results_by_index = {}
        for index, symbol in enumerate(symbols):
            symbol_name = symbol.get("symbol") if isinstance(symbol, dict) else symbol
            if not symbol_name:
                continue
            requested_category = symbol.get("category") if isinstance(symbol, dict) else None
            try:
                category = self._category(requested_category, symbol_name)
                broker_symbol = self._broker_symbol(symbol_name, "CRYPTO")
                grouped_symbols.setdefault(category, []).append((index, symbol_name, broker_symbol))
            except ValueError as exc:
                results_by_index[index] = {
                    "symbol": symbol_name,
                    "exchange": "CRYPTO",
                    "category": requested_category or "linear",
                    "error": str(exc),
                }

        for category, category_symbols in grouped_symbols.items():
            if category == "option":
                for index, symbol_name, broker_symbol in category_symbols:
                    try:
                        item = self._ticker_for(broker_symbol, category)
                        if not item:
                            raise ValueError(f"No ticker data returned for {symbol_name}")
                        quote = self._quote_from_ticker(symbol_name, "CRYPTO", category, item)
                        results_by_index[index] = {
                            "symbol": symbol_name,
                            "exchange": "CRYPTO",
                            "category": category,
                            "data": quote,
                        }
                    except ValueError as exc:
                        results_by_index[index] = {
                            "symbol": symbol_name,
                            "exchange": "CRYPTO",
                            "category": category,
                            "error": str(exc),
                        }
                continue

            try:
                result = self._public_get("/v5/market/tickers", params={"category": category})
                items = result.get("list")
                if not isinstance(items, list):
                    raise ValueError("Bybit ticker response is malformed")
                ticker_by_symbol = {
                    item.get("symbol"): item
                    for item in items
                    if isinstance(item, dict) and item.get("symbol")
                }
            except ValueError as exc:
                for index, symbol_name, _ in category_symbols:
                    results_by_index[index] = {
                        "symbol": symbol_name,
                        "exchange": "CRYPTO",
                        "category": category,
                        "error": str(exc),
                    }
                continue

            for index, symbol_name, broker_symbol in category_symbols:
                item = ticker_by_symbol.get(broker_symbol)
                if not item:
                    results_by_index[index] = {
                        "symbol": symbol_name,
                        "exchange": "CRYPTO",
                        "category": category,
                        "error": f"No ticker data returned for {symbol_name}",
                    }
                    continue
                quote = self._quote_from_ticker(symbol_name, "CRYPTO", category, item)
                results_by_index[index] = {
                    "symbol": symbol_name,
                    "exchange": "CRYPTO",
                    "category": category,
                    "data": quote,
                }

        for index in range(len(symbols)):
            if index in results_by_index:
                response.append(results_by_index[index])
        return response

    def get_history(self, symbol, exchange, interval, start, end, category=None):
        if exchange != "CRYPTO":
            raise ValueError(f"Bybit integration only supports CRYPTO exchange, got {exchange}")

        category = self._category(category, symbol)
        self.get_timeframe_map(category)
        broker_symbol = self._broker_symbol(symbol, exchange)
        interval_key = self.TIMEFRAME_MAP.get(interval)
        if interval_key is None:
            raise ValueError(f"Unsupported Bybit interval: {interval}")
        start_ms = _timestamp_ms(start)
        end_ms = _timestamp_ms(end, end_of_day=True)
        if start_ms > end_ms:
            raise ValueError("Bybit history start date must not be after end date")
        page_end_ms = end_ms
        rows = []
        while page_end_ms >= start_ms:
            result = self._public_get(
                "/v5/market/kline",
                params={
                    "category": category,
                    "symbol": broker_symbol,
                    "interval": interval_key,
                    "start": start_ms,
                    "end": page_end_ms,
                    "limit": 1000,
                },
            )
            candles = result.get("list", [])
            if not candles:
                break

            page_timestamps = [int(candle[0]) for candle in candles]
            oldest_timestamp = min(page_timestamps)
            for candle, timestamp in zip(candles, page_timestamps):
                if start_ms <= timestamp <= end_ms:
                    rows.append(
                        {
                            "timestamp": int(timestamp / 1000),
                            "open": _safe_float(candle[1]),
                            "high": _safe_float(candle[2]),
                            "low": _safe_float(candle[3]),
                            "close": _safe_float(candle[4]),
                            "volume": _safe_float(candle[5]),
                            "oi": 0.0,
                        }
                    )

            if oldest_timestamp <= start_ms:
                break
            next_end_ms = oldest_timestamp - 1
            if next_end_ms >= page_end_ms:
                raise ValueError("Bybit history pagination did not advance")
            page_end_ms = next_end_ms

        candles = pd.DataFrame(rows)
        if not candles.empty:
            candles = candles.drop_duplicates(subset=["timestamp"])
            candles = candles.sort_values("timestamp").reset_index(drop=True)
        return candles
