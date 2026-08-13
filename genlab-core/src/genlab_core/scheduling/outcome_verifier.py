"""Post-apply outcome verification for strategist proposals.

**PHASE 2 SCAFFOLD** — interface + data model only. The runner
(48h post-apply metric check + auto-rollback) is deferred to a
follow-up session so the interface can be reviewed before wiring.

## Motivation

Today's auto-accept flow applies proposals with no feedback loop.
If a proposal predicted "raise FB.shares weight to 0.45 → +30% reach"
but reach actually dropped, nothing catches it — the change persists
silently, degrading the whole learning signal.

Adding an outcome verifier closes the loop: 48h after apply, compare
the metric the proposal claimed to move against baseline. If moved
in the wrong direction beyond a tolerance, mark for auto-rollback.

## Data model

Every accepted proposal that materializes into a config change gets
one row in ``strategist_outcome_verification``:

  * ``proposal_id`` — links to strategist_reports.id + proposals[idx]
  * ``applied_at`` — when apply_strategist_actions wrote the change
  * ``metric_name`` — what the proposal claimed to move (e.g.
    "avg_reward_facebook_shares_anime")
  * ``baseline_value`` — metric value at t = applied_at
  * ``t_plus_48h_value`` — metric value at applied_at + 48h
  * ``verdict`` — 'improved' | 'unchanged' | 'regressed' | 'pending'
  * ``rollback_recommended`` — bool
  * ``operator_notes`` — free-form; operator can override verdict

## Interface

Callers of the auto-accept pipeline get a `Verifier` handle. Two
methods:

  * ``register(proposal, ...)`` — called from apply worker at t=0.
    Snapshots baseline + inserts pending row.
  * ``evaluate_pending(now=None)`` — called periodically by the
    verifier runner (deferred). Reads pending rows, checks age
    ≥48h, fetches current metric, sets verdict.

## Metric extraction

Proposals reference metrics via a target-string convention:

  * ``arm_add`` — measures the new arm's avg reward at 48h.
    Baseline is 0 (arm didn't exist). Verdict is 'improved' if
    n≥3 samples and reward > 0.1, 'unchanged' if <3 samples,
    'regressed' if reward < 0.05.
  * ``reward_weight`` — measures the affected niche×platform
    avg reward. Baseline is the metric snapshot right before
    apply. 'improved' if >5% higher, 'regressed' if >5% lower.
  * ``gate_threshold`` — measures approval rate + downstream
    reward. Complex; deferred to phase 2 runner.
  * ``novelty_rate`` — measures exploration coverage (fraction
    of arms with n≥1). 'improved' if coverage goes up.

## Auto-rollback

When ``rollback_recommended = True``, a follow-up commit reverses
the applied change. Two shapes:

  * ``arm_add`` rollback: mark the arm as ``paused = True`` in
    bandit_arms (not delete — historical data useful for future
    diagnostics)
  * ``reward_weight`` rollback: reset to baseline weight
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Time window for outcome measurement. 48h chosen because:
#   * Matches pending_feedback reward_48h cadence — same signal
#     the bandit already uses
#   * Long enough for engagement to accumulate + short enough
#     to catch bad changes before they persist a full week
_MEASUREMENT_WINDOW_HOURS: int = 48

# Verdict thresholds
_IMPROVED_PCT: float = 5.0   # metric must rise by ≥5%
_REGRESSED_PCT: float = -5.0  # metric drop >5% triggers rollback


class Verdict(str, Enum):
    IMPROVED = "improved"
    UNCHANGED = "unchanged"
    REGRESSED = "regressed"
    PENDING = "pending"


@dataclass(frozen=True)
class VerificationRecord:
    """One row in strategist_outcome_verification."""

    proposal_id: str  # strategist_reports.id + ":" + index
    proposal_type: str
    proposal_target: str
    niche_id: str
    applied_at: datetime
    metric_name: str
    baseline_value: float | None
    t_plus_48h_value: float | None
    verdict: Verdict
    rollback_recommended: bool
    operator_notes: str = ""


class MetricSnapshotProvider(Protocol):
    """Injectable metric-source for testability. Prod implementation
    queries pending_feedback + bandit_arms; test implementations use
    fixtures."""

    def snapshot(self, niche_id: str, metric_name: str) -> float | None:
        """Return current value of `metric_name` for `niche_id`, or
        None if the metric can't be resolved (e.g. brand new arm with
        no observations yet)."""
        ...


class VerificationRecordStore(Protocol):
    """Injectable persistence layer. Prod = Postgres, test = in-memory."""

    def insert(self, record: VerificationRecord) -> None: ...
    def update_verdict(
        self, proposal_id: str, t_plus_48h_value: float | None,
        verdict: Verdict, rollback_recommended: bool,
    ) -> None: ...
    def list_pending(self, older_than: datetime) -> list[VerificationRecord]: ...


class Verifier:
    """Register-and-evaluate two-step outcome verification.

    Usage from apply_strategist_actions (t=0):

        v = Verifier(metrics=..., store=...)
        v.register(
            proposal_id="uuid:3",
            proposal={...},
            niche_id="anime",
            applied_at=datetime.now(UTC),
        )

    Usage from verifier runner (t≥48h):

        for pending in v.list_pending():
            v.evaluate(pending)
    """

    def __init__(
        self, *, metrics: MetricSnapshotProvider, store: VerificationRecordStore,
    ) -> None:
        self._metrics = metrics
        self._store = store

    def register(
        self, *, proposal_id: str, proposal: dict[str, Any], niche_id: str,
        applied_at: datetime,
    ) -> VerificationRecord | None:
        """Snapshot baseline + insert pending row. Returns the record
        on success, None if metric can't be resolved (in which case
        we skip verification for this proposal)."""
        metric_name = self._infer_metric_name(proposal, niche_id)
        if metric_name is None:
            logger.info(
                "[outcome_verifier] skipping unregisterable proposal_id=%s "
                "type=%s target=%s",
                proposal_id, proposal.get("type"),
                proposal.get("target"),
            )
            return None
        baseline = self._metrics.snapshot(niche_id, metric_name)
        record = VerificationRecord(
            proposal_id=proposal_id,
            proposal_type=proposal.get("type", ""),
            proposal_target=str(proposal.get("target", ""))[:200],
            niche_id=niche_id,
            applied_at=applied_at,
            metric_name=metric_name,
            baseline_value=baseline,
            t_plus_48h_value=None,
            verdict=Verdict.PENDING,
            rollback_recommended=False,
        )
        self._store.insert(record)
        return record

    def evaluate(self, record: VerificationRecord) -> Verdict:
        """Fetch current metric value + compare to baseline."""
        current = self._metrics.snapshot(record.niche_id, record.metric_name)
        verdict, rollback = self._classify(record.baseline_value, current)
        self._store.update_verdict(
            record.proposal_id, current, verdict, rollback,
        )
        return verdict

    def list_pending(self, *, now: datetime | None = None) -> list[VerificationRecord]:
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(hours=_MEASUREMENT_WINDOW_HOURS)
        return self._store.list_pending(older_than=cutoff)

    @staticmethod
    def _infer_metric_name(proposal: dict[str, Any], niche_id: str) -> str | None:
        """Map (type, target) → metric_name. Returns None for shapes
        that don't have a clear metric to measure."""
        ptype = proposal.get("type", "")
        target = str(proposal.get("target", ""))
        if ptype == "arm_add":
            # Metric: newly-added arm's avg reward
            proposed = proposal.get("proposed") or {}
            if isinstance(proposed, dict):
                arm_id = proposed.get("arm_id", "")
                if arm_id:
                    return f"arm_reward:{niche_id}:{arm_id}"
        if ptype == "reward_weight":
            # Metric: affected niche×platform avg reward.
            # target format: {niche}.reward_weight.{platform}.{metric}
            parts = target.split(".")
            if len(parts) == 4:
                platform = parts[2]
                return f"platform_reward:{niche_id}:{platform}"
        if ptype == "novelty_rate":
            # Metric: bandit coverage (fraction of arms with n≥1)
            return f"bandit_coverage:{niche_id}"
        # gate_threshold + playbook_update + manual_action:
        # too coupled to full-system metrics to register at this layer.
        return None

    @staticmethod
    def _classify(
        baseline: float | None, current: float | None,
    ) -> tuple[Verdict, bool]:
        """Return (verdict, rollback_recommended)."""
        if current is None:
            return Verdict.UNCHANGED, False  # no data → don't rollback
        if baseline is None or baseline == 0:
            # No prior value (e.g., new arm) — improvement is any positive value
            if current > 0.1:
                return Verdict.IMPROVED, False
            if current < 0.05:
                return Verdict.REGRESSED, True
            return Verdict.UNCHANGED, False
        pct_change = 100.0 * (current - baseline) / abs(baseline)
        if pct_change >= _IMPROVED_PCT:
            return Verdict.IMPROVED, False
        if pct_change <= _REGRESSED_PCT:
            return Verdict.REGRESSED, True
        return Verdict.UNCHANGED, False


__all__ = [
    "MetricSnapshotProvider",
    "VerificationRecord",
    "VerificationRecordStore",
    "Verdict",
    "Verifier",
]
