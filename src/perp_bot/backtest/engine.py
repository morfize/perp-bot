"""Core backtest engine — row-by-row simulation over historical candles."""

from __future__ import annotations

import logging

import pandas as pd

from perp_bot.backtest.config import BacktestConfig
from perp_bot.backtest.cost_model import FeeModel, FundingModel, SlippageModel
from perp_bot.backtest.executor import BacktestExecutor
from perp_bot.backtest.metrics import compute_all
from perp_bot.backtest.results import BacktestResult
from perp_bot.backtest.risk_adapter import BacktestRiskManager
from perp_bot.config import BotConfig
from perp_bot.data.db import Database
from perp_bot.signals.engine import Signal, SignalEngine
from perp_bot.signals.prediction import PredictionRegime

logger = logging.getLogger(__name__)


class BacktestEngine:
    """Runs a historical simulation of the mean-reversion strategy."""

    def __init__(self, bot_config: BotConfig, bt_config: BacktestConfig) -> None:
        self.bot_config = bot_config
        self.bt_config = bt_config
        self.signal_engine = SignalEngine(bot_config.signals)

    def run(
        self,
        db: Database,
        symbol: str,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
        prediction_regime: PredictionRegime = PredictionRegime.NORMAL,
    ) -> BacktestResult:
        """Execute a full backtest over the specified time range.

        Args:
            db: Database with historical candles and funding rates.
            symbol: Trading pair to backtest.
            start_time_ms: Start of backtest window (None = all data).
            end_time_ms: End of backtest window (None = all data).
            prediction_regime: Fixed regime for the entire backtest.
        """
        bt = self.bt_config
        cfg = self.bot_config

        # --- Load data ---
        candles = db.get_candles(
            symbol, cfg.data.primary_timeframe, start_time=start_time_ms, limit=100_000
        )
        if not candles:
            logger.warning("No candles found for %s", symbol)
            return BacktestResult()

        df = pd.DataFrame(candles)

        # Filter by end_time if specified
        if end_time_ms is not None:
            df = df[df["open_time"] <= end_time_ms].reset_index(drop=True)
        if df.empty:
            return BacktestResult()

        # Compute indicators on the full DataFrame once
        df = self.signal_engine.compute_indicators(df)

        # Load funding rates for the period
        data_start = int(df.iloc[0]["open_time"])
        data_end = int(df.iloc[-1]["open_time"])
        funding_rates = db.get_funding_rates(symbol, data_start, data_end)

        # --- Build components ---
        fee_model = FeeModel(bt.maker_fee_rate, bt.taker_fee_rate)
        slippage_model = SlippageModel(bt.slippage_min_pct, bt.slippage_max_pct, bt.seed)
        funding_model = FundingModel(funding_rates)
        executor = BacktestExecutor(fee_model, slippage_model, funding_model)
        risk = BacktestRiskManager(cfg)

        # --- Determine warmup ---
        sc = cfg.signals
        warmup = max(sc.zscore_lookback, sc.bollinger_period, sc.rsi_period, sc.adx_period * 2)

        # --- Row-by-row simulation ---
        equity_curve: list[float] = []
        capital = cfg.trading.capital_usd
        closed_pnl = 0.0
        pending_order: _PendingOrder | None = None

        for i in range(warmup, len(df)):
            row = df.iloc[i]
            time_ms = int(row["open_time"])
            price = float(row["close"])

            # --- Fill pending order ---
            if pending_order is not None and i >= pending_order.fill_index:
                if bt.cancel_if_signal_gone:
                    # Re-evaluate signal at fill time
                    check_result = self.signal_engine.evaluate(
                        df.iloc[: i + 1],
                        position_side=None,
                        prediction_regime=prediction_regime,
                    )
                    if check_result.signal != pending_order.signal:
                        pending_order = None  # cancel — signal disappeared
                    else:
                        size = risk.compute_position_size(prediction_regime)
                        if size > 0 and risk.check_entry(time_ms):
                            executor.open_position(
                                symbol, pending_order.signal.value,
                                size, price, time_ms,
                            )
                        pending_order = None
                else:
                    size = risk.compute_position_size(prediction_regime)
                    if size > 0 and risk.check_entry(time_ms):
                        executor.open_position(
                            symbol, pending_order.signal.value,
                            size, price, time_ms,
                        )
                    pending_order = None

            # --- Manage open position ---
            if executor.has_position:
                t = executor.open_trade
                # Capital-based stop loss
                if risk.check_stop_loss(t.entry_price, price, t.side, t.size_usd):
                    record = executor.close_position(
                        price, time_ms, "capital_stop_loss", is_stop_loss=True
                    )
                    risk.record_trade_close(record.net_pnl, time_ms, is_stop_loss=True)
                    closed_pnl += record.net_pnl
                # Position timeout
                elif risk.check_position_timeout(t.entry_time_ms, time_ms):
                    record = executor.close_position(price, time_ms, "timeout_24h")
                    risk.record_trade_close(record.net_pnl, time_ms)
                    closed_pnl += record.net_pnl
                else:
                    # Signal-based exit
                    sig_result = self.signal_engine.evaluate(
                        df.iloc[: i + 1],
                        position_side=t.side,
                        prediction_regime=prediction_regime,
                    )
                    if sig_result.signal == Signal.CLOSE:
                        is_stop = "zscore_stop" in sig_result.reason
                        record = executor.close_position(
                            price, time_ms, sig_result.reason, is_stop_loss=is_stop
                        )
                        risk.record_trade_close(record.net_pnl, time_ms, is_stop_loss=is_stop)
                        closed_pnl += record.net_pnl

            # --- Entry signals (only if no position and no pending order) ---
            if not executor.has_position and pending_order is None:
                sig_result = self.signal_engine.evaluate(
                    df.iloc[: i + 1],
                    position_side=None,
                    prediction_regime=prediction_regime,
                )
                if sig_result.signal in (Signal.LONG, Signal.SHORT):
                    if risk.check_entry(time_ms):
                        if bt.entry_delay_candles == 0:
                            size = risk.compute_position_size(prediction_regime)
                            if size > 0:
                                executor.open_position(
                                    symbol, sig_result.signal.value,
                                    size, price, time_ms,
                                )
                        else:
                            pending_order = _PendingOrder(
                                signal=sig_result.signal,
                                fill_index=i + bt.entry_delay_candles,
                            )

            # --- Equity curve ---
            unrealised = 0.0
            if executor.has_position:
                t = executor.open_trade
                if t.side == "long":
                    unrealised = (price - t.entry_price) / t.entry_price * t.size_usd
                else:
                    unrealised = (t.entry_price - price) / t.entry_price * t.size_usd
            equity_curve.append(capital + closed_pnl + unrealised)

        # --- Force-close any open position at end ---
        if executor.has_position:
            last_price = float(df.iloc[-1]["close"])
            last_time = int(df.iloc[-1]["open_time"])
            record = executor.close_position(last_price, last_time, "backtest_end")
            risk.record_trade_close(record.net_pnl, last_time)
            closed_pnl += record.net_pnl
            # Update last equity point
            if equity_curve:
                equity_curve[-1] = capital + closed_pnl

        # --- Build result ---
        metrics = compute_all(executor.trades, equity_curve, capital)
        result = BacktestResult(
            trades=executor.trades,
            equity_curve=equity_curve,
            metrics=metrics,
            initial_capital=capital,
            start_time_ms=data_start,
            end_time_ms=data_end,
        )

        if bt.export_trades_csv:
            result.trades_to_csv(bt.export_trades_csv)

        return result


class _PendingOrder:
    """A pending order waiting for execution delay to elapse."""
    __slots__ = ("signal", "fill_index")

    def __init__(self, signal: Signal, fill_index: int) -> None:
        self.signal = signal
        self.fill_index = fill_index
