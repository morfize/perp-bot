"""Orchestrates data fetching and storage — initial backfill + incremental updates."""

from __future__ import annotations

import logging
import time

from perp_bot.config import BotConfig, PredictionConfig
from perp_bot.data.client import HyperliquidClient
from perp_bot.data.db import Database

logger = logging.getLogger(__name__)


class DataIngestor:
    def __init__(self, config: BotConfig, db: Database, client: HyperliquidClient) -> None:
        self.config = config
        self.db = db
        self.client = client

    def backfill_candles(self, symbol: str) -> dict[str, int]:
        """Backfill historical candles for all configured timeframes.

        Returns dict of timeframe -> number of candles inserted.
        """
        history_ms = self.config.data.history_days * 86_400_000
        default_start = int(time.time() * 1000) - history_ms
        results = {}

        for tf in self.config.data.timeframes:
            # Resume from where we left off, or start from history_days ago
            latest = self.db.get_latest_candle_time(symbol, tf)
            start = (latest + 1) if latest else default_start

            logger.info("Backfilling %s %s from %d", symbol, tf, start)
            candles = self.client.fetch_candles(symbol, tf, start)
            inserted = self.db.insert_candles(candles)
            results[tf] = inserted
            logger.info("Inserted %d candles for %s %s", inserted, symbol, tf)

        return results

    def backfill_funding(self, symbol: str) -> int:
        """Backfill funding rate history."""
        history_ms = self.config.data.history_days * 86_400_000
        start = int(time.time() * 1000) - history_ms

        logger.info("Backfilling funding rates for %s", symbol)
        rates = self.client.fetch_funding_history(symbol, start)
        inserted = self.db.insert_funding_rates(rates)
        logger.info("Inserted %d funding rates for %s", inserted, symbol)
        return inserted

    def update_candles(self, symbol: str) -> dict[str, int]:
        """Incremental update — fetch only new candles since last stored."""
        results = {}
        for tf in self.config.data.timeframes:
            latest = self.db.get_latest_candle_time(symbol, tf)
            if latest is None:
                logger.warning("No existing data for %s %s, run backfill first", symbol, tf)
                continue

            candles = self.client.fetch_candles(symbol, tf, latest + 1)
            inserted = self.db.insert_candles(candles)
            results[tf] = inserted
            if inserted:
                logger.info("Updated %d candles for %s %s", inserted, symbol, tf)
        return results

    def update_predictions(self, clients: dict, pred_config: PredictionConfig) -> int:
        """Fetch latest prediction market data and insert into DB.

        Groups configured markets by source and fetches in batch per source.
        Returns total number of snapshots inserted.
        """
        markets_by_source: dict[str, list[dict]] = {}
        for m in pred_config.markets:
            markets_by_source.setdefault(m.source, []).append({
                "market_id": m.market_id,
                "slug": m.slug,
                "category": m.category,
            })

        total = 0
        for source, market_list in markets_by_source.items():
            client = clients.get(source)
            if client is None:
                logger.warning("No client for source %s, skipping", source)
                continue
            snapshots = client.fetch_markets(market_list)
            inserted = self.db.insert_prediction_snapshots(snapshots)
            total += inserted
            if snapshots:
                logger.info(
                    "Fetched %d snapshots from %s, inserted %d",
                    len(snapshots), source, inserted,
                )
        return total

    def run_full_backfill(self) -> None:
        """Backfill all configured symbols."""
        for symbol in self.config.trading.symbols:
            self.backfill_candles(symbol)
            self.backfill_funding(symbol)
