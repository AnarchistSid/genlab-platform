"""Follower-delta fetcher — reads `audience_snapshots` deltas per niche+platform.

## Context

`reward_shaper.py:106` has declared `follower_gained: 0.20` (weight) for
tiktok since 2026-05, and `subscriber_gained: 0.2` (weight) for youtube.
But neither field was ever populated in the metric collection chain —
audit round 4 (2026-07-17) grep confirms zero writes to
`RewardShaper.compute_reward`'s `metric_values["follower_gained"]`.

Effect: the bandit has been optimizing for engagement metrics only
(views, saves, shares, watch time) — NOT for the strategic target of
follower growth. A hook that gets high engagement but drives ZERO new
follows scores just as high as one that gets equal engagement AND
grows the audience by 50 followers.

## What this module does

Reads the `audience_snapshots` table (populated daily by
`scripts/collect_audience_metrics.py`) and computes per-niche +
per-platform follower deltas over a rolling window. Callers pass a
publish_time; we return the follower delta over the [publish_time,
publish_time + window] range.

## Attribution semantics

Attribution is COARSE — audience_snapshots is per-niche + per-platform,
not per-post. Every post published on day D on niche N + platform P
gets credited with the same follower_delta for that day+window. This
is the correct semantic: follower growth on a given day is caused by
the COLLECTION of that day's activity, not by any single post. The
bandit's Beta-posterior aggregation naturally sorts out which content
arms drive growth by observing that "days where arm X was played
gained more followers than days where arm Y was played".

## Design invariants

- **Idempotent**: same (niche, platform, publish_time, window) → same delta
- **Fail-open**: any DB error returns None (metric absent, not zero)
- **Bounded**: caller can cap window to prevent 30-day lookbacks
- **Not per-post**: deliberately not attempting per-post attribution
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


# Maximum window (past this, the "cause" gets too diffuse to attribute).
_MAX_WINDOW_DAYS = 30

# Default: follower delta over the 7-day window following publish.
# This matches when the RewardShaper is invoked at the 168h reward
# collection window (see metric_collector.py). Shorter windows (24h,
# 48h) rarely show meaningful follower movement for new channels.
DEFAULT_WINDOW_DAYS = 7


def get_follower_delta(
    niche_id: str,
    platform: str,
    publish_time: datetime,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    metric_name: str = "followers",
) -> int | None:
    """Return follower delta over the window following `publish_time`.

    Reads `audience_snapshots` for (niche, platform, metric_name)
    at `publish_time.date()` and `publish_time.date() + window_days`.
    Returns the numeric delta (end - start) as an int, or None if
    either endpoint is missing.

    Args:
        niche_id: canonical niche id (`ai_creators`, `gaming`, etc.)
        platform: canonical platform id (`instagram`, `youtube`, etc.)
        publish_time: when the post went live (aware or naive datetime)
        window_days: lookahead window in days (capped at 30)
        metric_name: usually "followers" for IG/FB/Threads, or
            "subscribers" for YT — must match audience_snapshots values

    Returns:
        int follower delta, or None if either snapshot is missing.
        Zero is a REAL zero (no growth), not a missing signal.
    """
    window_days = max(1, min(window_days, _MAX_WINDOW_DAYS))
    start_date = publish_time.date()
    end_date = start_date + timedelta(days=window_days)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        logger.debug("[follower_delta] DATABASE_URL unset; returning None")
        return None

    try:
        import psycopg
    except ImportError:
        logger.debug("[follower_delta] psycopg not installed; returning None")
        return None

    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                # Fetch the closest snapshot on or before each endpoint.
                # The daily collect_audience_metrics timer may occasionally
                # miss a day; falling back to the nearest prior snapshot
                # is safer than returning None on a single missing day.
                cur.execute(
                    """
                    SELECT metric_value
                    FROM audience_snapshots
                    WHERE niche_id = %s AND platform = %s
                      AND metric_name = %s
                      AND snapshot_date <= %s
                    ORDER BY snapshot_date DESC
                    LIMIT 1
                    """,
                    (niche_id, platform, metric_name, start_date),
                )
                start_row = cur.fetchone()
                cur.execute(
                    """
                    SELECT metric_value
                    FROM audience_snapshots
                    WHERE niche_id = %s AND platform = %s
                      AND metric_name = %s
                      AND snapshot_date <= %s
                    ORDER BY snapshot_date DESC
                    LIMIT 1
                    """,
                    (niche_id, platform, metric_name, end_date),
                )
                end_row = cur.fetchone()
    except Exception as exc:
        logger.warning(
            "[follower_delta] query failed for niche=%s platform=%s: %s",
            niche_id, platform, exc,
        )
        return None

    if start_row is None or end_row is None:
        return None

    try:
        return int(end_row[0]) - int(start_row[0])
    except (TypeError, ValueError) as exc:
        logger.warning(
            "[follower_delta] non-int snapshot values for niche=%s platform=%s: %s",
            niche_id, platform, exc,
        )
        return None


def augment_metrics_with_follower_delta(
    metrics: dict[str, Any],
    niche_id: str,
    platform: str,
    publish_time: datetime | None,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict[str, Any]:
    """Add follower_gained / subscriber_gained to a metrics dict in-place.

    Reads audience_snapshots via `get_follower_delta` and writes into
    the metrics dict under the platform-appropriate key
    (`subscriber_gained` for YT, `follower_gained` otherwise) — matching
    the names in `reward_shaper.BASE_WEIGHTS`.

    Returns the augmented metrics dict (same object) for chaining.

    Missing publish_time or missing snapshot data leaves the metric
    absent (not zero) so RewardShaper redistributes the weight
    instead of pinning the follower contribution to a fake zero.
    """
    if publish_time is None:
        return metrics

    # YouTube uses "subscribers" metric_name in audience_snapshots + the
    # weight key is "subscriber_gained". IG/FB/Threads use "followers"
    # metric_name + weight key "follower_gained".
    if platform == "youtube":
        weight_key = "subscriber_gained"
        metric_name = "subscribers"
    else:
        weight_key = "follower_gained"
        metric_name = "followers"

    delta = get_follower_delta(
        niche_id, platform, publish_time,
        window_days=window_days, metric_name=metric_name,
    )
    if delta is not None:
        # Clamp at 0 — negative deltas (follower loss) shouldn't
        # NEGATIVELY score arms. Losses tend to happen for reasons
        # unrelated to any single post (spam-flag on account, algo
        # penalty, etc.). Zero = no growth attributed, which is what
        # we want the bandit to learn to avoid.
        metrics[weight_key] = max(0, delta)

    return metrics


__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "augment_metrics_with_follower_delta",
    "get_follower_delta",
]
