"""Detect static CHROME in a source and exclude it from the video rectangle.

Two sources of chrome, one detector:

* screen-captured embeds (Twitter/Reddit) carry tweet text and UI borders;
* broadcast footage carries a score/clock bug, usually a band along the bottom.

Both are static overlays composited onto live video, and both violate the same
rule: the background comes from the VIDEO, never from the chrome. A score bug
dragged into an ACTION crop gets graded, aura'd and punched-in along with the
fighters, which looks exactly as wrong as it sounds.

Chrome is separated from live picture by two signals together, because neither
alone is sufficient:

**Motion energy is the wrong instrument for a broadcast bug, and it fails
silently.** Measured on the UFC knockdown: the bottom band's YDIF sat at 50% of
peak and the detector reported "no chrome", but the frame plainly carries
``HUNT | DWCS 4:35 R1 | PEREA``. The clock TICKS -- 4:35, 4:34, 4:32 across the
window -- so the band is full of motion. Tweet chrome is motionless; a
scoreboard is not.

What separates chrome from picture is that most of its pixels NEVER CHANGE. The
bug's grey box, rules and lettering are identical every frame; only the digits
move. So the signal is the FROZEN-PIXEL FRACTION per row: the share of pixels
whose temporal standard deviation across the window is ~0.

    UFC knockdown, rows 948-990   frozen fraction 0.13-0.19
    same clip, whole frame        frozen fraction 0.007      (26x baseline)

The threshold is relative to the frame's own baseline, not absolute: the bug is
a centred box about 37% of frame width, so even a perfectly static one cannot
push a full row past ~0.4. This also subsumes the tweet-chrome case, where the
frozen fraction is near 1.0.

Preference is to CROP rather than dim: cropping removes it from every downstream
stage at once, and no later stage has to remember the band is special.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

BANDS = 12
# A row is chrome-like when its frozen-pixel fraction exceeds the frame's own
# baseline by this factor AND clears the absolute floor. Both are needed: the
# ratio alone explodes when the baseline is ~0, the floor alone misses a narrow
# bug on a mostly-static shot.
FROZEN_RATIO = 8.0
FROZEN_FLOOR = 0.04
# Temporal std below this counts a pixel as frozen (8-bit luma).
FROZEN_STD = 2.0
# Only the bottom of the frame is searched: scoreboards live there, and a static
# band across the middle is a composition choice, not chrome.
BOTTOM_SEARCH_FRAC = 0.25
MIN_BAND_ROWS = 8


def frozen_row_fraction(frames) -> np.ndarray:
    """Per-row share of pixels that never change across the sampled window."""
    stack = np.stack([np.asarray(f, np.float32) for f in frames])
    if stack.ndim == 4:
        stack = stack.mean(-1)
    return (stack.std(0) < FROZEN_STD).mean(1)


def detect_static_band(frames, src_h: int | None = None) -> dict:
    """Find a broadcast bug / chrome band along the BOTTOM of the frame.

    ``frames`` is a sequence of sampled luma or RGB arrays spanning the window
    (8+ is plenty). Returns ``{"found", "y0", "y1", "video_rect", "frozen",
    "baseline"}`` where ``video_rect`` is the live-picture rectangle to crop to.

    The rectangle keeps everything ABOVE the band. The thin strip of live
    picture below a floating bug is discarded with it -- a rectangle is what the
    crop takes, and re-including a sliver under the chrome would put the chrome
    back in the frame.
    """
    frac = frozen_row_fraction(frames)
    h = src_h or len(frac)
    baseline = float(np.median(frac))
    thr = max(baseline * FROZEN_RATIO, FROZEN_FLOOR)
    search_from = int(h * (1.0 - BOTTOM_SEARCH_FRAC))
    rows = [y for y in range(search_from, h) if frac[y] >= thr]
    if len(rows) < MIN_BAND_ROWS:
        logger.info(
            "[chrome] no bottom band (baseline=%.4f thr=%.4f, %d rows over)",
            baseline,
            thr,
            len(rows),
        )
        return {
            "found": False,
            "y0": None,
            "y1": None,
            "video_rect": (0, 0, None, h),
            "frozen": frac.tolist(),
            "baseline": baseline,
        }
    y0, y1 = min(rows), max(rows)
    logger.info(
        "[chrome] band y=%d..%d frozen=%.3f vs baseline %.4f — cropping to y<%d",
        y0,
        y1,
        float(frac[y0 : y1 + 1].mean()),
        baseline,
        y0,
    )
    return {
        "found": True,
        "y0": int(y0),
        "y1": int(y1),
        "video_rect": (0, 0, None, int(y0)),
        "frozen": frac.tolist(),
        "baseline": baseline,
    }
