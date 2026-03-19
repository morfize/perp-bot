"""Signal engine — combines all indicators to produce entry/exit signals."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd

from perp_bot.config import SignalConfig
from perp_bot.signals.indicators import adx, bollinger_bands, rsi, zscore
from perp_bot.signals.prediction import PredictionRegime


class Signal(Enum):
    LONG = "long"
    SHORT = "short"
    CLOSE = "close"
    NONE = "none"


@dataclass
class SignalResult:
    signal: Signal
    reason: str
    zscore_value: float
    rsi_value: float
    adx_value: float
    price: float
    prediction_regime: PredictionRegime = PredictionRegime.NORMAL


class SignalEngine:
    def __init__(self, config: SignalConfig) -> None:
        self.config = config

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add all indicator columns to a candle DataFrame.

        Expects df with columns: open, high, low, close, volume
        """
        c = self.config
        df = df.copy()

        df["zscore"] = zscore(df["close"], c.zscore_lookback)
        df["bb_mid"], df["bb_upper"], df["bb_lower"] = bollinger_bands(
            df["close"], c.bollinger_period, c.bollinger_std
        )
        df["rsi"] = rsi(df["close"], c.rsi_period)
        df["adx"] = adx(df["high"], df["low"], df["close"], c.adx_period)

        return df

    def evaluate(
        self,
        df: pd.DataFrame,
        position_side: str | None = None,
        prediction_regime: PredictionRegime = PredictionRegime.NORMAL,
        preferred_side: str | None = None,
    ) -> SignalResult:
        """Evaluate the latest candle for entry/exit signals.

        Args:
            df: DataFrame with indicator columns (from compute_indicators)
            position_side: "long", "short", or None if no position
            prediction_regime: current macro regime from prediction markets
            preferred_side: "long" or "short" funding preference, or None
        """
        latest = df.iloc[-1]
        c = self.config

        z = latest["zscore"]
        r = latest["rsi"]
        a = latest["adx"]
        price = latest["close"]

        result_kwargs = {
            "zscore_value": z,
            "rsi_value": r,
            "adx_value": a,
            "price": price,
            "prediction_regime": prediction_regime,
        }

        # --- Regime-adjusted thresholds ---
        entry_threshold = c.zscore_entry_threshold
        exit_threshold = c.zscore_exit_threshold

        if prediction_regime == PredictionRegime.HIGH_RISK:
            entry_threshold = 2.5  # stricter entry
            exit_threshold = 0.5   # take profits earlier
        elif prediction_regime == PredictionRegime.CRISIS:
            # Block all new entries but allow exits
            if position_side is None:
                return SignalResult(
                    signal=Signal.NONE,
                    reason=f"crisis_regime_block (z={z:.2f})",
                    **result_kwargs,
                )

        # --- Exit signals (checked first) ---
        if position_side is not None:
            # Profit exit: Z-score reverts to mean
            if abs(z) < exit_threshold:
                return SignalResult(
                    signal=Signal.CLOSE,
                    reason=f"mean_reversion_complete (z={z:.2f})",
                    **result_kwargs,
                )
            # Stop exit: Z-score extends further against position
            if position_side == "long" and z < -c.zscore_stop_threshold:
                return SignalResult(
                    signal=Signal.CLOSE,
                    reason=f"zscore_stop_long (z={z:.2f})",
                    **result_kwargs,
                )
            if position_side == "short" and z > c.zscore_stop_threshold:
                return SignalResult(
                    signal=Signal.CLOSE,
                    reason=f"zscore_stop_short (z={z:.2f})",
                    **result_kwargs,
                )

        # --- Entry signals (only if no position) ---
        if position_side is None and a < c.adx_threshold:
            # Regime-adjusted entry thresholds per side
            short_entry_threshold = entry_threshold
            long_entry_threshold = entry_threshold

            if prediction_regime == PredictionRegime.DOVISH_SHIFT:
                long_entry_threshold *= 0.9   # relax long entry slightly
            elif prediction_regime == PredictionRegime.HAWKISH_SHIFT:
                short_entry_threshold *= 0.9  # relax short entry slightly

            # Funding preference relaxation
            if preferred_side == "long":
                long_entry_threshold = min(long_entry_threshold, entry_threshold * 0.9)
            elif preferred_side == "short":
                short_entry_threshold = min(short_entry_threshold, entry_threshold * 0.9)

            # Short entry
            if (
                z > short_entry_threshold
                and r > c.rsi_overbought
                and price > latest["bb_upper"]
            ):
                return SignalResult(
                    signal=Signal.SHORT,
                    reason=f"short_entry (z={z:.2f}, rsi={r:.1f}, adx={a:.1f})",
                    **result_kwargs,
                )
            # Long entry
            if (
                z < -long_entry_threshold
                and r < c.rsi_oversold
                and price < latest["bb_lower"]
            ):
                return SignalResult(
                    signal=Signal.LONG,
                    reason=f"long_entry (z={z:.2f}, rsi={r:.1f}, adx={a:.1f})",
                    **result_kwargs,
                )

        return SignalResult(signal=Signal.NONE, reason="no_signal", **result_kwargs)
