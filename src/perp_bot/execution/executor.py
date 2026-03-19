"""Order execution — paper and live modes."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

from perp_bot.data.db import Database

logger = logging.getLogger(__name__)


class Executor(ABC):
    """Abstract executor interface."""

    @abstractmethod
    def open_position(self, symbol: str, side: str, size_usd: float, price: float) -> int | None:
        """Place an entry order. Returns trade_id from DB, or None on failure."""

    @abstractmethod
    def close_position(
        self, trade_id: int, symbol: str,
        price: float, pnl: float, reason: str,
    ) -> bool:
        """Close an existing position. Returns True on success."""


class PaperExecutor(Executor):
    """Logs trades to DB without placing real orders — for paper trading."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def open_position(self, symbol: str, side: str, size_usd: float, price: float) -> int | None:
        now = int(time.time() * 1000)
        trade_id = self.db.insert_trade({
            "symbol": symbol,
            "side": side,
            "entry_time": now,
            "entry_price": price,
            "size_usd": size_usd,
            "is_paper": 1,
        })
        logger.info(
            "[PAPER] Opened %s %s @ %.2f, size $%.2f (trade #%d)",
            side, symbol, price, size_usd, trade_id,
        )
        return trade_id

    def close_position(
        self, trade_id: int, symbol: str,
        price: float, pnl: float, reason: str,
    ) -> bool:
        now = int(time.time() * 1000)
        self.db.close_trade(trade_id, now, price, pnl, reason)
        logger.info(
            "[PAPER] Closed trade #%d %s @ %.2f, pnl=%.2f, reason=%s",
            trade_id, symbol, price, pnl, reason,
        )
        return True
