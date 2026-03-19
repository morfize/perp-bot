# perpbot

`perpbot` is a Python CLI for running a Hyperliquid mean-reversion perpetual futures bot, backfilling market data, backtesting, screening symbols, and monitoring a running daemon.

## Release status

- Python package with console entry point: `perpbot`
- GitHub Actions CI for lint, tests, and build validation
- GitHub tag-based release workflow that uploads wheel and source distribution artifacts

## Install

Local development install:

```bash
uv sync
uv run perpbot --help
```

Install directly from GitHub:

```bash
uv tool install git+https://github.com/morfize/perp-bot.git
perpbot --help
```

Or with `pip`:

```bash
pip install "git+https://github.com/morfize/perp-bot.git"
perpbot --help
```

Install a tagged release:

```bash
uv tool install git+https://github.com/morfize/perp-bot.git@v0.1.0
```

## Configuration

By default, `perpbot` looks for these files in your current working directory:

- `config.yaml`
- `.env`

You can also point the CLI at a specific config file:

```bash
perpbot trade --config /path/to/config.yaml
```

When `--config` is provided, `perpbot` also loads `.env` from that config file's directory.

The repository includes:

- `config.yaml` as a starting config
- `.env.example` as an environment variable template
- `CHANGELOG.md` for release notes

## CLI usage

```bash
perpbot --help
perpbot trade
perpbot trade --force
perpbot backfill
perpbot backfill-predictions
perpbot backtest
perpbot walkforward
perpbot sensitivity
perpbot screen
perpbot review --weeks 1
perpbot compare --days 7
perpbot tui
perpbot status
```
