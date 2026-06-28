"""IPS counterfactual replay over per-decision propensities.

Consumer-side companion to PR #634 (``pending_feedback.propensity``
column) + PR #636 (``_classify_arm_with_propensity`` wire-through).
Until this module landed, propensity was shipped infrastructure with
no readers — every decision logged a float that nothing ever consumed.

What this module computes
=========================

For each bandit arm, compute the Inverse Propensity Score (IPS) reward
estimator over the population of decisions where the arm was selected
AND the 48h reward window has closed:

    IPS(arm) = (1 / N_a) * sum_i [ reward_i * 1{arm_i == arm} / p_i ]

where:

* ``N_a`` is the count of decisions assigned to ``arm`` that have a
  populated ``reward_48h``.
* ``p_i`` is the propensity logged at decision time, truncated below
  at ``MIN_PROPENSITY = 1e-3`` to bound the importance weight (the
  classic IPS variance-explosion mitigation; see Bottou et al. 2013,
  "Counterfactual Reasoning and Learning Systems").

The truncation floor here (1e-3) is intentionally LARGER than the
producer-side floor in ``learning/linucb.py`` (``_MIN_PROPENSITY =
1e-6``). The producer floor exists only to avoid log(0) / div-by-0
in the softmax computation — it preserves the full propensity dynamic
range on the write side. Replay-side truncation at 1e-3 then caps the
maximum importance weight at 1000, which is the standard IPS practice
for keeping variance bounded over realistic sample sizes. Without this
trimming, a single near-zero-propensity outlier would dominate the
estimator.

Why a separate ``naive_reward`` is also returned
-------------------------------------------------

The naive mean ``(1/N_a) * sum_i reward_i`` is what every existing
bandit dashboard already shows. IPS only differs from naive when the
selection probability is non-uniform across the population — i.e.
the data was actually collected under a stochastic policy. In
deterministic mode (``GENLAB_LINUCB_STOCHASTIC_ENABLED=0``), every
recorded propensity is 1.0 and IPS reduces exactly to the naive mean.
Showing both side by side makes that degeneracy obvious and prevents
operators from mistaking "IPS confirms naive" for "IPS validated".

Effective sample size diagnostic
--------------------------------

ESS = (sum w_i)^2 / sum w_i^2 where w_i = 1/p_i. When weights are
uniform (deterministic mode → all weights = 1), ESS = N exactly.
When weights are concentrated on a few decisions (rare arm, low
propensity), ESS drops sharply and the IPS estimate is high-variance.
Conventional rule-of-thumb: ESS < N/2 → estimate is dominated by a
few high-weight observations → treat with skepticism.

Public surface
==============

* :func:`load_decisions` — read ``pending_feedback`` rows with
  ``propensity IS NOT NULL`` filtered by niche/since.
* :func:`compute_ips_per_arm` — apply the formula above per arm,
  returning a dict ``{arm_id: IPSEstimate}``.
* :func:`compute_doubly_robust_per_arm` — DR estimator stub returning
  None for every arm. DR requires a fitted reward model per (arm,
  context); the hook_classifier exists but doesn't expose a clean
  ``predict(arm, context) -> reward`` surface yet. Tracked as
  follow-up; IPS alone is sufficient for v1.

See ``docs/AGENT-AUTONOMY-RESEARCH.md`` Move #8 for the broader
context.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def pg_connect(*args, **kwargs):
    """Lazy proxy to :func:`genlab_core.storage.tenant_context.pg_connect`.

    Defined here as a module-level callable so tests can
    ``monkeypatch.setattr("genlab_core.learning.ips_replay.pg_connect",
    fake)`` without importing or mocking psycopg directly. The real
    helper is imported on first call; the proxy adds zero cost when
    the real path is taken.
    """
    from genlab_core.storage.tenant_context import pg_connect as _real

    return _real(*args, **kwargs)


def _dict_row_factory():
    """Lazy import of psycopg's dict_row.

    Same pattern as :func:`pg_connect` — keep the module loadable in
    environments where psycopg isn't installed (e.g. dashboard-only
    deployments).
    """
    from psycopg.rows import dict_row

    return dict_row


# Replay-side propensity truncation floor. Caps max importance weight
# at 1/MIN_PROPENSITY = 1000. Larger than the producer-side floor
# (1e-6) on purpose — see module docstring.
MIN_PROPENSITY: float = 1e-3

# Minimum decisions-with-reward per arm before IPS estimate is returned.
# Below this, IPS variance is too high for any meaningful comparison;
# the estimator field is set to None and the operator sees "n_rew" but
# not an IPS column. Conventional offline-policy-eval default.
MIN_DECISIONS_FOR_ESTIMATE: int = 10


@dataclass(frozen=True)
class DecisionRecord:
    """One pending_feedback row materialised for IPS replay.

    ``reward`` is ``None`` when the 48h window hasn't closed yet
    (``reward_48h IS NULL`` on the source row). Such records are
    loaded so the operator can see "n_decisions vs n_with_reward"
    in the output, but they're excluded from the IPS sum itself —
    you can't IPS-correct an outcome you don't have.
    """

    decision_id: str
    niche_id: str
    arm_id: str
    propensity: float
    reward: float | None
    decided_at: datetime
    platform: str


@dataclass
class IPSEstimate:
    """Per-arm IPS counterfactual reward estimate + diagnostics.

    All numeric fields are ``float | None``. ``None`` indicates the
    arm had too few observations (``n_with_reward < MIN_DECISIONS_FOR_ESTIMATE``)
    to compute a stable estimate. The arm row is still surfaced so the
    operator can see "this arm exists but isn't measurable yet."
    """

    arm_id: str
    n_decisions: int
    n_with_reward: int
    ips_reward: float | None
    naive_reward: float | None
    relative_lift: float | None
    effective_sample_size: float
    # IPS weights actually used (post-truncation), one per scored decision.
    # Exposed for downstream variance diagnostics (e.g. distribution plots).
    # Kept off the default __repr__ to avoid log-spam at large N.
    weights: list[float] = field(default_factory=list, repr=False)


@dataclass
class DREstimate:
    """Doubly-robust estimator placeholder.

    Filled with ``None`` for every arm in v1 (see module docstring).
    Kept as a typed dataclass so callers can wire the field through
    without refactoring once a reward-model surface exists.
    """

    arm_id: str
    dr_reward: float | None
    note: str = "DR not implemented in v1 (requires reward model)"


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_decisions(
    niche_id: str | None = None,
    since: datetime | None = None,
    *,
    dsn: str | None = None,
) -> list[DecisionRecord]:
    """Load ``pending_feedback`` rows with non-NULL propensity.

    ``reward_48h`` is hydrated from the same row (single-table SELECT —
    reward lives directly on pending_feedback, not on a separate
    analytics table; see ``learning/metric_collector.process_pending_task``
    which writes it via ``PendingFeedbackStore.update_window``).

    Args:
        niche_id: Filter to a single niche. ``None`` returns all niches
            (admin-mode read).
        since: Filter to decisions with ``publish_time >= since``.
            ``None`` returns all history with propensity logged.
        dsn: Database DSN. Defaults to ``DATABASE_URL`` env var. Tests
            inject a mock by patching the cursor itself, not the DSN.

    Returns:
        List of :class:`DecisionRecord`, ordered by ``publish_time DESC``.
        Empty list on any DB error (logged at WARNING).
    """
    rows = _query_pending_feedback(niche_id=niche_id, since=since, dsn=dsn)
    decisions: list[DecisionRecord] = []
    for row in rows:
        try:
            decisions.append(_row_to_decision(row))
        except (TypeError, ValueError, KeyError) as exc:
            logger.debug("[ips_replay] skipping malformed row: %s", exc)
    return decisions


def _query_pending_feedback(
    *,
    niche_id: str | None,
    since: datetime | None,
    dsn: str | None,
) -> list[dict[str, Any]]:
    """Run the actual SELECT. Isolated so tests can monkeypatch it.

    The query MUST include ``WHERE propensity IS NOT NULL`` — rows
    predating the v3q4r5s6t7u8 migration carry NULL and would propagate
    None into the IPS denominator. Pinned in
    ``test_load_decisions_returns_only_rows_with_propensity``.

    ``pg_connect`` is imported into this module's namespace at the top
    of the file (see :func:`_lazy_pg_connect`) so tests can
    ``monkeypatch.setattr("genlab_core.learning.ips_replay.pg_connect",
    ...)`` to inject a fake without having to mock psycopg directly.
    """
    effective_dsn = dsn or os.environ.get("DATABASE_URL")
    if not effective_dsn:
        logger.warning("[ips_replay] DATABASE_URL not set; returning empty list")
        return []

    # niche='all' opts into admin mode on tenant_context.pg_connect when
    # the caller wants cross-niche replay. The RLS policy on
    # pending_feedback accepts '' or 'all' as admin (see migration
    # d4e5f6a7b8c9). Passing the actual niche_id otherwise.
    rls_niche = niche_id or "all"

    where_clauses = ["propensity IS NOT NULL"]
    params: list[Any] = []
    if niche_id is not None:
        where_clauses.append("niche_id = %s")
        params.append(niche_id)
    if since is not None:
        where_clauses.append("publish_time >= %s")
        params.append(since)

    where_sql = " AND ".join(where_clauses)
    sql = f"""
        SELECT id::text AS id,
               niche_id,
               arm_id,
               platform,
               propensity,
               reward_48h,
               publish_time
        FROM pending_feedback
        WHERE {where_sql}
        ORDER BY publish_time DESC NULLS LAST
    """  # noqa: S608 — where_sql composed from literal fragments only

    try:
        with pg_connect(effective_dsn, niche_id=rls_niche, connect_timeout=10) as conn:
            with conn.cursor(row_factory=_dict_row_factory()) as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.warning("[ips_replay] query failed: %s", exc)
        return []


def _row_to_decision(row: dict[str, Any]) -> DecisionRecord:
    """Materialise one dict row into a DecisionRecord.

    Defensive about types: propensity comes back as float from psycopg
    when stored as REAL, but tests may pass int or Decimal — coerce.
    """
    propensity = row.get("propensity")
    if propensity is None:
        raise ValueError("row has no propensity (query filter should have excluded)")
    prop = float(propensity)

    reward_raw = row.get("reward_48h")
    reward: float | None
    if reward_raw is None:
        reward = None
    else:
        try:
            reward = float(reward_raw)
        except (TypeError, ValueError):
            reward = None

    decided_at = row.get("publish_time")
    if decided_at is None:
        # Rows without publish_time can't be ordered or windowed; skip.
        raise ValueError("row has no publish_time")

    return DecisionRecord(
        decision_id=str(row.get("id", "")),
        niche_id=str(row.get("niche_id", "")),
        arm_id=str(row.get("arm_id") or ""),
        propensity=prop,
        reward=reward,
        decided_at=decided_at,
        platform=str(row.get("platform") or ""),
    )


# ---------------------------------------------------------------------------
# IPS estimator
# ---------------------------------------------------------------------------


def compute_ips_per_arm(
    decisions: list[DecisionRecord],
    *,
    min_decisions: int = MIN_DECISIONS_FOR_ESTIMATE,
    min_propensity: float = MIN_PROPENSITY,
) -> dict[str, IPSEstimate]:
    """Compute the IPS reward estimate per arm.

    Args:
        decisions: Output of :func:`load_decisions`.
        min_decisions: Per-arm threshold below which ``ips_reward``
            and ``naive_reward`` are returned as ``None``. The
            ``n_decisions`` / ``n_with_reward`` counts are still
            populated so the operator can see arm coverage.
        min_propensity: Truncation floor for the importance weight
            ``1/p``. Decisions with ``propensity < min_propensity``
            have their weight capped at ``1/min_propensity``. Defaults
            to module-level :data:`MIN_PROPENSITY` (1e-3).

    Returns:
        Mapping arm_id → IPSEstimate. Empty input → empty mapping.
    """
    # Group decisions by arm. ``defaultdict(list)`` is fine — N is
    # typically < 10K (30 days × ~6 publishes/day × 5 niches ≈ 900).
    by_arm: dict[str, list[DecisionRecord]] = defaultdict(list)
    for d in decisions:
        by_arm[d.arm_id].append(d)

    results: dict[str, IPSEstimate] = {}
    for arm_id, arm_decisions in by_arm.items():
        results[arm_id] = _estimate_one_arm(
            arm_id,
            arm_decisions,
            min_decisions=min_decisions,
            min_propensity=min_propensity,
        )
    return results


def _estimate_one_arm(
    arm_id: str,
    decisions: list[DecisionRecord],
    *,
    min_decisions: int,
    min_propensity: float,
) -> IPSEstimate:
    """Apply the IPS + naive + ESS formulas to one arm's decision list."""
    n_decisions = len(decisions)
    with_reward = [d for d in decisions if d.reward is not None]
    n_with_reward = len(with_reward)

    if n_with_reward < min_decisions:
        return IPSEstimate(
            arm_id=arm_id,
            n_decisions=n_decisions,
            n_with_reward=n_with_reward,
            ips_reward=None,
            naive_reward=None,
            relative_lift=None,
            effective_sample_size=0.0,
            weights=[],
        )

    weights: list[float] = []
    weighted_rewards: list[float] = []
    rewards: list[float] = []
    for d in with_reward:
        # min_propensity is the truncation floor — never divide by less.
        p = max(d.propensity, min_propensity)
        w = 1.0 / p
        weights.append(w)
        # reward is guaranteed non-None by the with_reward filter above;
        # the cast is for the type checker.
        r = float(d.reward) if d.reward is not None else 0.0
        weighted_rewards.append(w * r)
        rewards.append(r)

    # IPS estimator: average of (reward * 1/p). Note: this is the
    # standard "unbiased IPS" form (Horvitz-Thompson). The
    # self-normalized variant would divide by sum(weights) instead of
    # N; we deliberately use the un-normalized form here so the
    # estimator stays interpretable when N is small and the operator
    # is comparing to the naive mean directly. Both forms share the
    # same MIN_PROPENSITY mitigation.
    ips_reward = sum(weighted_rewards) / n_with_reward
    naive_reward = sum(rewards) / n_with_reward

    relative_lift: float | None
    if naive_reward > 0:
        relative_lift = ips_reward / naive_reward - 1.0
    else:
        # naive_reward = 0 means every observed reward was 0, so any
        # lift number would be undefined (div by zero). Return None
        # rather than inf — the operator should see "lift unmeasurable"
        # not "lift infinite".
        relative_lift = None

    ess = _effective_sample_size(weights)

    return IPSEstimate(
        arm_id=arm_id,
        n_decisions=n_decisions,
        n_with_reward=n_with_reward,
        ips_reward=ips_reward,
        naive_reward=naive_reward,
        relative_lift=relative_lift,
        effective_sample_size=ess,
        weights=weights,
    )


def _effective_sample_size(weights: list[float]) -> float:
    """ESS = (sum w)^2 / sum w^2.

    Returns 0.0 on empty input. Uniform weights → ESS = N.
    Concentrated weights → ESS << N.
    """
    if not weights:
        return 0.0
    s = sum(weights)
    s2 = sum(w * w for w in weights)
    if s2 == 0:
        return 0.0
    return (s * s) / s2


# ---------------------------------------------------------------------------
# Doubly-robust estimator (v1 stub)
# ---------------------------------------------------------------------------


def compute_doubly_robust_per_arm(
    decisions: list[DecisionRecord],
) -> dict[str, DREstimate]:
    """Stub: returns ``dr_reward=None`` for every arm in v1.

    DR requires a fitted ``reward_model(arm, context) -> reward`` per
    arm. The :mod:`learning.hook_classifier` XGBoost model is the
    closest candidate but its output is a binary "good hook" label,
    not a calibrated reward in [0, 1]. Wiring it as the DR control
    variate without recalibration would bias the DR estimator more
    than IPS biases itself.

    Kept as a typed surface so the dashboard / CLI can render a
    "DR — coming soon" column without an extra refactor when the
    reward model lands.
    """
    arms = {d.arm_id for d in decisions}
    return {arm: DREstimate(arm_id=arm, dr_reward=None) for arm in arms}


__all__ = [
    "MIN_PROPENSITY",
    "MIN_DECISIONS_FOR_ESTIMATE",
    "DecisionRecord",
    "IPSEstimate",
    "DREstimate",
    "load_decisions",
    "compute_ips_per_arm",
    "compute_doubly_robust_per_arm",
]
