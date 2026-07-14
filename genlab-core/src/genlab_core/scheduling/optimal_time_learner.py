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
from datetime import UTC
from typing import Final

logger = logging.getLogger(__name__)

# Lookback window for hourly engagement aggregation. 60 days is enough to
# absorb a weekly cycle (T,W,Th,F all represented multiple times) without
# drowning recent strategy changes in stale data.
_LOOKBACK_DAYS: Final[int] = 60

# Minimum observations before a (niche, platform, hour) tuple is trusted.
# 2026-06-14: lowered from 5 → 3 because the 2026-06-14 learning-system
# audit found the learner returning [] for ALL 5 niches across ALL
# platforms — the per-hour buckets weren't accumulating fast enough at
# typical publish cadences (1 reel/day means ~5 weeks to get 5 obs at
# the same UTC hour). At 3 the bucket starts contributing signal in
# ~3 weeks; the prior_weight bump below absorbs the extra noise.
_MIN_OBSERVATIONS: Final[int] = 3

# Bayesian shrinkage prior weight. Pulls the per-hour score toward the
# global (niche × platform) average with this many phantom observations.
# 2026-06-14: bumped from 10 → 20 to compensate for the lower
# min_observations. With prior_weight=20 and min n=3, the shrunk score
# is dominated 87% by the global average — so a 3-obs spike doesn't
# overrule the 50-obs second-best. As n grows the prior's influence
# decays; by n=20 it's a 50/50 mix.
_SHRINKAGE_PRIOR: Final[float] = 20.0

# 2026-06-14: cold-start fallback — when per-hour buckets all fail the
# min_observations filter, retry at 4-hour granularity (6 bins covering
# the 24h day). Returns ONE bin's center hour (e.g. 14:00 UTC for the
# 12-16 bin) so the scheduler has something better than the static
# default. Disabled by passing min_observations >= MIN_FALLBACK_THRESHOLD.
_FALLBACK_BIN_HOURS: Final[int] = 4
_FALLBACK_MIN_OBS: Final[int] = 3

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


def _query_4hour_bins(
    niche_id: str,
    platform: str,
    lookback_days: int,
    dsn: str,
) -> list[OptimalHour]:
    """Cold-start fallback: aggregate engagement into 4-hour bins.

    Returns up to 6 ``OptimalHour`` entries — one per non-empty bin —
    with ``hour_utc`` set to the bin's CENTER hour (e.g. bin 12-16
    → hour_utc=14). Sample sizes are aggregated across the 4 hours so
    a niche with 1 obs/hour across the 4 hours gets n=4, enough to
    pass _FALLBACK_MIN_OBS=3.

    Same Bayesian shrinkage as the per-hour query. Caller treats the
    result as best-effort signal; the scheduler's collision check
    will still prevent two posts at exactly the same UTC hour.
    """
    try:
        import psycopg  # noqa: F401 — kept for the except below

        from genlab_core.storage.tenant_context import pg_connect

        # SR-A/C/D Tier-2 migration (2026-06-17): both call sites
        # take niche_id as their first positional arg.
        with pg_connect(dsn, niche_id=niche_id, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH per_hour AS (
                      SELECT
                        (EXTRACT(HOUR FROM pa.published_at)::int
                          / %s::int) * %s::int + (%s::int / 2) AS bin_center,
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
                      SELECT AVG(engagement) AS global_avg FROM per_hour
                    )
                    SELECT
                      ph.bin_center,
                      AVG(ph.engagement),
                      COUNT(*),
                      gs.global_avg
                    FROM per_hour ph
                    CROSS JOIN global_stats gs
                    GROUP BY ph.bin_center, gs.global_avg
                    HAVING COUNT(*) >= %s
                    """,
                    (
                        _FALLBACK_BIN_HOURS,
                        _FALLBACK_BIN_HOURS,
                        _FALLBACK_BIN_HOURS,
                        niche_id,
                        platform,
                        lookback_days,
                        _FALLBACK_MIN_OBS,
                    ),
                )
                rows = cur.fetchall()
    except Exception as exc:
        logger.debug(
            "[optimal_time_learner] 4-hour-bin fallback failed for %s/%s: %s",
            niche_id,
            platform,
            exc,
        )
        return []

    if not rows:
        return []

    hours: list[OptimalHour] = []
    for bin_center, avg_eng, n, global_avg in rows:
        n_f = float(n)
        avg_f = float(avg_eng)
        global_f = float(global_avg or 0.0)
        confidence = (n_f * avg_f + _SHRINKAGE_PRIOR * global_f) / (n_f + _SHRINKAGE_PRIOR)
        hours.append(
            OptimalHour(
                hour_utc=int(bin_center) % 24,
                avg_engagement=round(avg_f, 2),
                sample_size=int(n),
                confidence=round(confidence, 3),
            )
        )
    hours.sort(key=lambda h: h.confidence, reverse=True)
    return hours


def sample_optimal_hours_from_bandit(
    niche_id: str,
    platform: str,
    *,
    top_n: int = 3,
) -> list[OptimalHour]:
    """Thompson-sample the top-N hours for (niche, platform) from
    bandit posteriors.

    PR Z (2026-06-23): this is the READ side of the optimal-time
    bandit foundation. PendingFeedbackTask now captures publish-hour
    as ``hour:{H}:{platform}:{niche_id}`` extra-arms, and the
    metric_collector's multi-arm credit pass updates each arm's
    Beta(alpha, beta) posterior on the same reward as the content
    arm. This function samples each hour's Beta and returns the
    top-N hours sorted by descending sample.

    Returns empty list when:
      - BacklogClient init fails (no DSN, no creds)
      - bandit_arms proxy missing
      - no hour arms exist for (niche, platform) yet (cold start)
      - any unexpected error

    Cold-start behavior: the caller (``get_optimal_hours``) checks
    for empty here and falls through to the legacy Bayesian-shrinkage
    heuristic — so the bandit augments rather than replaces the
    heuristic until it has enough data to be confidently better.
    """
    try:
        from genlab_core.http.backlog_client import BacklogClient
        from genlab_core.learning.arm_loader import load_all_arms
    except ImportError:
        return []

    try:
        client = BacklogClient()
    except Exception as exc:  # noqa: BLE001 — bandit sampling is optional
        # WARNING (not silent): distinguishes DATABASE_URL misconfig
        # from real cold-start. Silent [] here made every niche revert
        # to heuristic hours invisibly on prod DB outage.
        logger.warning(
            "[optimal_time] BacklogClient construction failed for niche=%s: %s — "
            "reverting to heuristic hours",
            niche_id,
            exc,
        )
        return []

    proxy = getattr(client, "bandit_arms", None)
    if proxy is None:
        return []

    arms = load_all_arms(proxy, niche_id)
    suffix = f":{platform}:{niche_id}"
    hour_prefix = "hour:"

    # Thompson-sample each matching hour arm.
    import random as _random

    samples: list[tuple[int, float]] = []
    for arm_id, (alpha, beta) in arms.items():
        if not arm_id.startswith(hour_prefix) or not arm_id.endswith(suffix):
            continue
        # Extract H from "hour:{H}:{platform}:{niche_id}"
        try:
            hour_str = arm_id[len(hour_prefix) : arm_id.index(":", len(hour_prefix))]
            hour = int(hour_str)
        except (ValueError, IndexError):
            continue
        if not (0 <= hour <= 23):
            continue
        a = alpha if alpha > 0 else 1.0
        b = beta if beta > 0 else 1.0
        try:
            sample = _random.betavariate(a, b)
        except (ValueError, OverflowError):
            sample = 0.5
        samples.append((hour, sample))

    if not samples:
        return []

    samples.sort(key=lambda x: x[1], reverse=True)
    return [
        OptimalHour(
            hour_utc=hour,
            avg_engagement=sample,  # Beta sample as proxy for engagement
            sample_size=0,  # arm-derived; raw n_plays not surfaced here
            confidence=sample,  # Beta sample IS the score for ranking
        )
        for hour, sample in samples[:top_n]
    ]


def get_optimal_hours(
    niche_id: str,
    platform: str,
    *,
    top_n: int = 3,
    min_observations: int = _MIN_OBSERVATIONS,
    lookback_days: int = _LOOKBACK_DAYS,
) -> list[OptimalHour]:
    """Return up to ``top_n`` hours-of-day for the (niche, platform) pair.

    Two source paths:
      1. **Bandit path** (PR Z, env-gated): when
         ``GENLAB_OPTIMAL_TIME_BANDIT=1``, Thompson-sample from
         hour-arm posteriors written by PR Z's publish-hour capture.
         Returns immediately when the bandit has any data; falls
         through to the heuristic when arms are cold-start empty.
      2. **Heuristic path** (legacy default): Bayesian-shrunk
         engagement aggregation over ``analytics.composite``. Sorted
         descending by shrunk score.

    Empty list when both paths return empty.

    Cached per (niche, platform) for ``_CACHE_TTL_S`` seconds.
    """
    if not niche_id or not platform:
        return []

    # PR Z: bandit path is opt-in via env flag for staged rollout.
    # Ship the producer side first (PendingFeedbackTask hour-arm
    # capture); flip this flag once arms have ≥1 week of observations
    # so cold-start doesn't push worse picks than the heuristic.
    # The cache is intentionally NOT consulted on the bandit path —
    # Thompson sampling is stochastic by design and operator's
    # repeated "Refresh" should reveal posterior variance.
    #
    # Naming drift note (round-3 flag audit 2026-07-08): the canonical
    # name is ``GENLAB_OPTIMAL_TIME_BANDIT_ENABLED`` (matches the
    # ``_ENABLED`` suffix convention every other GENLAB flag uses) but
    # this file originally read ``GENLAB_OPTIMAL_TIME_BANDIT`` (no
    # suffix). Prod .env files still reference the un-suffixed name.
    # The ``legacy_name`` arg preserves backward compat until prod
    # migrates. See ``settings.env_true`` docstring.
    from genlab_core.settings import env_true

    if env_true(
        "GENLAB_OPTIMAL_TIME_BANDIT_ENABLED",
        legacy_name="GENLAB_OPTIMAL_TIME_BANDIT",
    ):
        bandit_hours = sample_optimal_hours_from_bandit(niche_id, platform, top_n=top_n)
        if bandit_hours:
            logger.debug(
                "[optimal_time] bandit sampled %d hours for %s/%s",
                len(bandit_hours),
                niche_id,
                platform,
            )
            return bandit_hours
        # Bandit returned empty → cold-start, fall through to heuristic

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
        import psycopg  # noqa: F401 — kept for the except below

        from genlab_core.storage.tenant_context import pg_connect

        # SR-A/C/D Tier-2 migration (2026-06-17): both call sites
        # take niche_id as their first positional arg.
        with pg_connect(dsn, niche_id=niche_id, connect_timeout=5) as conn:
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
        # 2026-06-14 cold-start fallback: no per-hour bucket met
        # min_observations. Re-query at 4-hour granularity so cold-start
        # niches get *some* signal instead of falling all the way back to
        # static yaml. The fallback returns up to top_n hours derived
        # from the best 4-hour bins.
        fallback = _query_4hour_bins(niche_id, platform, lookback_days, dsn)
        _cache[key] = (fallback, now)
        return fallback[:top_n]

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
