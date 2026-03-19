"""Tests for backtest cost models — fees, slippage, and funding."""

from __future__ import annotations

import pytest

from perp_bot.backtest.cost_model import FeeModel, FundingModel, SlippageModel

_HOUR_MS = 3_600_000


# ── FeeModel ──────────────────────────────────────────

class TestFeeModel:
    def test_entry_fee_uses_maker_rate(self):
        fm = FeeModel(maker_rate=0.00015, taker_rate=0.00045)
        assert fm.entry_fee(10_000) == pytest.approx(1.50)

    def test_exit_fee_profit_uses_maker(self):
        fm = FeeModel(maker_rate=0.00015, taker_rate=0.00045)
        assert fm.exit_fee(10_000, is_stop_loss=False) == pytest.approx(1.50)

    def test_exit_fee_stop_loss_uses_taker(self):
        fm = FeeModel(maker_rate=0.00015, taker_rate=0.00045)
        assert fm.exit_fee(10_000, is_stop_loss=True) == pytest.approx(4.50)

    def test_zero_notional(self):
        fm = FeeModel()
        assert fm.entry_fee(0) == 0.0
        assert fm.exit_fee(0) == 0.0


# ── SlippageModel ─────────────────────────────────────

class TestSlippageModel:
    def test_long_entry_price_increases(self):
        sm = SlippageModel(min_pct=0.001, max_pct=0.001, seed=1)
        result = sm.apply(100.0, "long", is_entry=True)
        assert result > 100.0

    def test_long_exit_price_decreases(self):
        sm = SlippageModel(min_pct=0.001, max_pct=0.001, seed=1)
        result = sm.apply(100.0, "long", is_entry=False)
        assert result < 100.0

    def test_short_entry_price_decreases(self):
        sm = SlippageModel(min_pct=0.001, max_pct=0.001, seed=1)
        result = sm.apply(100.0, "short", is_entry=True)
        assert result < 100.0

    def test_short_exit_price_increases(self):
        sm = SlippageModel(min_pct=0.001, max_pct=0.001, seed=1)
        result = sm.apply(100.0, "short", is_entry=False)
        assert result > 100.0

    def test_reproducible_with_same_seed(self):
        sm1 = SlippageModel(seed=42)
        sm2 = SlippageModel(seed=42)
        assert sm1.apply(100.0, "long", True) == sm2.apply(100.0, "long", True)

    def test_different_seeds_produce_different_results(self):
        sm1 = SlippageModel(seed=1)
        sm2 = SlippageModel(seed=2)
        assert sm1.apply(100.0, "long", True) != sm2.apply(100.0, "long", True)

    def test_slippage_within_bounds(self):
        sm = SlippageModel(min_pct=0.0001, max_pct=0.0005, seed=42)
        for _ in range(100):
            result = sm.apply(1000.0, "long", is_entry=True)
            slip = (result - 1000.0) / 1000.0
            assert 0.0001 <= slip <= 0.0005


# ── FundingModel ──────────────────────────────────────

class TestFundingModel:
    def _make_rates(self, hours: list[int], rate: float) -> list[dict]:
        return [{"time": h * _HOUR_MS, "rate": rate} for h in hours]

    def test_no_funding_within_same_hour(self):
        rates = self._make_rates([0, 1, 2], 0.001)
        fm = FundingModel(rates)
        # Position opened and closed within the same hour
        cost = fm.cost_between("long", 10_000, 0, _HOUR_MS - 1)
        assert cost == 0.0

    def test_long_pays_positive_rate(self):
        rates = self._make_rates([0, 1, 2, 3], 0.001)
        fm = FundingModel(rates)
        cost = fm.cost_between("long", 10_000, 0, 3 * _HOUR_MS)
        # Should cross 3 hourly boundaries: 1h, 2h, 3h
        assert cost == pytest.approx(0.001 * 10_000 * 3)

    def test_short_receives_positive_rate(self):
        rates = self._make_rates([0, 1, 2, 3], 0.001)
        fm = FundingModel(rates)
        cost = fm.cost_between("short", 10_000, 0, 3 * _HOUR_MS)
        assert cost == pytest.approx(-0.001 * 10_000 * 3)

    def test_missing_rate_defaults_to_zero(self):
        # Only rate at hour 1
        fm = FundingModel([{"time": _HOUR_MS, "rate": 0.005}])
        cost = fm.cost_between("long", 10_000, 0, 3 * _HOUR_MS)
        # Only hour 1 has a rate
        assert cost == pytest.approx(0.005 * 10_000)

    def test_two_hour_hold(self):
        rates = self._make_rates(range(24), 0.0001)
        fm = FundingModel(rates)
        # Open at 0.5h, close at 2.5h → crosses hour 1 and hour 2
        cost = fm.cost_between("long", 10_000, _HOUR_MS // 2, 5 * _HOUR_MS // 2)
        assert cost == pytest.approx(0.0001 * 10_000 * 2)
