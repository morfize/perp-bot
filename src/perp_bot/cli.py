"""Main entry point — data backfill and trading loop."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import pandas as pd

from perp_bot.backtest.config import BacktestConfig
from perp_bot.backtest.engine import BacktestEngine
from perp_bot.backtest.sensitivity import ParameterSensitivityAnalyzer
from perp_bot.backtest.walk_forward import WalkForwardRunner
from perp_bot.config import load_config
from perp_bot.data.client import INTERVAL_MS, HyperliquidClient
from perp_bot.data.db import Database
from perp_bot.data.ingest import DataIngestor
from perp_bot.data.prediction_client import KalshiClient, PolymarketClient
from perp_bot.data.ws_client import WsClient
from perp_bot.execution.executor import Executor, PaperExecutor
from perp_bot.execution.live_executor import LiveExecutor
from perp_bot.infra.alerts import send_discord_alert, send_telegram_alert
from perp_bot.infra.health import HealthChecker
from perp_bot.infra.logging import setup_logging
from perp_bot.ipc.protocol import get_socket_path
from perp_bot.ipc.server import DaemonStateServer
from perp_bot.ipc.state import DaemonState
from perp_bot.risk.manager import RiskManager
from perp_bot.signals.engine import Signal, SignalEngine
from perp_bot.signals.prediction import (
    PredictionRegime,
    compute_regime,
    funding_side_preference,
    rate_change_score,
    war_risk_score,
)

logger = logging.getLogger(__name__)


def run_backfill(config_path: str | None = None) -> None:
    """One-shot: backfill historical data into SQLite."""
    config = load_config(config_path)
    db = Database(config.data.db_path)
    client = HyperliquidClient()
    ingestor = DataIngestor(config, db, client)

    try:
        ingestor.run_full_backfill()
        logger.info("Backfill complete")
    finally:
        db.close()


def run_trading_loop(config_path: str | None = None, force: bool = False) -> None:
    """Main trading loop — runs until interrupted.

    Supports 'paper' and 'live' modes. In live mode, uses LiveExecutor
    for real order placement and WebSocket for real-time price feeds.
    Exposes runtime state via Unix socket for TUI attachment.
    """
    config = load_config(config_path)
    db = Database(config.data.db_path)
    client = HyperliquidClient()
    ingestor = DataIngestor(config, db, client)
    signal_engine = SignalEngine(config.signals)
    risk_manager = RiskManager(config, db)
    entries_halted = False

    # Mode-specific setup
    executor: Executor
    ws_client: WsClient | None = None

    if config.mode == "live":
        if not config.hl_private_key:
            logger.error("HL_PRIVATE_KEY required for live mode")
            sys.exit(1)
        executor = LiveExecutor(config, db)
        # Set leverage — refuse to start if it fails
        for sym in config.trading.symbols:
            if not executor.set_leverage(sym, config.trading.leverage):
                logger.error("Failed to set leverage for %s — aborting live mode", sym)
                sys.exit(1)
        # Reconcile exchange positions against DB
        _reconcile_positions(executor, db, config)
        logger.info("Live mode — real orders will be placed")
    else:
        executor = PaperExecutor(db)
        logger.info("Paper mode — no real orders")

    # Auto-stop after 3 consecutive losing weeks (§5.3)
    if not _check_losing_weeks(db) and not force:
        open_trade_count = len(db.get_open_trades())
        if config.mode == "live" and open_trade_count > 0:
            entries_halted = True
            logger.warning(
                "Losing weeks halt active — managing %d existing live position(s),"
                " new entries disabled",
                open_trade_count,
            )
            _alert(
                config,
                "BOT HALTED: 3 consecutive losing weeks — managing existing"
                " live positions only",
            )
        else:
            logger.error("Use --force to override the losing weeks halt")
            _alert(config, "BOT HALTED: 3 consecutive losing weeks")
            sys.exit(1)

    # WebSocket for real-time prices (both modes benefit)
    ws_client = WsClient()
    ws_client.subscribe_mid_prices(config.trading.symbols)

    # Health checker for periodic heartbeat
    health_checker = HealthChecker(config, db, ws_client, executor)

    # Prediction market clients
    prediction_clients = _init_prediction_clients(config)

    # IPC: daemon state + socket server for TUI attachment
    daemon_state = DaemonState(mode=config.mode)
    socket_path = get_socket_path(config.data.db_path)
    state_server = DaemonStateServer(socket_path, daemon_state, executor, db)
    state_server.start()

    # Set up file logging for TUI log tailing
    log_file = str(socket_path.parent / "perp-bot.log")
    setup_logging(log_file=log_file)

    tf = config.data.primary_timeframe
    interval_ms = INTERVAL_MS[tf]
    min_candles = max(
        config.signals.zscore_lookback,
        config.signals.bollinger_period,
        config.signals.rsi_period,
        config.signals.adx_period * 2,  # ADX needs more warmup
    )

    logger.info("Starting trading loop — mode=%s, timeframe=%s", config.mode, tf)
    if config.discord_webhook_url:
        send_discord_alert(
            config.discord_webhook_url,
            f"Bot started — mode={config.mode}",
        )

    last_prediction_poll_ms = 0
    prediction_regime_label = "NORMAL"

    try:
        while True:
            # WebSocket health check — reconnect if stale
            if ws_client and not ws_client.is_healthy():
                logger.warning("WebSocket stale — triggering reconnect")
                _alert(config, "WS RECONNECT: price feed was stale")
                ws_client.reconnect()

            # Check pause state — skip tick but still monitor health
            if daemon_state.paused:
                daemon_state.update(
                    ws_healthy=ws_client.is_healthy() if ws_client else False,
                )
                health_checker.tick(prediction_regime_label)
                _sleep_until_next_candle(interval_ms)
                continue

            last_prediction_poll_ms, prediction_regime_label = _tick(
                config, db, client, ingestor, signal_engine,
                risk_manager, executor, tf, min_candles,
                prediction_clients, last_prediction_poll_ms,
                ws_client, daemon_state, entries_halted,
            )

            # Update daemon state after tick
            _update_daemon_state(
                daemon_state, config, ws_client, risk_manager,
                executor, prediction_regime_label, db,
            )

            health_checker.tick(prediction_regime_label)
            _sleep_until_next_candle(interval_ms)
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        state_server.stop()
        if ws_client:
            ws_client.close()
        db.close()


def _reconcile_positions(
    executor: LiveExecutor, db: Database, config,
) -> None:
    """Reconcile exchange positions against DB on startup.

    - Exchange has position but DB doesn't → create DB record from exchange state
    - DB has open position but exchange doesn't → close DB record as reconciled
    """
    for symbol in config.trading.symbols:
        exchange_pos = executor.get_exchange_position(symbol)
        db_trades = db.get_open_trades(symbol)

        if exchange_pos and not db_trades:
            # Exchange has a position we don't know about
            now = int(time.time() * 1000)
            trade_id = db.insert_trade({
                "symbol": symbol,
                "side": exchange_pos["side"],
                "entry_time": now,
                "entry_price": exchange_pos["entry_price"],
                "size_usd": exchange_pos["entry_price"] * exchange_pos["size_base"],
                "is_paper": 0,
            })
            logger.warning(
                "RECONCILED: Found exchange position %s %s (%.4f @ %.2f) "
                "not in DB — created trade #%d",
                exchange_pos["side"], symbol,
                exchange_pos["size_base"], exchange_pos["entry_price"],
                trade_id,
            )
            _alert(
                config,
                f"RECONCILE: Adopted orphan {exchange_pos['side']} "
                f"{symbol} @ {exchange_pos['entry_price']:.2f}",
            )

        elif db_trades and not exchange_pos:
            # DB thinks we have a position but exchange doesn't
            for trade in db_trades:
                now = int(time.time() * 1000)
                db.close_trade(
                    trade["id"], now, trade["entry_price"], 0.0,
                    "reconciled_missing",
                )
                logger.warning(
                    "RECONCILED: DB trade #%d (%s %s) has no exchange position — "
                    "closed as reconciled_missing",
                    trade["id"], trade["side"], symbol,
                )
            _alert(
                config,
                f"RECONCILE: Closed {len(db_trades)} orphan DB trade(s) for {symbol}",
            )

        elif exchange_pos and db_trades:
            logger.info(
                "Position sync OK: %s %s matches DB trade #%d",
                exchange_pos["side"], symbol, db_trades[0]["id"],
            )


def _check_losing_weeks(db: Database, num_weeks: int = 3) -> bool:
    """Check if the last N weeks were all net-negative. Returns True if safe to start."""
    now_ms = int(time.time() * 1000)
    week_ms = 7 * 24 * 3600 * 1000

    losing_count = 0
    for i in range(num_weeks):
        end = now_ms - i * week_ms
        start = end - week_ms
        trades = db.get_closed_trades_in_range(start, end)
        if not trades:
            return True  # Not enough history — safe to start
        weekly_pnl = sum(t.get("pnl", 0) or 0 for t in trades)
        if weekly_pnl < 0:
            losing_count += 1

    if losing_count >= num_weeks:
        logger.error(
            "HALTED: %d consecutive losing weeks detected", num_weeks,
        )
        return False
    return True


def _init_prediction_clients(config) -> dict:
    """Create prediction market client instances based on configured sources."""
    clients: dict = {}
    if not config.prediction or not config.prediction.enabled:
        return clients
    sources = {m.source for m in config.prediction.markets}
    if "polymarket" in sources:
        clients["polymarket"] = PolymarketClient()
    if "kalshi" in sources:
        clients["kalshi"] = KalshiClient()
    return clients


def _tick(
    config, db, client, ingestor, signal_engine,
    risk_manager, executor, tf, min_candles,
    prediction_clients=None, last_prediction_poll_ms=0,
    ws_client=None, daemon_state=None, entries_halted=False,
) -> tuple[int, str]:
    """Single iteration of the trading loop. Returns (last_prediction_poll_ms, regime_label)."""
    prediction_clients = prediction_clients or {}

    # --- Prediction market polling ---
    prediction_regime = PredictionRegime.NORMAL
    preferred_side = None
    now_ms = int(time.time() * 1000)

    if config.prediction and config.prediction.enabled and prediction_clients:
        poll_interval_ms = config.prediction.poll_interval_minutes * 60_000
        if now_ms - last_prediction_poll_ms >= poll_interval_ms:
            last_prediction_poll_ms = now_ms
            ingestor.update_predictions(prediction_clients, config.prediction)

        # Compute regime from latest cached data
        prediction_regime, preferred_side = _compute_prediction_state(db, config)

    for symbol in config.trading.symbols:
        # Update candles
        ingestor.update_candles(symbol)

        # Load candles into DataFrame
        candles = db.get_candles(symbol, tf, limit=min_candles + 50)
        if len(candles) < min_candles:
            logger.warning(
                "Not enough candles for %s %s (%d < %d)",
                symbol, tf, len(candles), min_candles,
            )
            continue

        df = pd.DataFrame(candles)
        df = signal_engine.compute_indicators(df)

        # Current position state
        open_trades = db.get_open_trades(symbol)
        position_side = open_trades[0]["side"] if open_trades else None

        # Check position-level exits first
        if open_trades:
            trade = open_trades[0]
            current_price = _get_price(ws_client, client, symbol)

            # Capital-based stop loss
            if risk_manager.check_stop_loss(
                trade["entry_price"], current_price, trade["side"], trade["size_usd"],
            ):
                pnl = _compute_pnl(trade, current_price)
                executor.close_position(
                    trade["id"], symbol, current_price, pnl,
                    "capital_stop_loss",
                )
                risk_manager.record_stop_loss()
                _alert(config, f"STOP LOSS {symbol} pnl={pnl:.2f}")
                continue

            # Position timeout
            if risk_manager.check_position_timeout(trade["entry_time"]):
                pnl = _compute_pnl(trade, current_price)
                executor.close_position(
                    trade["id"], symbol, current_price, pnl,
                    "timeout_24h",
                )
                _alert(config, f"TIMEOUT {symbol} pnl={pnl:.2f}")
                continue

        # Signal evaluation with prediction regime
        result = signal_engine.evaluate(
            df, position_side, prediction_regime, preferred_side,
        )

        # Expose signal to daemon state for TUI
        if daemon_state is not None:
            daemon_state.latest_signals[symbol] = {
                "signal": result.signal.value,
                "reason": result.reason,
                "zscore": result.zscore_value,
                "rsi": result.rsi_value,
                "adx": result.adx_value,
                "price": result.price,
            }

        if result.signal == Signal.CLOSE and open_trades:
            trade = open_trades[0]
            current_price = _get_price(ws_client, client, symbol)
            pnl = _compute_pnl(trade, current_price)
            executor.close_position(
                trade["id"], symbol, current_price, pnl, result.reason,
            )
            _alert(config, f"CLOSE {symbol} {result.reason} pnl={pnl:.2f}")

        elif result.signal in (Signal.LONG, Signal.SHORT) and not open_trades:
            if entries_halted:
                logger.info(
                    "Entry blocked by losing weeks halt for %s", symbol,
                )
                continue
            risk_check = risk_manager.check_entry()
            if risk_check.allowed:
                size = risk_manager.compute_position_size(prediction_regime)
                if size <= 0:
                    logger.info(
                        "Position size zero — regime=%s",
                        prediction_regime.value,
                    )
                    continue
                price = _get_price(ws_client, client, symbol)
                trade_id = executor.open_position(
                    symbol, result.signal.value, size, price,
                )
                if trade_id is None:
                    logger.error(
                        "Entry failed for %s %s — skipping OPEN alert",
                        result.signal.value, symbol,
                    )
                    continue
                regime_tag = ""
                if prediction_regime != PredictionRegime.NORMAL:
                    regime_tag = f" [{prediction_regime.value}]"
                _alert(
                    config,
                    f"OPEN {result.signal.value} {symbol}"
                    f" @ {price:.2f} size=${size:.0f}"
                    f"{regime_tag}",
                )
                # Alert if server-side SL placement failed
                if (
                    isinstance(executor, LiveExecutor)
                    and executor._sl_failed
                ):
                    _alert(
                        config,
                        f"CRITICAL: {symbol} UNHEDGED — "
                        f"server-side SL failed!",
                    )
            else:
                logger.info("Entry blocked: %s", risk_check.reason)

    return last_prediction_poll_ms, prediction_regime.value


def _get_price(ws_client, rest_client, symbol: str) -> float:
    """Get mid price from WebSocket cache, falling back to REST."""
    if ws_client is not None:
        ws_price = ws_client.get_mid_price(symbol)
        if ws_price is not None:
            return ws_price
    return rest_client.get_mid_price(symbol)


def _compute_prediction_state(db, config):
    """Compute prediction regime and preferred side from latest DB snapshots."""
    snapshots = db.get_latest_predictions()
    if not snapshots:
        return PredictionRegime.NORMAL, None

    war_snapshots = [s for s in snapshots if s["category"] == "war_risk"]
    rate_snapshots = [s for s in snapshots if s["category"] == "rate_change"]

    market_weights = {
        m.slug: m.weight
        for m in config.prediction.markets
        if m.category == "war_risk"
    }
    w_risk = war_risk_score(war_snapshots, market_weights)
    r_change = rate_change_score(rate_snapshots)

    regime = compute_regime(w_risk, r_change, config.prediction)
    preferred = funding_side_preference(r_change, config.prediction.rate_change_threshold)

    if regime != PredictionRegime.NORMAL:
        logger.info(
            "Prediction regime=%s war_risk=%.3f rate_change=%.3f preferred=%s",
            regime.value, w_risk, r_change, preferred,
        )

    return regime, preferred


def _compute_pnl(trade: dict, current_price: float) -> float:
    """Compute unrealised P&L for a trade."""
    if trade["side"] == "long":
        return (current_price - trade["entry_price"]) / trade["entry_price"] * trade["size_usd"]
    else:
        return (trade["entry_price"] - current_price) / trade["entry_price"] * trade["size_usd"]


def _alert(config, message: str) -> None:
    logger.info(message)
    if config.discord_webhook_url:
        send_discord_alert(config.discord_webhook_url, message)
    if config.telegram_bot_token and config.telegram_chat_id:
        send_telegram_alert(
            config.telegram_bot_token, config.telegram_chat_id, message,
        )


def _sleep_until_next_candle(interval_ms: int) -> None:
    """Sleep until the next candle boundary plus a small buffer."""
    now_ms = int(time.time() * 1000)
    next_candle = ((now_ms // interval_ms) + 1) * interval_ms
    sleep_secs = (next_candle - now_ms) / 1000 + 5  # 5s buffer for candle to finalise
    logger.debug("Sleeping %.1fs until next candle", sleep_secs)
    time.sleep(sleep_secs)


def _update_daemon_state(
    daemon_state: DaemonState,
    config, ws_client, risk_manager, executor,
    prediction_regime_label: str, db,
) -> None:
    """Refresh the daemon state after a tick for IPC clients."""
    now_ms = int(time.time() * 1000)

    # Mid prices
    mid_prices = {}
    if ws_client:
        for sym in config.trading.symbols:
            p = ws_client.get_mid_price(sym)
            if p is not None:
                mid_prices[sym] = p

    # Risk check
    risk_check = risk_manager.check_entry()

    # Cooldown remaining
    cooldown_s = 0.0
    if risk_manager._last_stop_loss_time is not None:
        elapsed = now_ms - risk_manager._last_stop_loss_time
        remaining = risk_manager.risk.cooldown_seconds * 1000 - elapsed
        if remaining > 0:
            cooldown_s = remaining / 1000

    # Daily PnL
    day_start = (int(time.time()) - int(time.time()) % 86400) * 1000
    daily_pnl = db.get_daily_pnl(day_start)

    # Slippage
    slippage_stats = {"count": 0, "avg_pct": 0.0, "max_pct": 0.0}
    if hasattr(executor, "get_slippage_stats"):
        slippage_stats = executor.get_slippage_stats()

    daemon_state.update(
        tick_count=daemon_state.tick_count + 1,
        last_tick_ms=now_ms,
        mid_prices=mid_prices,
        ws_healthy=ws_client.is_healthy() if ws_client else False,
        prediction_regime=prediction_regime_label,
        risk_allowed=risk_check.allowed,
        risk_reason=risk_check.reason,
        cooldown_remaining_s=cooldown_s,
        daily_pnl=daily_pnl,
        slippage_stats=slippage_stats,
    )


def run_status(config_path: str | None = None) -> None:
    """One-shot: connect to daemon, print formatted state, exit."""
    from perp_bot.ipc.client import DaemonClient

    config = load_config(config_path)
    socket_path = get_socket_path(config.data.db_path)
    client = DaemonClient(socket_path)

    if not client.is_running():
        print("Daemon is not running (socket not found or not responding)")
        sys.exit(1)

    state = client.get_state()
    if state is None:
        print("Failed to get state from daemon")
        sys.exit(1)

    print(json.dumps(state, indent=2))


def run_tui(config_path: str | None = None) -> None:
    """Launch the TUI dashboard connecting to a running daemon."""
    from perp_bot.tui.app import PerpBotApp

    config = load_config(config_path)
    app = PerpBotApp(config)
    app.run()


def run_backfill_predictions(config_path: str | None = None) -> None:
    """One-shot: fetch current prediction market snapshots into SQLite."""
    config = load_config(config_path)
    if not config.prediction or not config.prediction.enabled:
        logger.error("Prediction markets not configured or disabled")
        sys.exit(1)

    db = Database(config.data.db_path)
    clients = _init_prediction_clients(config)
    ingestor = DataIngestor(config, db, HyperliquidClient())

    try:
        count = ingestor.update_predictions(clients, config.prediction)
        logger.info("Inserted %d prediction snapshots", count)
    finally:
        db.close()


def run_backtest(config_path: str | None = None) -> None:
    """Run a backtest over all historical data."""
    config = load_config(config_path)
    bt_config = config.backtest or BacktestConfig()
    db = Database(config.data.db_path)

    try:
        engine = BacktestEngine(config, bt_config)
        for symbol in config.trading.symbols:
            logger.info("Running backtest for %s", symbol)
            result = engine.run(db, symbol)
            print(result.summary())
            if bt_config.export_trades_csv:
                export_path = _trade_export_path(
                    bt_config.export_trades_csv, symbol, config.trading.symbols,
                )
                result.trades_to_csv(export_path)
                logger.info("Trades exported to %s", export_path)
    finally:
        db.close()


def run_walkforward(config_path: str | None = None) -> None:
    """Run walk-forward analysis."""
    config = load_config(config_path)
    bt_config = config.backtest or BacktestConfig()
    db = Database(config.data.db_path)

    try:
        for symbol in config.trading.symbols:
            # Find data range
            candles = db.get_candles(symbol, config.data.primary_timeframe, limit=1)
            if not candles:
                logger.warning("No data for %s", symbol)
                continue
            first_time = candles[0]["open_time"]
            latest = db.get_latest_candle_time(symbol, config.data.primary_timeframe)
            if latest is None:
                continue

            runner = WalkForwardRunner(config, bt_config)
            result = runner.run(db, symbol, first_time, latest)
            print(result.summary())
    finally:
        db.close()


def run_sensitivity(config_path: str | None = None) -> None:
    """Run parameter sensitivity analysis."""
    config = load_config(config_path)
    bt_config = config.backtest or BacktestConfig()
    db = Database(config.data.db_path)

    try:
        analyzer = ParameterSensitivityAnalyzer(config, bt_config)
        for symbol in config.trading.symbols:
            logger.info("Running sensitivity analysis for %s", symbol)
            report = analyzer.run(db, symbol)
            print(report.summary())
    finally:
        db.close()


def run_screen(config_path: str | None = None) -> None:
    """Screen symbols for mean-reversion suitability (§4.5).

    Criteria: Hurst < 0.5, 24h volume > $50M, tight spread.
    """
    from perp_bot.signals.indicators import hurst_exponent

    config = load_config(config_path)
    db = Database(config.data.db_path)
    client = HyperliquidClient()

    try:
        meta = client.get_asset_meta()
        if not meta or len(meta) < 2:
            print("Failed to fetch asset metadata")
            return

        universe = meta[0].get("universe", [])
        asset_ctxs = meta[1] if len(meta) > 1 else []

        # Build volume + symbol lookup
        vol_map: dict[str, float] = {}
        for i, asset in enumerate(universe):
            name = asset["name"]
            if i < len(asset_ctxs):
                ctx = asset_ctxs[i]
                vol_map[name] = float(ctx.get("dayNtlVlm", 0))

        symbols = [a["name"] for a in universe]

        header = (
            f"{'Symbol':<10} {'Vol24h($M)':>12} {'Spread%':>9}"
            f" {'Hurst':>8} {'Verdict'}"
        )
        print(header)
        print("-" * len(header))

        for symbol in symbols:
            vol_24h = vol_map.get(symbol, 0)

            # §4.5: 24h volume > $50M
            if vol_24h < 50_000_000:
                continue

            # Bid-ask spread
            try:
                l2 = client.info.l2_snapshot(symbol)
                bid = float(l2["levels"][0][0]["px"])
                ask = float(l2["levels"][1][0]["px"])
                mid = (bid + ask) / 2
                spread_pct = (ask - bid) / mid * 100 if mid > 0 else 999
            except Exception:
                spread_pct = 999.0

            # Hurst exponent from historical candles
            candles = db.get_candles(
                symbol, config.data.primary_timeframe, limit=500, descending=True,
            )
            if len(candles) < 50:
                continue
            closes = pd.Series([c["close"] for c in candles])
            h = hurst_exponent(closes)

            # Verdict: must pass all §4.5 criteria
            passes_hurst = h < 0.5
            passes_spread = spread_pct < 0.05
            if passes_hurst and passes_spread:
                verdict = "CANDIDATE"
            elif passes_hurst:
                verdict = "MEAN-REV (wide spread)"
            elif h > 0.55:
                verdict = "TRENDING"
            else:
                verdict = "RANDOM"

            vol_m = vol_24h / 1_000_000
            print(
                f"{symbol:<10} {vol_m:>12.1f} {spread_pct:>8.4f}%"
                f" {h:>8.3f} {verdict}"
            )
    finally:
        db.close()


def run_compare(
    config_path: str | None = None, days: int = 7,
) -> None:
    """Compare paper trading results against backtest for the same period."""
    from perp_bot.reporting.compare import compare_paper_vs_backtest

    config = load_config(config_path)
    db = Database(config.data.db_path)
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 24 * 3600 * 1000

    try:
        for symbol in config.trading.symbols:
            print(compare_paper_vs_backtest(
                config, db, symbol, start_ms, now_ms,
            ))
            print()
    finally:
        db.close()


def _trade_export_path(
    raw_path: str, symbol: str, all_symbols: list[str],
) -> str:
    """Return a per-symbol export path when backtesting multiple symbols."""
    if len(all_symbols) == 1:
        return raw_path

    path = Path(raw_path)
    if path.suffix:
        return str(path.with_name(f"{path.stem}-{symbol}{path.suffix}"))
    return str(path.with_name(f"{path.name}-{symbol}"))


def run_review(config_path: str | None = None, weeks: int = 1) -> None:
    """Print a weekly performance review."""
    from perp_bot.reporting.weekly import generate_weekly_report

    config = load_config(config_path)
    db = Database(config.data.db_path)
    try:
        print(generate_weekly_report(db, weeks))
    finally:
        db.close()


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Hyperliquid mean-reversion bot")
    parser.add_argument(
        "command",
        choices=[
            "backfill", "trade", "backfill-predictions",
            "backtest", "walkforward", "sensitivity",
            "screen", "review", "compare", "tui", "status",
        ],
        help="Command to run",
    )
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--weeks", type=int, default=1, help="Weeks for review report")
    parser.add_argument("--days", type=int, default=7, help="Days for compare range")
    parser.add_argument("--force", action="store_true", help="Override safety halts")
    args = parser.parse_args()

    if args.command == "backfill":
        run_backfill(args.config)
    elif args.command == "trade":
        run_trading_loop(args.config, force=args.force)
    elif args.command == "backfill-predictions":
        run_backfill_predictions(args.config)
    elif args.command == "backtest":
        run_backtest(args.config)
    elif args.command == "walkforward":
        run_walkforward(args.config)
    elif args.command == "sensitivity":
        run_sensitivity(args.config)
    elif args.command == "screen":
        run_screen(args.config)
    elif args.command == "review":
        run_review(args.config, args.weeks)
    elif args.command == "compare":
        run_compare(args.config, args.days)
    elif args.command == "tui":
        run_tui(args.config)
    elif args.command == "status":
        run_status(args.config)


if __name__ == "__main__":
    main()
