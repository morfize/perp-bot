"""Tests for the risk manager."""

import time

from perp_bot.config import (
    BotConfig,
    DataConfig,
    ExecutionConfig,
    RiskConfig,
    SignalConfig,
    TradingConfig,
)
from perp_bot.data.db import Database
from perp_bot.risk.manager import RiskManager


def _test_config() -> BotConfig:
    return BotConfig(
        trading=TradingConfig(
            symbols=["ETH"], leverage=3,
            capital_usd=670.0, margin_usage_limit=0.5,
        ),
        signals=SignalConfig(
            zscore_lookback=20, zscore_entry_threshold=2.0,
            zscore_exit_threshold=0.3, zscore_stop_threshold=3.0,
            bollinger_period=20, bollinger_std=2.0,
            rsi_period=14, rsi_overbought=70, rsi_oversold=30,
            adx_period=14, adx_threshold=25,
        ),
        risk=RiskConfig(
            max_loss_per_trade_pct=0.03, daily_loss_limit_pct=0.08,
            max_positions=1, cooldown_seconds=1800,
            position_timeout_hours=24,
        ),
        data=DataConfig(
            timeframes=["15m"], primary_timeframe="15m",
            history_days=90, db_path=":memory:",
        ),
        execution=ExecutionConfig(
            order_type="limit", taker_fallback_seconds=30,
            use_server_side_stop=True,
        ),
        mode="paper",
    )


class TestRiskManager:
    def test_entry_allowed_when_clean(self):
        config = _test_config()
        db = Database(":memory:")
        rm = RiskManager(config, db)
        check = rm.check_entry()
        assert check.allowed

    def test_entry_blocked_max_positions(self):
        config = _test_config()
        db = Database(":memory:")
        rm = RiskManager(config, db)
        db.insert_trade({
            "symbol": "ETH", "side": "long",
            "entry_time": int(time.time() * 1000),
            "entry_price": 3000.0, "size_usd": 1000.0, "is_paper": 1,
        })
        check = rm.check_entry()
        assert not check.allowed
        assert "max_positions" in check.reason

    def test_position_size(self):
        config = _test_config()
        db = Database(":memory:")
        rm = RiskManager(config, db)
        size = rm.compute_position_size()
        # 670 * 0.5 * 3 = 1005
        assert size == 1005.0

    def test_stop_loss_triggers(self):
        config = _test_config()
        db = Database(":memory:")
        rm = RiskManager(config, db)
        # Long position: entry 3000, current 2950 → loss = 50/3000 * 1005 = $16.75
        assert not rm.check_stop_loss(3000.0, 2950.0, "long", 1005.0)
        # Entry 3000, current 2900 → loss = 100/3000 * 1005 = $33.50 > $20.10 (3% of 670)
        assert rm.check_stop_loss(3000.0, 2900.0, "long", 1005.0)

    def test_stop_loss_uses_actual_trade_size(self):
        config = _test_config()
        db = Database(":memory:")
        rm = RiskManager(config, db)
        # Same 3.33% move, but only half-sized notional -> $16.75 loss, below threshold.
        assert not rm.check_stop_loss(3000.0, 2900.0, "long", 502.5)

    def test_cooldown_blocks_entry(self):
        config = _test_config()
        db = Database(":memory:")
        rm = RiskManager(config, db)
        rm.record_stop_loss()
        check = rm.check_entry()
        assert not check.allowed
        assert "cooldown" in check.reason

    def test_cooldown_persists_across_restart(self):
        config = _test_config()
        db = Database(":memory:")
        rm1 = RiskManager(config, db)
        rm1.record_stop_loss()
        # Verify it was persisted to DB
        val = db.get_state("last_stop_loss_time_ms")
        assert val is not None

        # Create a new RiskManager (simulates restart)
        rm2 = RiskManager(config, db)
        check = rm2.check_entry()
        assert not check.allowed
        assert "cooldown" in check.reason

    def test_expired_cooldown_not_restored(self):
        config = _test_config()
        db = Database(":memory:")
        # Set a cooldown time that's already expired (2 hours ago)
        old_time = int(time.time() * 1000) - 7200_000
        db.set_state("last_stop_loss_time_ms", str(old_time))
        rm = RiskManager(config, db)
        check = rm.check_entry()
        assert check.allowed
