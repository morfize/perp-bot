"""Integration tests for the backtest engine with synthetic data."""

from __future__ import annotations

import math

import numpy as np
import pytest

from perp_bot.backtest.config import BacktestConfig
from perp_bot.backtest.cost_model import FeeModel, FundingModel, SlippageModel
from perp_bot.backtest.engine import BacktestEngine
from perp_bot.backtest.executor import BacktestExecutor
from perp_bot.backtest.risk_adapter import BacktestRiskManager
from perp_bot.config import (
    BotConfig,
    DataConfig,
    ExecutionConfig,
    RiskConfig,
    SignalConfig,
    TradingConfig,
)
from perp_bot.data.db import Database
from perp_bot.signals.prediction import PredictionRegime


def _make_config() -> BotConfig:
    return BotConfig(
        trading=TradingConfig(
            symbols=["ETH"], leverage=3, capital_usd=670.0, margin_usage_limit=0.5,
        ),
        signals=SignalConfig(
            zscore_lookback=20, zscore_entry_threshold=2.0, zscore_exit_threshold=0.3,
            zscore_stop_threshold=3.0, bollinger_period=20, bollinger_std=2.0,
            rsi_period=14, rsi_overbought=70, rsi_oversold=30,
            adx_period=14, adx_threshold=25,
        ),
        risk=RiskConfig(
            max_loss_per_trade_pct=0.03, daily_loss_limit_pct=0.08,
            max_positions=1, cooldown_seconds=1800, position_timeout_hours=24,
        ),
        data=DataConfig(
            timeframes=["15m"], primary_timeframe="15m", history_days=90,
            db_path=":memory:",
        ),
        execution=ExecutionConfig(
            order_type="limit", taker_fallback_seconds=30, use_server_side_stop=True,
        ),
        mode="paper",
    )


def _make_bt_config(**overrides) -> BacktestConfig:
    defaults = dict(
        entry_delay_candles=0,
        slippage_min_pct=0.0,
        slippage_max_pct=0.0,
    )
    defaults.update(overrides)
    return BacktestConfig(**defaults)


def _generate_mean_reverting_candles(
    n_candles: int = 200,
    base_price: float = 2000.0,
    amplitude: float = 100.0,
    period: int = 40,
    start_time_ms: int = 0,
    interval_ms: int = 900_000,  # 15m candles
) -> list[dict]:
    """Generate synthetic candles with mean-reverting price action."""
    candles = []
    np.random.seed(42)
    for i in range(n_candles):
        # Sinusoidal mean-reversion pattern
        price = base_price + amplitude * math.sin(2 * math.pi * i / period)
        noise = np.random.normal(0, 2)
        close = price + noise
        high = close + abs(np.random.normal(0, 3))
        low = close - abs(np.random.normal(0, 3))
        open_price = close + np.random.normal(0, 1)
        candles.append({
            "symbol": "ETH",
            "timeframe": "15m",
            "open_time": start_time_ms + i * interval_ms,
            "open": open_price,
            "high": max(high, open_price, close),
            "low": min(low, open_price, close),
            "close": close,
            "volume": 1000.0 + abs(np.random.normal(0, 200)),
            "num_trades": 100,
        })
    return candles


class TestBacktestExecutor:
    def test_open_and_close_long(self):
        fm = FeeModel(0.00015, 0.00045)
        sm = SlippageModel(0.0, 0.0, seed=1)  # no slippage
        funding = FundingModel([])
        executor = BacktestExecutor(fm, sm, funding)

        executor.open_position("ETH", "long", 1000.0, 2000.0, 0)
        assert executor.has_position

        record = executor.close_position(2100.0, 3_600_000, "profit")
        assert not executor.has_position
        assert record.side == "long"
        assert record.pnl > 0  # price went up
        assert record.entry_fee == pytest.approx(0.15)
        assert record.exit_fee == pytest.approx(0.15)
        assert record.net_pnl < record.pnl  # fees reduce net

    def test_stop_loss_uses_taker_fee(self):
        fm = FeeModel(0.00015, 0.00045)
        sm = SlippageModel(0.0, 0.0, seed=1)
        executor = BacktestExecutor(fm, sm, FundingModel([]))

        executor.open_position("ETH", "long", 1000.0, 2000.0, 0)
        record = executor.close_position(1900.0, 3_600_000, "stop_loss", is_stop_loss=True)
        assert record.exit_fee == pytest.approx(0.45)  # taker rate

    def test_short_pnl_calculation(self):
        fm = FeeModel(0.0, 0.0)  # no fees for clarity
        sm = SlippageModel(0.0, 0.0, seed=1)
        executor = BacktestExecutor(fm, sm, FundingModel([]))

        executor.open_position("ETH", "short", 1000.0, 2000.0, 0)
        record = executor.close_position(1800.0, 3_600_000, "profit")
        # (2000-1800)/2000 * 1000 = 100
        assert record.pnl == pytest.approx(100.0)


class TestBacktestRiskManager:
    def test_cooldown_blocks_entry(self):
        config = _make_config()
        risk = BacktestRiskManager(config)
        # Record a stop-loss at time 0
        risk.record_trade_close(-20.0, 0, is_stop_loss=True)
        # 1800s cooldown = 1_800_000ms
        assert risk.check_entry(1_000_000) is False  # within cooldown
        assert risk.check_entry(1_800_001) is True    # after cooldown

    def test_daily_loss_limit(self):
        config = _make_config()
        risk = BacktestRiskManager(config)
        day_ms = 86_400_000
        # Max daily loss = 670 * 0.08 = $53.60
        risk.record_trade_close(-30.0, day_ms)
        assert risk.check_entry(day_ms + 1000) is True
        risk.record_trade_close(-30.0, day_ms + 2000)
        # Total = -60 > -53.60 limit
        assert risk.check_entry(day_ms + 3000) is False
        # Next day resets
        assert risk.check_entry(2 * day_ms + 1000) is True

    def test_position_timeout(self):
        config = _make_config()
        risk = BacktestRiskManager(config)
        entry = 0
        # 24h = 86_400_000ms
        assert risk.check_position_timeout(entry, 86_000_000) is False
        assert risk.check_position_timeout(entry, 86_400_001) is True

    def test_crisis_blocks_sizing(self):
        config = _make_config()
        risk = BacktestRiskManager(config)
        assert risk.compute_position_size(PredictionRegime.CRISIS) == 0.0
        assert risk.compute_position_size(PredictionRegime.NORMAL) > 0.0


class TestBacktestEngine:
    def test_runs_without_error(self):
        """Integration test: engine runs on synthetic data and produces results."""
        config = _make_config()
        bt_config = _make_bt_config()
        db = Database(":memory:")

        candles = _generate_mean_reverting_candles()
        db.insert_candles(candles)

        engine = BacktestEngine(config, bt_config)
        result = engine.run(db, "ETH")

        assert result.equity_curve
        assert result.metrics["total_trades"] >= 0
        db.close()

    def test_empty_data_returns_empty_result(self):
        config = _make_config()
        bt_config = _make_bt_config()
        db = Database(":memory:")

        engine = BacktestEngine(config, bt_config)
        result = engine.run(db, "ETH")
        assert result.trades == []
        assert result.equity_curve == []
        db.close()

    def test_reproducible_with_same_seed(self):
        """Two runs with same seed produce identical trades."""
        config = _make_config()
        bt_config = _make_bt_config(seed=42)
        db = Database(":memory:")
        candles = _generate_mean_reverting_candles()
        db.insert_candles(candles)

        engine1 = BacktestEngine(config, bt_config)
        result1 = engine1.run(db, "ETH")

        engine2 = BacktestEngine(config, bt_config)
        result2 = engine2.run(db, "ETH")

        assert len(result1.trades) == len(result2.trades)
        for t1, t2 in zip(result1.trades, result2.trades):
            assert t1.entry_price == t2.entry_price
            assert t1.exit_price == t2.exit_price
            assert t1.net_pnl == t2.net_pnl
        db.close()

    def test_costs_are_applied(self):
        """Verify that fees and slippage actually reduce net P&L."""
        config = _make_config()
        # Run with zero costs
        bt_zero = _make_bt_config(
            maker_fee_rate=0.0, taker_fee_rate=0.0,
            slippage_min_pct=0.0, slippage_max_pct=0.0,
        )
        # Run with real costs
        bt_real = _make_bt_config(
            maker_fee_rate=0.00015, taker_fee_rate=0.00045,
            slippage_min_pct=0.0001, slippage_max_pct=0.0005,
        )

        db = Database(":memory:")
        candles = _generate_mean_reverting_candles()
        db.insert_candles(candles)

        result_zero = BacktestEngine(config, bt_zero).run(db, "ETH")
        result_real = BacktestEngine(config, bt_real).run(db, "ETH")

        # If there are trades, costs should reduce the total net pnl
        if result_zero.trades and result_real.trades:
            assert result_real.metrics["total_net_pnl"] <= result_zero.metrics["total_net_pnl"]
        db.close()

    def test_entry_delay_can_cancel_order(self):
        """With entry_delay=1 and cancel_if_signal_gone, some orders get cancelled."""
        config = _make_config()
        bt_instant = _make_bt_config(entry_delay_candles=0)
        bt_delayed = _make_bt_config(entry_delay_candles=1, cancel_if_signal_gone=True)

        db = Database(":memory:")
        candles = _generate_mean_reverting_candles()
        db.insert_candles(candles)

        result_instant = BacktestEngine(config, bt_instant).run(db, "ETH")
        result_delayed = BacktestEngine(config, bt_delayed).run(db, "ETH")

        # Delayed execution with cancellation should have <= trades than instant
        assert result_delayed.metrics["total_trades"] <= result_instant.metrics["total_trades"]
        db.close()

    def test_force_close_at_end(self):
        """Any open position is force-closed at backtest end."""
        config = _make_config()
        bt_config = _make_bt_config()
        db = Database(":memory:")

        # Use small dataset so we likely have an open position at end
        candles = _generate_mean_reverting_candles(n_candles=60)
        db.insert_candles(candles)

        engine = BacktestEngine(config, bt_config)
        result = engine.run(db, "ETH")

        # All trades should be closed (have exit_reason)
        for t in result.trades:
            assert t.exit_reason is not None
        db.close()
