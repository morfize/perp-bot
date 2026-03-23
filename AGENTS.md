# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

Hyperliquid perpetual futures mean-reversion trading bot. Designed for small capital (~$670, 3x leverage) day-trading on Hyperliquid's on-chain CLOB DEX. The full design specification is in `hyperliquid-mean-reversion-bot-design.md` (Japanese).

## Tech Stack

- **Language**: Python 3.12+
- **Exchange SDK**: `hyperliquid-python-sdk` + `websockets`
- **Data store**: SQLite (initial) -> DuckDB (analysis)
- **Indicators**: Self-implemented with pandas/numpy (no ta-lib dependency — intentional for full logic transparency)
- **Deployment**: GCP Compute Engine (e2-small), systemd service
- **Alerts**: Discord Webhook or Telegram Bot API
- **Logging**: Python `logging` module, JSON-structured

## Architecture (Planned)

Seven modules:

| Module | Responsibility |
|---|---|
| **Data Layer** | REST + WebSocket data ingestion (`WsClient` for real-time mid prices, candles, BBO), SQLite persistence, OHLCV candles (1m/5m/15m/1h), prediction market snapshots |
| **Signal Engine** | Z-Score, Bollinger Bands, RSI, ADX calculations. Entry signal = all 4 indicators agree + ADX regime filter. Prediction market regime modifiers |
| **Prediction Layer** | Polymarket (war risk) + Kalshi (Fed rate) polling, regime classification (NORMAL/HIGH_RISK/DOVISH/HAWKISH/CRISIS), funding side preference |
| **Execution Layer** | `PaperExecutor` (DB-only) + `LiveExecutor` (real orders via Exchange API). Limit-first with taker-fallback, atomic server-side SL attachment, crash recovery via exchange position query |
| **Risk Manager** | Stop-loss (3% per trade), daily loss limit (8%), max 1 position, 30min cooldown after SL, 50% margin usage cap, regime-based position sizing |
| **Backtester** | Fee model (Maker 0.015%/Taker 0.045%), slippage sim, walk-forward analysis for overfitting prevention |
| **Infrastructure** | Structured logging (stdout + file), alert dispatch, health checks |
| **IPC Layer** | Unix socket daemon state server (`DaemonStateServer`), client (`DaemonClient`), thread-safe `DaemonState` container. Socket carries volatile state; SQLite WAL provides concurrent read access for persistent data |
| **TUI Dashboard** | Textual-based terminal UI (`PerpBotApp`). Attaches/detaches freely from daemon. Panels: header, position, signals, risk, trades, log. Key bindings: pause/resume/emergency close |

## Key Trading Logic

- **Entry**: Z-score > ±2.0 AND RSI overbought/oversold AND price outside Bollinger Band AND ADX < 25 (range-bound regime)
- **Exit (profit)**: Z-score returns to ±0.3 (mean reversion complete)
- **Exit (stop)**: Z-score exceeds ±3.0 against position, OR 3% capital loss, OR 24h timeout
- **Order strategy**: Limit orders (Maker) preferred; fallback to market (Taker) if unfilled. Server-side stop orders on Hyperliquid for redundancy.

## Development Phases

1. **Data Pipeline** — OHLCV + funding rate ingestion into SQLite, WebSocket real-time feed
2. **Signal Engine + Backtester** — Indicator modules, backtest engine with realistic cost model, walk-forward overfitting checks
3. **Risk Management + Paper Trading** — Risk modules, paper-trade mode, validate against backtest results
4. **Live Trading** — Signed exchange API, GCP deploy, staged rollout (start with 20% of capital)

## Hyperliquid API Notes

- Gas-free: no gas costs for order placement/modification/cancellation
- Funding rate: settled hourly (1/8 of 8h rate each hour)
- REST info endpoint: `POST /info` with varying `type` field (metaAndAssetCtxs, candleSnapshot, fundingHistory, clearinghouseState)
- Exchange endpoint: `POST /exchange` (signed)
- Known risk: API server overload incidents have caused 30min+ downtime — always pair bot-side SL with server-side stop orders

## Commands

```bash
uv sync                          # Install dependencies
uv run pytest tests/ -v          # Run all tests
uv run pytest --cov=src/perp_bot --cov-report=term-missing
uv run pytest tests/test_indicators.py -v  # Run a single test file
uv run ruff check src/ tests/    # Lint
perpbot backfill               # Backfill historical data from Hyperliquid
perpbot trade                  # Start trading loop (paper mode by default, set mode: "live" in config.yaml for real orders)
perpbot backfill-predictions   # Fetch current prediction market snapshots
perpbot backtest               # Run backtest over historical data
perpbot walkforward            # Walk-forward overfitting analysis
perpbot sensitivity            # Parameter sensitivity sweep
perpbot screen                 # Screen symbols by Hurst exponent for mean-reversion fit
perpbot review --weeks 1       # Weekly performance report
perpbot compare --days 7       # Compare paper trades vs backtest over same period
perpbot tui                    # Launch TUI dashboard (attach to running daemon)
perpbot status                 # One-shot daemon state query (JSON output)
```

## Conventions

- Design doc and code comments may be in Japanese
- All indicator calculations are self-implemented (do not introduce ta-lib or similar libraries)
- Backtest must include: trading fees, slippage, funding costs, and execution delay simulation
