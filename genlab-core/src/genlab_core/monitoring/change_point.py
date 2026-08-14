"""Phase 2.F — CUSUM change-point detection on reward distributions.

Detects mean-shift in a time-series (positive or negative). Used to
flag "this niche×platform's reward distribution shifted meaningfully"
— typically a platform algorithm change (Meta downranking, YT
algorithm update, IG reach cap) that would otherwise show up as
silent degradation.

## CUSUM algorithm

For each new observation x_t, maintain two running sums:

  S+_t = max(0, S+_{t-1} + (x_t - target - k))
  S-_t = max(0, S-_{t-1} + (target - x_t - k))

Where:
  * target = pre-shift mean (rolling window baseline)
  * k = allowance = 0.5 × σ (half the standard deviation)
  * threshold h = 4 × σ (change flagged when S+ or S- exceeds h)

CUSUM is well-behaved for detecting persistent shifts even when
individual samples are noisy — much better than "compare current
week's mean to last week's mean" which triggers on random spikes.

## Output

`detect_change_point(series)` returns None if no change detected, or
a ChangePoint with:

  * ``direction`` — 'up' | 'down'
  * ``at_index`` — where in the series the change fired
  * ``magnitude`` — |shift| / σ (effect size)
  * ``confidence`` — proxy for posterior probability (0..1)

## Design notes

Real Bayesian change-point (BOCPD) would give principled posterior
probabilities but is much heavier. CUSUM is Occam's razor here —
2-4× less code, 10× faster, catches the same class of shifts.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Minimum samples before we trust the detector
_MIN_SAMPLES: int = 14

# CUSUM tuning knobs — h/σ ratio determines sensitivity.
# h=4σ is the standard textbook choice: catches shifts ≥ 0.5σ with
# average run length ~168 samples in-control (false-positive rate
# per sample ~= 0.6% at that threshold).
_H_SIGMA_MULTIPLIER: float = 4.0
_K_SIGMA_MULTIPLIER: float = 0.5


@dataclass(frozen=True)
class ChangePoint:
    direction: str          # 'up' | 'down'
    at_index: int           # position in series where CUSUM fired
    magnitude: float        # |shift| / σ
    confidence: float       # proxy for posterior probability (0..1)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list[float]) -> float:
    """Population standard deviation. Returns 0.0 for len<2."""
    n = len(xs)
    if n < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / n)


def detect_change_point(
    series: list[float],
    *,
    baseline_window: int = 7,
) -> ChangePoint | None:
    """Run CUSUM over the series with a rolling baseline_window
    prefix. Returns the FIRST detected change point (if any).

    baseline_window controls how many initial samples are used to
    estimate `target` and `σ`. Default 7 matches a weekly cycle.

    Fail-open: any exception returns None (never crashes the caller).
    """
    try:
        if len(series) < _MIN_SAMPLES:
            return None
        if len(series) <= baseline_window:
            return None

        baseline = series[:baseline_window]
        target = _mean(baseline)
        sigma = _std(baseline)
        if sigma <= 0:
            # Degenerate baseline (all-zero or constant) — can't compute
            # CUSUM. Return None rather than divide by zero.
            return None

        h = _H_SIGMA_MULTIPLIER * sigma
        k = _K_SIGMA_MULTIPLIER * sigma

        s_plus = 0.0
        s_minus = 0.0
        for i, x in enumerate(series[baseline_window:], start=baseline_window):
            s_plus = max(0.0, s_plus + (x - target - k))
            s_minus = max(0.0, s_minus + (target - x - k))

            if s_plus >= h:
                # Upward shift detected
                magnitude = s_plus / sigma
                # Confidence proxy: 1 - exp(-CUSUM/h). Bounded [0, 1).
                confidence = min(0.99, 1.0 - math.exp(-s_plus / h))
                return ChangePoint(
                    direction="up", at_index=i,
                    magnitude=round(magnitude, 3),
                    confidence=round(confidence, 3),
                )
            if s_minus >= h:
                magnitude = s_minus / sigma
                confidence = min(0.99, 1.0 - math.exp(-s_minus / h))
                return ChangePoint(
                    direction="down", at_index=i,
                    magnitude=round(magnitude, 3),
                    confidence=round(confidence, 3),
                )
        return None
    except Exception as exc:
        logger.debug("[change_point] detection failed: %s", exc)
        return None


__all__ = [
    "ChangePoint",
    "detect_change_point",
]
