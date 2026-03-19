"""Risk manager — enforces all risk limits before allowing trades."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from perp_bot.config import BotConfig
from perp_bot.data.db import Database
from perp_bot.signals.prediction import PredictionRegime

logger = logging.getLogger(__name__)


@dataclass
class RiskCheck:
    allowed: bool
    reason: str


class RiskManager:
    def __init__(self, config: BotConfig, db: Database) -> None:
        self.config = config
        self.risk = config.risk
        self.trading = config.trading
        self.db = db
        self._last_stop_loss_time: int | None = self._load_cooldown_state()

    def check_entry(self) -> RiskCheck:
        """Run all pre-entry risk checks. Returns whether a new trade is allowed."""
        # Max positions
        open_trades = self.db.get_open_trades()
        if len(open_trades) >= self.risk.max_positions:
            return RiskCheck(False, f"max_positions ({self.risk.max_positions}) reached")

        # Daily loss limit
        day_start = _day_start_ms()
        daily_pnl = self.db.get_daily_pnl(day_start)
        max_daily_loss = self.trading.capital_usd * self.risk.daily_loss_limit_pct
        if daily_pnl <= -max_daily_loss:
            return RiskCheck(False, f"daily_loss_limit hit ({daily_pnl:.2f})")

        # Cooldown after stop-loss
        if self._last_stop_loss_time is not None:
            elapsed = int(time.time() * 1000) - self._last_stop_loss_time
            if elapsed < self.risk.cooldown_seconds * 1000:
                remaining = (self.risk.cooldown_seconds * 1000 - elapsed) / 1000
                return RiskCheck(False, f"cooldown active ({remaining:.0f}s remaining)")

        return RiskCheck(True, "all_checks_passed")

    def compute_position_size(
        self, prediction_regime: PredictionRegime = PredictionRegime.NORMAL,
    ) -> float:
        """Compute position size in USD respecting margin usage limit.

        Reduces size under HIGH_RISK regime, blocks entirely under CRISIS.
        """
        if prediction_regime == PredictionRegime.CRISIS:
            return 0.0
        max_margin = self.trading.capital_usd * self.trading.margin_usage_limit
        size = max_margin * self.trading.leverage
        if prediction_regime == PredictionRegime.HIGH_RISK and self.config.prediction:
            size *= self.config.prediction.position_size_reduction
        return size

    def check_stop_loss(self, entry_price: float, current_price: float, side: str) -> bool:
        """Check if the capital-based stop-loss threshold is breached.

        Returns True if the position should be stopped out.
        """
        position_size = self.compute_position_size()
        if side == "long":
            pnl = (current_price - entry_price) / entry_price * position_size
        else:
            pnl = (entry_price - current_price) / entry_price * position_size

        max_loss = self.trading.capital_usd * self.risk.max_loss_per_trade_pct
        return pnl <= -max_loss

    def check_position_timeout(self, entry_time: int) -> bool:
        """Check if position has exceeded the maximum holding time."""
        elapsed_hours = (int(time.time() * 1000) - entry_time) / 3_600_000
        return elapsed_hours >= self.risk.position_timeout_hours

    def record_stop_loss(self) -> None:
        """Record that a stop-loss was hit, starting the cooldown timer."""
        self._last_stop_loss_time = int(time.time() * 1000)
        self.db.set_state(
            "last_stop_loss_time_ms", str(self._last_stop_loss_time)
        )
        logger.warning("Stop-loss triggered — cooldown started for %ds", self.risk.cooldown_seconds)

    def _load_cooldown_state(self) -> int | None:
        """Load persisted cooldown timestamp from DB."""
        val = self.db.get_state("last_stop_loss_time_ms")
        if val is not None:
            ts = int(val)
            elapsed = int(time.time() * 1000) - ts
            if elapsed < self.risk.cooldown_seconds * 1000:
                logger.info(
                    "Restored cooldown state — %ds remaining",
                    (self.risk.cooldown_seconds * 1000 - elapsed) / 1000,
                )
                return ts
        return None


def _day_start_ms() -> int:
    """Return epoch ms for the start of the current UTC day."""
    now = int(time.time())
    return (now - now % 86400) * 1000
