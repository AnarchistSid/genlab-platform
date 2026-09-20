"""Is the rendered frame actually filled, or is there a letterbox bar?

Two checks, and only one of them is authoritative.

**Structural** — a crop taken at or above ``full_bleed_floor(src_h)`` and lying
inside the source rectangle CANNOT letterbox. That is geometry, it is decidable
before a single pixel is rendered, and it is the check that should gate.

**Photometric** — look at the rendered edges for a dark, flat, frozen strip.
This exists to catch a rendering mistake the geometry would not predict. It is a
guard, not the gate.

The photometric thresholds cannot be absolute, and this is the bug being fixed.
The strip test was ``mean < 10 and std < 2 and temporal_diff < 1.0``, tuned on
an arena source. UFC cage-side footage has a genuinely near-black, flat, nearly
static shadow along the top of the crop:

    false positives measured   mean 1.40-1.67   std 0.45-0.66   diff 0.42-0.83

which is indistinguishable from a letterbox bar by brightness or stillness --
12 of 95 frames were flagged on a crop that is full-bleed BY CONSTRUCTION.

What does separate them is UNIFORMITY ALONG THE STRIP. A letterbox bar is
exactly uniform (std ~ 0); a shadow varies. So the threshold is derived from the
source's own edge-strip spread rather than assumed:

    threshold = p01(edge strip std over the clip) * 0.5

On this source that is 0.588 * 0.5 = 0.294, which gives:

    old absolute (std < 2.0)   12/95 flagged   full-bleed  87.37%
    derived per source          0/95 flagged   full-bleed 100.00%
    synthetic 120px black bar   7/7 caught     (still fires on a real bar)
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

LUM = np.array([0.299, 0.587, 0.114], np.float32)
STRIP_PX = 6
DARK_MAX = 10.0  # a bar is dark; shadow can be too, so this alone is weak
FROZEN_MAX = 1.0  # frame-to-frame change along the strip
STD_PERCENTILE = 1.0  # derive from the source's own quietest strips
STD_MARGIN = 0.5  # ...and sit half-way under them
# Floor for the derived threshold. A persistent bar is its own p01: if every
# frame carries one, the derivation takes std~0 as normal and the threshold
# collapses to zero, declaring the bar to be content. Measured: a synthetic bar
# in all frames drove the derived threshold to 0.000 and 0/39 were flagged. This
# floor sits below any real content spread (UFC's darkest genuine strip is 0.45)
# and above a true bar (exactly 0).
MIN_BAR_STD = 0.15


def _strips(rgb: np.ndarray) -> list[np.ndarray]:
    y = rgb @ LUM if rgb.ndim == 3 else rgb
    return [y[:STRIP_PX, :], y[-STRIP_PX:, :], y[:, :STRIP_PX], y[:, -STRIP_PX:]]


def derive_std_threshold(frames) -> float:
    """Uniformity threshold below which a strip is a BAR, not dark content."""
    spread = np.array([float(s.std()) for f in frames for s in _strips(np.asarray(f, np.float32))])
    return max(float(np.percentile(spread, STD_PERCENTILE) * STD_MARGIN), MIN_BAR_STD)


def structural_full_bleed(mag: float, floor: float, crop, src) -> tuple[bool, str]:
    """Geometry-only: can this crop letterbox? Returns (ok, reason)."""
    x0, y0, cw, ch = crop
    sw, sh = src
    if mag < floor - 1e-6:
        return False, f"magnification {mag:.4f} below full-bleed floor {floor:.4f}"
    if x0 < -1e-6 or y0 < -1e-6 or x0 + cw > sw + 1e-6 or y0 + ch > sh + 1e-6:
        return False, (
            f"crop ({x0:.0f},{y0:.0f},{cw:.0f}x{ch:.0f}) leaves the source rect {sw}x{sh}"
        )
    return True, "crop is inside the source at or above the floor"


def photometric_full_bleed(frames, std_threshold: float | None = None) -> dict:
    """Guard pass over rendered frames. Returns counts and the threshold used."""
    frames = [np.asarray(f, np.float32) for f in frames]
    thr = derive_std_threshold(frames) if std_threshold is None else std_threshold
    prev, flagged = None, 0
    for f in frames:
        st = _strips(f)
        if prev is not None:
            for s_, ps in zip(st, prev, strict=False):
                if (
                    s_.mean() < DARK_MAX
                    and s_.std() < thr
                    and float(np.abs(s_ - ps).mean()) < FROZEN_MAX
                ):
                    flagged += 1
                    break
        prev = st
    n = max(len(frames) - 1, 1)
    pct = 100.0 * (1.0 - flagged / n)
    logger.info(
        "[full-bleed] %.2f%% (%d/%d flagged, std threshold %.3f derived)", pct, flagged, n, thr
    )
    return {
        "full_bleed_pct": round(pct, 2),
        "flagged": flagged,
        "frames": n,
        "std_threshold": round(thr, 4),
    }
