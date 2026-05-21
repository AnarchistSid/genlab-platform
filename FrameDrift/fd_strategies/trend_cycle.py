"""Anime trend cycle stage detection for FrameDrift.

Anime trends follow an S-curve adoption model:
  EMERGING: being picked up by early anime media but not yet mainstream.
            First-mover advantage for content.
  PEAK:     appearing across multiple outlets simultaneously.
            High engagement but audience saturation starting.
  DECLINING: already heavily covered. Low incremental value.
  UNKNOWN:  insufficient signal to classify.

Determined by cross-source mention velocity:
  1. Cross-source mention count (same trend in 3+ outlets = PEAK)
  2. Time since first mention (24h = EMERGING, 48-72h = PEAK, 96h+ = DECLINING)

Score multipliers:
  EMERGING:  1.4  (first-mover advantage)
  PEAK:      1.0  (baseline — competitive but relevant)
  DECLINING: 0.5  (low incremental value)
  UNKNOWN:   0.7  (slight penalty for unclear signal)
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum


class TrendCycleStage(StrEnum):
    EMERGING = "emerging"
    PEAK = "peak"
    DECLINING = "declining"
    UNKNOWN = "unknown"


def detect_trend_cycle(
    first_seen_at: datetime | None,
    source_mention_count: int,
    config: dict | None = None,
) -> TrendCycleStage:
    """Detect where a trend sits in its adoption cycle.

    Args:
        first_seen_at: When the trend was first observed across any source.
        source_mention_count: How many distinct sources have covered this trend.
        config: Optional niche config with freshness.trend_cycle_modes overrides.

    Returns:
        TrendCycleStage enum value.
    """
    if first_seen_at is None:
        return TrendCycleStage.UNKNOWN

    now = datetime.now(UTC)
    if first_seen_at.tzinfo is None:
        first_seen_at = first_seen_at.replace(tzinfo=UTC)

    age_hours = (now - first_seen_at).total_seconds() / 3600

    # Read thresholds from config or use defaults
    modes = (config or {}).get("freshness", {}).get("trend_cycle_modes", {})
    emerging_window = modes.get("emerging_window_hours", 24)
    peak_window = modes.get("peak_window_hours", 72)
    modes.get("declining_threshold_hours", 96)

    # Classification matrix: age × mention count
    if source_mention_count >= 4:
        if age_hours <= peak_window:
            return TrendCycleStage.PEAK
        return TrendCycleStage.DECLINING

    if source_mention_count >= 2:
        if age_hours <= emerging_window:
            return TrendCycleStage.EMERGING
        if age_hours <= peak_window:
            return TrendCycleStage.PEAK
        return TrendCycleStage.DECLINING

    # Single source
    if age_hours <= 12:
        return TrendCycleStage.EMERGING
    return TrendCycleStage.UNKNOWN


def get_trend_cycle_multiplier(stage: TrendCycleStage, config: dict) -> float:
    """Returns score multiplier for this trend cycle stage from config."""
    multipliers = config.get("trend_cycle_multipliers", {})
    defaults = {
        "emerging": 1.4,
        "peak": 1.0,
        "declining": 0.5,
        "unknown": 0.7,
    }
    return float(multipliers.get(stage.value, defaults.get(stage.value, 1.0)))
