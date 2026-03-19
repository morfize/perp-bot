"""Tests for self-implemented technical indicators."""

import numpy as np
import pandas as pd

from perp_bot.signals.indicators import adx, bollinger_bands, hurst_exponent, rsi, zscore


def _random_prices(n: int = 100, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    returns = rng.normal(0, 0.02, n)
    prices = 100 * np.exp(np.cumsum(returns))
    return pd.Series(prices)


class TestZScore:
    def test_mean_returns_zero(self):
        """A constant series should have Z-score of 0."""
        series = pd.Series([100.0] * 30)
        z = zscore(series, 20)
        # Z-score is NaN where std=0 (constant series)
        assert z.iloc[-1] != z.iloc[-1] or z.iloc[-1] == 0  # NaN or 0

    def test_above_mean_is_positive(self):
        series = pd.Series(list(range(50, 70)) + [100])
        z = zscore(series, 20)
        assert z.iloc[-1] > 0

    def test_below_mean_is_negative(self):
        series = pd.Series(list(range(50, 70)) + [30])
        z = zscore(series, 20)
        assert z.iloc[-1] < 0


class TestBollingerBands:
    def test_upper_above_lower(self):
        series = _random_prices()
        mid, upper, lower = bollinger_bands(series, 20, 2.0)
        valid = mid.dropna().index
        assert (upper[valid] >= mid[valid]).all()
        assert (mid[valid] >= lower[valid]).all()

    def test_band_width_scales_with_std(self):
        series = _random_prices()
        _, upper1, lower1 = bollinger_bands(series, 20, 1.0)
        _, upper2, lower2 = bollinger_bands(series, 20, 2.0)
        valid = upper1.dropna().index
        width1 = (upper1 - lower1)[valid]
        width2 = (upper2 - lower2)[valid]
        assert (width2 > width1).all()


class TestRSI:
    def test_uptrend_rsi_high(self):
        """Steadily rising prices should give RSI > 50."""
        series = pd.Series(np.linspace(100, 150, 50))
        r = rsi(series, 14)
        assert r.iloc[-1] > 70

    def test_downtrend_rsi_low(self):
        series = pd.Series(np.linspace(150, 100, 50))
        r = rsi(series, 14)
        assert r.iloc[-1] < 30

    def test_rsi_bounded(self):
        series = _random_prices(200)
        r = rsi(series, 14)
        valid = r.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()


class TestHurstExponent:
    def test_random_walk_near_half(self):
        """A random walk should have Hurst exponent close to 0.5."""
        rng = np.random.default_rng(42)
        prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 500)))
        h = hurst_exponent(pd.Series(prices))
        assert 0.35 < h < 0.65

    def test_mean_reverting_below_half(self):
        """A strongly mean-reverting series should have H < 0.5."""
        rng = np.random.default_rng(99)
        # Generate mean-reverting prices (Ornstein-Uhlenbeck-like)
        n = 500
        prices = np.zeros(n)
        prices[0] = 100
        for i in range(1, n):
            prices[i] = prices[i - 1] + 0.3 * (100 - prices[i - 1]) + rng.normal(0, 0.5)
        h = hurst_exponent(pd.Series(prices))
        assert h < 0.5

    def test_trending_above_half(self):
        """A trending series should have H > 0.5."""
        prices = pd.Series(np.linspace(100, 200, 500))
        h = hurst_exponent(prices)
        assert h > 0.5

    def test_short_series_returns_default(self):
        """Too few data points should return 0.5 (random walk assumption)."""
        h = hurst_exponent(pd.Series([100, 101, 102]))
        assert h == 0.5


class TestADX:
    def test_trending_market_high_adx(self):
        """Strong trend should produce ADX > 25."""
        n = 100
        high = pd.Series(np.linspace(100, 200, n)) + 1
        low = pd.Series(np.linspace(100, 200, n)) - 1
        close = pd.Series(np.linspace(100, 200, n))
        a = adx(high, low, close, 14)
        assert a.iloc[-1] > 25

    def test_range_bound_low_adx(self):
        """Choppy sideways market should produce lower ADX."""
        rng = np.random.default_rng(123)
        n = 200
        base = 100 + rng.normal(0, 0.5, n).cumsum()
        # Mean-revert aggressively
        base = 100 + (base - base.mean()) * 0.1
        high = pd.Series(base + rng.uniform(0.5, 1.5, n))
        low = pd.Series(base - rng.uniform(0.5, 1.5, n))
        close = pd.Series(base)
        a = adx(high, low, close, 14)
        # Range-bound ADX should be lower than trending
        assert a.iloc[-1] < 30
