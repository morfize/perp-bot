# perp-bot

`perp-bot` is a Python CLI for running a Hyperliquid perpetual futures mean-reversion bot, collecting market data, backtesting the strategy, and monitoring a live daemon from a terminal UI.

This repository is structured as an installable package with a `perpbot` console command, SQLite-backed local state, and separate modules for data ingestion, signals, execution, risk, reporting, IPC, and the Textual TUI.

## Status

- Package name: `perp-bot`
- CLI entry point: `perpbot`
- Python requirement: `>=3.12`
- Current release: `0.1.0`
- Maturity: alpha

## What It Does

- Backfills OHLCV candles and funding history from Hyperliquid into SQLite
- Polls prediction markets and stores regime signals alongside market data
- Runs a paper or live trading loop with risk controls and health checks
- Exposes daemon state over a Unix socket for `status` and `tui`
- Backtests the strategy with fees, slippage, funding, and execution delay
- Produces comparison and weekly review reports from stored trades

## Quick Start

### 1. Install the CLI

```bash
curl -fsSL https://raw.githubusercontent.com/morfize/perp-bot/main/scripts/install.sh | sh
```

This installs a prebuilt standalone binary to `~/.local/bin/perpbot`.

### 2. Verify the command

```bash
perpbot --help
```

### 3. Create local secrets

```bash
cp .env.example .env
```

For paper trading, you can leave the Hyperliquid key empty.

### 4. Review the sample config

The repository ships with a starter [`config.yaml`](config.yaml). By default, `perpbot` reads:

- `config.yaml` from the current working directory
- `.env` from the current working directory

If you pass `--config /path/to/config.yaml`, the CLI also loads `.env` from that config file's directory.

### 5. Backfill data

```bash
perpbot backfill
```

### 6. Run a backtest

```bash
perpbot backtest
```

### 7. Start paper trading

```bash
perpbot trade
```

In another terminal, you can inspect the daemon:

```bash
perpbot status
perpbot tui
```

## Installation

### One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/morfize/perp-bot/main/scripts/install.sh | sh
```

This downloads the latest GitHub release binary for your OS and installs it to `~/.local/bin/perpbot`.

### Install a tagged release

```bash
curl -fsSL https://raw.githubusercontent.com/morfize/perp-bot/main/scripts/install.sh | env PERPBOT_VERSION=v0.1.0 sh
```

### Local development install

```bash
uv sync --group dev
source .venv/bin/activate
perpbot --help
```

### Compatibility shim

Repo-local execution still works, but this is not the primary install path:

```bash
./perpbot --help
./main.py --help
python3 main.py --help
```

## Configuration

### YAML config

The main config file is [`config.yaml`](config.yaml). Top-level sections are:

- `trading`
- `signals`
- `risk`
- `data`
- `execution`
- `prediction`
- `backtest`
- `mode`

Important defaults in the sample config:

- symbol universe starts with `["ETH"]`
- primary timeframe is `15m`
- database path is `perp_bot.db`
- mode defaults to `paper`

### Environment variables

Secrets and alert integrations are read from `.env`:

- `HL_PRIVATE_KEY`
- `HL_WALLET_ADDRESS`
- `DISCORD_WEBHOOK_URL`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Use [`.env.example`](.env.example) as the starting template.

### Live mode

To enable live execution:

1. Set `mode: "live"` in `config.yaml`
2. Provide `HL_PRIVATE_KEY` in `.env`
3. Optionally provide `HL_WALLET_ADDRESS` if you do not want to rely on key-derived address detection

Live mode sets leverage on startup, reconciles exchange positions against the local database, and attempts to attach a server-side stop-loss to each new position.

## CLI Commands

| Command | Purpose |
| --- | --- |
| `perpbot backfill` | Backfill historical candles and funding into SQLite |
| `perpbot trade` | Start the main paper/live trading loop |
| `perpbot trade --force` | Override the consecutive losing-weeks startup halt |
| `perpbot backfill-predictions` | Fetch current prediction market snapshots |
| `perpbot backtest` | Run a full backtest over stored market data |
| `perpbot walkforward` | Run walk-forward analysis |
| `perpbot sensitivity` | Run parameter sensitivity analysis |
| `perpbot screen` | Screen markets for mean-reversion suitability |
| `perpbot review --weeks 1` | Generate a weekly performance review |
| `perpbot compare --days 7` | Compare paper trading against backtest results |
| `perpbot status` | Print daemon state as JSON |
| `perpbot tui` | Launch the Textual monitoring dashboard |

Run the global help for all flags:

```bash
perpbot --help
```

## Architecture Summary

The main runtime pieces are:

- `perp_bot.data`: Hyperliquid REST/WebSocket access and SQLite persistence
- `perp_bot.signals`: indicator calculations and signal evaluation
- `perp_bot.risk`: pre-entry checks, stop-loss logic, cooldowns, and sizing
- `perp_bot.execution`: paper executor and Hyperliquid live executor
- `perp_bot.backtest`: historical simulation, cost model, metrics, walk-forward, sensitivity
- `perp_bot.ipc`: Unix socket server/client and daemon state model
- `perp_bot.tui`: Textual dashboard for monitoring and emergency actions
- `perp_bot.reporting`: weekly reports and paper-vs-backtest comparison
- `perp_bot.infra`: logging, alerts, and health checks

At a high level:

1. `backfill` stores candles and funding in SQLite.
2. `trade` updates candles, computes indicators, checks risk, then opens/closes positions.
3. The trading daemon publishes volatile runtime state over a Unix socket.
4. `status` and `tui` attach to that socket while reading persistent trade data from SQLite.
5. `backtest` reuses the same signal and risk logic against historical data.

## Repository Layout

```text
.
├── src/perp_bot/
│   ├── backtest/
│   ├── data/
│   ├── execution/
│   ├── infra/
│   ├── ipc/
│   ├── reporting/
│   ├── risk/
│   ├── signals/
│   └── tui/
├── tests/
├── deploy/
├── docs/
├── config.yaml
├── .env.example
└── pyproject.toml
```

## Developer Docs

- [Documentation index](docs/README.md)
- [Architecture](docs/architecture.md)
- [Configuration reference](docs/configuration.md)
- [Development workflow](docs/development.md)
- [Operations and deployment](docs/operations.md)

## Development

### Common commands

```bash
uv sync --group dev
uv run ruff check src tests
uv run pytest
uv build
./scripts/build-release-archive.sh perpbot-macos-arm64.tar.gz
```

### Test matrix

GitHub Actions runs:

- lint on `src` and `tests`
- test suite on Python `3.12` and `3.13`
- Python package build validation
- standalone binary build validation

## Deployment

The repository includes:

- [`deploy/deploy.sh`](deploy/deploy.sh) for pushing the repo to a GCP Compute Engine instance
- [`deploy/perp-bot.service`](deploy/perp-bot.service) for systemd

The service expects the app at `/opt/perp-bot` and starts:

```bash
/opt/perp-bot/.venv/bin/perpbot trade
```

## Safety Notes

- This is trading software. Review the code, config, and exchange behavior before enabling live mode.
- The project includes capital-based stop-losses, daily loss limits, cooldowns, and a losing-weeks startup halt, but those controls do not eliminate market, exchange, or software risk.
- The software is not financial advice.
