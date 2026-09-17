"""§1 — the subject's garment colour, named in HSV, matched inside the foreground.

CONTRACT CHANGED 2026-09-17. The previous spec was
`{r_min, g_min, b_over_r, r_minus_b}` -- a brightness-and-ratio test. It can
express "bright yellow" and "bright red"; it CANNOT express "dark red", because
for a dark garment the percentile-derived floors collapse toward zero and the
test stops discriminating. Measured on UFC eHX2_5XKJ5Q: it found the right hue
(2.8 deg, maroon trunks) and then selected 10-19% of the frame -- cage padding,
canvas logos, crowd -- and SAM2 lost the subject for 35 of 96 frames.

HSV names a dark colour without difficulty: hue is independent of brightness.

The second half of the fix is scope. The seed is evaluated ONLY inside the
birefnet foreground, never against the whole frame. `clicks.py` already had that
constraint for its negative click; the seed had not inherited it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

# PIL packs hue into 0-255. Skin sits in a narrow hue band at moderate
# saturation; excluding it stops a bare torso becoming the seed.
SKIN_HUE_DEG = (5.0, 35.0)
SKIN_SAT_MAX = 0.60

DEFAULT_TOL = 12.0
DEFAULT_SAT_MIN = 0.45
DEFAULT_VAL_MIN = 0.15


@dataclass(frozen=True)
class ColourSeed:
    spec: dict | None
    coverage: float  # fraction OF THE FRAME the spec selects
    source: str  # "garment" | "none"

    @property
    def has_colour_prior(self) -> bool:
        return self.spec is not None

    @property
    def hue_deg(self) -> float | None:
        return None if self.spec is None else self.spec["hue_deg"]


def _hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    hsv = np.asarray(Image.fromarray(rgb.astype(np.uint8)).convert("HSV"))
    h = hsv[..., 0].astype(np.float32) * (360.0 / 255.0)
    s = hsv[..., 1].astype(np.float32) / 255.0
    v = hsv[..., 2].astype(np.float32) / 255.0
    return h, s, v


def _hue_dist(h: np.ndarray, centre: float) -> np.ndarray:
    d = np.abs(h - centre)
    return np.minimum(d, 360.0 - d)


def match(rgb: np.ndarray, spec: dict, foreground: np.ndarray | None = None) -> np.ndarray:
    """Pixels matching the spec. Scoped to `foreground` whenever one is given --
    an unscoped match is what selected the cage padding."""
    h, s, v = _hsv(rgb)
    m = (
        (_hue_dist(h, float(spec["hue_deg"])) <= float(spec.get("hue_tol", DEFAULT_TOL)))
        & (s >= float(spec.get("sat_min", DEFAULT_SAT_MIN)))
        & (v >= float(spec.get("val_min", DEFAULT_VAL_MIN)))
    )
    if foreground is not None:
        m &= foreground > 0.5
    return m.astype(np.float32)


def derive(
    rgb: np.ndarray,
    foreground: np.ndarray,
    min_px: int = 400,
    hue_tol: float = DEFAULT_TOL,
    sat_min: float = DEFAULT_SAT_MIN,
    val_min: float = DEFAULT_VAL_MIN,
) -> ColourSeed:
    """Dominant saturated non-skin hue inside the foreground."""
    h, s, v = _hsv(rgb)
    fg = foreground > 0.5
    skin = (h >= SKIN_HUE_DEG[0]) & (h <= SKIN_HUE_DEG[1]) & (s < SKIN_SAT_MAX)
    cand = fg & (s >= sat_min) & (v >= val_min) & ~skin
    if int(cand.sum()) < min_px:
        return ColourSeed(None, 0.0, "none")

    hist = np.bincount(np.round(h[cand]).astype(np.int32) % 360, minlength=360).astype(np.float32)
    # circular smoothing: 359 deg and 1 deg are neighbours
    k = np.array([1, 2, 3, 2, 1], np.float32)
    hist = np.convolve(np.concatenate([hist[-2:], hist, hist[:2]]), k, "same")[2:-2]
    peak = float(np.argmax(hist))

    spec = {
        "hue_deg": round(peak, 1),
        "hue_tol": float(hue_tol),
        "sat_min": float(sat_min),
        "val_min": float(val_min),
    }
    sel = match(rgb, spec, foreground)
    if float(sel.sum()) < min_px:
        return ColourSeed(None, 0.0, "none")
    return ColourSeed(spec, float(sel.mean()), "garment")
