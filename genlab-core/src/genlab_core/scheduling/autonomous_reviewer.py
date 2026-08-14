"""Autonomous reviewer augmenter (Phase 5.B session 1).

Sits ABOVE ``llm_proposal_reviewer.Reviewer`` and boosts / dampens
its confidence with two additional evidence sources:

  * **Outcome-verifier history** — how did similar proposals of
    this type fare after applying? Reads from
    ``strategist_outcome_verification`` (Phase 1.C).
  * **Meta-strategist grade** — did last week's meta-strategist
    grade this proposal_type as A/B/C/D/F? Reads from
    ``meta_strategist_reports`` (Phase 2.G).

## Augmentation rules

Base verdict from LLM reviewer: (decision, confidence, reason).

  * Outcome history matches decision → confidence += bump_outcome
  * Outcome history contradicts decision → confidence -= dampener
  * Meta-strategist grade == A → confidence += bump_meta_a
  * Meta-strategist grade == F → confidence -= dampen_meta_f
  * Insufficient outcome history (<3 verdicts) → mark
    ``outcome_immature=True`` so caller knows to escalate

Confidence is clamped to [0, 1] after augmentation.

## Escalation contract (session-2 wire consumes)

Escalate to operator when ANY of:
  * augmented confidence < 0.5
  * outcome_immature = True (< 3 prior verdicts for this type)
  * novel proposal type (heuristic classifier's unknown_shape reason)

## Fail-open

DB errors reading outcome history / meta grade → augmentation
skipped; base verdict passes through unchanged. Never blocks
the base reviewer's decision path.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Augmentation magnitudes — small enough that a base verdict is
# never fully overridden by evidence, but large enough to move
# borderline cases (0.5-0.7 confidence range).
_BUMP_OUTCOME_ALIGNED = 0.15
_DAMPEN_OUTCOME_CONTRADICTS = 0.20
_BUMP_META_A_GRADE = 0.10
_DAMPEN_META_F_GRADE = 0.25
_MIN_OUTCOME_HISTORY_MATURE = 3


@dataclass(frozen=True)
class OutcomeHistory:
    """Aggregate of prior verdicts for one proposal type."""
    proposal_type: str
    n_verdicts: int
    n_improved: int
    n_unchanged: int
    n_regressed: int

    @property
    def is_mature(self) -> bool:
        return self.n_verdicts >= _MIN_OUTCOME_HISTORY_MATURE

    @property
    def success_rate(self) -> float:
        """(improved) / (improved + regressed) — unchanged counted
        as neither win nor loss so a slow / stable arm doesn't
        pollute the signal."""
        denom = self.n_improved + self.n_regressed
        if denom == 0:
            return 0.0
        return self.n_improved / denom

    def to_dict(self) -> dict:
        return {
            "proposal_type": self.proposal_type,
            "n_verdicts": self.n_verdicts,
            "n_improved": self.n_improved,
            "n_unchanged": self.n_unchanged,
            "n_regressed": self.n_regressed,
            "is_mature": self.is_mature,
            "success_rate": self.success_rate,
        }


@dataclass(frozen=True)
class AugmentedVerdict:
    """Base verdict + augmentation trail + escalation decision.

    ``should_escalate`` is the load-bearing output for the
    session-2 wire — when True, don't auto-act, punt to operator."""
    decision: str  # accept | reject | abstain (from base)
    base_confidence: float
    augmented_confidence: float
    reason: str
    outcome_history: OutcomeHistory | None
    meta_grade: str | None
    augmentation_trail: list[str] = field(default_factory=list)
    should_escalate: bool = True

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "base_confidence": self.base_confidence,
            "augmented_confidence": self.augmented_confidence,
            "reason": self.reason,
            "outcome_history": (
                self.outcome_history.to_dict() if self.outcome_history else None
            ),
            "meta_grade": self.meta_grade,
            "augmentation_trail": list(self.augmentation_trail),
            "should_escalate": self.should_escalate,
        }


def fetch_outcome_history(
    conn, proposal_type: str, lookback_weeks: int = 8,
) -> OutcomeHistory:
    """Aggregate strategist_outcome_verification for the type.
    Fail-open to empty history (n=0)."""
    empty = OutcomeHistory(
        proposal_type=proposal_type, n_verdicts=0,
        n_improved=0, n_unchanged=0, n_regressed=0,
    )
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)::int AS n,
                   COUNT(*) FILTER (WHERE verdict = 'improved')::int AS n_imp,
                   COUNT(*) FILTER (WHERE verdict = 'unchanged')::int AS n_unc,
                   COUNT(*) FILTER (WHERE verdict = 'regressed')::int AS n_reg
            FROM strategist_outcome_verification
            WHERE proposal_type = %s
              AND verdict != 'pending'
              AND applied_at >= NOW() - (%s || ' weeks')::INTERVAL
            """,
            (proposal_type, lookback_weeks),
        ).fetchone()
    except Exception as exc:
        logger.warning(
            "[augmenter] outcome-history query failed type=%s: %s",
            proposal_type, exc,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return empty
    if row is None:
        return empty
    return OutcomeHistory(
        proposal_type=proposal_type,
        n_verdicts=int(row.get("n") if hasattr(row, "get") else row[0] or 0),
        n_improved=int(row.get("n_imp") if hasattr(row, "get") else row[1] or 0),
        n_unchanged=int(row.get("n_unc") if hasattr(row, "get") else row[2] or 0),
        n_regressed=int(row.get("n_reg") if hasattr(row, "get") else row[3] or 0),
    )


def fetch_meta_grade(
    conn, proposal_type: str,
) -> str | None:
    """Latest meta_strategist_reports.per_type_grades[proposal_type].
    None on any failure."""
    try:
        row = conn.execute(
            """
            SELECT per_type_grades
            FROM meta_strategist_reports
            ORDER BY week_of DESC
            LIMIT 1
            """
        ).fetchone()
    except Exception as exc:
        logger.warning(
            "[augmenter] meta-grade query failed type=%s: %s",
            proposal_type, exc,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    if row is None:
        return None
    grades = row.get("per_type_grades") if hasattr(row, "get") else row[0]
    if isinstance(grades, str):
        import json
        try:
            grades = json.loads(grades)
        except Exception:
            return None
    if not isinstance(grades, dict):
        return None
    grade = grades.get(proposal_type)
    if grade is None:
        return None
    g = str(grade).strip().upper()
    return g if g in {"A", "B", "C", "D", "F"} else None


def augment(
    decision: str,
    base_confidence: float,
    reason: str,
    proposal_type: str,
    conn,
    *,
    is_novel: bool = False,
) -> AugmentedVerdict:
    """Boost / dampen the base verdict confidence using outcome
    history + meta grade. Returns AugmentedVerdict with
    should_escalate flag set per the roadmap escalation rules.

    Never raises."""
    history = fetch_outcome_history(conn, proposal_type)
    meta_grade = fetch_meta_grade(conn, proposal_type)
    trail: list[str] = []

    conf = max(0.0, min(1.0, float(base_confidence)))

    # Outcome-history augmentation
    if history.is_mature:
        # Only apply outcome signal for accept/reject decisions
        # (abstain is unaffected).
        if decision == "accept":
            if history.success_rate >= 0.6:
                conf += _BUMP_OUTCOME_ALIGNED
                trail.append(
                    f"outcome:aligned:success_rate={history.success_rate:.2f}"
                    f" +{_BUMP_OUTCOME_ALIGNED}"
                )
            elif history.success_rate <= 0.3:
                conf -= _DAMPEN_OUTCOME_CONTRADICTS
                trail.append(
                    f"outcome:contradicts:success_rate={history.success_rate:.2f}"
                    f" -{_DAMPEN_OUTCOME_CONTRADICTS}"
                )
        elif decision == "reject":
            # Rejecting a type that historically SUCCEEDS is
            # suspicious → dampen confidence
            if history.success_rate >= 0.6:
                conf -= _DAMPEN_OUTCOME_CONTRADICTS
                trail.append(
                    f"outcome:reject-vs-success:{history.success_rate:.2f}"
                    f" -{_DAMPEN_OUTCOME_CONTRADICTS}"
                )
            elif history.success_rate <= 0.3:
                conf += _BUMP_OUTCOME_ALIGNED
                trail.append(
                    f"outcome:reject-aligned:{history.success_rate:.2f}"
                    f" +{_BUMP_OUTCOME_ALIGNED}"
                )

    # Meta-strategist grade augmentation
    if meta_grade == "A":
        conf += _BUMP_META_A_GRADE
        trail.append(f"meta:grade=A +{_BUMP_META_A_GRADE}")
    elif meta_grade == "F":
        conf -= _DAMPEN_META_F_GRADE
        trail.append(f"meta:grade=F -{_DAMPEN_META_F_GRADE}")

    # Clamp
    augmented = max(0.0, min(1.0, conf))

    # Escalation rules per roadmap: escalate when confidence < 0.5
    # OR outcome verifier hasn't matured OR novel proposal type.
    should_escalate = (
        augmented < 0.5
        or not history.is_mature
        or is_novel
    )

    return AugmentedVerdict(
        decision=decision,
        base_confidence=base_confidence,
        augmented_confidence=augmented,
        reason=reason,
        outcome_history=history,
        meta_grade=meta_grade,
        augmentation_trail=trail,
        should_escalate=should_escalate,
    )
