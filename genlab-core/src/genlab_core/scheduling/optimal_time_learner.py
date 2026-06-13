"""Per-platform optimal publish-time learner.

Owner decision 2026-06-12 (see [[feedback-owner-decisions-2026-06-12]]):
"agent also pick optimal publish-times per platform and can publish more
than once if required."

This module mines the `analytics.composite` table to find which hour-of-day
maximises engagement for each (niche × platform) pair, then returns the
top-N hours as candidate publishing slots. The scheduler in
``dashboard.server.core.publishing_queue._next_available_slot`` consults
this learner before falling back to the static ``publishing.yaml``
slots — so when there's signal, the agent self-tunes; when there isn't,
the current 12:00 IST default holds.

Design notes:

- **Source of truth is ``analytics.composite``**, not
  ``publishing_analytics.views``. The 2026-06-13 audit found
  publishing_analytics has views=0 for 99.4% of rows (RENDER findings;
  fix #1 from PR #177 is the long-term fix). analytics.composite carries
  the real engagement numbers — sports YouTube 13K-view hit shows up
  here, not in publishing_analytics.

- **Bayesian shrinkage** prevents tiny-n outliers from dominating. A
  gaming-FB 7:00 UTC entry with n=3 and avg=970 should not beat the
  6:00 UTC entry with n=28 and avg=139, because n=3 is too small to
  trust. The shrinkage pulls the score toward the global average with
  weight proportional to ``prior_weight``.

- **1-hour cache** on the per-(niche, platform) lookup. Recent-post
  engagement doesn't shift on shorter timescales and the scheduler
  consults this on every approve+schedule action.

- **Falls back to None / empty** when DSN is absent, psycopg is missing,
  the query fails, or no hour meets ``min_observations``. Callers
  (the scheduler) MUST handle the empty case by falling back to the
  static yaml slots.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Final

logger = logging.getLogger(__name__)

# Lookback window for hourly engagement aggregation. 60 days is enough to
# absorb a weekly cycle (T,W,Th,F all represented multiple times) without
# drowning recent strategy changes in stale data.
_LOOKBACK_DAYS: Final[int] = 60

# Minimum observations before a (niche, platform, hour) tuple is trusted.
# Picked empirically from the 2026-06-13 data probe: anomalies like
# gaming-FB-7UTC (avg=970, n=3) need filtering, while real signals like
# anime-FB-10UTC (avg=171, n=5) just barely clear.
_MIN_OBSERVATIONS: Final[int] = 5

# Bayesian shrinkage prior weight. Pulls the per-hour score toward the
# global (niche × platform) average with this many phantom observations.
# 10 means "trust the global mean as if you had 10 observations of it";
# a real bucket with n=10 weighs equally with the prior, n=30 weighs 3x.
_SHRINKAGE_PRIOR: Final[float] = 10.0

# Cache TTL — the engagement distribution doesn't shift fast.
_CACHE_TTL_S: Final[int] = 3600

# (niche_id, platform) -> (sorted_hours_list, cached_at_monotonic)
_cache: dict[tuple[str, str], tuple[list[OptimalHour], float]] = {}


@dataclass(frozen=True)
class OptimalHour:
    """One hour-of-day's engagement signal for a (niche, platform) pair."""

    hour_utc: int
    avg_engagement: float
    sample_size: int
    confidence: float  # Bayesian-shrunk score


def get_optimal_hours(
    niche_id: str,
    platform: str,
    *,
    top_n: int = 3,
    min_observations: int = _MIN_OBSERVATIONS,
    lookback_days: int = _LOOKBACK_DAYS,
) -> list[OptimalHour]:
    """Return up to ``top_n`` hours-of-day for the (niche, platform) pair,
    sorted by Bayesian-shrunk engagement score (descending).

    Empty list when:
      - DATABASE_URL not set
      - psycopg unavailable / connection fails
      - No hours meet ``min_observations``
      - Query errors

    Cached per (niche, platform) for ``_CACHE_TTL_S`` seconds.
    """
    if not niche_id or not platform:
        return []

    key = (niche_id, platform)
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and (now - cached[1]) < _CACHE_TTL_S:
        return cached[0][:top_n]

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        _cache[key] = ([], now)
        return []

    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                # Per-hour engagement aggregated over the lookback window.
                # We join analytics → publishing_analytics on (post_id,
                # platform) to recover the publish-time from the latter,
                # because analytics.collected_at is when we read the metric
                # (could be 6h/24h/48h/168h after publish), not when the
                # post went live.
                cur.execute(
                    """
                    WITH per_hour AS (
                      SELECT
                        EXTRACT(HOUR FROM pa.published_at)::int AS hour_utc,
                        a.value::float AS engagement
                      FROM analytics a
                      JOIN publishing_analytics pa USING (post_id, platform)
                      WHERE a.niche_id = %s
                        AND a.platform = %s
                        AND a.metric_type = 'composite'
                        AND a.value > 0
                        AND a.collected_at >= NOW() - (%s::int * INTERVAL '1 day')
                        AND pa.published_at IS NOT NULL
                    ),
                    global_stats AS (
                      SELECT AVG(engagement) AS global_avg, COUNT(*) AS total_n
                      FROM per_hour
                    )
                    SELECT
                      ph.hour_utc,
                      AVG(ph.engagement) AS avg_engagement,
                      COUNT(*) AS sample_size,
                      gs.global_avg
                    FROM per_hour ph
                    CROSS JOIN global_stats gs
                    GROUP BY ph.hour_utc, gs.global_avg
                    HAVING COUNT(*) >= %s
                    """,
                    (niche_id, platform, lookback_days, min_observations),
                )
                rows = cur.fetchall()
    except Exception as exc:
        logger.debug(
            "[optimal_time_learner] Query failed for %s/%s: %s",
            niche_id,
            platform,
            exc,
        )
        _cache[key] = ([], now)
        return []

    if not rows:
        _cache[key] = ([], now)
        return []

    hours: list[OptimalHour] = []
    for hour_utc, avg_eng, n, global_avg in rows:
        # Bayesian shrinkage:
        #   confidence = (n * avg + prior * global_avg) / (n + prior)
        # With prior=10 phantom observations of the global mean, a bucket
        # with n=10 and avg=X yields confidence = (X + global_avg) / 2;
        # n=30 yields (3X + global_avg) / 4 — favoring the larger samples.
        n_f = float(n)
        avg_f = float(avg_eng)
        global_f = float(global_avg or 0.0)
        confidence = (n_f * avg_f + _SHRINKAGE_PRIOR * global_f) / (n_f + _SHRINKAGE_PRIOR)
        hours.append(
            OptimalHour(
                hour_utc=int(hour_utc),
                avg_engagement=round(avg_f, 2),
                sample_size=int(n),
                confidence=round(confidence, 3),
            )
        )

    hours.sort(key=lambda h: h.confidence, reverse=True)
    _cache[key] = (hours, now)
    return hours[:top_n]


def optimal_slots_hhmm(
    niche_id: str,
    platform: str,
    *,
    timezone_str: str = "Asia/Kolkata",
    top_n: int = 3,
) -> list[str]:
    """Convenience wrapper: return optimal slots as ``HH:MM`` strings in
    the given timezone, ready to drop into the publishing scheduler.

    Empty list when the learner has no signal — caller should fall back
    to the static yaml ``schedule_slots`` in that case.

    Example::

        slots = optimal_slots_hhmm("movies", "facebook")
        # → ["15:30", "12:00"]  (10 UTC and 6:30 UTC in IST)
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:  # pragma: no cover — Python 3.8 backport
        from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]
    from datetime import datetime

    hours = get_optimal_hours(niche_id, platform, top_n=top_n)
    if not hours:
        return []

    tz = ZoneInfo(timezone_str)
    today = datetime.now(UTC).date()
    slots: list[str] = []
    seen: set[str] = set()
    for h in hours:
        # Build a datetime in UTC for today at the optimal hour, then
        # convert to local for the HH:MM string. Dropping seconds and
        # de-duping in case two UTC hours map to the same local minute.
        utc_dt = datetime(today.year, today.month, today.day, h.hour_utc, 0, tzinfo=UTC)
        local_dt = utc_dt.astimezone(tz)
        hhmm = local_dt.strftime("%H:%M")
        if hhmm not in seen:
            seen.add(hhmm)
            slots.append(hhmm)
    return slots


def reset_cache() -> None:
    """Drop the per-key cache. Used in tests; not for production."""
    _cache.clear()
