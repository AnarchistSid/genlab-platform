"""Outcome-based auto-approval readiness signal.

The AUTO #2 auto-approver rollout ladder advances only when the
calibration ratchet crosses ``sample_count >= 30 AND agreement_rate
>= 0.90``. Both signals come from ``auto_approval_calibration`` rows
written when the operator clicks Approve/Reject in the dashboard.

Problem: when the auto-approver approves a blueprint, the operator
DOESN'T click — the blueprint is scheduled and published without
review. So no calibration row is written, and the ratchet stays at
zero samples indefinitely. In prod (2026-06-29 -> 2026-07-23) it
has been stuck at 10% rollout for 24 days.

This module adds an INDEPENDENT readiness signal that doesn't
require operator engagement: whenever an auto-approved blueprint's
reward_48h is finalized, we check if the outcome was "good" (post
performed above a low-bar threshold). Fraction of good outcomes
across auto-approved blueprints is the outcome-based agreement
rate.

Semantics:
    gate approved -> outcome good  = gate was right      (agree)
    gate approved -> outcome bad   = gate was wrong      (disagree)

This is NOT the same as operator agreement. Both signals are useful
and complementary. This module produces a SEPARATE readiness
verdict; the caller decides how to combine them (initial rollout:
operator OR outcome path either can advance the ratchet; future
policy: require both).

Observability first: this module is READ-ONLY. It does NOT write
to auto_approval_calibration. A downstream card renders the signal;
the operator eyeballs it before we wire it into the auto-approver's
advancement path.

Fail-open on every DB error.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)


# Reward threshold above which we consider an outcome "good enough"
# for gate validation. reward_48h is per-arm; on-brand min lift for a
# well-performing post has historically been ~0.05 in this codebase
# (from RewardShaper's threshold proximity boost signal). We use 0.05
# as the low bar — post cleared the "did anything at all" line.
DEFAULT_OUTCOME_GOOD_THRESHOLD: Final[float] = 0.05

# Sample count threshold — mirrors AUTO #2's operator-side threshold
# so operator + outcome signals are comparable at the same n.
MIN_SAMPLES: Final[int] = 30

# Agreement rate threshold. Deliberately lower than the operator
# ratchet's 0.90 because outcome-good is a NOISIER signal than
# operator-approval — early-window rewards fluctuate, platform
# algorithms don't guarantee delivery, etc. Setting 0.75 means "3 in
# 4 auto-approved posts cleared the low bar." This is a starting
# calibration; expect operator to adjust after eyeballing.
DEFAULT_AGREEMENT_RATE_THRESHOLD: Final[float] = 0.75

# Blueprint action_taken_source tag written by auto_approver on
# auto-approvals. Mirrors ``auto_approver.AUTO_APPROVAL_SOURCE_TAG``
# — mirrored here rather than imported to avoid a circular dep in
# a package that dashboards read.
_AUTO_APPROVAL_SOURCE_TAG: Final[str] = "auto_approver_v1"


@dataclass(frozen=True)
class OutcomeReadiness:
    """Per-niche outcome readiness verdict.

    Fields
    ------
    niche_id : str
    window_days : int
        Rolling window over which auto-approved blueprints were
        looked up.
    sample_count : int
        Number of auto-approved blueprints in the window that had a
        measurable outcome (at least one platform with reward_48h
        populated in pending_feedback).
    outcome_good_count : int
        Number of those samples where max(reward_48h across platforms)
        met ``threshold``.
    outcome_good_rate : float
        outcome_good_count / sample_count, or 0.0 for empty samples.
    threshold : float
        The reward_48h low-bar used.
    ready : bool
        True iff sample_count >= MIN_SAMPLES AND outcome_good_rate
        >= DEFAULT_AGREEMENT_RATE_THRESHOLD.
    """

    niche_id: str
    window_days: int
    sample_count: int
    outcome_good_count: int
    outcome_good_rate: float
    threshold: float
    ready: bool


def check_outcome_readiness(
    conn,
    niche_id: str,
    *,
    window_days: int = 14,
    threshold: float = DEFAULT_OUTCOME_GOOD_THRESHOLD,
    min_samples: int = MIN_SAMPLES,
    agreement_rate_threshold: float = DEFAULT_AGREEMENT_RATE_THRESHOLD,
) -> OutcomeReadiness:
    """Compute the outcome-based readiness verdict for one niche.

    Linkage from blueprints to pending_feedback:
        pending_feedback.task_id = f"{blueprints.candidate_id}__{platform}"
    (see PendingFeedbackTask.to_sharepoint_fields at
    feedback_registration.py:120 — ``content_id = candidate_id or
    record_id[:16]``. In prod, ``candidate_id`` is a 64-char hash
    always populated by push_to_backlog. My initial implementation
    used record_id[:16] which never matches — 2026-07-24 discovery.)
    Since one blueprint publishes to N platforms, we aggregate
    ``MAX(reward_48h)`` across all matching pending_feedback rows —
    an outcome is "good" iff ANY platform cleared the threshold.
    This mirrors real-world audience reach: the goal is one viral
    post per blueprint, not uniform performance across all platforms.

    Fail-open: any DB error returns an empty ready=False verdict
    rather than raising.

    Args:
        conn: psycopg connection with dict_row factory.
        niche_id: One of the 5 canonical niche IDs.
        window_days: How far back to look for auto-approved
            blueprints. Default 14 (spans 2 full weekly strategist
            cycles).
        threshold: reward_48h low bar. Default 0.05.
        min_samples: Sample count threshold for ready. Default 30.
        agreement_rate_threshold: Fraction of outcomes that must be
            good for ready. Default 0.75.

    Returns:
        OutcomeReadiness. Never raises.
    """
    try:
        row = conn.execute(
            """
            WITH auto_approved AS (
                SELECT id::text AS blueprint_id, candidate_id
                FROM blueprints
                WHERE niche_id = %s
                  AND action_taken_source = %s
                  AND action_taken = 'approved'
                  AND reviewed_at IS NOT NULL
                  AND reviewed_at > NOW() - make_interval(days => %s)
                  AND candidate_id IS NOT NULL
            ),
            per_bp_outcome AS (
                SELECT
                    aa.blueprint_id,
                    MAX(pf.reward_48h) AS max_reward
                FROM auto_approved aa
                LEFT JOIN pending_feedback pf
                    -- task_id = "{candidate_id}__{platform}" per
                    -- feedback_registration.py:120. Prod discovery
                    -- 2026-07-24: initial impl used blueprint_id[:16]
                    -- which never matches (candidate_id is a 64-char
                    -- hash, always populated).
                    ON pf.task_id LIKE (aa.candidate_id || '__%%')
                    AND pf.reward_48h IS NOT NULL
                GROUP BY aa.blueprint_id
            )
            SELECT
                COUNT(*) FILTER (WHERE max_reward IS NOT NULL)::int
                    AS sample_count,
                COUNT(*) FILTER (WHERE max_reward >= %s)::int
                    AS outcome_good_count
            FROM per_bp_outcome
            """,
            (
                niche_id,
                _AUTO_APPROVAL_SOURCE_TAG,
                window_days,
                threshold,
            ),
        ).fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[outcome_readiness] query failed for niche=%s: %s", niche_id, exc
        )
        return OutcomeReadiness(
            niche_id=niche_id,
            window_days=window_days,
            sample_count=0,
            outcome_good_count=0,
            outcome_good_rate=0.0,
            threshold=threshold,
            ready=False,
        )

    if row is None:
        sample_count = 0
        outcome_good_count = 0
    elif hasattr(row, "get"):
        sample_count = int(row.get("sample_count") or 0)
        outcome_good_count = int(row.get("outcome_good_count") or 0)
    else:
        sample_count = int(row[0] or 0)
        outcome_good_count = int(row[1] or 0)

    rate = (
        outcome_good_count / sample_count if sample_count > 0 else 0.0
    )
    ready = (
        sample_count >= min_samples and rate >= agreement_rate_threshold
    )
    return OutcomeReadiness(
        niche_id=niche_id,
        window_days=window_days,
        sample_count=sample_count,
        outcome_good_count=outcome_good_count,
        outcome_good_rate=rate,
        threshold=threshold,
        ready=ready,
    )


def check_all_niches(
    conn, *, window_days: int = 14
) -> dict[str, OutcomeReadiness]:
    """Convenience wrapper — computes readiness for the 5 canonical
    niches. Order is stable so the dashboard renders consistent
    rows."""
    result: dict[str, OutcomeReadiness] = {}
    for nid in ("ai_creators", "gaming", "sports", "movies", "anime"):
        result[nid] = check_outcome_readiness(
            conn, nid, window_days=window_days
        )
    return result


__all__ = [
    "DEFAULT_AGREEMENT_RATE_THRESHOLD",
    "DEFAULT_OUTCOME_GOOD_THRESHOLD",
    "MIN_SAMPLES",
    "OutcomeReadiness",
    "check_all_niches",
    "check_outcome_readiness",
]
