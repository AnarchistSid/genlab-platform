"""Auto-tune calibration thresholds (Phase 5.A).

Reads ``auto_approval_calibration``, computes per-niche confusion
matrix, suggests ``min_confidence`` delta so the gate self-corrects
FP-heavy (raise threshold) or FN-heavy (lower threshold) skew.

## Confusion matrix

  operator_action = 'approved' → positive
  operator_action in ('rejected', 'revised', 'skipped') → negative
  gate_approved = TRUE → gate says positive

  TP: gate=TRUE  operator=positive  (correct approve)
  TN: gate=FALSE operator=negative  (correct reject)
  FP: gate=TRUE  operator=negative  (false approve — bad)
  FN: gate=FALSE operator=positive  (false reject — missed opportunity)

## Suggestion rule

Both FP and FN cost the operator; the tuner treats them as
symmetric in the delta calculation:

    imbalance = (fp - fn) / max(1, tp + tn + fp + fn)
    delta = imbalance × 0.10   # scale to [-0.10, +0.10]

Positive delta → gate approved too eagerly → RAISE min_confidence.
Negative delta → gate rejected too eagerly → LOWER min_confidence.

## Auto-apply safety window

Only auto-apply when |delta| <= 0.05. Larger deltas require the
operator to eyeball the confusion matrix before making a bigger
threshold move. This is the same safety pattern as Phase 2.B
portfolio_bandit (recommend allocations; operator approves large
shifts).

## Rule #22 discipline

The tuner must compute the FULL matrix, not just agreement %. The
2026-07-17 incident (moved enrollment based on broken query
comparing 'approve' vs actual DB 'approved') is why this task
exists. Pin test asserts the exact operator_action string set to
prevent regression.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)


# Rule #22: pin the exact operator_action string set. Prod DB
# values are 'approved' / 'rejected' / 'revised' / 'skipped'.
# ANY change here should trigger a re-audit — treating 'approve'
# as positive when DB has 'approved' silently produces zeros.
_POSITIVE_OPERATOR_ACTIONS = frozenset({"approved"})
_NEGATIVE_OPERATOR_ACTIONS = frozenset({"rejected", "revised", "skipped"})

# Safe auto-apply window per roadmap: [-0.05, +0.05].
AUTO_APPLY_MAX_DELTA = 0.05

# Minimum samples per niche to compute a suggestion. Lowered 2026-08-15
# from 15 → 5. Rationale: outcome_readiness now writes source='outcome'
# calibration rows for every auto-approved blueprint after 48h. Even
# with rollout_pct=1.0 the slow-cadence niches (anime, movies) only
# accumulate ~3-5 rows/week — floor of 15 meant they'd never trigger
# a suggestion. Small-sample noise is bounded by AUTO_APPLY_MAX_DELTA
# (0.05) — suggestions with |delta| > 0.05 become MANUAL entries for
# operator eyeball (surfaced via calibration_tuning_suggestions +
# operator briefing) rather than silent auto-apply.
#
# Live 2026-08-15 state that motivated this:
#   ai_creators n=46 (tuning), sports n=22 (tuning),
#   anime n=5 (stuck), movies n=3 (stuck), gaming n=0.
_MIN_SAMPLES = 5


@dataclass(frozen=True)
class ConfusionMatrix:
    """Full 2x2 matrix + derived rates. Every task-5 consumer reads
    from here so no code path derives just agreement % without also
    checking the FP/FN breakdown."""
    tp: int
    tn: int
    fp: int
    fn: int

    @property
    def n(self) -> int:
        return self.tp + self.tn + self.fp + self.fn

    @property
    def agreement_pct(self) -> float:
        if self.n == 0:
            return 0.0
        return (self.tp + self.tn) / self.n * 100

    @property
    def imbalance(self) -> float:
        """(fp - fn) / n. Positive when gate over-approves; negative
        when gate over-rejects."""
        if self.n == 0:
            return 0.0
        return (self.fp - self.fn) / self.n

    def to_dict(self) -> dict:
        return {
            "tp": self.tp, "tn": self.tn,
            "fp": self.fp, "fn": self.fn,
            "n": self.n,
            "agreement_pct": self.agreement_pct,
            "imbalance": self.imbalance,
        }


@dataclass(frozen=True)
class TuningSuggestion:
    """Complete output for one niche's weekly analysis."""
    niche_id: str
    confusion: ConfusionMatrix
    current_min_confidence: float
    suggested_delta: float
    suggested_min_confidence: float
    within_auto_apply: bool
    rationale: str


# 2026-08-21: absolute ceiling for an auto-tuned min_confidence.
# Above this the threshold is indistinguishable from "auto-approval
# disabled" — and disabling must be an explicit operator act
# (auto_publish.enabled: false), not an emergent property of a tuner
# integrating an error signal its own output cannot influence.
_HARD_CEILING: Final[float] = 0.95


def compute_confusion(rows: list[dict]) -> ConfusionMatrix:
    """Build the confusion matrix from calibration rows. Each row
    must have keys ``gate_approved`` (bool) + ``operator_action``
    (str).

    Rows with operator_action outside the pinned positive/negative
    sets are SKIPPED — better to under-count than double-count on
    a value drift."""
    tp = tn = fp = fn = 0
    for r in rows:
        action = r.get("operator_action")
        gate = r.get("gate_approved")
        if action in _POSITIVE_OPERATOR_ACTIONS:
            if gate is True:
                tp += 1
            elif gate is False:
                fn += 1
        elif action in _NEGATIVE_OPERATOR_ACTIONS:
            if gate is True:
                fp += 1
            elif gate is False:
                tn += 1
        # skip rows with unknown operator_action or NULL gate_approved
    return ConfusionMatrix(tp=tp, tn=tn, fp=fp, fn=fn)


def suggest_min_confidence(
    niche_id: str,
    confusion: ConfusionMatrix,
    current_min_confidence: float,
    achievable_ceiling: float | None = None,
) -> TuningSuggestion:
    """Compute a suggested min_confidence delta from the confusion
    matrix. Returns a full TuningSuggestion with rationale.

    Fail-safe: if sample size is below _MIN_SAMPLES OR imbalance
    is essentially zero, suggests delta=0 with an explicit
    "insufficient data" or "well-calibrated" rationale."""
    delta = 0.0
    rationale = ""

    if confusion.n < _MIN_SAMPLES:
        rationale = (
            f"insufficient samples (n={confusion.n} < {_MIN_SAMPLES}) — "
            f"holding threshold steady"
        )
    elif abs(confusion.imbalance) < 0.02:
        rationale = (
            f"gate well-calibrated (imbalance={confusion.imbalance:.3f}, "
            f"n={confusion.n}) — no change suggested"
        )
    else:
        # imbalance × 0.10 → scale to a reasonable threshold move
        delta = round(confusion.imbalance * 0.10, 3)
        direction = "raise" if delta > 0 else "lower"
        cause = "FP > FN (over-approving)" if delta > 0 else "FN > FP (over-rejecting)"
        rationale = (
            f"{cause}, TP={confusion.tp} TN={confusion.tn} "
            f"FP={confusion.fp} FN={confusion.fn} (n={confusion.n}, "
            f"agreement={confusion.agreement_pct:.1f}%) — "
            f"{direction} min_confidence by {abs(delta):.3f}"
        )

    # ── Clamp ────────────────────────────────────────────────────────
    # 2026-08-21: the only clamp used to be the mathematical [0.0, 1.0],
    # which is a bound on the NUMBER, not on anything operational. Two
    # ceilings now apply on top of it.
    #
    # Why this was needed. `compute_confusion` compares the gate's
    # 5-check verdict (`gate_approved`) against `operator_action`.
    # `min_confidence` is NOT an input to it — the threshold is a
    # separate downstream filter that decides whether to ACT on a gate
    # approval, and it cannot change whether the gate approves. So when
    # FP > FN persists, raising min_confidence does not move the signal
    # that caused the raise, and the next run sees the same imbalance
    # and raises again. An open loop wearing the costume of a closed
    # one: it ratchets to the numeric ceiling and stops there.
    #
    # It did. On 2026-08-21 prod held anime=1.0, movies=1.0,
    # sports=0.986, against a best-observed gate confidence of ~0.91.
    # A threshold above the achievable maximum does not make the gate
    # selective, it switches it off — 701 gate-approved blueprints
    # across those three niches in 7 days, zero auto-approved, the
    # entire backlog diverted to manual review.
    #
    #   * _HARD_CEILING — an absolute cap. Above this a threshold is
    #     indistinguishable from "disabled", and disabling should be an
    #     explicit operator act (enabled: false), never a side effect of
    #     a tuner integrating a constant error.
    #   * achievable_ceiling — caller-supplied, derived from the recent
    #     confidence distribution this niche actually produces. Guards
    #     the same failure a niche at a time, because the ceiling is a
    #     property of the signals available, not a global constant.
    ceiling = _HARD_CEILING
    if achievable_ceiling is not None:
        ceiling = min(ceiling, achievable_ceiling)

    raw = current_min_confidence + delta
    suggested = max(0.0, min(1.0, raw))
    if suggested > ceiling:
        rationale += (
            f" | CLAMPED {suggested:.3f} → {ceiling:.3f}: a threshold above "
            f"the achievable confidence ceiling disables auto-approval "
            f"rather than tightening it"
        )
        suggested = ceiling
        delta = round(suggested - current_min_confidence, 3)
    within_apply = abs(delta) <= AUTO_APPLY_MAX_DELTA and delta != 0.0

    return TuningSuggestion(
        niche_id=niche_id,
        confusion=confusion,
        current_min_confidence=current_min_confidence,
        suggested_delta=delta,
        suggested_min_confidence=suggested,
        within_auto_apply=within_apply,
        rationale=rationale,
    )
