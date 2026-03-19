"""WebSocket client — real-time price, candle, and order update streaming."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from hyperliquid.info import Info
from hyperliquid.utils import constants

logger = logging.getLogger(__name__)


class WsClient:
    """Wraps Hyperliquid WebSocket subscriptions with thread-safe price cache.

    Usage:
        ws = WsClient()
        ws.subscribe_mid_prices(["ETH", "BTC"])
        ws.subscribe_candles("ETH", "15m", on_candle_callback)

        # Later, from any thread:
        price = ws.get_mid_price("ETH")  # returns cached value instantly
    """

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = base_url or constants.MAINNET_API_URL
        self.info = Info(self._base_url, skip_ws=False)
        self._mid_prices: dict[str, float] = {}
        self._lock = threading.Lock()
        self._sub_ids: list[tuple[dict, int]] = []
        self._last_mid_update: float = 0.0
        self._stale_threshold_seconds: float = 30.0

    # ── Mid price streaming ──────────────────────────────

    def subscribe_mid_prices(self, symbols: list[str] | None = None) -> None:
        """Subscribe to allMids channel for real-time mid prices.

        If symbols is provided, only those are cached (saves memory).
        Otherwise all mids are cached.
        """
        self._watched_symbols = set(symbols) if symbols else None

        def _on_all_mids(msg: dict) -> None:
            mids = msg.get("data", {}).get("mids", {})
            with self._lock:
                if self._watched_symbols:
                    for sym in self._watched_symbols:
                        if sym in mids:
                            self._mid_prices[sym] = float(mids[sym])
                else:
                    for sym, px in mids.items():
                        self._mid_prices[sym] = float(px)
                self._last_mid_update = time.time()

        sub_id = self.info.subscribe({"type": "allMids"}, _on_all_mids)
        self._sub_ids.append(({"type": "allMids"}, sub_id))
        logger.info("Subscribed to allMids WebSocket feed")

    def get_mid_price(self, symbol: str) -> float | None:
        """Get the latest cached mid price. Returns None if not yet received."""
        with self._lock:
            return self._mid_prices.get(symbol)

    # ── Candle streaming ─────────────────────────────────

    def subscribe_candles(
        self,
        symbol: str,
        interval: str,
        callback: Callable[[dict], None],
    ) -> int:
        """Subscribe to candle updates for a symbol/interval.

        The callback receives a normalised dict:
            {symbol, timeframe, open_time, open, high, low, close, volume, num_trades}
        """
        def _on_candle(msg: dict) -> None:
            d = msg.get("data", {})
            try:
                normalised = {
                    "symbol": d["s"],
                    "timeframe": d["i"],
                    "open_time": d["t"],
                    "open": float(d["o"]),
                    "high": float(d["h"]),
                    "low": float(d["l"]),
                    "close": float(d["c"]),
                    "volume": float(d["v"]),
                    "num_trades": int(d["n"]),
                }
                callback(normalised)
            except (KeyError, ValueError):
                logger.warning("Malformed candle message: %s", d)

        sub = {"type": "candle", "coin": symbol, "interval": interval}
        sub_id = self.info.subscribe(sub, _on_candle)
        self._sub_ids.append((sub, sub_id))
        logger.info("Subscribed to %s %s candle feed", symbol, interval)
        return sub_id

    # ── L2 book streaming ────────────────────────────────

    def subscribe_bbo(
        self,
        symbol: str,
        callback: Callable[[float, float], None],
    ) -> int:
        """Subscribe to best bid/offer for a symbol.

        Callback receives (best_bid, best_ask).
        """
        def _on_bbo(msg: dict) -> None:
            data = msg.get("data", {})
            bbo = data.get("bbo", [None, None])
            try:
                bid = float(bbo[0]["px"]) if bbo[0] else None
                ask = float(bbo[1]["px"]) if bbo[1] else None
                if bid is not None and ask is not None:
                    callback(bid, ask)
            except (KeyError, TypeError, IndexError):
                pass

        sub = {"type": "bbo", "coin": symbol}
        sub_id = self.info.subscribe(sub, _on_bbo)
        self._sub_ids.append((sub, sub_id))
        logger.info("Subscribed to %s BBO feed", symbol)
        return sub_id

    # ── Order update streaming ───────────────────────────

    def subscribe_order_updates(
        self,
        user_address: str,
        callback: Callable[[dict], None],
    ) -> int:
        """Subscribe to order status updates for a user.

        Only one subscriber per Info instance is allowed.
        """
        sub = {"type": "orderUpdates", "user": user_address}
        sub_id = self.info.subscribe(sub, callback)
        self._sub_ids.append((sub, sub_id))
        logger.info("Subscribed to order updates for %s", user_address[:10])
        return sub_id

    # ── User fill streaming ──────────────────────────────

    def subscribe_user_fills(
        self,
        user_address: str,
        callback: Callable[[list[dict]], None],
    ) -> int:
        """Subscribe to user fill events."""
        def _on_fills(msg: dict) -> None:
            data = msg.get("data", {})
            fills = data.get("fills", [])
            if fills:
                callback(fills)

        sub = {"type": "userFills", "user": user_address}
        sub_id = self.info.subscribe(sub, _on_fills)
        self._sub_ids.append((sub, sub_id))
        logger.info("Subscribed to user fills for %s", user_address[:10])
        return sub_id

    # ── Health check ──────────────────────────────────────

    def is_healthy(self) -> bool:
        """Check if the WebSocket is receiving data.

        Returns False if no mid price update has been received within
        the stale threshold, indicating the connection may be dead.
        """
        if self._last_mid_update == 0.0:
            return True  # No subscriptions yet or just started
        elapsed = time.time() - self._last_mid_update
        return elapsed < self._stale_threshold_seconds

    def reconnect(self) -> None:
        """Tear down and recreate the WebSocket connection, re-subscribing."""
        logger.warning("Reconnecting WebSocket — stale data detected")
        old_subs = list(self._sub_ids)
        self._sub_ids.clear()

        for sub, sub_id in old_subs:
            try:
                self.info.unsubscribe(sub, sub_id)
            except Exception:
                pass

        # Recreate Info with fresh WebSocket
        self.info = Info(self._base_url, skip_ws=False)
        with self._lock:
            self._last_mid_update = 0.0

        # Re-subscribe mid prices if they were active
        watched = getattr(self, "_watched_symbols", None)
        if watched is not None or any(
            s.get("type") == "allMids" for s, _ in old_subs
        ):
            symbols = list(watched) if watched else None
            self.subscribe_mid_prices(symbols)

        logger.info("WebSocket reconnected and re-subscribed")

    # ── Lifecycle ────────────────────────────────────────

    def close(self) -> None:
        """Unsubscribe all and close the WebSocket connection."""
        for sub, sub_id in self._sub_ids:
            try:
                self.info.unsubscribe(sub, sub_id)
            except Exception:
                pass
        self._sub_ids.clear()
        logger.info("WebSocket client closed")
