"""Tests for backtest performance metrics."""

from __future__ import annotations

import pytest

from perp_bot.backtest.metrics import (
    avg_holding_hours,
    calmar_ratio,
    compute_all,
    expected_value,
    max_drawdown,
    profit_factor,
    sharpe_ratio,
    win_rate,
)
from perp_bot.backtest.results import TradeRecord


def _trade(net_pnl: float, entry_ms: int = 0, exit_ms: int = 3_600_000) -> TradeRecord:
    """Helper to create a minimal TradeRecord for metric tests."""
    return TradeRecord(
        id=1, symbol="ETH", side="long",
        entry_time_ms=entry_ms, exit_time_ms=exit_ms,
        entry_price=100.0, exit_price=101.0,
        raw_entry_price=100.0, raw_exit_price=101.0,
        size_usd=1000.0, pnl=net_pnl + 1.0,  # approximate
        entry_fee=0.5, exit_fee=0.5, funding_cost=0.0,
        net_pnl=net_pnl, exit_reason="test",
    )


class TestWinRate:
    def test_all_winners(self):
        trades = [_trade(10), _trade(5), _trade(1)]
        assert win_rate(trades) == pytest.approx(1.0)

    def test_all_losers(self):
        trades = [_trade(-10), _trade(-5)]
        assert win_rate(trades) == pytest.approx(0.0)

    def test_mixed(self):
        trades = [_trade(10), _trade(-5), _trade(3)]
        assert win_rate(trades) == pytest.approx(2 / 3)

    def test_empty(self):
        assert win_rate([]) == 0.0

    def test_zero_pnl_not_winner(self):
        trades = [_trade(0.0)]
        assert win_rate(trades) == 0.0


class TestExpectedValue:
    def test_positive(self):
        trades = [_trade(10), _trade(20)]
        assert expected_value(trades) == pytest.approx(15.0)

    def test_empty(self):
        assert expected_value([]) == 0.0


class TestProfitFactor:
    def test_normal(self):
        trades = [_trade(20), _trade(-10)]
        assert profit_factor(trades) == pytest.approx(2.0)

    def test_no_losses(self):
        trades = [_trade(10), _trade(5)]
        assert profit_factor(trades) == float("inf")

    def test_no_wins_no_losses(self):
        assert profit_factor([]) == 0.0


class TestSharpeRatio:
    def test_constant_equity(self):
        curve = [100.0, 100.0, 100.0, 100.0]
        assert sharpe_ratio(curve) == 0.0

    def test_steadily_increasing(self):
        # Daily 1% return → high Sharpe
        curve = [100.0]
        for _ in range(100):
            curve.append(curve[-1] * 1.01)
        s = sharpe_ratio(curve)
        assert s > 5.0  # very high because no variance

    def test_too_short(self):
        assert sharpe_ratio([100.0]) == 0.0
        assert sharpe_ratio([]) == 0.0


class TestMaxDrawdown:
    def test_no_drawdown(self):
        curve = [100, 101, 102, 103]
        pct, usd = max_drawdown(curve)
        assert pct == 0.0
        assert usd == 0.0

    def test_known_drawdown(self):
        curve = [100, 110, 90, 95, 105]
        pct, usd = max_drawdown(curve)
        # Peak=110, trough=90 → dd=20/110
        assert usd == pytest.approx(20.0)
        assert pct == pytest.approx(20 / 110)

    def test_empty(self):
        pct, usd = max_drawdown([])
        assert pct == 0.0


class TestCalmarRatio:
    def test_normal(self):
        # 20% annual return, 10% max dd, 1 year
        assert calmar_ratio(0.20, 0.10, 1.0) == pytest.approx(2.0)

    def test_zero_drawdown(self):
        assert calmar_ratio(0.20, 0.0, 1.0) == 0.0


class TestAvgHoldingHours:
    def test_known_holding(self):
        t1 = _trade(10, entry_ms=0, exit_ms=2 * 3_600_000)  # 2h
        t2 = _trade(5, entry_ms=0, exit_ms=4 * 3_600_000)   # 4h
        assert avg_holding_hours([t1, t2]) == pytest.approx(3.0)

    def test_empty(self):
        assert avg_holding_hours([]) == 0.0


class TestComputeAll:
    def test_returns_all_keys(self):
        trades = [_trade(10), _trade(-5)]
        curve = [100, 102, 98, 105]
        result = compute_all(trades, curve, 100.0)
        expected_keys = {
            "total_trades", "win_rate", "expected_value", "profit_factor",
            "sharpe_ratio", "max_drawdown_pct", "max_drawdown_usd",
            "calmar_ratio", "avg_holding_hours", "total_net_pnl",
            "total_fees", "total_funding", "total_slippage", "total_return_pct",
        }
        assert set(result.keys()) == expected_keys

    def test_total_net_pnl(self):
        trades = [_trade(10), _trade(-3)]
        result = compute_all(trades, [100, 107], 100.0)
        assert result["total_net_pnl"] == pytest.approx(7.0)
