# Operations and Deployment

This document covers day-to-day operation of `perp-bot`, from local paper trading to systemd deployment.

## Runtime Modes

### Paper Mode

Default mode. Uses `PaperExecutor` and records simulated trades in SQLite.

Recommended for:

- local development
- validating config changes
- checking TUI behavior
- smoke testing a new release

### Live Mode

Uses `LiveExecutor` and places real orders on Hyperliquid.

Additional startup behavior:

- requires `HL_PRIVATE_KEY`
- sets leverage for configured symbols
- reconciles exchange positions with local DB state
- attempts to attach server-side stop-loss protection

## Paper Trading Runbook

### Start a fresh local session

```bash
cp .env.example .env
uv sync --group dev
source .venv/bin/activate
perpbot backfill
perpbot trade
```

In another terminal:

```bash
perpbot status
perpbot tui
```

### What to expect

- SQLite DB at the configured `db_path`
- Unix socket next to that DB
- rotating log file next to that DB
- open and closed trades recorded in SQLite

## Live Trading Checklist

Before enabling live mode:

1. confirm `mode: "live"` intentionally
2. verify symbol list and leverage
3. verify `capital_usd`, margin cap, and stop-loss settings
4. verify `HL_PRIVATE_KEY`
5. verify alert endpoints if you depend on Discord or Telegram
6. run a paper session with the same config shape first
7. verify `perpbot status` and `perpbot tui` against the daemon

After startup, confirm:

- leverage setup succeeded
- position reconciliation logs look correct
- WebSocket feed is healthy
- daemon status returns valid JSON

## Safety Controls Worth Monitoring

Built-in controls include:

- per-trade capital stop loss
- daily loss limit
- cooldown after stop loss
- 24-hour position timeout
- max position count
- crisis-regime entry block
- three-consecutive-losing-weeks startup halt

If `trade` refuses to start because of the losing-weeks halt, you can override it with:

```bash
perpbot trade --force
```

Use that only intentionally.

## Daemon Interfaces

### JSON status

```bash
perpbot status
```

This reads the daemon state over the Unix socket and prints JSON.

### TUI

```bash
perpbot tui
```

Key bindings include:

- `p`: pause the daemon
- `r`: resume the daemon
- `e`: emergency close flow
- `q`: quit the TUI

The TUI is safe to attach and detach repeatedly.

## Logging

The app uses JSON structured logs.

Outputs:

- stdout
- rotating file log when the trading daemon starts the IPC/TUI stack

Log file location:

- `<db directory>/perp-bot.log`

Fields include:

- timestamp
- level
- logger name
- message
- exception, when present

## Deployment

The repository includes:

- `deploy/deploy.sh`
- `deploy/perp-bot.service`

The deploy script assumes:

- a GCP Compute Engine instance
- `gcloud` authenticated locally
- SSH access to the instance
- `uv` installed on the remote machine

### Deployment flow

```bash
./deploy/deploy.sh <instance-name> [zone]
```

The script:

1. copies the repo to `/opt/perp-bot`
2. runs `uv sync --frozen` remotely
3. installs the bundled systemd service
4. restarts the service
5. prints service status

### systemd expectations

The service file currently expects:

- app directory: `/opt/perp-bot`
- environment file: `/opt/perp-bot/.env`
- service user: `perp-bot`
- executable: `/opt/perp-bot/.venv/bin/perpbot trade`

If your target environment differs, edit `deploy/perp-bot.service` before deployment.

## Useful Service Commands

On the host:

```bash
sudo systemctl status perp-bot --no-pager
sudo journalctl -u perp-bot -n 200 --no-pager
sudo journalctl -u perp-bot -f
sudo systemctl restart perp-bot
sudo systemctl stop perp-bot
```

## Troubleshooting

### `perpbot status` Says The Daemon Is Not Running

Check:

- whether `perpbot trade` is actually running
- whether the socket path matches the configured `db_path`
- whether the daemon has permissions to create files in the DB directory

### `tui` Opens But Shows No Useful State

Check:

- the daemon is already running
- the TUI is pointed at the same config file
- the daemon has produced at least one tick
- the log file exists next to the socket

### Trade Loop Warns About Missing Data

Check:

- `perpbot backfill` has been run
- the configured `primary_timeframe` exists in the DB
- `history_days` and timeframe are sufficient to satisfy indicator warmup

### Live Mode Exits At Startup

Check:

- `HL_PRIVATE_KEY`
- exchange connectivity
- leverage configuration for the selected symbols
- whether reconciliation surfaced inconsistent local state

### WebSocket Is Repeatedly Marked Stale

Check:

- network stability on the host
- whether the Hyperliquid WebSocket feed is reachable
- daemon logs around reconnect attempts

The bot will try to reconnect automatically, but repeated staleness should be treated as an operational issue.
