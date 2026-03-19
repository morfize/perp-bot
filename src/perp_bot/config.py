"""Centralised configuration — loads YAML config + .env secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv


@dataclass(frozen=True)
class TradingConfig:
    symbols: list[str]
    leverage: int
    capital_usd: float
    margin_usage_limit: float


@dataclass(frozen=True)
class SignalConfig:
    zscore_lookback: int
    zscore_entry_threshold: float
    zscore_exit_threshold: float
    zscore_stop_threshold: float
    bollinger_period: int
    bollinger_std: float
    rsi_period: int
    rsi_overbought: int
    rsi_oversold: int
    adx_period: int
    adx_threshold: int


@dataclass(frozen=True)
class RiskConfig:
    max_loss_per_trade_pct: float
    daily_loss_limit_pct: float
    max_positions: int
    cooldown_seconds: int
    position_timeout_hours: int


@dataclass(frozen=True)
class DataConfig:
    timeframes: list[str]
    primary_timeframe: str
    history_days: int
    db_path: str


@dataclass(frozen=True)
class ExecutionConfig:
    order_type: str
    taker_fallback_seconds: int
    use_server_side_stop: bool


@dataclass(frozen=True)
class PredictionMarketDef:
    slug: str
    source: str  # "polymarket" | "kalshi"
    market_id: str
    category: str  # "war_risk" | "rate_change"
    weight: float


@dataclass(frozen=True)
class PredictionConfig:
    enabled: bool
    poll_interval_minutes: int
    war_risk_threshold: float
    war_risk_crisis_threshold: float
    rate_change_threshold: float
    position_size_reduction: float
    markets: list[PredictionMarketDef]


@dataclass(frozen=True)
class BacktestConfig:
    """All parameters controlling backtest behaviour."""

    maker_fee_rate: float = 0.00015
    taker_fee_rate: float = 0.00045
    slippage_min_pct: float = 0.0001
    slippage_max_pct: float = 0.0005
    entry_delay_candles: int = 1
    cancel_if_signal_gone: bool = True
    train_days: int = 60
    test_days: int = 15
    step_days: int = 15
    export_trades_csv: str | None = None
    seed: int = 42


@dataclass(frozen=True)
class BotConfig:
    trading: TradingConfig
    signals: SignalConfig
    risk: RiskConfig
    data: DataConfig
    execution: ExecutionConfig
    mode: str  # "paper" | "live"
    prediction: PredictionConfig | None = None
    backtest: BacktestConfig | None = None

    # Secrets from env
    hl_private_key: str = field(default="", repr=False)
    hl_wallet_address: str = ""
    discord_webhook_url: str = ""
    telegram_bot_token: str = field(default="", repr=False)
    telegram_chat_id: str = ""


def load_config(config_path: str | Path | None = None) -> BotConfig:
    """Load config from YAML file + environment variables."""
    if config_path is None:
        config_path = Path.cwd() / "config.yaml"
        env_path = Path.cwd() / ".env"
    else:
        config_path = Path(config_path).expanduser().resolve()
        env_path = config_path.parent / ".env"

    load_dotenv(env_path)

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    prediction = None
    if "prediction" in raw:
        pred_raw = raw["prediction"]
        markets = [PredictionMarketDef(**m) for m in pred_raw.get("markets", [])]
        prediction = PredictionConfig(
            enabled=pred_raw.get("enabled", False),
            poll_interval_minutes=pred_raw.get("poll_interval_minutes", 15),
            war_risk_threshold=pred_raw.get("war_risk_threshold", 0.4),
            war_risk_crisis_threshold=pred_raw.get("war_risk_crisis_threshold", 0.7),
            rate_change_threshold=pred_raw.get("rate_change_threshold", 0.3),
            position_size_reduction=pred_raw.get("position_size_reduction", 0.5),
            markets=markets,
        )

    backtest = None
    if "backtest" in raw:
        bt_raw = raw["backtest"]
        backtest = BacktestConfig(
            maker_fee_rate=bt_raw.get("maker_fee_rate", 0.00015),
            taker_fee_rate=bt_raw.get("taker_fee_rate", 0.00045),
            slippage_min_pct=bt_raw.get("slippage_min_pct", 0.0001),
            slippage_max_pct=bt_raw.get("slippage_max_pct", 0.0005),
            entry_delay_candles=bt_raw.get("entry_delay_candles", 1),
            cancel_if_signal_gone=bt_raw.get("cancel_if_signal_gone", True),
            train_days=bt_raw.get("train_days", 60),
            test_days=bt_raw.get("test_days", 15),
            step_days=bt_raw.get("step_days", 15),
            export_trades_csv=bt_raw.get("export_trades_csv"),
            seed=bt_raw.get("seed", 42),
        )

    return BotConfig(
        trading=TradingConfig(**raw["trading"]),
        signals=SignalConfig(**raw["signals"]),
        risk=RiskConfig(**raw["risk"]),
        data=DataConfig(**raw["data"]),
        execution=ExecutionConfig(**raw["execution"]),
        mode=raw.get("mode", "paper"),
        prediction=prediction,
        backtest=backtest,
        hl_private_key=os.getenv("HL_PRIVATE_KEY", ""),
        hl_wallet_address=os.getenv("HL_WALLET_ADDRESS", ""),
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
    )
