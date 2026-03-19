"""Tests for the signal engine."""

import numpy as np
import pandas as pd

from perp_bot.config import SignalConfig
from perp_bot.signals.engine import Signal, SignalEngine


def _default_config() -> SignalConfig:
    return SignalConfig(
        zscore_lookback=20,
        zscore_entry_threshold=2.0,
        zscore_exit_threshold=0.3,
        zscore_stop_threshold=3.0,
        bollinger_period=20,
        bollinger_std=2.0,
        rsi_period=14,
        rsi_overbought=70,
        rsi_oversold=30,
        adx_period=14,
        adx_threshold=25,
    )


def _make_candles(close_prices: list[float], n_warmup: int = 50) -> pd.DataFrame:
    """Build a DataFrame with OHLCV from close prices, prepending warmup data."""
    rng = np.random.default_rng(42)
    warmup = [100 + rng.normal(0, 1) for _ in range(n_warmup)]
    all_close = warmup + close_prices
    return pd.DataFrame({
        "open": all_close,
        "high": [c + 0.5 for c in all_close],
        "low": [c - 0.5 for c in all_close],
        "close": all_close,
        "volume": [1000.0] * len(all_close),
    })


class TestSignalEngine:
    def test_no_signal_on_normal_data(self):
        engine = SignalEngine(_default_config())
        df = _make_candles([100.0] * 30)
        df = engine.compute_indicators(df)
        result = engine.evaluate(df, position_side=None)
        assert result.signal == Signal.NONE

    def test_close_on_mean_reversion(self):
        """If Z-score is within exit threshold, position should close."""
        engine = SignalEngine(_default_config())
        # Prices that revert to mean
        df = _make_candles([100.0] * 30)
        df = engine.compute_indicators(df)
        # Force zscore to be within exit threshold
        df.loc[df.index[-1], "zscore"] = 0.1
        result = engine.evaluate(df, position_side="long")
        assert result.signal == Signal.CLOSE
        assert "mean_reversion" in result.reason

    def test_stop_loss_signal(self):
        """Z-score exceeding stop threshold should trigger close."""
        engine = SignalEngine(_default_config())
        df = _make_candles([100.0] * 30)
        df = engine.compute_indicators(df)
        # Force extreme zscore against long position
        df.loc[df.index[-1], "zscore"] = -3.5
        result = engine.evaluate(df, position_side="long")
        assert result.signal == Signal.CLOSE
        assert "stop" in result.reason

    def test_no_entry_during_trend(self):
        """ADX above threshold should prevent entries."""
        engine = SignalEngine(_default_config())
        df = _make_candles([100.0] * 30)
        df = engine.compute_indicators(df)
        # Set up conditions that would trigger short entry, but ADX is high
        df.loc[df.index[-1], "zscore"] = 2.5
        df.loc[df.index[-1], "rsi"] = 75
        df.loc[df.index[-1], "bb_upper"] = 99.0  # price above upper band
        df.loc[df.index[-1], "adx"] = 35  # trending!
        result = engine.evaluate(df, position_side=None)
        assert result.signal == Signal.NONE
