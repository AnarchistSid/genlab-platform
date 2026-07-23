"""Cross-platform posterior gate — skip low-signal (niche, platform)
combinations without needing operator config changes.

Motivating problem: today's publisher fan-out publishes every reel to
every ``enabled`` platform per the niche's publishing.yaml. But some
(niche, platform) combinations have historically produced near-zero
reward — memory notes flag ``movies threads`` (n=3 avg=0.0023) and
``sports instagram`` (n=25 avg=0.0107) as chronic underperformers.
Continuing to publish there burns rate-limit budget + inflates the
publish-failure metrics without producing engagement.

This module adds a lightweight Beta-posterior gate. Given ``(niche_id,
platform)`` it returns whether the historical signal is strong enough
to justify skipping the platform for this publish. Fail-open in every
error path — if the check is uncertain, publish.

Discipline
==========

* **Beta posterior, not raw average.** ``alpha = sum(reward) + 1``,
  ``beta = n - sum(reward) + 1`` so small-sample low averages don't
  trigger the gate on n=3 rows with zero-signal noise.
* **Cold-start guard.** Requires ``≥ MIN_OBS`` evidence before the
  gate can fire. Below that the fair-open publishing pattern wins.
* **Flag-gated.** ``GENLAB_CROSS_PLATFORM_GATE_ENABLED`` opts in.
  Off by default — one week of shadow observation (via
  ``report_skips_that_would_have_fired``) before flipping is the
  intended rollout.
* **Fail-open on any error.** DB unreachable, unexpected schema,
  psycopg missing → return ``(False, "fallback:...")`` and publish.

Threshold rationale: the posterior mean cutoff of ``0.02`` is
conservative — half of the "chronic underperformer" cases in memory
have avg reward 0.01-0.03. Anything above that clears easily; anything
persistently below is genuinely dead traffic.

See:
* Memory: ``agent-learning-insights-2026-07-22`` — per-(niche, platform)
  reward distribution audit
* Rule #22 sibling: never gate an action on a raw average without
  variance — Beta posterior gives us that.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Final

logger = logging.getLogger(__name__)


_ENABLE_ENV_VAR: Final[str] = "GENLAB_CROSS_PLATFORM_GATE_ENABLED"

# Minimum observations before the gate can fire. n=15 clears
# small-sample cold-start noise but stays reachable within ~2-3 weeks
# of publishing for the "chronic underperformer" combos.
MIN_OBS: Final[int] = 15

# Posterior mean cutoff. Half the historical dud cases sit at
# ~0.01-0.03; 0.02 is the natural threshold above which we're
# probably picking up real engagement signal.
POSTERIOR_MEAN_THRESHOLD: Final[float] = 0.02

# Rolling window for reward history. 30 days matches other reward-
# path windows in the codebase.
LOOKBACK_DAYS: Final[int] = 30

# Cache per (niche, platform) to avoid re-querying on every publish.
# 6-hour TTL — long enough to batch pipeline runs, short enough that
# a niche's posterior shifts on new data land within the day.
_CACHE_TTL_S: Final[float] = 6 * 3600.0

_cache: dict[tuple[str, str], tuple["SkipDecision", float]] = {}


@dataclass(frozen=True)
class SkipDecision:
    """Result of the cross-platform gate check.

    Fields
    ------
    should_skip : bool
        True iff both (a) posterior mean below threshold AND
        (b) enough evidence to trust the posterior. False when
        signal is thin OR posterior looks healthy OR the gate is
        opting out (flag off / DB error).
    posterior_mean : float
        The Beta(α, β) mean. Between 0.0 and 1.0.
    n_observations : int
        Number of pending_feedback rows with reward_48h in the
        window. Not α+β — we exclude the Beta(1,1) prior's baseline
        so cold-start is visible.
    reason : str
        Human-readable diagnosis. Values start with:
        * "skip:" — gate fired, posterior evidence + threshold
        * "publish:" — gate did not fire (signal healthy)
        * "fallback:" — flag off / DB error / cold-start
    """

    should_skip: bool
    posterior_mean: float
    n_observations: int
    reason: str


def _integration_enabled() -> bool:
    """Exact-true match — matches temporal_context / stochastic bandit
    disciplines. Prevents the ``"1"/"yes"/"on"`` ambiguity that has bit
    other flags."""
    return os.environ.get(_ENABLE_ENV_VAR, "") in ("true", "TRUE", "True")


def _query_posterior(niche_id: str, platform: str) -> tuple[float, float, int]:
    """Return ``(alpha, beta, n_obs)`` for the (niche, platform) pair.

    Beta prior: (1, 1). n_obs is the raw pending_feedback count with
    reward_48h set — kept distinct from α+β so the caller can distinguish
    cold-start from real signal.
    """
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        return 1.0, 1.0, 0
    try:
        import psycopg
    except ImportError:
        return 1.0, 1.0, 0

    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*)::int AS n,
                    COALESCE(SUM(reward_48h), 0.0)::float AS sum_r
                FROM pending_feedback
                WHERE niche_id = %s
                  AND platform = %s
                  AND reward_48h IS NOT NULL
                  AND updated_at > NOW() - make_interval(days => %s)
                """,
                (niche_id, platform, LOOKBACK_DAYS),
            ).fetchone()
        if not row:
            return 1.0, 1.0, 0
        n = int(row[0] or 0)
        sum_r = float(row[1] or 0.0)
        # Beta posterior: α = sum(reward)+1, β = n - sum(reward)+1
        alpha = sum_r + 1.0
        beta = max(0.0, n - sum_r) + 1.0
        return alpha, beta, n
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.debug(
            "[cross_platform_gate] posterior query failed niche=%s platform=%s: %s",
            niche_id,
            platform,
            exc,
        )
        return 1.0, 1.0, 0


def should_skip_platform(
    niche_id: str,
    platform: str,
    *,
    min_obs: int = MIN_OBS,
    threshold: float = POSTERIOR_MEAN_THRESHOLD,
) -> SkipDecision:
    """Return whether the publisher should skip this platform for this
    niche based on Beta-posterior evidence.

    Consumers call this from the publisher fan-out loop before firing
    the per-platform publish. Skip decisions are logged so the
    publisher's caller sees the reason.
    """
    if not _integration_enabled():
        return SkipDecision(
            should_skip=False,
            posterior_mean=0.0,
            n_observations=0,
            reason="fallback:flag_off",
        )
    if not niche_id or not platform:
        return SkipDecision(
            should_skip=False,
            posterior_mean=0.0,
            n_observations=0,
            reason="fallback:empty_input",
        )

    key = (niche_id, platform)
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and (now - cached[1]) < _CACHE_TTL_S:
        return cached[0]

    alpha, beta, n_obs = _query_posterior(niche_id, platform)
    posterior_mean = alpha / (alpha + beta)

    if n_obs < min_obs:
        decision = SkipDecision(
            should_skip=False,
            posterior_mean=round(posterior_mean, 4),
            n_observations=n_obs,
            reason=f"fallback:cold_start (n={n_obs} < {min_obs})",
        )
    elif posterior_mean < threshold:
        decision = SkipDecision(
            should_skip=True,
            posterior_mean=round(posterior_mean, 4),
            n_observations=n_obs,
            reason=(
                f"skip:posterior {posterior_mean:.3f} < {threshold:.3f} "
                f"(n={n_obs})"
            ),
        )
    else:
        decision = SkipDecision(
            should_skip=False,
            posterior_mean=round(posterior_mean, 4),
            n_observations=n_obs,
            reason=f"publish:posterior {posterior_mean:.3f} (n={n_obs})",
        )

    _cache[key] = (decision, now)
    return decision


def report_skips_that_would_have_fired(
    niche_id: str,
    platforms: list[str],
) -> list[str]:
    """Shadow-mode diagnostic: what platforms WOULD the gate skip
    right now if it were enabled?

    Bypasses the flag guard so operators can preview the gate's
    behaviour before flipping the flag on. Returns the list of
    platforms that would be skipped.
    """
    skips: list[str] = []
    for platform in platforms:
        # Direct DB probe — bypass the flag gate but keep the
        # threshold + cold-start logic.
        alpha, beta, n_obs = _query_posterior(niche_id, platform)
        posterior_mean = alpha / (alpha + beta)
        if n_obs >= MIN_OBS and posterior_mean < POSTERIOR_MEAN_THRESHOLD:
            skips.append(platform)
    return skips


def reset_cache() -> None:
    """Drop the (niche, platform) cache. Tests only."""
    _cache.clear()


__all__ = [
    "MIN_OBS",
    "POSTERIOR_MEAN_THRESHOLD",
    "SkipDecision",
    "report_skips_that_would_have_fired",
    "reset_cache",
    "should_skip_platform",
]
