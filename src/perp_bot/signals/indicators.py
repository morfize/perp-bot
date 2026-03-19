"""Self-implemented technical indicators — no ta-lib dependency.

All functions take pandas Series (typically close prices) and return Series.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def zscore(series: pd.Series, lookback: int) -> pd.Series:
    """Rolling Z-score: (price - mean) / std."""
    mean = series.rolling(lookback).mean()
    std = series.rolling(lookback).std(ddof=0)
    return (series - mean) / std.replace(0, np.nan)


def bollinger_bands(
    series: pd.Series, period: int, num_std: float
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (middle, upper, lower) Bollinger Bands."""
    middle = series.rolling(period).mean()
    std = series.rolling(period).std(ddof=0)
    upper = middle + num_std * std
    lower = middle - num_std * std
    return middle, upper, lower


def rsi(series: pd.Series, period: int) -> pd.Series:
    """RSI using Wilder's smoothing (EMA with alpha=1/period)."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    # Wilder smoothing = EMA with alpha = 1/period
    alpha = 1.0 / period
    avg_gain = gain.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

    # When avg_loss is 0 (pure uptrend), RSI = 100
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100.0 - (100.0 / (1.0 + rs))
    # Fill NaN from zero-loss (pure uptrend) with 100, zero-gain (pure downtrend) with 0
    result = result.fillna(pd.Series(
        np.where(avg_loss == 0, 100.0, np.where(avg_gain == 0, 0.0, np.nan)),
        index=result.index,
    ))
    return result


def hurst_exponent(series: pd.Series, max_lag: int = 100) -> float:
    """Hurst exponent via the rescaled range (R/S) method.

    H < 0.5 → mean-reverting (good for this strategy)
    H = 0.5 → random walk
    H > 0.5 → trending

    Args:
        series: Price or log-return series.
        max_lag: Maximum lag for R/S calculation.

    Returns:
        Estimated Hurst exponent as a float.
    """
    series = series.dropna().values
    n = len(series)
    if n < 20:
        return 0.5  # Not enough data — assume random walk

    # Use log returns for stationarity
    log_returns = np.diff(np.log(series))

    # Compute R/S for various lag sizes
    lags = []
    rs_values = []
    for lag in range(10, min(max_lag, n // 2) + 1):
        rs_list = []
        for start in range(0, len(log_returns) - lag + 1, lag):
            chunk = log_returns[start : start + lag]
            mean_chunk = np.mean(chunk)
            deviations = np.cumsum(chunk - mean_chunk)
            r = np.max(deviations) - np.min(deviations)
            s = np.std(chunk, ddof=1)
            if s > 0:
                rs_list.append(r / s)
        if rs_list:
            lags.append(lag)
            rs_values.append(np.mean(rs_list))

    if len(lags) < 2:
        return 0.5

    # Linear regression of log(R/S) on log(lag)
    log_lags = np.log(lags)
    log_rs = np.log(rs_values)
    poly = np.polyfit(log_lags, log_rs, 1)
    return float(poly[0])


def adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int
) -> pd.Series:
    """Average Directional Index (ADX) using Wilder's smoothing.

    Measures trend strength: < 25 = range-bound, > 25 = trending.
    """
    # True Range
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    # Directional Movement
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_dm = pd.Series(plus_dm, index=high.index)
    minus_dm = pd.Series(minus_dm, index=high.index)

    # Wilder smoothing
    alpha = 1.0 / period
    atr = tr.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    smooth_plus_dm = plus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    smooth_minus_dm = minus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

    # Directional Indicators
    plus_di = 100.0 * smooth_plus_dm / atr.replace(0, np.nan)
    minus_di = 100.0 * smooth_minus_dm / atr.replace(0, np.nan)

    # ADX
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
