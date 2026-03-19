"""Prediction market API clients — Polymarket + Kalshi."""

from __future__ import annotations

import json
import logging
import time
import urllib.request

logger = logging.getLogger(__name__)

_POLYMARKET_BASE = "https://gamma-api.polymarket.com"
_KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"


class PolymarketClient:
    """Fetch market probabilities from Polymarket's public API."""

    def fetch_market(self, condition_id: str, slug: str, category: str) -> dict | None:
        """Fetch a single market by condition ID.

        Returns normalised snapshot dict, or None on failure.
        """
        url = f"{_POLYMARKET_BASE}/markets/{condition_id}"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())

            # Polymarket returns outcomePrices as JSON string "[\"0.65\",\"0.35\"]"
            # First price is YES probability
            prices = json.loads(data.get("outcomePrices", "[\"0.5\",\"0.5\"]"))
            probability = float(prices[0]) if prices else 0.5
            volume = float(data.get("volume24hr", 0) or 0)

            return {
                "source": "polymarket",
                "market_id": condition_id,
                "market_slug": slug,
                "category": category,
                "timestamp": int(time.time() * 1000),
                "probability": probability,
                "volume_24h": volume,
            }
        except Exception:
            logger.warning("Polymarket API error for %s", condition_id, exc_info=True)
            return None

    def fetch_markets(
        self, markets: list[dict],
    ) -> list[dict]:
        """Fetch multiple markets. Each dict needs condition_id, slug, category.

        Returns list of successful snapshots (failures are skipped with a warning).
        """
        results = []
        for m in markets:
            snapshot = self.fetch_market(m["market_id"], m["slug"], m["category"])
            if snapshot is not None:
                results.append(snapshot)
        return results


class KalshiClient:
    """Fetch market probabilities from Kalshi's public API."""

    def fetch_market(self, ticker: str, slug: str, category: str) -> dict | None:
        """Fetch a single market by ticker.

        Returns normalised snapshot dict, or None on failure.
        """
        url = f"{_KALSHI_BASE}/markets/{ticker}"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())

            market = data.get("market", data)
            # Kalshi prices are in cents (1-99), convert to 0-1 probability
            yes_price = market.get("yes_ask", market.get("last_price", 50))
            probability = float(yes_price) / 100.0 if yes_price > 1 else float(yes_price)
            volume = float(market.get("volume_24h", market.get("volume", 0)) or 0)

            return {
                "source": "kalshi",
                "market_id": ticker,
                "market_slug": slug,
                "category": category,
                "timestamp": int(time.time() * 1000),
                "probability": probability,
                "volume_24h": volume,
            }
        except Exception:
            logger.warning("Kalshi API error for %s", ticker, exc_info=True)
            return None

    def fetch_markets(self, markets: list[dict]) -> list[dict]:
        """Fetch multiple markets. Returns list of successful snapshots."""
        results = []
        for m in markets:
            snapshot = self.fetch_market(m["market_id"], m["slug"], m["category"])
            if snapshot is not None:
                results.append(snapshot)
        return results
