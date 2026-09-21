"""Does this generated still look like it belongs to THIS show?

ANIME-13 §3. A generated still is allowed into an anime reel only as
connective tissue, and only when it was generated WITH the show's cover as an
image reference. This module is the check that the reference actually took.

ANIME-13 specifies a blocking gate: a colour-histogram distance under a
threshold calibrated on the cover-versus-banner pair of the same show. That
was built and MEASURED, and it does not hold. It ships ADVISORY.

Three controls, all on real art, all reproducible from this module:

1. **Calibration pair, 13 FALL-2026 shows with both cover and banner.**
   Same-show distance: mean 0.6615, sd 0.1732 -> threshold 0.8347.
2. **Negative control, 78 different-show cover pairs.** Mean 0.6794 — within
   noise of the same-show mean, and 87.2% of them sit UNDER the threshold. A
   gate that admits seven of every eight wrong answers is not a gate. A cover
   and a banner are different crops of different scenes; they are not a tight
   palette pair, and two unrelated anime covers share a palette because they
   are both anime key art.
3. **Positive control — the show's OWN PV frame against its OWN cover:
   0.8583, versus a DIFFERENT show's cover at 0.8698.** This is the one that
   settles it. The metric rates the show's actual footage as far from the
   show's key art as a stranger's key art is. Gating on it would reject the
   most on-brand material that exists.

What the experiment DID establish, generating the same three prompts with the
cover as an image reference and without it:

    prompt      WITH ref    NO ref     delta
    #0            0.6156    0.8453    +0.2297
    #1            0.6163    0.6316    +0.0153
    #2            0.6995    0.7450    +0.0455
    mean          0.6438    0.7407    +0.0969

The reference moves every prompt toward the cover and the pictures visibly
match the show. But the two sets OVERLAP (worst-with 0.6995 > best-without
0.6316), so the number cannot decide a single still even though it can
describe a batch.

So the rule that survives is STRUCTURAL, not metric: a generated still is
allowed only when it was generated WITH the cover as a reference, and where
there is no cover there is no generated still — a PV frame is used instead.
That rule needs no threshold to enforce, which is why it is the one kept.

The distance is still computed and recorded per still, because a batch mean
that drifts is worth seeing. It does not reject anything.
"""

from __future__ import annotations

import logging
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_UA = {"User-Agent": "GenLab/1.0 (+https://github.com/AnarchistSid/genlab-platform)"}

#: Hue is the axis a style match lives on; a 16-bin hue histogram weighted by
#: saturation and value ignores how dark a crop happens to be and compares the
#: palette itself. Straight RGB histograms score two pictures of the same show
#: as different when one is a night scene.
_HUE_BINS = 16
_SAT_BINS = 4


@dataclass(frozen=True)
class PaletteDistance:
    value: float
    threshold: float

    @property
    def passes(self) -> bool:
        return self.value <= self.threshold

    def row(self) -> str:
        return f"{self.value:.4f} vs {self.threshold:.4f} {'PASS' if self.passes else 'MISS'}"


def fetch_image(url: str, dest: Path, *, timeout_s: int = 60) -> Path:
    """Download one image. Explicit UA per rule #25 — WAF-fronted CDNs answer
    403 to Python-urllib's default and it reads as a missing asset."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        dest.write_bytes(r.read())
    return dest


def _load_rgb(path: Path, side: int = 128) -> np.ndarray:
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB").resize((side, side))
        return np.asarray(im, dtype=np.float32) / 255.0


def palette_histogram(path: Path) -> np.ndarray:
    """Saturation-weighted hue x saturation histogram, L1-normalised.

    Weighted by saturation AND value so that flat greys and near-black areas —
    which every picture has and which say nothing about a show's palette —
    cannot dominate the comparison.
    """
    rgb = _load_rgb(path)
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    delta = mx - mn
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]

    hue = np.zeros_like(mx)
    nz = delta > 1e-6
    with np.errstate(invalid="ignore", divide="ignore"):
        rmask = nz & (mx == r)
        gmask = nz & (mx == g)
        bmask = nz & (mx == b)
        hue[rmask] = ((g - b)[rmask] / delta[rmask]) % 6
        hue[gmask] = ((b - r)[gmask] / delta[gmask]) + 2
        hue[bmask] = ((r - g)[bmask] / delta[bmask]) + 4
    hue = (hue / 6.0) % 1.0
    sat = np.where(mx > 1e-6, delta / np.maximum(mx, 1e-6), 0.0)

    hb = np.clip((hue * _HUE_BINS).astype(int), 0, _HUE_BINS - 1)
    sb = np.clip((sat * _SAT_BINS).astype(int), 0, _SAT_BINS - 1)
    weight = sat * mx

    hist = np.zeros((_HUE_BINS, _SAT_BINS), dtype=np.float64)
    np.add.at(hist, (hb, sb), weight)
    total = hist.sum()
    return (hist / total).ravel() if total > 0 else hist.ravel()


def distance(a: Path, b: Path) -> float:
    """Hellinger distance between two palette histograms. 0 = identical.

    Hellinger rather than a straight L1: it is bounded in [0, 1], symmetric,
    and behaves sensibly when one picture concentrates its weight in a couple
    of bins, which anime key art routinely does.
    """
    ha, hb = palette_histogram(a), palette_histogram(b)
    return float(np.sqrt(1.0 - np.sum(np.sqrt(ha * hb))))


def advisory_threshold(pairs: list[tuple[Path, Path]]) -> float:
    """Threshold from real cover/banner pairs of the SAME show. ADVISORY.

    Kept because the number is worth reporting and because the calibration is
    the evidence for why it is not enforced — see the module docstring for the
    negative and positive controls that disqualified it as a gate. Callers
    must not branch on it.
    """
    if not pairs:
        raise ValueError("cannot calibrate a threshold with no pairs")
    ds = np.array([distance(a, b) for a, b in pairs], dtype=np.float64)
    thr = float(ds.mean() + ds.std())
    logger.info(
        "[palette] ADVISORY threshold from %d same-show cover/banner pairs: "
        "mean %.4f, sd %.4f, threshold %.4f (min %.4f, max %.4f)",
        len(ds),
        ds.mean(),
        ds.std(),
        thr,
        ds.min(),
        ds.max(),
    )
    return thr


def check(still: Path, cover: Path, threshold: float) -> PaletteDistance:
    """Measure and record. ``PaletteDistance.passes`` is REPORTING, not a gate.

    Nothing in the render path may branch on it — see the module docstring.
    """
    d = PaletteDistance(distance(still, cover), threshold)
    if not d.passes:
        logger.info("[palette] advisory: still sits %s (not blocking)", d.row())
    return d


def frame_from(video: Path, t: float, dest: Path) -> Path:
    """One frame of the PV, for use where a generated still is not allowed."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(video),
            "-ss",
            f"{t:.3f}",
            "-frames:v",
            "1",
            str(dest),
        ],
        check=True,
        timeout=120,
    )
    return dest
