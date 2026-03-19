"""Result dataclasses for backtest output."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field


@dataclass
class TradeRecord:
    """A single completed backtest trade with full cost breakdown."""

    id: int
    symbol: str
    side: str  # "long" | "short"
    entry_time_ms: int
    exit_time_ms: int
    entry_price: float   # after slippage
    exit_price: float    # after slippage
    raw_entry_price: float  # pre-slippage
    raw_exit_price: float   # pre-slippage
    size_usd: float
    pnl: float           # raw P&L before costs
    entry_fee: float
    exit_fee: float
    funding_cost: float
    net_pnl: float       # pnl - entry_fee - exit_fee - funding_cost
    exit_reason: str


@dataclass
class BacktestResult:
    """Aggregate result from a single backtest run."""

    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    initial_capital: float = 0.0
    start_time_ms: int = 0
    end_time_ms: int = 0

    def summary(self) -> str:
        """Human-readable performance summary."""
        m = self.metrics
        lines = [
            "═══ Backtest Summary ═══",
            f"  Period:          {self.start_time_ms} → {self.end_time_ms}",
            f"  Trades:          {m.get('total_trades', 0)}",
            f"  Win rate:        {m.get('win_rate', 0):.1%}",
            f"  Net P&L:         ${m.get('total_net_pnl', 0):.2f}",
            f"  Expected value:  ${m.get('expected_value', 0):.2f}/trade",
            f"  Profit factor:   {m.get('profit_factor', 0):.2f}",
            f"  Sharpe ratio:    {m.get('sharpe_ratio', 0):.2f}",
            f"  Max drawdown:    {m.get('max_drawdown_pct', 0):.1%}"
            f" (${m.get('max_drawdown_usd', 0):.2f})",
            f"  Calmar ratio:    {m.get('calmar_ratio', 0):.2f}",
            f"  Avg holding:     {m.get('avg_holding_hours', 0):.1f}h",
            f"  Total fees:      ${m.get('total_fees', 0):.2f}",
            f"  Total funding:   ${m.get('total_funding', 0):.2f}",
            f"  Total slippage:  ${m.get('total_slippage', 0):.2f}",
            "════════════════════════",
        ]
        return "\n".join(lines)

    def trades_to_csv(self, path: str) -> None:
        """Export trade records to CSV."""
        if not self.trades:
            return
        fieldnames = [
            "id", "symbol", "side", "entry_time_ms", "exit_time_ms",
            "entry_price", "exit_price", "raw_entry_price", "raw_exit_price",
            "size_usd", "pnl", "entry_fee", "exit_fee", "funding_cost",
            "net_pnl", "exit_reason",
        ]
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for t in self.trades:
                writer.writerow({k: getattr(t, k) for k in fieldnames})


@dataclass
class WalkForwardWindow:
    """One train/test window pair."""

    train_start_ms: int
    train_end_ms: int
    test_start_ms: int
    test_end_ms: int
    train_result: BacktestResult
    test_result: BacktestResult


@dataclass
class WalkForwardResult:
    """Aggregate walk-forward analysis result."""

    windows: list[WalkForwardWindow] = field(default_factory=list)
    overfitting_score: float = 0.0

    def summary(self) -> str:
        lines = ["═══ Walk-Forward Analysis ═══"]
        for i, w in enumerate(self.windows):
            train_m = w.train_result.metrics
            test_m = w.test_result.metrics
            lines.append(
                f"  Window {i+1}: "
                f"Train Sharpe={train_m.get('sharpe_ratio', 0):.2f} "
                f"Test Sharpe={test_m.get('sharpe_ratio', 0):.2f} "
                f"Train trades={train_m.get('total_trades', 0)} "
                f"Test trades={test_m.get('total_trades', 0)}"
            )
        lines.append(f"  Overfitting score: {self.overfitting_score:.2f} (0=good, 1=severe)")
        lines.append("═════════════════════════════")
        return "\n".join(lines)
