# Architecture

This document describes how the main parts of `perp-bot` fit together and which source files own each responsibility.

## System Overview

`perp-bot` has two main execution styles:

- one-shot commands such as `backfill`, `backtest`, `review`, and `compare`
- the long-running `trade` daemon, which can be observed through `status` and `tui`

The trading daemon uses:

- SQLite for persistent historical data, trades, and small pieces of durable bot state
- a Unix socket for volatile runtime state and control commands
- JSON logs to stdout and an optional rotating file for the TUI log pane

## Module Map

### `perp_bot.cli`

Owns CLI argument parsing and command dispatch. It is also the composition root for the trading loop, backtests, and reporting commands.

Key responsibilities:

- load config
- instantiate the database, clients, executors, and risk/signal engines
- run the main trading tick loop
- expose `status` and `tui`

### `perp_bot.config`

Defines the typed dataclass config model and loads:

- `config.yaml`
- `.env` in the working directory, or next to the explicit `--config` path

The config loader is a central dependency for almost every command.

### `perp_bot.data`

Owns exchange and prediction market data access.

Important files:

- `client.py`: Hyperliquid REST wrapper
- `ws_client.py`: WebSocket wrapper with mid-price cache and reconnect logic
- `db.py`: SQLite schema and persistence API
- `ingest.py`: orchestration for full backfill and incremental updates
- `prediction_client.py`: prediction market adapters

### `perp_bot.signals`

Owns indicator calculation and signal evaluation.

Important files:

- `indicators.py`: Z-score, Bollinger Bands, RSI, ADX, Hurst exponent
- `engine.py`: converts indicator state into `LONG`, `SHORT`, `CLOSE`, or `NONE`
- `prediction.py`: prediction-market-derived regime classification

The backtest and live trading loop intentionally reuse this logic.

### `perp_bot.risk`

Owns runtime risk checks and position sizing.

Important responsibilities:

- max open positions
- daily realized loss limit
- cooldown after stop loss
- position timeout
- regime-adjusted position sizing

The backtest has a separate adapter in `backtest/risk_adapter.py` so the simulation can mirror live behavior.

### `perp_bot.execution`

Owns order execution.

- `executor.py`: abstract interface plus `PaperExecutor`
- `live_executor.py`: Hyperliquid order placement, leverage setup, stop-loss attachment, slippage tracking, and exchange reconciliation helpers

This separation keeps the trading loop mostly mode-agnostic.

### `perp_bot.backtest`

Owns historical simulation and analysis.

Important files:

- `engine.py`: row-by-row backtest driver
- `executor.py`: simulated trade executor
- `cost_model.py`: fees, slippage, funding
- `metrics.py`: performance metrics
- `walk_forward.py`: train/test window analysis
- `sensitivity.py`: parameter sweeps
- `results.py`: backtest result model and summaries

### `perp_bot.ipc`

Owns daemon state sharing and control.

- `state.py`: thread-safe daemon state container
- `server.py`: Unix socket state server
- `client.py`: socket client used by `status` and TUI actions
- `protocol.py`: command names and socket path helper

### `perp_bot.tui`

Owns the Textual dashboard.

The TUI attaches to a running daemon, reads volatile state over IPC, reads trades from SQLite in read-only mode, and tails the rotating log file.

### `perp_bot.reporting`

Owns derived reporting on top of stored trades and backtests.

- `weekly.py`: weekly performance review
- `compare.py`: paper-vs-backtest comparison

### `perp_bot.infra`

Shared operational utilities:

- `logging.py`: JSON logging
- `health.py`: periodic heartbeat alerts
- `alerts.py`: Discord and Telegram integration

## Runtime Flows

### 1. Backfill Flow

Command: `perpbot backfill`

1. Load config and open the database.
2. Create `HyperliquidClient` and `DataIngestor`.
3. For each configured symbol, backfill candles for every configured timeframe.
4. Backfill funding history.
5. Persist rows into SQLite with `INSERT OR IGNORE`.

### 2. Trading Flow

Command: `perpbot trade`

1. Load config and open the database.
2. Create the data ingestor, signal engine, risk manager, and executor.
3. In live mode, validate `HL_PRIVATE_KEY`, set exchange leverage per symbol, and reconcile exchange state with local DB state.
4. Start WebSocket mid-price subscription.
5. Start the IPC server for `status` and `tui`.
6. Enter the candle-aligned main loop.

Each trading tick performs roughly this sequence:

1. Reconnect WebSocket if the price feed is stale.
2. Poll prediction markets on schedule and compute regime state.
3. Incrementally update candles for each symbol.
4. Load the latest candle window from SQLite.
5. Compute indicators.
6. Check position-level exits first, including capital stop loss and position timeout.
7. Evaluate signal-engine exits or entries.
8. Run pre-entry risk checks before opening anything new.
9. Update daemon state for the IPC/TUI layer.
10. Emit heartbeat alerts and sleep until the next candle boundary.

### 3. Backtest Flow

Command: `perpbot backtest`

1. Load historical candles and funding history from SQLite.
2. Compute indicators once across the full frame.
3. Simulate the strategy row by row after the warmup window.
4. Apply fee, slippage, funding, entry-delay, and cancel-if-signal-gone rules.
5. Produce a `BacktestResult` with trades, equity curve, and metrics.

The backtest deliberately shares signal logic with live trading to reduce divergence.

### 4. Monitoring Flow

Commands: `perpbot status`, `perpbot tui`

- `status` is a simple socket client that prints the daemon snapshot as JSON.
- `tui` attaches to the same socket, reads open and recent trades directly from SQLite, and tails the daemon log file.

This split keeps the socket payload small while letting SQLite remain the source of truth for historical trade data.

## Persistence Model

The SQLite database currently contains these tables:

- `candles`
- `funding_rates`
- `prediction_snapshots`
- `trades`
- `bot_state`

Notable usage:

- `candles` and `funding_rates` power both live evaluation and backtests
- `prediction_snapshots` decouples regime polling from signal evaluation
- `trades` is the primary audit trail for paper and live execution
- `bot_state` stores small durable flags such as stop-loss cooldown timestamps

## Runtime Artifacts

Given `data.db_path`, the process derives:

- SQLite DB: configured directly, for example `perp_bot.db`
- Unix socket: `<db directory>/perp-bot.sock`
- rotating log file for TUI: `<db directory>/perp-bot.log`

Keeping these together makes it possible for `status` and `tui` to locate the daemon from the same config.

## Safety Controls

Important runtime controls implemented in code:

- max positions
- daily realized loss cap
- cooldown after stop loss
- 24-hour position timeout
- position-size reduction in high-risk prediction regimes
- new-entry block in crisis regimes
- startup halt after three consecutive losing weeks unless `--force` is used
- live position reconciliation before the losing-weeks halt is enforced

## Extension Points

Common places to extend the system:

- add a new report in `perp_bot.reporting`
- add a new CLI command in `perp_bot.cli`
- add new indicators in `perp_bot.signals.indicators`
- add a new prediction source behind `prediction_client.py`
- add new TUI widgets under `perp_bot.tui.widgets`

When extending behavior, keep live and backtest logic aligned wherever possible. The current design intentionally shares signal semantics across both modes.
