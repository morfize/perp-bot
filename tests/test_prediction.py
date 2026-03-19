"""Tests for prediction market scoring and regime integration."""

import numpy as np
import pandas as pd

from perp_bot.config import (
    BotConfig,
    DataConfig,
    ExecutionConfig,
    PredictionConfig,
    PredictionMarketDef,
    RiskConfig,
    SignalConfig,
    TradingConfig,
)
from perp_bot.data.db import Database
from perp_bot.risk.manager import RiskManager
from perp_bot.signals.engine import Signal, SignalEngine
from perp_bot.signals.prediction import (
    PredictionRegime,
    compute_regime,
    funding_side_preference,
    rate_change_score,
    war_risk_score,
)


def _pred_config(**overrides) -> PredictionConfig:
    defaults = {
        "enabled": True,
        "poll_interval_minutes": 15,
        "war_risk_threshold": 0.4,
        "war_risk_crisis_threshold": 0.7,
        "rate_change_threshold": 0.3,
        "position_size_reduction": 0.5,
        "markets": [],
    }
    defaults.update(overrides)
    return PredictionConfig(**defaults)


def _bot_config(prediction=None) -> BotConfig:
    return BotConfig(
        trading=TradingConfig(
            symbols=["ETH"], leverage=3,
            capital_usd=670.0, margin_usage_limit=0.5,
        ),
        signals=SignalConfig(
            zscore_lookback=20, zscore_entry_threshold=2.0,
            zscore_exit_threshold=0.3, zscore_stop_threshold=3.0,
            bollinger_period=20, bollinger_std=2.0,
            rsi_period=14, rsi_overbought=70, rsi_oversold=30,
            adx_period=14, adx_threshold=25,
        ),
        risk=RiskConfig(
            max_loss_per_trade_pct=0.03, daily_loss_limit_pct=0.08,
            max_positions=1, cooldown_seconds=1800,
            position_timeout_hours=24,
        ),
        data=DataConfig(
            timeframes=["15m"], primary_timeframe="15m",
            history_days=90, db_path=":memory:",
        ),
        execution=ExecutionConfig(
            order_type="limit", taker_fallback_seconds=30,
            use_server_side_stop=True,
        ),
        mode="paper",
        prediction=prediction,
    )


def _signal_config() -> SignalConfig:
    return SignalConfig(
        zscore_lookback=20, zscore_entry_threshold=2.0, zscore_exit_threshold=0.3,
        zscore_stop_threshold=3.0, bollinger_period=20, bollinger_std=2.0,
        rsi_period=14, rsi_overbought=70, rsi_oversold=30, adx_period=14, adx_threshold=25,
    )


def _make_candles(close_prices: list[float], n_warmup: int = 50) -> pd.DataFrame:
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


# === war_risk_score tests ===

class TestWarRiskScore:
    def test_single_market(self):
        snapshots = [{"market_slug": "iran", "probability": 0.6}]
        weights = {"iran": 1.0}
        assert war_risk_score(snapshots, weights) == 0.6

    def test_weighted_average(self):
        snapshots = [
            {"market_slug": "iran", "probability": 0.8},
            {"market_slug": "ukraine", "probability": 0.2},
        ]
        weights = {"iran": 0.6, "ukraine": 0.4}
        expected = (0.8 * 0.6 + 0.2 * 0.4) / (0.6 + 0.4)
        assert abs(war_risk_score(snapshots, weights) - expected) < 1e-10

    def test_empty_snapshots(self):
        assert war_risk_score([], {"iran": 1.0}) == 0.0

    def test_unknown_slugs_ignored(self):
        snapshots = [{"market_slug": "unknown", "probability": 0.9}]
        weights = {"iran": 1.0}
        assert war_risk_score(snapshots, weights) == 0.0


# === rate_change_score tests ===

class TestRateChangeScore:
    def test_rate_hike(self):
        snapshots = [{"probability": 0.8}]
        score = rate_change_score(snapshots)
        assert score > 0  # hike expectation → positive

    def test_rate_cut(self):
        snapshots = [{"probability": 0.2}]
        score = rate_change_score(snapshots)
        assert score < 0  # cut expectation → negative

    def test_neutral(self):
        snapshots = [{"probability": 0.5}]
        assert rate_change_score(snapshots) == 0.0

    def test_empty(self):
        assert rate_change_score([]) == 0.0

    def test_range(self):
        # Full hike certainty
        assert rate_change_score([{"probability": 1.0}]) == 1.0
        # Full cut certainty
        assert rate_change_score([{"probability": 0.0}]) == -1.0


# === compute_regime tests ===

class TestComputeRegime:
    def test_normal(self):
        config = _pred_config()
        assert compute_regime(0.2, 0.1, config) == PredictionRegime.NORMAL

    def test_high_risk(self):
        config = _pred_config()
        assert compute_regime(0.5, 0.0, config) == PredictionRegime.HIGH_RISK

    def test_crisis(self):
        config = _pred_config()
        assert compute_regime(0.8, 0.0, config) == PredictionRegime.CRISIS

    def test_crisis_trumps_rate(self):
        """CRISIS takes priority over rate-based regimes."""
        config = _pred_config()
        assert compute_regime(0.8, -0.5, config) == PredictionRegime.CRISIS

    def test_dovish(self):
        config = _pred_config()
        assert compute_regime(0.1, -0.5, config) == PredictionRegime.DOVISH_SHIFT

    def test_hawkish(self):
        config = _pred_config()
        assert compute_regime(0.1, 0.5, config) == PredictionRegime.HAWKISH_SHIFT

    def test_high_risk_trumps_rate(self):
        """HIGH_RISK takes priority over DOVISH/HAWKISH."""
        config = _pred_config()
        assert compute_regime(0.5, -0.5, config) == PredictionRegime.HIGH_RISK


# === funding_side_preference tests ===

class TestFundingSidePreference:
    def test_dovish_prefers_long(self):
        assert funding_side_preference(-0.5, 0.3) == "long"

    def test_hawkish_prefers_short(self):
        assert funding_side_preference(0.5, 0.3) == "short"

    def test_neutral_no_preference(self):
        assert funding_side_preference(0.1, 0.3) is None


# === Signal engine with regime ===

class TestSignalEngineRegime:
    def test_crisis_blocks_entry(self):
        engine = SignalEngine(_signal_config())
        df = _make_candles([100.0] * 30)
        df = engine.compute_indicators(df)
        # Force conditions that would normally trigger short entry
        df.loc[df.index[-1], "zscore"] = 2.5
        df.loc[df.index[-1], "rsi"] = 75
        df.loc[df.index[-1], "bb_upper"] = 99.0
        df.loc[df.index[-1], "adx"] = 15
        result = engine.evaluate(df, position_side=None, prediction_regime=PredictionRegime.CRISIS)
        assert result.signal == Signal.NONE
        assert "crisis" in result.reason

    def test_crisis_allows_exit(self):
        engine = SignalEngine(_signal_config())
        df = _make_candles([100.0] * 30)
        df = engine.compute_indicators(df)
        df.loc[df.index[-1], "zscore"] = 0.1
        result = engine.evaluate(
            df, position_side="long",
            prediction_regime=PredictionRegime.CRISIS,
        )
        assert result.signal == Signal.CLOSE

    def test_high_risk_tightens_entry(self):
        """Z-score of 2.1 should be enough for NORMAL but not HIGH_RISK."""
        engine = SignalEngine(_signal_config())
        df = _make_candles([100.0] * 30)
        df = engine.compute_indicators(df)
        df.loc[df.index[-1], "zscore"] = 2.1
        df.loc[df.index[-1], "rsi"] = 75
        df.loc[df.index[-1], "bb_upper"] = 99.0
        df.loc[df.index[-1], "adx"] = 15

        # NORMAL: should trigger
        normal_result = engine.evaluate(
            df, position_side=None,
            prediction_regime=PredictionRegime.NORMAL,
        )
        assert normal_result.signal == Signal.SHORT

        # HIGH_RISK: z=2.1 < 2.5 threshold, should NOT trigger
        high_risk_result = engine.evaluate(
            df, position_side=None,
            prediction_regime=PredictionRegime.HIGH_RISK,
        )
        assert high_risk_result.signal == Signal.NONE

    def test_high_risk_earlier_exit(self):
        """Z-score of 0.4 should hold in NORMAL but exit in HIGH_RISK."""
        engine = SignalEngine(_signal_config())
        df = _make_candles([100.0] * 30)
        df = engine.compute_indicators(df)
        df.loc[df.index[-1], "zscore"] = 0.4

        # NORMAL: z=0.4 > 0.3 threshold, should NOT close
        normal_result = engine.evaluate(
            df, position_side="long",
            prediction_regime=PredictionRegime.NORMAL,
        )
        assert normal_result.signal == Signal.NONE

        # HIGH_RISK: z=0.4 < 0.5 threshold, should close
        high_risk_result = engine.evaluate(
            df, position_side="long",
            prediction_regime=PredictionRegime.HIGH_RISK,
        )
        assert high_risk_result.signal == Signal.CLOSE

    def test_prediction_regime_in_result(self):
        engine = SignalEngine(_signal_config())
        df = _make_candles([100.0] * 30)
        df = engine.compute_indicators(df)
        result = engine.evaluate(df, prediction_regime=PredictionRegime.HIGH_RISK)
        assert result.prediction_regime == PredictionRegime.HIGH_RISK

    def test_normal_regime_backward_compatible(self):
        """Calling evaluate without regime args should behave identically to before."""
        engine = SignalEngine(_signal_config())
        df = _make_candles([100.0] * 30)
        df = engine.compute_indicators(df)
        result = engine.evaluate(df)
        assert result.prediction_regime == PredictionRegime.NORMAL


# === Risk manager with regime ===

class TestRiskManagerRegime:
    def test_normal_size_unchanged(self):
        config = _bot_config()
        db = Database(":memory:")
        rm = RiskManager(config, db)
        assert rm.compute_position_size(PredictionRegime.NORMAL) == 1005.0

    def test_high_risk_reduces_size(self):
        pred = _pred_config(position_size_reduction=0.5)
        config = _bot_config(prediction=pred)
        db = Database(":memory:")
        rm = RiskManager(config, db)
        size = rm.compute_position_size(PredictionRegime.HIGH_RISK)
        assert size == 1005.0 * 0.5

    def test_crisis_zero_size(self):
        config = _bot_config()
        db = Database(":memory:")
        rm = RiskManager(config, db)
        assert rm.compute_position_size(PredictionRegime.CRISIS) == 0.0

    def test_no_prediction_config_normal_size(self):
        """Without prediction config, HIGH_RISK doesn't reduce size."""
        config = _bot_config(prediction=None)
        db = Database(":memory:")
        rm = RiskManager(config, db)
        # HIGH_RISK without prediction config → no reduction applied
        size = rm.compute_position_size(PredictionRegime.HIGH_RISK)
        assert size == 1005.0

    def test_backward_compatible_no_args(self):
        config = _bot_config()
        db = Database(":memory:")
        rm = RiskManager(config, db)
        assert rm.compute_position_size() == 1005.0


# === Database prediction methods ===

class TestDatabasePredictions:
    def test_insert_and_get_latest(self):
        db = Database(":memory:")
        rows = [
            {
                "source": "polymarket", "market_id": "abc123", "market_slug": "iran",
                "category": "war_risk", "timestamp": 1000, "probability": 0.4, "volume_24h": 50000,
            },
            {
                "source": "polymarket", "market_id": "abc123", "market_slug": "iran",
                "category": "war_risk", "timestamp": 2000, "probability": 0.6, "volume_24h": 55000,
            },
        ]
        db.insert_prediction_snapshots(rows)
        latest = db.get_latest_predictions()
        assert len(latest) == 1
        assert latest[0]["probability"] == 0.6
        assert latest[0]["timestamp"] == 2000

    def test_insert_ignores_duplicates(self):
        db = Database(":memory:")
        row = {
            "source": "kalshi", "market_id": "FED-1", "market_slug": "fed_rate",
            "category": "rate_change", "timestamp": 1000, "probability": 0.5, "volume_24h": 100,
        }
        assert db.insert_prediction_snapshots([row]) == 1
        assert db.insert_prediction_snapshots([row]) == 0

    def test_get_prediction_history(self):
        db = Database(":memory:")
        rows = [
            {
                "source": "polymarket", "market_id": "abc", "market_slug": "iran",
                "category": "war_risk", "timestamp": t,
                "probability": 0.3 + t * 0.001, "volume_24h": 100,
            }
            for t in range(10)
        ]
        db.insert_prediction_snapshots(rows)
        history = db.get_prediction_history("iran", start_time=5, limit=100)
        assert len(history) == 5
        assert history[0]["timestamp"] == 5

    def test_end_to_end_regime_transition(self):
        """Insert snapshots → compute regime → verify signal changes."""
        db = Database(":memory:")

        # Insert war risk snapshots at 0.6 → should trigger HIGH_RISK
        pred_cfg = _pred_config(
            markets=[
                PredictionMarketDef(
                    slug="iran", source="polymarket",
                    market_id="abc", category="war_risk",
                    weight=1.0,
                ),
            ],
        )
        db.insert_prediction_snapshots([{
            "source": "polymarket", "market_id": "abc",
            "market_slug": "iran", "category": "war_risk",
            "timestamp": 1000, "probability": 0.6,
            "volume_24h": 50000,
        }])

        # Compute regime from DB (mirrors main._compute_prediction_state)
        snapshots = db.get_latest_predictions()
        war_snaps = [
            s for s in snapshots if s["category"] == "war_risk"
        ]
        w_risk = war_risk_score(
            war_snaps,
            {m.slug: m.weight for m in pred_cfg.markets},
        )
        regime = compute_regime(w_risk, 0.0, pred_cfg)
        assert regime == PredictionRegime.HIGH_RISK

        # Build candles with z=2.1: passes NORMAL (2.0) but not
        # HIGH_RISK (2.5)
        engine = SignalEngine(_signal_config())
        df = _make_candles([100.0] * 30)
        df = engine.compute_indicators(df)
        df.loc[df.index[-1], "zscore"] = 2.1
        df.loc[df.index[-1], "rsi"] = 75
        df.loc[df.index[-1], "bb_upper"] = 99.0
        df.loc[df.index[-1], "adx"] = 15

        normal = engine.evaluate(
            df, prediction_regime=PredictionRegime.NORMAL,
        )
        assert normal.signal == Signal.SHORT

        high_risk = engine.evaluate(df, prediction_regime=regime)
        assert high_risk.signal == Signal.NONE
        assert high_risk.prediction_regime == PredictionRegime.HIGH_RISK
