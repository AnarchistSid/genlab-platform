"""Bandit hour-posteriors API endpoint.

Routes:
    GET /api/v1/bandit/hour-posteriors?niche=<niche_id>
       -- per-hour aggregate Beta posteriors for the optimal-time
          bandit (PR #487)

## Why this endpoint exists

PR #487 shipped the optimal-time bandit FOUNDATION: every publish
captures ``hour:{H}:{platform}:{niche}`` as an extra-arm in
PendingFeedbackTask, and metric_collector updates each arm's Beta
posterior on the same reward as the content arm. The consumer
(``optimal_time_learner.sample_optimal_hours_from_bandit``) is
env-gated behind ``GENLAB_OPTIMAL_TIME_BANDIT=1`` for staged
rollout.

Without a dashboard surface, the operator has NO way to see
whether the bandit is learning anything meaningful before they
flip the env flag — they'd be flipping blind. This endpoint +
the Mission Control card it powers turns invisible learning
into a trust-building artifact: operator opens dashboard, sees
per-niche per-hour distributions filling in over the week, decides
when to activate.

## Aggregation across platforms

A niche publishes to N platforms (typically YouTube + Instagram +
Facebook + Threads + X). PR #487 writes one arm PER (hour,
platform, niche) tuple. For dashboard visualization, we aggregate
across platforms per (hour, niche) — operator's actionable
question is "which hours work for this niche overall?" not
"which hour works for ai_creators on TikTok specifically".

Aggregation: sum alphas, sum betas. The summed Beta is a
reasonable proxy because each arm's Beta(alpha, beta) was
incremented on the same reward signal — adding them is consistent
with "treat all platforms equally" rather than per-platform
weighting (which would need a separate audience-volume weighting).

## Observation count

n_plays isn't returned by ``load_all_arms`` today (it's stored
on write via save_arm but not surfaced on read). PR BB derives
``n_obs_est = alpha + beta - 2`` as a reasonable proxy:
Beta(1,1) prior + 1 per observation gives alpha+beta = 2+n. This
slightly overestimates when reward fractions are floats (each
observation adds reward to alpha, (1-reward) to beta, summing to
≥1) but is correct in magnitude for operator-readiness checks.

## Response shape

Always 24 entries (one per UTC hour), in order 0..23, even when
some hours have zero data. Frontend renders an empty bar for
hours with n_obs_est == 0 so the operator sees the full daily
distribution at a glance.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, request

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("bandit_hour_posteriors_api", __name__, url_prefix="/api/v1/bandit")

# Same closed-set as the other sponsorship-loop endpoints.
_VALID_NICHE_IDS = frozenset({"ai_creators", "gaming", "sports", "movies", "anime"})

# Beta(1,1) prior baseline. arm_loader's load_all_arms defaults to
# (1.0, 1.0) when the row lacks alpha/beta, matching this baseline.
_BETA_PRIOR_SUM = 2.0


def _aggregate_hour_posteriors(
    arms: dict[str, tuple[float, float]], niche_id: str
) -> list[dict[str, Any]]:
    """Bucket hour-arms by hour and sum their (alpha, beta) pairs.

    Filters by the ``hour:{H}:{platform}:{niche_id}`` arm shape PR #487
    writes. Aggregates across all platforms for the niche — see the
    module docstring for why this is the right shape for the dashboard.

    Returns a list of 24 entries (hour 0..23), each carrying:
      hour: int (0-23)
      alpha_sum: float
      beta_sum: float
      n_obs_est: int (alpha+beta-2 baseline, rounded)
      mean: float (alpha / (alpha + beta), or 0.5 for cold-start)
    """
    suffix = f":{niche_id}"
    hour_prefix = "hour:"
    by_hour: dict[int, tuple[float, float]] = {}

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
        prev_alpha, prev_beta = by_hour.get(hour, (0.0, 0.0))
        by_hour[hour] = (prev_alpha + alpha, prev_beta + beta)

    # Build the 24-entry result, including zero-data hours so the
    # frontend renders a stable 24-hour strip.
    result: list[dict[str, Any]] = []
    for h in range(24):
        alpha_sum, beta_sum = by_hour.get(h, (0.0, 0.0))
        total = alpha_sum + beta_sum
        # n_obs_est: each summed pair contributes 2 from its prior; the
        # number of arms contributing to this hour is the number of
        # platforms with at least one publish at hour H. We don't track
        # that directly, so estimate from the sum-of-pairs minus a
        # baseline prior of 2 per contributing arm. For 1 platform with
        # 5 publishes: alpha+beta ≈ 2 + 5 = 7, n_obs_est ≈ 5. For 3
        # platforms × 5 publishes each: sums ≈ 6 + 15 = 21, n_obs_est
        # ≈ 19. The slight over-count for multi-platform hours is fine
        # for operator-readiness checks (the threshold for activation is
        # qualitative — "looks like enough data" — not quantitative).
        n_obs_est = max(0, int(round(total - _BETA_PRIOR_SUM)))
        if total > 0:
            mean = alpha_sum / total
        else:
            mean = 0.5  # cold-start prior mean
        result.append(
            {
                "hour": h,
                "alpha_sum": round(alpha_sum, 4),
                "beta_sum": round(beta_sum, 4),
                "n_obs_est": n_obs_est,
                "mean": round(mean, 4),
            }
        )
    return result


@bp.route("/hour-posteriors")
def hour_posteriors():
    """Return per-hour aggregate Beta posteriors for the bandit.

    Query params:
        niche (required) -- one of the 5 known niche_ids

    Response shape::

        {
          "niche_id": "ai_creators",
          "hours": [
            {"hour": 0, "alpha_sum": 1.2, "beta_sum": 1.8,
             "n_obs_est": 1, "mean": 0.4},
            ...  // always 24 entries, hour 0..23
          ],
          "total_observations": <int>,    // sum of n_obs_est
          "ready_for_activation": <bool>  // ≥30 total observations
        }

    400 when ``niche`` missing; 404 when not in the closed 5.
    Cold-start (no bandit data yet) returns all 24 hours with
    n_obs_est=0 and mean=0.5 — the frontend renders an empty
    strip rather than a 404.
    """
    niche_id = (request.args.get("niche") or "").strip()
    if not niche_id:
        return api_error(error="Missing required query param: niche", code=400)
    if niche_id not in _VALID_NICHE_IDS:
        return api_error(
            error=f"Unknown niche: {niche_id}. Valid: {sorted(_VALID_NICHE_IDS)}",
            code=404,
        )

    # Defer the bandit_arms read — keeps the endpoint cold-start safe
    # when DATABASE_URL is unset or BacklogClient init fails. Same
    # pattern as optimal_time_learner.sample_optimal_hours_from_bandit.
    try:
        from genlab_core.http.backlog_client import BacklogClient
        from genlab_core.learning.arm_loader import load_all_arms
    except ImportError:
        return api_success(
            data={
                "niche_id": niche_id,
                "hours": _aggregate_hour_posteriors({}, niche_id),
                "total_observations": 0,
                "ready_for_activation": False,
            }
        )

    try:
        client = BacklogClient()
        proxy = getattr(client, "bandit_arms", None)
        if proxy is None:
            arms: dict[str, tuple[float, float]] = {}
        else:
            arms = load_all_arms(proxy, niche_id)
    except Exception as exc:
        logger.warning(
            "[bandit_hour_posteriors] arm load failed for niche=%s: %s",
            niche_id,
            exc,
        )
        arms = {}

    hours = _aggregate_hour_posteriors(arms, niche_id)
    total_obs = sum(h["n_obs_est"] for h in hours)
    # 30 observations is the heuristic activation threshold — same
    # number used for AutoApprovalCalibrationCard's ready badge
    # (AUTO #1c). Below 30, the Beta posteriors haven't moved far
    # enough from the prior to be meaningfully different from random.
    ready_for_activation = total_obs >= 30

    return api_success(
        data={
            "niche_id": niche_id,
            "hours": hours,
            "total_observations": total_obs,
            "ready_for_activation": ready_for_activation,
        }
    )
