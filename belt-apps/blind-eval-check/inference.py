"""blind-eval-check — did your calibration measure the detector against itself?

## The trap

You build a detector. You want to know if it works, so you take its output,
look at a few of its picks, confirm they're real, and score it. It reports
100%.

It reports 100% because the ground truth WAS the output. That measures
precision-by-inspection and says nothing about recall — whether the things it
missed were the ones that mattered. A real measurement of the same detector,
against marks written before it ran, came back at **recall 0.00**: not one of
three ground-truth events had a pick within tolerance, and the nearest
approach was four times the tolerance away.

Both numbers described the same code on the same clip. The difference was
entirely in where the ground truth came from.

## What this checks

Give it ground-truth positions and detector picks (1-D: timestamps, frame
indices, offsets, any ordered scalar) and it returns:

  * **precision and recall separately** — never a single "hit rate", because
    a detector that fires constantly scores well on one and badly on the
    other, and which one you quote decides what you believe
  * a **circularity verdict** — if every error is far below your tolerance,
    the marks almost certainly came from the picks
  * an **ordering verdict** — if you supply when each was written, it refuses
    a run where the detector's output predates the ground truth
  * **degeneracy warnings** — far more picks than marks inflates recall while
    precision quietly collapses; identical sets; empty inputs

It is deliberately format-agnostic and has no dependencies: the trap is in
the method, not in any particular domain.
"""

import logging
from typing import List, Optional

from inferencesh import BaseApp, BaseAppSetup
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

#: An error this far below tolerance is not agreement, it is identity.
CIRCULAR_RATIO = 0.10
#: Firing this many times more often than there are marks buys recall cheaply.
SPRAY_RATIO = 4.0


class Match(BaseModel):
    mark: float = Field(description="A ground-truth position")
    nearest_pick: Optional[float] = Field(default=None, description="Closest detector pick")
    error: Optional[float] = Field(default=None, description="Absolute distance")
    matched: bool = Field(description="Whether it fell inside tolerance")


class AppSetup(BaseAppSetup):
    """No setup state."""


class RunInput(BaseModel):
    marks: List[float] = Field(
        description="Ground-truth positions — timestamps, frame indices, offsets. "
                    "These must come from something OTHER than the detector.")
    picks: List[float] = Field(description="What the detector returned.")
    tolerance: float = Field(
        default=0.3,
        description="A pick within this distance of a mark counts as a hit. Same "
                    "unit as marks and picks.")
    marks_written_at: Optional[float] = Field(
        default=None,
        description="Epoch seconds when the ground truth was recorded. Supply "
                    "both this and picks_written_at to have the ordering checked.")
    picks_written_at: Optional[float] = Field(
        default=None, description="Epoch seconds when the detector output was recorded.")
    label: str = Field(default="", description="Free-text name for this run.")


class RunOutput(BaseModel):
    trustworthy: bool = Field(
        description="False if the result is circular, out of order, or degenerate")
    verdict: str = Field(description="One line on what this measurement establishes")
    precision: float = Field(description="Picks that landed near a mark")
    recall: float = Field(description="Marks that had a pick near them")
    f1: float = Field(description="Harmonic mean, for completeness — read the two above")
    n_marks: int
    n_picks: int
    tolerance: float
    matched_marks: int
    missed_marks: List[float] = Field(default_factory=list)
    spurious_picks: List[float] = Field(
        default_factory=list, description="Picks with no mark near them")
    per_mark: List[Match] = Field(default_factory=list)
    circular: bool = Field(description="Ground truth appears to be derived from the picks")
    ordering_verified: bool = Field(description="Ground truth provably predates the picks")
    warnings: List[str] = Field(default_factory=list)


class App(BaseApp):
    async def setup(self, config: AppSetup):
        logger.info("[blind-eval] ready — pure arithmetic, no network, no files")

    async def run(self, input_data: RunInput) -> RunOutput:
        marks = list(input_data.marks)
        picks = list(input_data.picks)
        tol = float(input_data.tolerance)
        warnings: List[str] = []
        logger.info("[blind-eval] %d marks vs %d picks at tolerance %s",
                    len(marks), len(picks), tol)

        if tol <= 0:
            warnings.append("tolerance must be positive; nothing can match at <= 0")
        if not marks:
            return RunOutput(
                trustworthy=False, verdict="no ground truth — nothing to measure",
                precision=0.0, recall=0.0, f1=0.0, n_marks=0, n_picks=len(picks),
                tolerance=tol, matched_marks=0, circular=False,
                ordering_verified=False,
                warnings=warnings + ["marks is empty"])

        per_mark: List[Match] = []
        for m in marks:
            if picks:
                nearest = min(picks, key=lambda p: abs(p - m))
                err = abs(nearest - m)
                per_mark.append(Match(mark=m, nearest_pick=nearest, error=round(err, 6),
                                      matched=err <= tol))
            else:
                per_mark.append(Match(mark=m, matched=False))

        matched_marks = sum(1 for x in per_mark if x.matched)
        matched_picks = [p for p in picks if any(abs(p - m) <= tol for m in marks)]
        recall = matched_marks / len(marks)
        precision = (len(matched_picks) / len(picks)) if picks else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

        # ── circularity ───────────────────────────────────────────────────
        errs = [x.error for x in per_mark if x.error is not None]
        circular = bool(errs) and all(e <= tol * CIRCULAR_RATIO for e in errs)
        if circular:
            warnings.append(
                f"CIRCULAR: every error is at or under {tol * CIRCULAR_RATIO:g} — "
                f"{CIRCULAR_RATIO:.0%} of your tolerance. Ground truth that agrees "
                f"with a detector this exactly was almost certainly taken FROM it. "
                f"This establishes precision by inspection and says nothing about "
                f"recall.")

        # ── ordering ──────────────────────────────────────────────────────
        ordering_verified = False
        mw, pw = input_data.marks_written_at, input_data.picks_written_at
        if mw is not None and pw is not None:
            if pw < mw:
                warnings.append(
                    "OUT OF ORDER: the detector's output predates the ground truth. "
                    "Marks written after seeing picks are not marks.")
            else:
                ordering_verified = True
        else:
            warnings.append(
                "ordering unverified — supply marks_written_at and picks_written_at "
                "to have it checked. Without it, nothing here can tell whether the "
                "ground truth came first.")

        # ── degeneracy ────────────────────────────────────────────────────
        if picks and len(picks) > SPRAY_RATIO * len(marks):
            warnings.append(
                f"{len(picks)} picks for {len(marks)} marks — firing "
                f"{len(picks) / len(marks):.1f}x more often than there are events buys "
                f"recall cheaply. Precision is {precision:.2f}; read it, not recall.")
        if set(marks) == set(picks):
            warnings.append("marks and picks are the identical set")
        if not picks:
            warnings.append("picks is empty — the detector returned nothing")

        out_of_order = any(w.startswith("OUT OF ORDER") for w in warnings)
        trustworthy = not (circular or out_of_order or not picks)
        if circular:
            verdict = "precision by inspection only — the ground truth is not independent"
        elif out_of_order:
            verdict = "not a calibration — the detector ran before the ground truth existed"
        elif not picks:
            verdict = "the detector returned nothing"
        elif not ordering_verified:
            verdict = (f"recall {recall:.2f}, precision {precision:.2f} — plausible, but "
                       f"the ordering is unverified")
        else:
            verdict = f"recall {recall:.2f}, precision {precision:.2f} against blind marks"

        logger.info("[blind-eval] %s", verdict)
        return RunOutput(
            trustworthy=trustworthy, verdict=verdict,
            precision=round(precision, 4), recall=round(recall, 4), f1=round(f1, 4),
            n_marks=len(marks), n_picks=len(picks), tolerance=tol,
            matched_marks=matched_marks,
            missed_marks=[x.mark for x in per_mark if not x.matched],
            spurious_picks=[p for p in picks if p not in matched_picks],
            per_mark=per_mark, circular=circular, ordering_verified=ordering_verified,
            warnings=warnings)
