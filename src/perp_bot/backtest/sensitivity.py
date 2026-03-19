"""Parameter sensitivity analysis — one-at-a-time sweep."""

from __future__ import annotations

import logging
from dataclasses import replace

from perp_bot.backtest.config import BacktestConfig
from perp_bot.backtest.engine import BacktestEngine
from perp_bot.config import BotConfig
from perp_bot.data.db import Database
from perp_bot.signals.prediction import PredictionRegime

logger = logging.getLogger(__name__)

# Default parameter ranges for one-at-a-time sweep
DEFAULT_RANGES: dict[str, list] = {
    "zscore_entry_threshold": [1.8, 1.9, 2.0, 2.1, 2.2],
    "zscore_exit_threshold": [0.2, 0.3, 0.4, 0.5],
    "rsi_overbought": [65, 70, 75],
    "rsi_oversold": [25, 30, 35],
    "adx_threshold": [20, 25, 30],
    "zscore_lookback": [15, 20, 25, 30],
    "bollinger_period": [15, 20, 25],
}


class ParameterSensitivityAnalyzer:
    """One-at-a-time parameter sweep to test strategy robustness."""

    def __init__(
        self,
        bot_config: BotConfig,
        bt_config: BacktestConfig,
        param_ranges: dict[str, list] | None = None,
    ) -> None:
        self.bot_config = bot_config
        self.bt_config = bt_config
        self.param_ranges = param_ranges or DEFAULT_RANGES

    def run(
        self,
        db: Database,
        symbol: str,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
        prediction_regime: PredictionRegime = PredictionRegime.NORMAL,
    ) -> SensitivityReport:
        """Run one-at-a-time sweep over all parameter ranges."""
        results: dict[str, list[ParameterResult]] = {}

        for param_name, values in self.param_ranges.items():
            param_results: list[ParameterResult] = []
            logger.info("Sweeping %s: %s", param_name, values)

            for val in values:
                # Create modified signal config
                modified_signals = replace(self.bot_config.signals, **{param_name: val})
                modified_config = replace(self.bot_config, signals=modified_signals)
                engine = BacktestEngine(modified_config, self.bt_config)

                bt_result = engine.run(
                    db, symbol, start_time_ms, end_time_ms, prediction_regime
                )
                param_results.append(ParameterResult(
                    value=val,
                    sharpe=bt_result.metrics.get("sharpe_ratio", 0.0),
                    net_pnl=bt_result.metrics.get("total_net_pnl", 0.0),
                    win_rate=bt_result.metrics.get("win_rate", 0.0),
                    num_trades=bt_result.metrics.get("total_trades", 0),
                    max_dd_pct=bt_result.metrics.get("max_drawdown_pct", 0.0),
                ))

            results[param_name] = param_results

        return SensitivityReport(results=results)


class ParameterResult:
    """Result for a single parameter value."""
    __slots__ = ("value", "sharpe", "net_pnl", "win_rate", "num_trades", "max_dd_pct")

    def __init__(
        self, value, sharpe: float, net_pnl: float, win_rate: float,
        num_trades: int, max_dd_pct: float,
    ) -> None:
        self.value = value
        self.sharpe = sharpe
        self.net_pnl = net_pnl
        self.win_rate = win_rate
        self.num_trades = num_trades
        self.max_dd_pct = max_dd_pct


class SensitivityReport:
    """Aggregate sensitivity analysis results."""

    def __init__(self, results: dict[str, list[ParameterResult]]) -> None:
        self.results = results

    def summary(self) -> str:
        lines = ["═══ Sensitivity Analysis ═══"]
        for param, param_results in self.results.items():
            lines.append(f"\n  {param}:")
            for pr in param_results:
                profitable = "✓" if pr.net_pnl > 0 else "✗"
                lines.append(
                    f"    {pr.value:>8} → Sharpe={pr.sharpe:+.2f}  "
                    f"PnL=${pr.net_pnl:+.2f}  WR={pr.win_rate:.0%}  "
                    f"trades={pr.num_trades}  DD={pr.max_dd_pct:.1%}  [{profitable}]"
                )
            # Robustness check
            profitable_count = sum(1 for pr in param_results if pr.net_pnl > 0)
            pct = profitable_count / len(param_results) if param_results else 0
            robust = "ROBUST" if pct >= 0.7 else "FRAGILE"
            lines.append(
                f"    → {profitable_count}/{len(param_results)} profitable ({pct:.0%}) — {robust}"
            )
        lines.append("\n═════════════════════════════")
        return "\n".join(lines)
