"""Performance metrics — pure functions on trade lists and equity curves."""

from __future__ import annotations

import math

from perp_bot.backtest.results import TradeRecord


def win_rate(trades: list[TradeRecord]) -> float:
    """Fraction of trades with positive net P&L."""
    if not trades:
        return 0.0
    winners = sum(1 for t in trades if t.net_pnl > 0)
    return winners / len(trades)


def expected_value(trades: list[TradeRecord]) -> float:
    """Average net P&L per trade."""
    if not trades:
        return 0.0
    return sum(t.net_pnl for t in trades) / len(trades)


def profit_factor(trades: list[TradeRecord]) -> float:
    """Gross wins / gross losses. Returns inf if no losses."""
    gross_wins = sum(t.net_pnl for t in trades if t.net_pnl > 0)
    gross_losses = abs(sum(t.net_pnl for t in trades if t.net_pnl < 0))
    if gross_losses == 0:
        return float("inf") if gross_wins > 0 else 0.0
    return gross_wins / gross_losses


def sharpe_ratio(equity_curve: list[float], annualization_factor: float = 252.0) -> float:
    """Annualised Sharpe ratio from an equity curve.

    Computes daily returns from consecutive equity values.
    """
    if len(equity_curve) < 2:
        return 0.0
    returns = [
        (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
        for i in range(1, len(equity_curve))
        if equity_curve[i - 1] != 0
    ]
    if not returns:
        return 0.0
    mean_r = sum(returns) / len(returns)
    if len(returns) < 2:
        return 0.0
    variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std_r = math.sqrt(variance)
    if std_r == 0:
        return 0.0
    return (mean_r / std_r) * math.sqrt(annualization_factor)


def max_drawdown(equity_curve: list[float]) -> tuple[float, float]:
    """Returns (max_drawdown_pct, max_drawdown_usd) from peak-to-trough."""
    if len(equity_curve) < 2:
        return 0.0, 0.0
    peak = equity_curve[0]
    max_dd_pct = 0.0
    max_dd_usd = 0.0
    for val in equity_curve:
        if val > peak:
            peak = val
        dd_usd = peak - val
        dd_pct = dd_usd / peak if peak > 0 else 0.0
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct
            max_dd_usd = dd_usd
    return max_dd_pct, max_dd_usd


def calmar_ratio(
    total_return_pct: float, max_dd_pct: float, period_years: float
) -> float:
    """Calmar ratio = annualised return / max drawdown."""
    if max_dd_pct == 0 or period_years == 0:
        return 0.0
    annualised_return = total_return_pct / period_years
    return annualised_return / max_dd_pct


def avg_holding_hours(trades: list[TradeRecord]) -> float:
    """Average holding time across all trades, in hours."""
    if not trades:
        return 0.0
    total_ms = sum(t.exit_time_ms - t.entry_time_ms for t in trades)
    return total_ms / len(trades) / 3_600_000


def compute_all(
    trades: list[TradeRecord],
    equity_curve: list[float],
    initial_capital: float,
) -> dict:
    """Compute all performance metrics and return as a dictionary."""
    total_net_pnl = sum(t.net_pnl for t in trades)
    total_fees = sum(t.entry_fee + t.exit_fee for t in trades)
    total_funding = sum(t.funding_cost for t in trades)
    total_slippage = sum(
        abs(t.entry_price - t.raw_entry_price) / t.raw_entry_price * t.size_usd
        + abs(t.exit_price - t.raw_exit_price) / t.raw_exit_price * t.size_usd
        for t in trades
    ) if trades else 0.0

    dd_pct, dd_usd = max_drawdown(equity_curve)

    # Period duration for Calmar
    if len(equity_curve) > 1:
        period_days = len(equity_curve) - 1
        period_years = period_days / 365.0
        total_return_pct = (
            (equity_curve[-1] - initial_capital) / initial_capital if initial_capital > 0 else 0.0
        )
    else:
        period_years = 0.0
        total_return_pct = 0.0

    return {
        "total_trades": len(trades),
        "win_rate": win_rate(trades),
        "expected_value": expected_value(trades),
        "profit_factor": profit_factor(trades),
        "sharpe_ratio": sharpe_ratio(equity_curve),
        "max_drawdown_pct": dd_pct,
        "max_drawdown_usd": dd_usd,
        "calmar_ratio": calmar_ratio(total_return_pct, dd_pct, period_years),
        "avg_holding_hours": avg_holding_hours(trades),
        "total_net_pnl": total_net_pnl,
        "total_fees": total_fees,
        "total_funding": total_funding,
        "total_slippage": total_slippage,
        "total_return_pct": total_return_pct,
    }
