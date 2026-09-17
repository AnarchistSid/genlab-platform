"""Per-instance mattes for ACTION: foreground, clicks, propagation, warp.

This is the step that made every other ACTION effect possible. Before it, the
aura, the edge arcs, the flash and the trails all read from a centroid blob and
looked like it. Every one of them is built on the silhouette.

Runs INSIDE the worker, never on prod -- measured 2026-09-17 on the 2-core /
3.8 GB / no-GPU box: SAM2 at 20.66 s/frame (138 min for a 384-frame reel
against a 15-minute budget) and birefnet OOM-killed at 2,931 MB with the box
idle. The pipeline side calls ``matte_worker.request_matte`` and falls to legacy.

Four things here were each learned by getting them wrong:

* **birefnet only on annotation frames.** It is the expensive half and its only
  job is to scope the hue search; SAM2 propagates the rest.
* **Seed propagation with a MASK, not a point.** A click on trunks propagates
  the trunks: 1.42% mean matte area and 9 empty frames. The image predictor's
  largest whole-body mask gives 7.87% native / 30.92% in crop, 0 empty.
* **Re-click at every cut.** Propagation across a cut tracks whatever is now at
  those coordinates, which is usually the wrong person.
* **A matte over ~60% of the foreground is a garment, not a person** -- the
  tracker has locked onto a colour field rather than a body.

Heavy dependencies (torch, sam2, rembg) are INJECTED. This module imports none
of them, so its pins run in milliseconds and the worker owns the model loading.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

# Read from impact.yaml's `measured` block; defaults here match it so the module
# is usable standalone and a drift between the two is a test failure, not a
# silent difference.
MATTE_AREA_BAND = (0.12, 0.55)
GARMENT_NOT_PERSON_FRAC = 0.60
MIN_COMPONENT_PX = 400


@dataclass
class MatteReport:
    frames: int = 0
    empty_frames: list[int] = field(default_factory=list)
    area_min: float = 0.0
    area_max: float = 0.0
    area_mean: float = 0.0
    annotations: int = 0
    negatives: int = 0
    rejected_garment: list[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.frames > 0 and not self.empty_frames

    def as_dict(self) -> dict:
        return {"frames": self.frames, "empty_frames": self.empty_frames,
                "area_min": round(self.area_min, 4),
                "area_max": round(self.area_max, 4),
                "area_mean": round(self.area_mean, 4),
                "annotations": self.annotations, "negatives": self.negatives,
                "rejected_garment": self.rejected_garment}


def annotation_frames(n_frames: int, cuts: Sequence[int] = (),
                      every: int = 12) -> list[int]:
    """Frames to annotate: a regular cadence, plus the first frame after EVERY cut.

    The cut frames are the load-bearing half. SAM2 propagating across a cut
    follows whatever now occupies those coordinates -- on the UFC window that
    meant the tracker walking from one fighter to the other mid-shot. A cut is
    a new shot and needs a new click, whatever the cadence says.
    """
    if n_frames <= 0:
        return []
    frames = set(range(0, n_frames, max(every, 1)))
    frames.add(n_frames - 1)
    for c in cuts:
        if 0 <= c < n_frames:
            frames.add(c)
        if 0 <= c + 1 < n_frames:
            frames.add(c + 1)
    return sorted(frames)


def is_garment_not_person(mask: np.ndarray, foreground: np.ndarray) -> bool:
    """True when the matte covers so much of the foreground it cannot be one body.

    Measured: a healthy per-instance matte runs 12-55% of the CROP. Against the
    FOREGROUND, a correct body is well under 60%; above it the tracker has a
    colour field -- trunks plus canvas plus the other fighter -- rather than a
    person.
    """
    fg = float((foreground > 0.5).sum())
    if fg <= 0:
        return False
    return float((mask > 0.5).sum()) / fg >= GARMENT_NOT_PERSON_FRAC


def warp_to_crop(mask: np.ndarray, rect: tuple[float, float, float, float],
                 out_w: int = 1080, out_h: int = 1920,
                 resize_fn: Callable | None = None) -> np.ndarray:
    """Warp a NATIVE-resolution mask into the tight crop the reel renders.

    The mask is computed on the wide frame (SAM2 needs the context) but every
    effect reads it in crop space. Getting the space wrong is silent: an earlier
    pass applied a crop-space warp to a native mask and got 1.4% area where 28%
    was correct, and nothing raised.
    """
    x0, y0, cw, ch = (int(round(v)) for v in rect)
    h, w = mask.shape[:2]
    x0, y0 = max(0, min(x0, w - 1)), max(0, min(y0, h - 1))
    x1, y1 = min(w, x0 + max(cw, 1)), min(h, y0 + max(ch, 1))
    sub = mask[y0:y1, x0:x1]
    if sub.size == 0:
        return np.zeros((out_h, out_w), mask.dtype)
    if resize_fn is not None:
        return resize_fn(sub, (out_w, out_h))
    ys = (np.linspace(0, sub.shape[0] - 1, out_h)).astype(np.int32)
    xs = (np.linspace(0, sub.shape[1] - 1, out_w)).astype(np.int32)
    return sub[np.ix_(ys, xs)]


def summarise(masks: dict[int, np.ndarray]) -> MatteReport:
    """Report the numbers the gate table asks for, over crop-space masks."""
    rep = MatteReport(frames=len(masks))
    if not masks:
        return rep
    areas = []
    for i, m in sorted(masks.items()):
        a = float((m > 0.5).mean())
        areas.append(a)
        if a < 0.005:
            rep.empty_frames.append(i)
    rep.area_min, rep.area_max = min(areas), max(areas)
    rep.area_mean = float(np.mean(areas))
    return rep


def build_mattes(
    n_frames: int,
    *,
    cuts: Sequence[int] = (),
    foreground_fn: Callable[[int], np.ndarray],
    silhouette_fn: Callable[[int], dict[float, np.ndarray]],
    propagate_fn: Callable[[dict[int, np.ndarray]], dict[int, np.ndarray]],
    subject_hue: float,
    hue_tol: float = 45.0,
    every: int = 12,
) -> tuple[dict[int, np.ndarray], MatteReport]:
    """Seed SAM2 with whole-body masks on the annotation frames, then propagate.

    ``silhouette_fn`` returns ``{hue: mask}`` for one frame -- it is the image
    predictor with a negative click on the other body, taking the LARGEST mask
    rather than the highest-scoring one. Both matter and both were learned the
    hard way; see ``silhouette_subject``.
    """
    anns = annotation_frames(n_frames, cuts, every)
    seeds: dict[int, np.ndarray] = {}
    rep = MatteReport()
    for f in anns:
        sils = silhouette_fn(f)
        if not sils:
            continue
        mine = [m for h, m in sils.items()
                if abs((h - subject_hue + 180) % 360 - 180) <= hue_tol]
        if not mine:
            continue
        mask = max(mine, key=lambda m: float((m > 0.5).sum()))
        fg = foreground_fn(f)
        if is_garment_not_person(mask, fg):
            rep.rejected_garment.append(f)
            logger.warning("[matte] frame %d: seed covers >=%.0f%% of the "
                           "foreground — a garment, not a body; not seeding",
                           f, GARMENT_NOT_PERSON_FRAC * 100)
            continue
        seeds[f] = mask > 0.5
        rep.negatives += int(len(sils) > 1)
    rep.annotations = len(seeds)
    if not seeds:
        logger.warning("[matte] no usable seed on any of %d annotation frames", len(anns))
        return {}, rep
    masks = propagate_fn(seeds)
    out = summarise(masks)
    out.annotations, out.negatives = rep.annotations, rep.negatives
    out.rejected_garment = rep.rejected_garment
    return masks, out
