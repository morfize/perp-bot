"""Tests for paper-vs-backtest comparison reporting."""

from __future__ import annotations

from types import SimpleNamespace

from perp_bot.config import (
    BotConfig,
    DataConfig,
    ExecutionConfig,
    RiskConfig,
    SignalConfig,
    TradingConfig,
)
from perp_bot.data.db import Database
from perp_bot.reporting.compare import compare_paper_vs_backtest


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


def test_compare_filters_out_live_trades(monkeypatch):
    config = _make_config()
    db = Database(":memory:")

    paper_trade_id = db.insert_trade({
        "symbol": "ETH",
        "side": "long",
        "entry_time": 1_000,
        "entry_price": 2_500.0,
        "size_usd": 1_000.0,
        "is_paper": 1,
    })
    db.close_trade(paper_trade_id, 2_000, 2_550.0, 20.0, "paper_win")

    live_trade_id = db.insert_trade({
        "symbol": "ETH",
        "side": "long",
        "entry_time": 3_000,
        "entry_price": 2_500.0,
        "size_usd": 1_000.0,
        "is_paper": 0,
    })
    db.close_trade(live_trade_id, 4_000, 2_350.0, -60.0, "live_loss")

    class FakeBacktestEngine:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self, *_args, **_kwargs):
            return SimpleNamespace(trades=[])

    monkeypatch.setattr("perp_bot.reporting.compare.BacktestEngine", FakeBacktestEngine)

    report = compare_paper_vs_backtest(
        config, db, "ETH", start_ms=0, end_ms=10_000,
    )

    lines = {
        line.split()[0]: line.split()
        for line in report.splitlines()
        if line and line.split()[0] in {"trades", "net_pnl"}
    }

    assert lines["trades"][1:] == ["1", "0", "+1"]
    assert lines["net_pnl"][1:] == ["20.00$", "0.00$", "+20.00$"]
    db.close()
