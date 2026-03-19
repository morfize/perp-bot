"""Weekly performance report — aggregates trade data for review."""

from __future__ import annotations

import math
import time

from perp_bot.data.db import Database


def generate_weekly_report(db: Database, weeks: int = 1) -> str:
    """Generate a performance summary for the last N weeks of closed trades.

    Returns a formatted text report.
    """
    now_ms = int(time.time() * 1000)
    week_ms = 7 * 24 * 3600 * 1000
    start_ms = now_ms - weeks * week_ms

    trades = db.get_closed_trades_in_range(start_ms, now_ms)

    if not trades:
        return f"No closed trades in the last {weeks} week(s)."

    pnls = [t.get("pnl", 0) or 0 for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_pnl = sum(pnls)
    win_rate = len(wins) / len(pnls) * 100 if pnls else 0
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0

    # Sharpe ratio (annualised, assuming daily returns)
    sharpe = _compute_sharpe(pnls)

    # Max drawdown
    max_dd = _compute_max_drawdown(pnls)

    # Exit reason breakdown
    reasons: dict[str, int] = {}
    for t in trades:
        r = t.get("exit_reason", "unknown") or "unknown"
        reasons[r] = reasons.get(r, 0) + 1

    lines = [
        f"=== Weekly Performance Report ({weeks}w) ===",
        f"Period:       {weeks} week(s) ending now",
        f"Total trades: {len(trades)}",
        f"Win rate:     {win_rate:.1f}%",
        f"Net P&L:      ${total_pnl:.2f}",
        f"Avg win:      ${avg_win:.2f}",
        f"Avg loss:     ${avg_loss:.2f}",
        f"Sharpe (ann): {sharpe:.2f}",
        f"Max drawdown: ${max_dd:.2f}",
        "",
        "Exit reasons:",
    ]
    for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
        lines.append(f"  {reason}: {count}")

    return "\n".join(lines)


def _compute_sharpe(pnls: list[float], risk_free: float = 0.0) -> float:
    """Annualised Sharpe ratio from a list of per-trade P&Ls."""
    if len(pnls) < 2:
        return 0.0
    mean = sum(pnls) / len(pnls) - risk_free
    std = math.sqrt(sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1))
    if std == 0:
        return 0.0
    # Annualise assuming ~1 trade/day, 365 trading days
    return (mean / std) * math.sqrt(365)


def _compute_max_drawdown(pnls: list[float]) -> float:
    """Maximum drawdown from cumulative P&L curve."""
    if not pnls:
        return 0.0
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cumulative += p
        peak = max(peak, cumulative)
        dd = peak - cumulative
        max_dd = max(max_dd, dd)
    return max_dd
