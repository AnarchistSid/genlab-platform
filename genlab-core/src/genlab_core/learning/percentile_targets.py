"""Percentile-relative reward targets backed by the ``analytics`` table.

Fix #6 of the autonomy roadmap. The hardcoded :data:`_METRIC_TARGETS` in
:mod:`genlab_core.learning.reward_shaper` use a single absolute value per
(platform, metric) — e.g. YouTube views target = 200. That works fine for
a "median" channel but produces near-zero rewards on niches where most
posts cluster well below the target (which is the current state for the
ai_creators YouTube channel at avg 0.9 views, max 6) AND saturates at 1.0
on niches where most posts well above (sports YouTube avg 689, max 13,126).

The bandit can't distinguish a content tweak from random noise when both
the "boring" and "great" posts get the same reward.

This module provides a Postgres-backed callable that returns the
:attr:`~genlab_core.learning.reward_shaper.RewardShaper.PERCENTILE_FOR_TARGET`
(default 70th) percentile of recent observations for each (niche × platform
× metric) triple. The :class:`RewardShaper` falls back to the hardcoded
target when this callable returns None (cold start, insufficient data,
DB unreachable).

Self-calibration property:
- A niche where YT posts cluster around 50 views: target=50 → top 30%
  yield reward ≥ 0.7 (the 70th-pctile post gets exactly 0.7; viral 500-view
  post gets 1.0 capped). Above-50 = winner, below-50 = loser.
- A niche where YT posts cluster around 1000 views: target=1000 → top 30%
  yield reward ≥ 0.7. The bandit can finally distinguish 1500-view posts
  from 50-view posts on the same niche.

Cache TTL is 1 hour: the recent-post distribution doesn't shift faster
than that, and a 1-hour stale target is much better than no signal.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Final

logger = logging.getLogger(__name__)

# Read N most-recent observations per triple. Larger N = smoother target
# but slower to adapt to new content strategies. 60 is ~2 months at the
# 1-publish-per-day SLO, enough to be statistically stable.
_LOOKBACK_OBSERVATIONS: Final[int] = 60

# Minimum observations before percentile is meaningful — below this we
# return None so the shaper falls back to the hardcoded absolute target.
_MIN_OBSERVATIONS: Final[int] = 20

# Cache TTL in seconds. The percentile-target query is one row per triple,
# but called per-reward-compute, so a 1-hour cache cuts DB hits ~99%.
_CACHE_TTL_S: Final[int] = 3600

# (niche_id, platform, metric) -> (target_value_or_none, cached_at)
_cache: dict[tuple[str, str, str], tuple[float | None, float]] = {}


def _quantile(values: list[float], q: float) -> float:
    """Linear-interpolation quantile, stdlib-only (numpy not available
    in every install path). Mirrors numpy.percentile method='linear'."""
    if not values:
        return 0.0
    sorted_vs = sorted(values)
    pos = q * (len(sorted_vs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vs) - 1)
    frac = pos - lo
    return sorted_vs[lo] * (1 - frac) + sorted_vs[hi] * frac


def get_percentile_target(
    niche_id: str,
    platform: str,
    metric: str,
    *,
    percentile: float = 0.70,
) -> float | None:
    """Return the percentile-th value of the last N analytics observations.

    Returns None when:
      - DATABASE_URL not set
      - psycopg unavailable / connection fails
      - Fewer than _MIN_OBSERVATIONS recent values for this triple
      - Query errors (caller falls back to hardcoded target)

    Cached for _CACHE_TTL_S seconds per (niche, platform, metric).
    """
    key = (niche_id, platform, metric)
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and (now - cached[1]) < _CACHE_TTL_S:
        return cached[0]

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        _cache[key] = (None, now)
        return None

    try:
        import psycopg  # noqa: F401 — kept for the except below

        from genlab_core.storage.tenant_context import pg_connect

        # SR-A/C/D Tier-2 migration (2026-06-17): niche_id is the
        # function's required arg; the WHERE clause filters by it too
        # (belt + suspenders with RLS).
        with pg_connect(dsn, niche_id=niche_id, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                # analytics.value carries the metric reading; metric_type
                # carries the canonical metric name; platform + niche_id
                # filter for the triple.
                cur.execute(
                    """
                    SELECT value FROM analytics
                    WHERE niche_id = %s AND platform = %s AND metric_type = %s
                      AND value > 0
                    ORDER BY collected_at DESC
                    LIMIT %s
                    """,
                    (niche_id, platform, metric, _LOOKBACK_OBSERVATIONS),
                )
                values = [float(row[0]) for row in cur.fetchall()]
    except Exception as exc:
        logger.debug(
            "[percentile_targets] Query failed for %s/%s/%s: %s",
            niche_id,
            platform,
            metric,
            exc,
        )
        _cache[key] = (None, now)
        return None

    if len(values) < _MIN_OBSERVATIONS:
        _cache[key] = (None, now)
        return None

    target = _quantile(values, percentile)
    _cache[key] = (target if target > 0 else None, now)
    return target if target > 0 else None


def reset_cache() -> None:
    """Drop the cache. Used in tests; not for production."""
    _cache.clear()
