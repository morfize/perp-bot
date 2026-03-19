"""Tests for config loading behavior when installed as a CLI."""

from __future__ import annotations

from pathlib import Path

from perp_bot.config import load_config

CONFIG_YAML = """\
trading:
  symbols: ["ETH"]
  leverage: 3
  capital_usd: 670.0
  margin_usage_limit: 0.5
signals:
  zscore_lookback: 20
  zscore_entry_threshold: 2.0
  zscore_exit_threshold: 0.3
  zscore_stop_threshold: 3.0
  bollinger_period: 20
  bollinger_std: 2.0
  rsi_period: 14
  rsi_overbought: 70
  rsi_oversold: 30
  adx_period: 14
  adx_threshold: 25
risk:
  max_loss_per_trade_pct: 0.03
  daily_loss_limit_pct: 0.08
  max_positions: 1
  cooldown_seconds: 1800
  position_timeout_hours: 24
data:
  timeframes: ["15m"]
  primary_timeframe: "15m"
  history_days: 90
  db_path: ":memory:"
execution:
  order_type: "limit"
  taker_fallback_seconds: 30
  use_server_side_stop: true
mode: "paper"
"""


def test_load_config_defaults_to_current_working_directory(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    (tmp_path / "config.yaml").write_text(CONFIG_YAML)
    (tmp_path / ".env").write_text("DISCORD_WEBHOOK_URL=https://example.test/hook\n")

    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.data.db_path == ":memory:"
    assert config.discord_webhook_url == "https://example.test/hook"


def test_load_config_uses_env_next_to_explicit_config(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    config_dir = tmp_path / "instance"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(CONFIG_YAML)
    (config_dir / ".env").write_text("TELEGRAM_CHAT_ID=12345\n")

    monkeypatch.chdir(tmp_path)

    config = load_config(config_dir / "config.yaml")

    assert config.telegram_chat_id == "12345"
