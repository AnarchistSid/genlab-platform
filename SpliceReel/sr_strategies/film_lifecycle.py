"""Film lifecycle stage detection for SpliceReel.

Films exist in three distinct content modes:
  PRE_RELEASE: trailer drops, casting news, production updates
  OPENING_WEEKEND: first 72 hours after theatrical release
  LONG_TAIL: ongoing coverage after opening weekend (reviews, awards, streaming)

The lifecycle stage determines scoring multipliers, hook template pools,
and caption tone.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import Enum


class FilmLifecycleStage(str, Enum):
    PRE_RELEASE = "pre_release"
    OPENING_WEEKEND = "opening_weekend"
    LONG_TAIL = "long_tail"
    UNKNOWN = "unknown"


def detect_lifecycle_stage(
    release_date: datetime | None,
    story_published_at: datetime | None = None,
    config: dict | None = None,
) -> FilmLifecycleStage:
    """Determine where in its lifecycle a film sits.

    Uses the film's release_date (from TMDB), NOT the story publication date.
    If release_date is None, returns UNKNOWN.
    """
    if release_date is None:
        return FilmLifecycleStage.UNKNOWN

    if story_published_at is None:
        story_published_at = datetime.now(UTC)

    if release_date.tzinfo is None:
        release_date = release_date.replace(tzinfo=UTC)
    if story_published_at.tzinfo is None:
        story_published_at = story_published_at.replace(tzinfo=UTC)

    cfg = config or {}
    opening_hours = cfg.get("opening_weekend_hours", 72)
    long_tail_days = cfg.get("long_tail_days", 21)
    pre_release_days = cfg.get("pre_release_days", 30)

    delta = story_published_at - release_date

    if delta < timedelta(0):
        days_until_release = abs(delta.days)
        if days_until_release <= pre_release_days:
            return FilmLifecycleStage.PRE_RELEASE
        return FilmLifecycleStage.UNKNOWN

    if delta <= timedelta(hours=opening_hours):
        return FilmLifecycleStage.OPENING_WEEKEND

    if delta <= timedelta(days=long_tail_days):
        return FilmLifecycleStage.LONG_TAIL

    return FilmLifecycleStage.UNKNOWN


def get_lifecycle_score_multiplier(
    stage: FilmLifecycleStage,
    config: dict,
) -> float:
    """Return score multiplier for this lifecycle stage from config.

    Reads from config["film_lifecycle_multipliers"]. Falls back to defaults.
    """
    multipliers = config.get("film_lifecycle_multipliers", {})
    defaults = {
        FilmLifecycleStage.PRE_RELEASE: 1.0,
        FilmLifecycleStage.OPENING_WEEKEND: 1.6,
        FilmLifecycleStage.LONG_TAIL: 0.7,
        FilmLifecycleStage.UNKNOWN: 0.3,
    }
    return float(multipliers.get(stage.value, defaults.get(stage, 1.0)))
