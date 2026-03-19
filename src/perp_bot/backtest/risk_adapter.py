"""Backtest-compatible risk manager — uses explicit timestamps instead of time.time()."""

from __future__ import annotations

from perp_bot.config import BotConfig
from perp_bot.signals.prediction import PredictionRegime

_DAY_MS = 86_400_000


class BacktestRiskManager:
    """Reimplements core risk checks with injectable time for deterministic backtesting."""

    def __init__(self, config: BotConfig) -> None:
        self.risk = config.risk
        self.trading = config.trading
        self.prediction = config.prediction
        self._last_stop_loss_time_ms: int | None = None
        self._daily_pnl: float = 0.0
        self._current_day: int = 0  # day boundary tracker

    def check_entry(self, current_time_ms: int) -> bool:
        """Check whether a new entry is allowed at the given time."""
        # Daily loss limit (reset on day boundary)
        day = current_time_ms // _DAY_MS
        if day != self._current_day:
            self._daily_pnl = 0.0
            self._current_day = day

        max_daily_loss = self.trading.capital_usd * self.risk.daily_loss_limit_pct
        if self._daily_pnl <= -max_daily_loss:
            return False

        # Cooldown after stop-loss
        if self._last_stop_loss_time_ms is not None:
            elapsed = current_time_ms - self._last_stop_loss_time_ms
            if elapsed < self.risk.cooldown_seconds * 1000:
                return False

        return True

    def check_stop_loss(
        self, entry_price: float, current_price: float, side: str, size_usd: float
    ) -> bool:
        """Check if the capital-based stop-loss threshold is breached."""
        if side == "long":
            pnl = (current_price - entry_price) / entry_price * size_usd
        else:
            pnl = (entry_price - current_price) / entry_price * size_usd
        max_loss = self.trading.capital_usd * self.risk.max_loss_per_trade_pct
        return pnl <= -max_loss

    def check_position_timeout(self, entry_time_ms: int, current_time_ms: int) -> bool:
        """Check if position has exceeded maximum holding time."""
        elapsed_hours = (current_time_ms - entry_time_ms) / 3_600_000
        return elapsed_hours >= self.risk.position_timeout_hours

    def compute_position_size(
        self, prediction_regime: PredictionRegime = PredictionRegime.NORMAL
    ) -> float:
        """Compute position size in USD, regime-adjusted."""
        if prediction_regime == PredictionRegime.CRISIS:
            return 0.0
        max_margin = self.trading.capital_usd * self.trading.margin_usage_limit
        size = max_margin * self.trading.leverage
        if prediction_regime == PredictionRegime.HIGH_RISK and self.prediction:
            size *= self.prediction.position_size_reduction
        return size

    def record_trade_close(
        self, pnl: float, exit_time_ms: int, is_stop_loss: bool = False
    ) -> None:
        """Update accumulators after a trade closes."""
        # Ensure day boundary is current
        day = exit_time_ms // _DAY_MS
        if day != self._current_day:
            self._daily_pnl = 0.0
            self._current_day = day
        self._daily_pnl += pnl

        if is_stop_loss:
            self._last_stop_loss_time_ms = exit_time_ms
