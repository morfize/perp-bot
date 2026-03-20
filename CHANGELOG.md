# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project follows Semantic Versioning.

## [Unreleased]

## [0.1.3] - 2026-03-20

### Added

- One-line installer script that downloads a prebuilt standalone binary from GitHub Releases.
- Release packaging script for OS-specific standalone `perpbot` binaries.

### Changed

- README now treats bare `perpbot` usage as the primary CLI flow instead of `uv run perpbot`.
- Release automation now targets downloadable standalone binaries in addition to Python package artifacts.

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
