# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project follows Semantic Versioning.

## [0.1.0] - 2026-03-19

### Added

- Packaged CLI entry point as `perpbot` with GitHub-installable build artifacts.
- Config loading that works from the current working directory or an explicit `--config` path.
- Public package metadata, README install instructions, and release automation scaffolding.

### Fixed

- Losing-weeks halt now reconciles live positions before halting new entries.
- Live stop-loss now uses actual trade notional instead of recomputed regime size.
- Paper-vs-backtest reporting now excludes live trades.
- Symbol screening now uses the latest candle window.
- Live `OPEN` alerts are only emitted after a successful entry.
- Multi-symbol backtest CSV exports no longer overwrite each other.
