"""Image primitives the Impact effects are built from.

Ported from the deliverable that produced the approved reels. Two deliberate
changes, neither of which alters the maths:

* **Shape is a parameter, not a module global.** The originals closed over
  OH/OW, which made them single-resolution and untestable on a small fixture.
* **The noise cache is keyed by shape as well as seed.** Sharing one cache
  across resolutions returned a wrongly-sized array, silently.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

LUM = np.array([0.299, 0.587, 0.114], np.float32)
WHITE = np.array([255.0, 250.0, 242.0], np.float32)
ORANGE = np.array([255.0, 92.0, 54.0], np.float32)  # crimson, not amber
RED = np.array([255.0, 46.0, 58.0], np.float32)
BOLT = np.array([255.0, 118.0, 150.0], np.float32)  # pink end to end: a white
# core has red-excess ~0 and
# is invisible to the gate

_NOISE: dict[tuple, np.ndarray] = {}


def blur(a: np.ndarray, s: float) -> np.ndarray:
    if s <= 0:
        return a
    return (
        np.asarray(
            Image.fromarray(np.clip(a * 255, 0, 255).astype(np.uint8)).filter(
                ImageFilter.GaussianBlur(s)
            ),
            np.float32,
        )
        / 255.0
    )


def dilate(m: np.ndarray, r: float) -> np.ndarray:
    return (blur(m, r * 0.55) > 0.10).astype(np.float32)


def erode(m: np.ndarray, r: float) -> np.ndarray:
    return 1.0 - dilate(1.0 - m, r)


def screen(b: np.ndarray, a: np.ndarray) -> np.ndarray:
    return 1.0 - (1.0 - b) * (1.0 - a)


def luma(rgb01: np.ndarray) -> np.ndarray:
    return rgb01 @ LUM


def dist_outside(m: np.ndarray, max_px: int = 360, scale: int = 4) -> np.ndarray:
    """Distance from the silhouette, in pixels, for everything outside it.

    Iterated 3x3 max-filter on a 1/scale mask: each pass grows the region by one
    low-res pixel, so the pass index at which a pixel is first covered IS its
    distance. A blur cannot substitute -- the aura runs a colour ramp along a
    real radial coordinate, and a blur gives a gradient, not a distance.
    """
    h, w = m.shape[0] // scale, m.shape[1] // scale
    small = Image.fromarray(((m > 0.5) * 255).astype(np.uint8)).resize(
        (max(w, 1), max(h, 1)), Image.NEAREST
    )
    cur = np.asarray(small, np.float32) / 255.0
    d = np.full(cur.shape, np.inf, np.float32)
    d[cur > 0.5] = 0.0
    img = small
    for i in range(1, max(max_px // scale, 1) + 1):
        img = img.filter(ImageFilter.MaxFilter(3))
        nxt = np.asarray(img, np.float32) / 255.0
        newly = (nxt > 0.5) & ~np.isfinite(d)
        d[newly] = i * scale
        if not (nxt > 0.5).any():
            break
    d[~np.isfinite(d)] = max_px + scale
    return np.asarray(
        Image.fromarray(np.clip(d, 0, 65535).astype(np.uint16)).resize(
            (m.shape[1], m.shape[0]), Image.BILINEAR
        ),
        np.float32,
    )


def noise(seed: int, shape: tuple[int, int], octaves: tuple[int, ...] = (10, 26)) -> np.ndarray:
    """Multi-octave value noise in [0,1]. Cached per (seed, shape, octaves)."""
    key = (seed, shape, octaves)
    if key in _NOISE:
        return _NOISE[key]
    h, w = shape
    rng = np.random.default_rng(seed)
    acc = np.zeros(shape, np.float32)
    amp, tot = 1.0, 0.0
    for o in octaves:
        s = rng.random((o, o)).astype(np.float32)
        up = (
            np.asarray(
                Image.fromarray((s * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC),
                np.float32,
            )
            / 255.0
        )
        acc += up * amp
        tot += amp
        amp *= 0.55
    _NOISE[key] = acc / tot
    return _NOISE[key]


def subject_height(m: np.ndarray) -> float:
    ys, _ = np.nonzero(m > 0.5)
    return float(ys.max() - ys.min() + 1) if len(ys) else float(m.shape[0])


def centroid(m: np.ndarray) -> tuple[float, float] | None:
    ys, xs = np.nonzero(m > 0.5)
    if not len(ys):
        return None
    return float(xs.mean()), float(ys.mean())


def limb_weight(
    m: np.ndarray, limb: float = 0.7, erode_px: float = 46.0, blur_px: float = 22.0
) -> np.ndarray:
    """Torso at full strength, limbs at ``limb``.

    Heavy erosion keeps the torso and loses the arms and legs, which is exactly
    the distinction wanted -- no skeleton needed.
    """
    core = erode(m, erode_px)
    return limb + (1.0 - limb) * np.clip(blur(core, blur_px), 0, 1)


def vertical_weight(m: np.ndarray) -> np.ndarray:
    """Strongest at the shoulders, falling toward the feet."""
    h, w = m.shape
    ys, _ = np.nonzero(m > 0.5)
    if len(ys) < 50:
        return np.ones((h, w), np.float32)
    y0, y1 = float(ys.min()), float(ys.max())
    t = np.clip((np.arange(h, dtype=np.float32) - y0) / max(y1 - y0, 1.0), 0, 1)
    prof = np.interp(t, [0.0, 0.55, 1.0], [1.0, 0.6, 0.4]).astype(np.float32)
    return np.repeat(prof[:, None], w, axis=1)
