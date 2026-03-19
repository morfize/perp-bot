"""Tests for main CLI/runtime flows."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import perp_bot.cli as main
from perp_bot.config import (
    BacktestConfig,
    BotConfig,
    DataConfig,
    ExecutionConfig,
    RiskConfig,
    SignalConfig,
    TradingConfig,
)
from perp_bot.signals.engine import Signal


def _make_config(
    *,
    mode: str = "paper",
    symbols: list[str] | None = None,
    export_trades_csv: str | None = None,
) -> BotConfig:
    return BotConfig(
        trading=TradingConfig(
            symbols=symbols or ["ETH"], leverage=3,
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
        mode=mode,
        backtest=(
            BacktestConfig(export_trades_csv=export_trades_csv)
            if export_trades_csv
            else None
        ),
        hl_private_key="0x" + "ab" * 32 if mode == "live" else "",
    )


def test_tick_skips_open_alert_when_entry_fails(monkeypatch):
    alerts: list[str] = []

    class FakeDb:
        def get_candles(self, *_args, **_kwargs):
            return [
                {
                    "open_time": i,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0 + i,
                    "volume": 1000.0,
                    "num_trades": 10,
                }
                for i in range(3)
            ]

        def get_open_trades(self, _symbol=None):
            return []

    class FakeIngestor:
        def update_candles(self, _symbol):
            pass

    class FakeSignalEngine:
        def compute_indicators(self, df):
            return df

        def evaluate(self, *_args, **_kwargs):
            return SimpleNamespace(
                signal=Signal.LONG,
                reason="entry",
                zscore_value=2.5,
                rsi_value=75.0,
                adx_value=10.0,
                price=100.0,
            )

    class FakeRiskManager:
        def check_entry(self):
            return SimpleNamespace(allowed=True, reason="ok")

        def compute_position_size(self, *_args, **_kwargs):
            return 1_000.0

    class FakeExecutor:
        _sl_failed = False

        def open_position(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(main, "_get_price", lambda *_args, **_kwargs: 100.0)
    monkeypatch.setattr(main, "_alert", lambda _config, message: alerts.append(message))

    last_prediction_poll_ms, regime = main._tick(
        _make_config(),
        FakeDb(),
        client=object(),
        ingestor=FakeIngestor(),
        signal_engine=FakeSignalEngine(),
        risk_manager=FakeRiskManager(),
        executor=FakeExecutor(),
        tf="15m",
        min_candles=2,
    )

    assert alerts == []
    assert last_prediction_poll_ms == 0
    assert regime == "normal"


def test_run_backtest_exports_distinct_files_per_symbol(monkeypatch):
    exported_paths: list[str] = []

    class FakeResult:
        def summary(self):
            return "summary"

        def trades_to_csv(self, path):
            exported_paths.append(path)

    class FakeEngine:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self, _db, _symbol):
            return FakeResult()

    class FakeDb:
        def __init__(self, _path):
            pass

        def close(self):
            pass

    monkeypatch.setattr(
        main,
        "load_config",
        lambda _path: _make_config(
            symbols=["ETH", "BTC"], export_trades_csv="trades.csv",
        ),
    )
    monkeypatch.setattr(main, "BacktestEngine", FakeEngine)
    monkeypatch.setattr(main, "Database", FakeDb)

    main.run_backtest()

    assert exported_paths == ["trades-ETH.csv", "trades-BTC.csv"]


def test_run_trading_loop_reconciles_before_losing_weeks_halt(monkeypatch):
    call_order: list[str] = []

    class FakeDb:
        last_instance = None

        def __init__(self, _path):
            self.open_trades = []
            FakeDb.last_instance = self

        def get_open_trades(self, _symbol=None):
            return list(self.open_trades)

        def close(self):
            pass

    class FakeExecutor:
        def __init__(self, _config, _db):
            pass

        def set_leverage(self, _symbol, _leverage):
            return True

    class FakeWsClient:
        def subscribe_mid_prices(self, _symbols):
            pass

        def is_healthy(self):
            return True

        def close(self):
            pass

    class FakeHealthChecker:
        def __init__(self, *_args, **_kwargs):
            pass

        def tick(self, _label):
            pass

    class FakeStateServer:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    def fake_reconcile(_executor, db, _config):
        call_order.append("reconcile")
        db.open_trades.append({"id": 1, "symbol": "ETH", "side": "long"})

    def fake_check_losing_weeks(_db):
        call_order.append("check")
        return False

    def fake_tick(*args, **_kwargs):
        call_order.append("tick")
        assert args[-1] is True
        raise KeyboardInterrupt

    monkeypatch.setattr(main, "load_config", lambda _path: _make_config(mode="live"))
    monkeypatch.setattr(main, "Database", FakeDb)
    monkeypatch.setattr(main, "HyperliquidClient", lambda: object())
    monkeypatch.setattr(main, "DataIngestor", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(main, "SignalEngine", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(main, "RiskManager", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(main, "LiveExecutor", FakeExecutor)
    monkeypatch.setattr(main, "WsClient", FakeWsClient)
    monkeypatch.setattr(main, "HealthChecker", FakeHealthChecker)
    monkeypatch.setattr(main, "DaemonStateServer", FakeStateServer)
    monkeypatch.setattr(main, "setup_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "_init_prediction_clients", lambda _config: {})
    monkeypatch.setattr(main, "_reconcile_positions", fake_reconcile)
    monkeypatch.setattr(main, "_check_losing_weeks", fake_check_losing_weeks)
    monkeypatch.setattr(main, "_tick", fake_tick)
    monkeypatch.setattr(main, "_alert", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "get_socket_path", lambda _db_path: Path("/tmp/perp-bot.sock"))

    main.run_trading_loop()

    assert call_order == ["reconcile", "check", "tick"]


def test_main_passes_force_to_trade_command(monkeypatch):
    called: list[tuple[str | None, bool]] = []

    monkeypatch.setattr(main, "setup_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        main,
        "run_trading_loop",
        lambda config_path=None, force=False: called.append((config_path, force)),
    )
    monkeypatch.setattr(sys, "argv", ["perpbot", "trade", "--config", "config.yaml", "--force"])

    main.main()

    assert called == [("config.yaml", True)]
