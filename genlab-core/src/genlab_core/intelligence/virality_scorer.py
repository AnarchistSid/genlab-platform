"""Pre-publish virality score estimator.

Scores a blueprint 0-100 based on heuristic signals correlated with
high engagement. Used to prioritize which content to publish first.

Signals:
  - Hook quality (features from hook_features.py)
  - Niche performance baseline (historical average reach)
  - Timing (day of week, hour)
  - Content freshness (trending topic match)
  - Video availability (has rendered video)
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Historical niche baselines (from analytics table as of 2026-03-24)
# Updated as more data accumulates
_NICHE_REACH_BASELINE: dict[str, float] = {
    "ai_creators": 6111.0,  # avg reach per post
    "gaming": 57.0,
    "sports": 48.0,
    "movies": 91.0,
    "anime": 50.0,
}

# Day-of-week multipliers (engagement patterns from social media research)
# Index 0 = Monday, 6 = Sunday
_DOW_MULTIPLIER = [0.9, 1.0, 1.0, 1.1, 1.1, 1.2, 1.0]

# Hour multipliers (IST posting windows — engagement peaks)
_HOUR_MULTIPLIER = {
    range(6, 9): 0.8,    # Early morning
    range(9, 12): 1.0,   # Morning
    range(12, 15): 1.2,  # Lunch peak
    range(15, 18): 1.1,  # Afternoon
    range(18, 21): 1.3,  # Evening peak
    range(21, 24): 1.0,  # Night
}


def _get_hour_multiplier(hour: int) -> float:
    for hour_range, mult in _HOUR_MULTIPLIER.items():
        if hour in hour_range:
            return mult
    return 0.8


def score_blueprint(
    blueprint: dict[str, Any],
    niche_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Score a blueprint's virality potential (0-100).

    Returns dict with:
        score: float (0-100)
        signals: dict of individual signal scores
        recommendation: str ("publish", "review", "skip")
    """
    if now is None:
        now = datetime.now(UTC)

    signals: dict[str, float] = {}

    # 1. Hook quality (0-30 points)
    hook = blueprint.get("hook", "") or blueprint.get("hook_text", "") or ""
    hook_score = 0.0
    if hook:
        try:
            from genlab_core.learning.hook_features import build_feature_vector
            feats = build_feature_vector(hook)
            # Longer hooks with specificity score higher
            hook_score += min(feats.get("word_count", 0) * 1.5, 15)
            hook_score += feats.get("has_number", 0) * 5     # Numbers add specificity
            hook_score += feats.get("has_question", 0) * 3   # Questions drive engagement
            hook_score += feats.get("has_superlative", 0) * 4  # Superlatives create curiosity
            hook_score += feats.get("unique_word_ratio", 0) * 3  # Unique = not generic
        except Exception:
            hook_score = 15  # Default middle score
    signals["hook_quality"] = min(hook_score, 30)

    # 2. Niche baseline (0-20 points)
    baseline = _NICHE_REACH_BASELINE.get(niche_id, 50)
    # Higher baseline niche = higher score (BB content naturally reaches more)
    niche_score = min(baseline / 500, 20)  # Caps at 20 for niches with 10K+ avg
    signals["niche_strength"] = round(niche_score, 1)

    # 3. Timing (0-15 points)
    dow_mult = _DOW_MULTIPLIER[now.weekday()]
    # Convert UTC to IST for hour check
    ist_hour = (now.hour + 5) % 24  # Approximate IST
    hour_mult = _get_hour_multiplier(ist_hour)
    timing_score = 10 * dow_mult * hour_mult
    signals["timing"] = round(min(timing_score, 15), 1)

    # 4. Content completeness (0-20 points)
    completeness = 0.0
    if hook and len(hook) >= 20:
        completeness += 5
    if blueprint.get("caption"):
        completeness += 5
    if blueprint.get("video_url") or blueprint.get("visual_paths"):
        completeness += 8  # Video is critical
    if blueprint.get("hashtags"):
        completeness += 2
    signals["completeness"] = min(completeness, 20)

    # 5. Freshness / trending (0-15 points)
    freshness = 10.0  # Default: assume reasonably fresh
    title = blueprint.get("title", "") or ""
    # Boost for trending indicators
    trending_keywords = ["just", "breaking", "new", "first", "leaked", "revealed", "announced"]
    for kw in trending_keywords:
        if kw in title.lower() or kw in hook.lower():
            freshness += 2
            break
    signals["freshness"] = min(freshness, 15)

    # Total score
    total = sum(signals.values())
    total = round(min(total, 100), 1)

    # Recommendation
    if total >= 70:
        recommendation = "publish"
    elif total >= 40:
        recommendation = "review"
    else:
        recommendation = "skip"

    return {
        "score": total,
        "signals": signals,
        "recommendation": recommendation,
    }
