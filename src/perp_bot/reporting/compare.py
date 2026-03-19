"""Paper-vs-backtest comparison — validates live behaviour against backtest expectations."""

from __future__ import annotations

import time

from perp_bot.backtest.engine import BacktestEngine
from perp_bot.config import BacktestConfig, BotConfig
from perp_bot.data.db import Database


def compare_paper_vs_backtest(
    config: BotConfig,
    db: Database,
    symbol: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> str:
    """Run a backtest over the given range and compare against paper trades in DB.

    Returns a formatted comparison report.
    """
    if end_ms is None:
        end_ms = int(time.time() * 1000)
    if start_ms is None:
        start_ms = end_ms - 7 * 24 * 3600 * 1000  # default: last 7 days

    # --- Paper trade metrics ---
    paper_trades = db.get_closed_trades_in_range(start_ms, end_ms)
    paper_trades = [t for t in paper_trades if t["symbol"] == symbol]
    paper_stats = _compute_stats(paper_trades, "Paper")

    # --- Backtest over the same range ---
    bt_config = config.backtest or BacktestConfig()
    engine = BacktestEngine(config, bt_config)
    bt_result = engine.run(
        db, symbol, start_time_ms=start_ms, end_time_ms=end_ms,
    )
    bt_trades = bt_result.trades
    bt_stats = _compute_stats(bt_trades, "Backtest")

    # --- Format comparison ---
    lines = [
        f"=== Paper vs Backtest Comparison: {symbol} ===",
        f"Period: {_fmt_ms(start_ms)} → {_fmt_ms(end_ms)}",
        "",
        f"{'Metric':<20} {'Paper':>12} {'Backtest':>12} {'Delta':>12}",
        "-" * 58,
    ]

    for key in ["trades", "win_rate_pct", "net_pnl", "avg_pnl", "avg_hold_h"]:
        p_val = paper_stats.get(key, 0)
        b_val = bt_stats.get(key, 0)
        delta = p_val - b_val
        if key == "win_rate_pct":
            lines.append(
                f"{key:<20} {p_val:>11.1f}% {b_val:>11.1f}% {delta:>+11.1f}%"
            )
        elif key == "trades":
            lines.append(
                f"{key:<20} {p_val:>12d} {b_val:>12d} {delta:>+12d}"
            )
        else:
            lines.append(
                f"{key:<20} {p_val:>11.2f}$ {b_val:>11.2f}$ {delta:>+11.2f}$"
            )

    # --- Verdict ---
    lines.append("")
    if not paper_trades:
        lines.append("Verdict: No paper trades in this period — nothing to compare.")
    elif not bt_trades:
        lines.append("Verdict: No backtest trades generated — check data availability.")
    else:
        pnl_delta_pct = abs(
            (paper_stats["net_pnl"] - bt_stats["net_pnl"])
            / max(abs(bt_stats["net_pnl"]), 1)
            * 100
        )
        wr_delta = abs(
            paper_stats["win_rate_pct"] - bt_stats["win_rate_pct"]
        )
        if pnl_delta_pct > 50 or wr_delta > 20:
            lines.append(
                "Verdict: SIGNIFICANT DIVERGENCE — paper results differ "
                "materially from backtest. Investigate execution quality."
            )
        elif pnl_delta_pct > 25 or wr_delta > 10:
            lines.append(
                "Verdict: MODERATE DIVERGENCE — some difference between "
                "paper and backtest. Monitor closely."
            )
        else:
            lines.append(
                "Verdict: CONSISTENT — paper trading aligns with "
                "backtest expectations."
            )

    return "\n".join(lines)


def _compute_stats(trades: list, label: str) -> dict:
    """Compute aggregate statistics from trade dicts or TradeRecord dataclasses."""
    if not trades:
        return {
            "trades": 0,
            "win_rate_pct": 0.0,
            "net_pnl": 0.0,
            "avg_pnl": 0.0,
            "avg_hold_h": 0.0,
        }

    pnls = [_get(t, "pnl", 0) for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    net = sum(pnls)

    hold_hours = []
    for t in trades:
        entry = _get(t, "entry_time", 0) or _get(t, "entry_time_ms", 0)
        exit_ = _get(t, "exit_time", 0) or _get(t, "exit_time_ms", 0)
        if entry and exit_:
            hold_hours.append((exit_ - entry) / 3_600_000)

    return {
        "trades": len(trades),
        "win_rate_pct": wins / len(trades) * 100,
        "net_pnl": net,
        "avg_pnl": net / len(trades),
        "avg_hold_h": sum(hold_hours) / len(hold_hours) if hold_hours else 0,
    }


def _get(obj, key: str, default=None):
    """Get an attribute from a dict or dataclass."""
    if isinstance(obj, dict):
        return obj.get(key, default) or default
    return getattr(obj, key, default) or default


def _fmt_ms(ms: int) -> str:
    """Format epoch ms as a human-readable date string."""
    import datetime

    dt = datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")
