"""Hyperliquid REST + WebSocket client wrapper."""

from __future__ import annotations

import logging
import time

from hyperliquid.info import Info
from hyperliquid.utils import constants

logger = logging.getLogger(__name__)

# Hyperliquid candleSnapshot interval strings
INTERVAL_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
}

# Milliseconds per interval for pagination
INTERVAL_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

# Max candles per API request
MAX_CANDLES_PER_REQUEST = 5000


class HyperliquidClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.info = Info(base_url or constants.MAINNET_API_URL, skip_ws=True)

    def fetch_candles(
        self,
        symbol: str,
        interval: str,
        start_time: int,
        end_time: int | None = None,
    ) -> list[dict]:
        """Fetch OHLCV candles, paginating if the range exceeds one request.

        Returns list of dicts with keys:
            symbol, timeframe, open_time, open, high, low, close, volume, num_trades
        """
        if end_time is None:
            end_time = int(time.time() * 1000)

        interval_str = INTERVAL_MAP.get(interval, interval)
        interval_ms = INTERVAL_MS[interval]
        all_candles: list[dict] = []
        cursor = start_time

        while cursor < end_time:
            raw = self.info.candles_snapshot(symbol, interval_str, cursor, end_time)
            if not raw:
                break

            for c in raw:
                all_candles.append({
                    "symbol": symbol,
                    "timeframe": interval,
                    "open_time": c["t"],
                    "open": float(c["o"]),
                    "high": float(c["h"]),
                    "low": float(c["l"]),
                    "close": float(c["c"]),
                    "volume": float(c["v"]),
                    "num_trades": int(c["n"]),
                })

            # Advance cursor past the last candle
            last_t = raw[-1]["t"]
            cursor = last_t + interval_ms

            logger.debug(
                "Fetched %d candles for %s %s, cursor now %d",
                len(raw), symbol, interval, cursor,
            )

        return all_candles

    def fetch_funding_history(
        self,
        symbol: str,
        start_time: int,
        end_time: int | None = None,
    ) -> list[dict]:
        """Fetch funding rate history.

        Returns list of dicts: symbol, time, rate, premium
        """
        if end_time is None:
            end_time = int(time.time() * 1000)

        raw = self.info.funding_history(symbol, start_time, end_time)
        return [
            {
                "symbol": symbol,
                "time": entry["time"],
                "rate": float(entry["fundingRate"]),
                "premium": float(entry.get("premium", 0)),
            }
            for entry in raw
        ]

    def get_mid_price(self, symbol: str) -> float:
        """Get current mid price from the L2 order book."""
        l2 = self.info.l2_snapshot(symbol)
        best_bid = float(l2["levels"][0][0]["px"])
        best_ask = float(l2["levels"][1][0]["px"])
        return (best_bid + best_ask) / 2

    def get_asset_meta(self) -> list[dict]:
        """Get metadata for all perpetual assets."""
        meta = self.info.meta_and_asset_ctxs()
        return meta
