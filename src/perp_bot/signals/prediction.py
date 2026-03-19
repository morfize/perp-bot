"""Prediction market scoring — regime classification and funding side preference."""

from __future__ import annotations

from enum import Enum

from perp_bot.config import PredictionConfig


class PredictionRegime(Enum):
    NORMAL = "normal"
    HIGH_RISK = "high_risk"
    DOVISH_SHIFT = "dovish_shift"
    HAWKISH_SHIFT = "hawkish_shift"
    CRISIS = "crisis"


def war_risk_score(snapshots: list[dict], market_weights: dict[str, float]) -> float:
    """Compute weighted average war risk probability.

    Args:
        snapshots: list of prediction snapshot dicts with 'market_slug' and 'probability'
        market_weights: mapping of market_slug → weight for war_risk markets

    Returns:
        Weighted average probability (0.0 to 1.0), or 0.0 if no data.
    """
    total_weight = 0.0
    weighted_sum = 0.0
    for snap in snapshots:
        slug = snap["market_slug"]
        if slug in market_weights:
            w = market_weights[slug]
            weighted_sum += snap["probability"] * w
            total_weight += w
    return weighted_sum / total_weight if total_weight > 0 else 0.0


def rate_change_score(snapshots: list[dict]) -> float:
    """Compute net rate direction from rate_change prediction markets.

    Convention:
        - probability > 0.5 → market expects a rate hike → positive score
        - probability < 0.5 → market expects a rate cut → negative score

    Returns:
        Score from -1.0 (strong cut expectation) to +1.0 (strong hike expectation).
        0.0 if no data.
    """
    if not snapshots:
        return 0.0
    # Average probability across rate_change markets, then centre on 0.5
    avg_prob = sum(s["probability"] for s in snapshots) / len(snapshots)
    # Map [0, 1] → [-1, 1]: score = (prob - 0.5) * 2
    return (avg_prob - 0.5) * 2.0


def compute_regime(
    war_risk: float,
    rate_change: float,
    config: PredictionConfig,
) -> PredictionRegime:
    """Determine the prediction regime from current scores.

    Priority: CRISIS > HIGH_RISK > DOVISH/HAWKISH > NORMAL
    """
    if war_risk >= config.war_risk_crisis_threshold:
        return PredictionRegime.CRISIS
    if war_risk >= config.war_risk_threshold:
        return PredictionRegime.HIGH_RISK
    if rate_change < -config.rate_change_threshold:
        return PredictionRegime.DOVISH_SHIFT
    if rate_change > config.rate_change_threshold:
        return PredictionRegime.HAWKISH_SHIFT
    return PredictionRegime.NORMAL


def funding_side_preference(rate_change: float, threshold: float) -> str | None:
    """Suggest a preferred trading side based on rate expectations.

    - Dovish (rate cuts expected) → prefer long (assets tend to rise)
    - Hawkish (rate hikes expected) → prefer short (assets tend to fall)
    - Neutral → no preference

    Returns:
        "long", "short", or None
    """
    if rate_change < -threshold:
        return "long"
    if rate_change > threshold:
        return "short"
    return None
