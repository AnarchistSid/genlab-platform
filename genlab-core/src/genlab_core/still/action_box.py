"""ANIME-PEAK-08 §2 — the crop follows the fighters, or it does not crop.

Fights are staged horizontally and the reel is vertical, so the default
centre crop throws away whichever fighter is not in the middle. The box is
the union of where the fighters are; the layout is decided from its width.

The geometry decides the rule. A full-bleed 9:16 crop out of 1920x1080 is
exactly 608x1080 -- that is the widest 9:16 rectangle the source contains --
so every full-bleed crop magnifies by exactly 1080/608 = 1.778x. There is no
continuum to tune. Either the padded action box fits inside 608 px and the
crop can hold both fighters, or it does not and the only honest option is the
wide band over a blurred fill, which keeps the whole staging at the cost of
frame height. That is why the packet's "zoom-to-fit at <= 1.8x" and "else the
wide band" are the same decision asked twice.

Boxes come from colour seeds where the fighters wear a nameable colour, and
from motion energy where they do not -- King is black-purple and black has no
hue, so his seed cannot hold and his shots fall to motion.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

OUT_W, OUT_H = 1080, 1920
# Source dimensions are PROBED, never assumed. The pilot clips are not all
# 1920x1080 -- the My Hero Academia upload is 1280x720 -- and a crop width
# computed against the wrong frame is wider than the source, which fails as
# "could not open encoder" rather than as anything that names the cause.
SRC_W, SRC_H = 1920, 1080                        # reference frame only


def full_bleed_w(src_h: int) -> int:
    """Widest 9:16 rectangle the source contains.

    Floored, not rounded: 1080 * 1080 / 1920 is 607.5, and rounding UP makes
    the crop fractionally wider than 9:16, so scaling it to 1080 wide lands
    at 1918 px and leaves a two-pixel letterbox on a shot that is supposed to
    fill the reel.
    """
    return int(src_h * OUT_W // OUT_H)

PAD = 0.10               # the packet's 10% pad around the union
# A colour seed locates a GARMENT, not a person. When that garment already
# fills half the frame the shot is a close-up and magnifying it crops into
# fabric -- measured on two Demon Slayer frames whose boxes were the same
# width (499 vs 551 px) but whose heights were 34% and 58% of frame: the
# first held the whole fighter, the second was haori pattern. Provisional
# at n=2; the direction (never magnify what is already large) is not.
ALREADY_TIGHT_H = 0.50   # box height share above which no zoom is applied
TIGHT_MIN_CROP_FRAC = 0.443   # ...and the narrowest crop, as a share of width
MIN_SEED_FRAC = 0.002    # a seed selecting less than this did not find a fighter
ABSENT_FRAC = 0.0015     # below this, the fighter's colour is not in the shot
MOTION_PCT = 88.0        # percentile of frame difference counted as "moving"
SAMPLE_W = 480           # boxes are gross geometry; work small


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float
    source: str          # "colour:<name>" | "motion" | "union"

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    def padded(self, pad: float = PAD, w: int = SRC_W, h: int = SRC_H) -> Box:  # noqa: D401
        dx, dy = self.w * pad, self.h * pad
        return Box(max(0.0, self.x0 - dx), max(0.0, self.y0 - dy),
                   min(w, self.x1 + dx), min(h, self.y1 + dy), self.source)

    def union(self, other: Box | None) -> Box:
        if other is None:
            return self
        return Box(min(self.x0, other.x0), min(self.y0, other.y0),
                   max(self.x1, other.x1), max(self.y1, other.y1), "union")


@dataclass(frozen=True)
class Layout:
    kind: str            # "box" (full-bleed) | "band" (letterboxed)
    crop_x: int          # left edge of the crop
    crop_w: int          # width of the crop, >= FULL_BLEED_W
    magnification: float
    box: Box | None
    fighters_inside: bool

    src_h: int = SRC_H

    @property
    def band_height(self) -> int:
        """Height the cropped strip occupies in the 1080x1920 output."""
        return min(OUT_H, int(round(OUT_W * self.src_h / self.crop_w)))

    def row(self, label: str) -> str:
        b = f"{self.box.w:4.0f}px" if self.box else " none"
        return (f"  {label:<22}{self.kind:<5} box {b} crop {self.crop_w:>4}px "
                f"x={self.crop_x:>4} {self.magnification:.2f}x "
                f"fills {self.band_height / OUT_H:>4.0%} "
                f"{'covered' if self.fighters_inside else 'not covered'}")


def probe_dims(path: Path) -> tuple[int, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True, check=True)
    w, h = out.stdout.strip().split(",")[:2]
    return int(w), int(h)


def _frame(path: Path, t: float):
    import numpy as np
    from PIL import Image

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "f.png"
        subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(path),
                        "-frames:v", "1", "-vf", f"scale={SAMPLE_W}:-2",
                        "-y", str(p)], check=True)
        return np.asarray(Image.open(p).convert("RGB"), dtype=np.uint8)


COMPONENT_KEEP = 0.35    # components this fraction of the largest are kept too


def _mask_box(mask, min_frac: float, source: str,
              dims: tuple[int, int] = (SRC_W, SRC_H)) -> Box | None:
    """Bounding box of the matching OBJECT, not of the matching pixels.

    A box over the raw mask is full-frame every time: anime backgrounds and
    effects carry the same saturated hues as the costumes, so a colour seed
    selects specks everywhere and a bounding box is maximally sensitive to
    exactly those. Percentile trimming does not help when the outliers are
    spread rather than few. Connected components change the question from
    "where are the matching pixels" to "where is the matching thing".
    """
    import cv2
    import numpy as np

    if float(mask.mean()) < min_frac:
        return None
    m = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE,
                         np.ones((5, 5), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return None
    areas = stats[1:, cv2.CC_STAT_AREA]
    keep = np.nonzero(areas >= max(areas.max() * COMPONENT_KEEP,
                                   min_frac * mask.size))[0]
    if keep.size == 0:
        return None
    sel = stats[1:][keep]
    x0 = float(sel[:, cv2.CC_STAT_LEFT].min())
    y0 = float(sel[:, cv2.CC_STAT_TOP].min())
    x1 = float((sel[:, cv2.CC_STAT_LEFT] + sel[:, cv2.CC_STAT_WIDTH]).max())
    y1 = float((sel[:, cv2.CC_STAT_TOP] + sel[:, cv2.CC_STAT_HEIGHT]).max())
    sx, sy = dims[0] / mask.shape[1], dims[1] / mask.shape[0]
    return Box(x0 * sx, y0 * sy, x1 * sx, y1 * sy, source)


def colour_box(path: Path, t: float, spec: dict, name: str) -> Box | None:
    from genlab_core.action import colour_seed

    rgb = _frame(path, t)
    mask = colour_seed.match(rgb, spec) > 0.5
    return _mask_box(mask, MIN_SEED_FRAC, f"colour:{name}", probe_dims(path))


def motion_box(path: Path, t: float, dt: float = 0.25) -> Box | None:
    """Where the frame changed. The fallback when no colour holds."""
    import numpy as np

    a = _frame(path, t).astype(np.int16)
    b = _frame(path, t + dt).astype(np.int16)
    d = np.abs(a - b).max(axis=2)
    thr = np.percentile(d, MOTION_PCT)
    return _mask_box(d > max(thr, 12.0), 0.01, "motion", probe_dims(path))


@dataclass(frozen=True)
class ShotEvidence:
    box: Box | None
    fired: tuple[str, ...]        # seeds that located a fighter
    unaccounted: tuple[str, ...]  # seeds whose colour IS in shot but unlocated

    @property
    def covers_everyone(self) -> bool:
        return not self.unaccounted


def colour_presence(path: Path, t: float, spec: dict) -> float:
    """Share of the frame matching the seed, with no component grouping."""
    from genlab_core.action import colour_seed

    return float((colour_seed.match(_frame(path, t), spec) > 0.5).mean())


def shot_evidence(path: Path, t: float, seeds: dict[str, dict]) -> ShotEvidence:
    """Where the fighters are, and whether that accounts for all of them.

    Cropping is only safe when the evidence covers every fighter. The
    measured failure was not box accuracy but coverage: on a two-fighter
    clash where only one seed fires, the box goes narrow, the crop follows
    it, and the other fighter is cut out of the reel -- which is the defect
    this whole section exists to remove.

    A seed that does not fire is ambiguous on its own: the fighter may be out
    of shot, or present but unmatched. Their colour's raw share of the frame
    separates the two. Absent colour means out of shot and the crop may
    proceed; present-but-unlocated means unknown, and the shot falls to the
    band that keeps the whole staging.
    """
    box: Box | None = None
    fired: list[str] = []
    unaccounted: list[str] = []
    for name, spec in seeds.items():
        c = colour_box(path, t, spec, name)
        if c is not None:
            box = c if box is None else box.union(c)
            fired.append(name)
        elif colour_presence(path, t, spec) >= ABSENT_FRAC:
            unaccounted.append(name)
    if box is None:
        box = motion_box(path, t)
    return ShotEvidence(box, tuple(fired), tuple(unaccounted))


def shot_box(path: Path, t: float, seeds: dict[str, dict]) -> Box | None:
    return shot_evidence(path, t, seeds).box


def layout_for(box: Box | None, dims: tuple[int, int] = (SRC_W, SRC_H), *,
               covers_everyone: bool = True) -> Layout:
    """The crop width follows the box, floored at full bleed.

    An earlier version chose between a 608 px full-bleed crop and the whole
    1920 px frame. Rendered, that put most shots in a 1080x608 strip filling
    32% of a 1080x1920 reel -- it reads as black bars, not as a frame. The
    choice is not binary: cropping to the BOX's width and letterboxing only
    the remainder zooms as far as the staging allows on every shot, and
    degrades smoothly to the full band when the fighters really are spread
    across the frame.
    """
    src_w, src_h = dims
    fb = full_bleed_w(src_h)
    if box is None or not covers_everyone:
        p = box.padded(w=src_w, h=src_h) if box else None
        return Layout("band", 0, src_w, OUT_W / src_w, p, box is None, src_h)
    p = box.padded(w=src_w, h=src_h)
    floor = (int(TIGHT_MIN_CROP_FRAC * src_w)
             if p.h >= ALREADY_TIGHT_H * src_h else fb)
    crop_w = int(round(min(max(p.w, floor), src_w)))
    x = int(round(min(max(0.0, p.cx - crop_w / 2), src_w - crop_w)))
    inside = p.x0 >= x - 1 and p.x1 <= x + crop_w + 1
    kind = "box" if crop_w <= fb else "band"
    return Layout(kind, x, crop_w, OUT_W / crop_w, p, inside, src_h)


def layout_at(path: Path, t: float, seeds: dict[str, dict]) -> tuple[Layout, ShotEvidence]:
    ev = shot_evidence(path, t, seeds)
    return layout_for(ev.box, probe_dims(path), covers_everyone=ev.covers_everyone), ev
