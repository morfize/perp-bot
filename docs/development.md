# Development Workflow

This document is for contributors working on the codebase locally.

## Prerequisites

- Python `3.12+`
- `uv`

Optional but useful:

- SQLite tooling such as `sqlite3`
- a terminal that handles Textual applications well

## Local Setup

### Clone and install

```bash
git clone https://github.com/morfize/perp-bot.git
cd perp-bot
uv sync --group dev
cp .env.example .env
```

### Verify the CLI

```bash
source .venv/bin/activate
perpbot --help
```

### Bootstrap a local database

```bash
perpbot backfill
```

Without a local database, many commands will not have useful data to work with.

## Common Commands

### Run tests

```bash
uv run pytest
```

### Run a focused test

```bash
uv run pytest tests/test_config.py -v
```

### Lint

```bash
uv run ruff check src tests
```

### Build the package

```bash
uv build
```

### Build a standalone binary archive

```bash
./scripts/build-release-archive.sh perpbot-macos-arm64.tar.gz
```

### Run the bot locally

```bash
perpbot trade
perpbot status
perpbot tui
```

## Project Conventions

### Packaging

- the package source lives under `src/perp_bot`
- the console entry point is `perpbot = "perp_bot.cli:main"`
- `main.py` is only a compatibility shim for direct script-style execution

### Testing

- the test suite lives under `tests/`
- pytest is configured in `pyproject.toml`
- GitHub Actions runs tests on Python `3.12` and `3.13`

### Linting

- Ruff is configured in `pyproject.toml`
- current target version is Python `3.12`
- line length is `100`

### Implementation Style Already Established In The Repo

- dataclasses for typed config models
- module-local helper functions where orchestration would otherwise become noisy
- pandas/numpy for indicators and simulation logic
- SQLite as the default local system of record
- JSON structured logs rather than free-form text logs

## Typical Workflows

### Change Strategy or Signal Behavior

Main files to inspect:

- `src/perp_bot/signals/indicators.py`
- `src/perp_bot/signals/engine.py`
- `src/perp_bot/risk/manager.py`
- `src/perp_bot/backtest/engine.py`

Recommended loop:

1. update signal or risk logic
2. add or update tests
3. run `uv run pytest`
4. run `perpbot backtest`
5. inspect downstream report changes

### Change Execution Behavior

Main files to inspect:

- `src/perp_bot/execution/executor.py`
- `src/perp_bot/execution/live_executor.py`
- `src/perp_bot/cli.py`

Be careful to preserve:

- trade persistence semantics
- slippage tracking
- server-side stop-loss behavior
- startup reconciliation

### Change Data Ingestion or Schema

Main files to inspect:

- `src/perp_bot/data/client.py`
- `src/perp_bot/data/ws_client.py`
- `src/perp_bot/data/ingest.py`
- `src/perp_bot/data/db.py`

If you change schema or persistence behavior, also inspect:

- `tui`
- reporting code
- backtest loaders
- tests that rely on the DB API

### Add a New CLI Command

The current CLI is centralized in `src/perp_bot/cli.py`.

Typical steps:

1. implement a `run_<name>()` function
2. add the command name to `argparse` choices
3. wire the function into `main()`
4. add tests that cover dispatch or behavior
5. document the command in `README.md`

## Working With the TUI

The TUI is attach-only. It does not run the trading loop itself.

Important implications:

- start `perpbot trade` first
- then run `perpbot tui`
- trade history comes from SQLite
- volatile runtime state comes from the IPC socket
- the log panel tails the rotating daemon log file

## Packaging and Releases

Local validation:

```bash
uv build
./scripts/build-release-archive.sh perpbot-macos-arm64.tar.gz
```

GitHub Actions:

- `.github/workflows/ci.yml` runs lint, tests, and build validation
- `.github/workflows/release.yml` builds Python artifacts plus standalone release binaries when a `v*` tag is pushed

Before cutting a release:

1. update code and tests
2. update `CHANGELOG.md`
3. verify `README.md` if commands or config changed
4. run lint, tests, and build locally

## Practical Tips

- keep paper and live configs separate
- keep one database per runtime instance
- prefer reusing the existing typed config model instead of passing raw dicts through new code
- keep live and backtest semantics aligned when changing strategy behavior
- if you add new operator-facing behavior, document it in `README.md` and `docs/operations.md`
