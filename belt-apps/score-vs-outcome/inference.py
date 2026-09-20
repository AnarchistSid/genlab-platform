"""Check whether a score you act on actually predicts the outcome you care about.

Most systems that rank things accumulate a quality score — a heuristic, a model
output, a weighted composite — and then gate on it: approve above 0.3, promote
the top decile, sort by it. Almost nobody goes back and checks that the score
correlates with the thing it is supposed to predict.

This came out of auditing a content pipeline that had **five** independent
scoring systems. Measured against realised reach, they scored −0.193, 0.043,
−0.041, −0.191 and unused. The primary gate — the one that decided what got
published — was *anti*-correlated: acting on it was marginally worse than
ignoring it. The system had been optimising against that number for months.

The dangerous case is not a weak score. It is a **negative** one, because every
downstream loop that maximises it moves away from the goal while reporting
progress.

What this reports:

  * Pearson (linear) and Spearman (monotone) correlation
  * whether |r| is distinguishable from zero at this sample size
  * **decile lift** — do the top-scored items actually outperform the bottom?
    This is the practical question, and it survives non-linear relationships
    that Pearson misses
  * **gate analysis** — if you pass the threshold you currently act on, what
    that gate actually buys you versus keeping everything

Pure Python, no dependencies. Correlation is not causation and this cannot tell
you why; it tells you whether the number you trust deserves it.
"""
import logging
import math
from typing import List, Optional

from inferencesh import BaseApp, BaseAppSetup
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class DecileRow(BaseModel):
    decile: int = Field(description="1 = lowest-scored tenth, 10 = highest.")
    n: int = Field(description="Items in this decile.")
    mean_score: float = Field(description="Average score in the decile.")
    mean_outcome: float = Field(description="Average realised outcome.")


class AppSetup(BaseAppSetup):
    """Stateless — pure arithmetic."""


class RunInput(BaseModel):
    scores: List[float] = Field(
        default_factory=list,
        description="What your heuristic/model predicted, in item order.",
    )
    outcomes: List[float] = Field(
        default_factory=list,
        description="What actually happened for the same items, same order. "
        "Views, revenue, conversions, clicks — whatever the score is meant to predict.",
    )
    gate_threshold: Optional[float] = Field(
        None,
        description="If you currently act on scores above some value, pass it "
        "here and the report will tell you what that gate actually buys.",
    )
    higher_score_means_better: bool = Field(
        True,
        description="Set false if a LOWER score is supposed to mean a better outcome.",
    )


class RunOutput(BaseModel):
    n: int = Field(description="Usable pairs after dropping non-finite values.")
    pearson_r: Optional[float] = Field(description="Linear correlation, −1..1. Null when a series has no variance.")
    spearman_r: Optional[float] = Field(description="Rank correlation — catches monotone-but-curved relationships Pearson misses.")
    noise_floor: float = Field(description="|r| below this is indistinguishable from zero at this n (≈2/√n).")
    verdict: str = Field(description="predictive | weak | noise | ANTI-CORRELATED | insufficient-data | no-variance")
    top_vs_bottom_decile_lift: Optional[float] = Field(description="Mean outcome of the top-scored decile ÷ the bottom. 1.0 = the score sorts nothing.")
    deciles: List[DecileRow] = Field(description="Decile table, so you can see the shape rather than trust one number.")
    gate_kept_mean: Optional[float] = Field(description="Mean outcome of items your gate keeps.")
    gate_dropped_mean: Optional[float] = Field(description="Mean outcome of items it drops.")
    gate_verdict: Optional[str] = Field(description="What the gate is actually doing.")
    warnings: List[str] = Field(description="Things that make the numbers less trustworthy than they look.")
    report: str = Field(description="Human-readable summary.")


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _rank(vals: List[float]) -> List[float]:
    """Average ranks, so ties do not distort Spearman."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman(xs: List[float], ys: List[float]) -> Optional[float]:
    return _pearson(_rank(xs), _rank(ys))


class App(BaseApp):
    async def setup(self, config: AppSetup):
        logger.info("score-vs-outcome ready (stateless)")

    async def run(self, input_data: RunInput) -> RunOutput:
        pairs = [
            (s, o) for s, o in zip(input_data.scores, input_data.outcomes)
            if math.isfinite(s) and math.isfinite(o)
        ]
        warnings: List[str] = []
        dropped = min(len(input_data.scores), len(input_data.outcomes)) - len(pairs)
        if dropped:
            warnings.append(f"dropped {dropped} pair(s) with non-finite values")
        if len(input_data.scores) != len(input_data.outcomes):
            warnings.append(
                f"scores ({len(input_data.scores)}) and outcomes "
                f"({len(input_data.outcomes)}) differ in length — compared "
                f"the first {min(len(input_data.scores), len(input_data.outcomes))}"
            )

        n = len(pairs)
        if n < 10:
            return RunOutput(
                n=n, pearson_r=None, spearman_r=None, noise_floor=1.0,
                verdict="insufficient-data", top_vs_bottom_decile_lift=None,
                deciles=[], gate_kept_mean=None, gate_dropped_mean=None,
                gate_verdict=None, warnings=warnings,
                report=f"Only {n} usable pair(s). Need at least 10, ideally 30+.",
            )

        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        if not input_data.higher_score_means_better:
            xs = [-x for x in xs]
            warnings.append("scores negated because higher_score_means_better=false")

        r = _pearson(xs, ys)
        rho = _spearman(xs, ys)
        floor = 2.0 / math.sqrt(n)

        zeros = sum(1 for y in ys if y == 0)
        if zeros / n > 0.3:
            warnings.append(
                f"{zeros*100//n}% of outcomes are exactly zero — correlation is "
                "weak evidence on zero-inflated data; read the decile table instead"
            )
        if len(set(xs)) < max(3, n // 20):
            warnings.append("scores take very few distinct values — limited ability to rank")

        if r is None:
            verdict = "no-variance"
        elif n < 30:
            verdict = "insufficient-data"
        elif r <= -floor:
            verdict = "ANTI-CORRELATED"
        elif abs(r) < floor:
            verdict = "noise"
        elif r < 0.3:
            verdict = "weak"
        else:
            verdict = "predictive"

        # Decile table
        ordered = sorted(pairs, key=lambda p: p[0])
        deciles: List[DecileRow] = []
        size = max(1, n // 10)
        for d in range(10):
            chunk = ordered[d * size: (d + 1) * size] if d < 9 else ordered[9 * size:]
            if not chunk:
                continue
            deciles.append(DecileRow(
                decile=d + 1, n=len(chunk),
                mean_score=round(sum(c[0] for c in chunk) / len(chunk), 4),
                mean_outcome=round(sum(c[1] for c in chunk) / len(chunk), 4),
            ))
        lift = None
        if len(deciles) >= 2 and deciles[0].mean_outcome != 0:
            lift = round(deciles[-1].mean_outcome / deciles[0].mean_outcome, 3)

        # Gate analysis
        kept_mean = dropped_mean = None
        gate_verdict = None
        if input_data.gate_threshold is not None:
            t = input_data.gate_threshold
            hib = input_data.higher_score_means_better
            kept = [o for sc, o in pairs if (sc >= t if hib else sc <= t)]
            drop = [o for sc, o in pairs if (sc < t if hib else sc > t)]

            # A gate verdict from a handful of items is exactly the error this
            # app exists to catch. On the dataset that motivated it, only 1 of
            # 207 items fell below the threshold — and the first version of this
            # code duly announced "THE GATE IS BACKWARDS" from that single point.
            # Require a real sample on BOTH sides before saying anything
            # directional, and always report the split so the reader can judge.
            _MIN_SIDE = 10
            if not kept or not drop:
                gate_verdict = (
                    f"threshold {t} keeps {len(kept)} and drops {len(drop)} — "
                    "one side is empty, nothing to compare"
                )
            elif len(kept) < _MIN_SIDE or len(drop) < _MIN_SIDE:
                gate_verdict = (
                    f"threshold {t} splits {len(kept)} kept / {len(drop)} dropped — "
                    f"too lopsided to judge (need {_MIN_SIDE}+ each side). Means are "
                    f"{sum(kept)/len(kept):.2f} vs {sum(drop)/len(drop):.2f}, but the "
                    "small side is not a sample."
                )
                warnings.append(
                    f"gate analysis suppressed: only {min(len(kept), len(drop))} "
                    "item(s) on one side of the threshold"
                )
            else:
                kept_mean = round(sum(kept) / len(kept), 4)
                dropped_mean = round(sum(drop) / len(drop), 4)
                if kept_mean > dropped_mean:
                    gate_verdict = (
                        f"the gate helps: kept items average {kept_mean} vs "
                        f"{dropped_mean} dropped ({len(kept)} kept, {len(drop)} dropped)"
                    )
                elif kept_mean < dropped_mean:
                    gate_verdict = (
                        f"THE GATE IS BACKWARDS: items it keeps average {kept_mean}, "
                        f"items it drops average {dropped_mean} ({len(kept)} kept, "
                        f"{len(drop)} dropped). You are filtering out the better half."
                    )
                else:
                    gate_verdict = "the gate makes no difference to mean outcome"

        lines = [f"n={n}  pearson={r:.3f}" if r is not None else f"n={n}  pearson=undefined"]
        if rho is not None:
            lines[0] += f"  spearman={rho:.3f}"
        lines[0] += f"  (noise floor ±{floor:.3f})"
        lines.append(f"VERDICT: {verdict}")
        if verdict == "ANTI-CORRELATED":
            lines.append(
                "  The score is negatively associated with the outcome. Anything "
                "maximising it is moving away from the goal while reporting progress."
            )
        elif verdict == "noise":
            lines.append("  Indistinguishable from a random number at this sample size.")
        if lift is not None:
            lines.append(f"  top decile / bottom decile outcome = {lift}×")
        if gate_verdict:
            lines.append(f"  gate: {gate_verdict}")
        for w in warnings:
            lines.append(f"  ! {w}")

        logger.info("n=%d r=%s verdict=%s lift=%s", n, r, verdict, lift)
        return RunOutput(
            n=n, pearson_r=round(r, 4) if r is not None else None,
            spearman_r=round(rho, 4) if rho is not None else None,
            noise_floor=round(floor, 4), verdict=verdict,
            top_vs_bottom_decile_lift=lift, deciles=deciles,
            gate_kept_mean=kept_mean, gate_dropped_mean=dropped_mean,
            gate_verdict=gate_verdict, warnings=warnings,
            report="\n".join(lines),
        )
