# Configuration Reference

This document describes the runtime configuration surfaces used by `perp-bot`.

## Config Sources

`perp-bot` reads configuration from two places:

1. `config.yaml`
2. `.env`

Resolution behavior:

- default mode: both files are resolved from the current working directory
- explicit mode: when `--config /path/to/config.yaml` is provided, `.env` is loaded from that config file's directory

This behavior is covered by tests in `tests/test_config.py`.

## YAML Structure

The sample [`config.yaml`](../config.yaml) contains these top-level sections.

## `trading`

Controls the market universe and top-level position sizing inputs.

Fields:

- `symbols`: list of symbols to manage
- `leverage`: exchange leverage target in live mode
- `capital_usd`: modeled capital base
- `margin_usage_limit`: fraction of capital allowed to be committed as margin

Notes:

- The live trading loop iterates over `symbols` sequentially.
- `compute_position_size()` uses `capital_usd * margin_usage_limit * leverage`.

## `signals`

Controls the core mean-reversion thresholds.

Fields:

- `zscore_lookback`
- `zscore_entry_threshold`
- `zscore_exit_threshold`
- `zscore_stop_threshold`
- `bollinger_period`
- `bollinger_std`
- `rsi_period`
- `rsi_overbought`
- `rsi_oversold`
- `adx_period`
- `adx_threshold`

These values feed `SignalEngine`.

## `risk`

Controls hard trading constraints.

Fields:

- `max_loss_per_trade_pct`
- `daily_loss_limit_pct`
- `max_positions`
- `cooldown_seconds`
- `position_timeout_hours`

These values feed `RiskManager`.

## `data`

Controls local storage and the candle universe.

Fields:

- `timeframes`: all intervals to store during backfill and incremental updates
- `primary_timeframe`: interval used by the strategy and backtests
- `history_days`: lookback range for initial backfill
- `db_path`: SQLite database path

Notes:

- The database path also determines where the IPC socket and daemon log file live.
- If you want multiple independent instances, give each one its own `db_path`.

## `execution`

Controls entry order behavior.

Fields:

- `order_type`
- `taker_fallback_seconds`
- `use_server_side_stop`

Current implementation details:

- the live executor uses a limit-first strategy
- after `taker_fallback_seconds`, it may fall back to an IOC taker order
- server-side stop-loss attachment is attempted for live positions

## `prediction`

Controls optional prediction market regime input.

Fields:

- `enabled`
- `poll_interval_minutes`
- `war_risk_threshold`
- `war_risk_crisis_threshold`
- `rate_change_threshold`
- `position_size_reduction`
- `markets`

Each market entry includes:

- `slug`
- `source`
- `market_id`
- `category`
- `weight`

Notes:

- `enabled: true` is not enough on its own; the configured market identifiers must also be valid.
- When no prediction data exists yet, the bot falls back to `NORMAL` regime behavior.

## `backtest`

Controls simulation behavior.

Fields:

- `maker_fee_rate`
- `taker_fee_rate`
- `slippage_min_pct`
- `slippage_max_pct`
- `entry_delay_candles`
- `cancel_if_signal_gone`
- `train_days`
- `test_days`
- `step_days`
- `seed`
- `export_trades_csv` if you add it explicitly

Notes:

- If the `backtest` section is missing, code falls back to `BacktestConfig()` defaults.
- Multi-symbol export paths are automatically disambiguated by symbol name.

## `mode`

Allowed values:

- `paper`
- `live`

Behavior:

- `paper`: uses `PaperExecutor`, writes simulated trades to SQLite
- `live`: uses `LiveExecutor`, requires exchange credentials, sets leverage, and reconciles positions on startup

## Environment Variables

Use [`.env.example`](../.env.example) as the starting point.

### Required for live trading

- `HL_PRIVATE_KEY`

### Optional

- `HL_WALLET_ADDRESS`
- `DISCORD_WEBHOOK_URL`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Notes:

- `HL_WALLET_ADDRESS` is optional because the live executor can derive the address from the private key.
- Alert variables are optional and can be combined.

## Runtime Artifacts Derived From `db_path`

If `db_path` is `./perp_bot.db`, the daemon also uses:

- socket path: `./perp-bot.sock`
- rotating log file: `./perp-bot.log`

This is why `status` and `tui` can locate a daemon with only the config file.

## Recommended Local Config Patterns

### Paper development

- keep `mode: "paper"`
- use a local `db_path` such as `./perp_bot.db`
- start with a single symbol
- backfill before running `trade`, `backtest`, or `screen`

### Separate environments

Use separate directories or config files for:

- local development
- paper trading
- live trading

That keeps databases, socket files, logs, and secrets isolated.

### Live safety

Before switching to live mode:

1. verify the symbol list
2. verify leverage and risk limits
3. confirm alert delivery
4. verify `HL_PRIVATE_KEY` and wallet address
5. test `status` and `tui` against a paper daemon first
