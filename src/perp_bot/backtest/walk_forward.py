"""Walk-forward analysis — train/test window orchestrator for overfitting detection."""

from __future__ import annotations

import logging

from perp_bot.backtest.config import BacktestConfig
from perp_bot.backtest.engine import BacktestEngine
from perp_bot.backtest.results import WalkForwardResult, WalkForwardWindow
from perp_bot.config import BotConfig
from perp_bot.data.db import Database
from perp_bot.signals.prediction import PredictionRegime

logger = logging.getLogger(__name__)

_DAY_MS = 86_400_000


class WalkForwardRunner:
    """Runs walk-forward analysis over sliding train/test windows."""

    def __init__(self, bot_config: BotConfig, bt_config: BacktestConfig) -> None:
        self.bot_config = bot_config
        self.bt_config = bt_config
        self.engine = BacktestEngine(bot_config, bt_config)

    def run(
        self,
        db: Database,
        symbol: str,
        data_start_ms: int,
        data_end_ms: int,
        prediction_regime: PredictionRegime = PredictionRegime.NORMAL,
    ) -> WalkForwardResult:
        """Execute walk-forward analysis over the data range."""
        bt = self.bt_config
        train_ms = bt.train_days * _DAY_MS
        test_ms = bt.test_days * _DAY_MS
        step_ms = bt.step_days * _DAY_MS

        windows: list[WalkForwardWindow] = []
        window_start = data_start_ms

        while window_start + train_ms + test_ms <= data_end_ms:
            train_start = window_start
            train_end = train_start + train_ms
            test_start = train_end
            test_end = test_start + test_ms

            logger.info(
                "Walk-forward window %d: train=%d→%d, test=%d→%d",
                len(windows) + 1, train_start, train_end, test_start, test_end,
            )

            train_result = self.engine.run(
                db, symbol, train_start, train_end, prediction_regime
            )
            test_result = self.engine.run(
                db, symbol, test_start, test_end, prediction_regime
            )

            windows.append(WalkForwardWindow(
                train_start_ms=train_start,
                train_end_ms=train_end,
                test_start_ms=test_start,
                test_end_ms=test_end,
                train_result=train_result,
                test_result=test_result,
            ))

            window_start += step_ms

        # Compute overfitting score
        overfitting_score = _compute_overfitting_score(windows)

        return WalkForwardResult(windows=windows, overfitting_score=overfitting_score)


def _compute_overfitting_score(windows: list[WalkForwardWindow]) -> float:
    """Overfitting score = 1 - (avg_oos_sharpe / avg_is_sharpe).

    0 = no overfitting (OOS matches IS), 1 = severe (OOS is zero).
    Clamped to [0, 1].
    """
    if not windows:
        return 0.0

    is_sharpes = [w.train_result.metrics.get("sharpe_ratio", 0.0) for w in windows]
    oos_sharpes = [w.test_result.metrics.get("sharpe_ratio", 0.0) for w in windows]

    avg_is = sum(is_sharpes) / len(is_sharpes) if is_sharpes else 0.0
    avg_oos = sum(oos_sharpes) / len(oos_sharpes) if oos_sharpes else 0.0

    if avg_is <= 0:
        return 0.0

    score = 1.0 - (avg_oos / avg_is)
    return max(0.0, min(1.0, score))
