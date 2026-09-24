"""Are the fighters in the frame? The question the fight lane never asked.

ANIME-PEAK-07 §1, §2, §6. The first five pilot reels cropped 16:9 footage to
9:16 full-bleed, which discards about two thirds of the width. A fight is
staged HORIZONTALLY — two subjects, apart — so a centre crop drops one of
them and, on a wide shot, both.

Presence is measured with YuNet rather than a matte: rembg and the isolated
worker venv are not on this machine, and a face box is enough to answer the
questions that matter here — is anyone in this frame, where are they, and is
the crop about to cut them out.

Anime faces are not photographic faces, so the confidence floor is low and
the result is treated as PRESENCE, not identity: two boxes mean two fighters
are visible somewhere, not which is which.

MEASURED 2026-09-24 — THE PRESENCE SIGNAL IS A DIAGNOSTIC, NOT A GATE.

Scored against 50 hand-labelled frames from the five pilot clips
(.audit/fight-marks/CALIBRATION-04.md), the best operating point of this
detector is 68% recall on frames a human calls "a character is clearly on
screen", at a 53% false-positive rate on debris and blast frames. An anime-
specific cascade scored worse (32%). Nothing here separates a character from
an explosion well enough to gate a window on it.

The failure is structural rather than a matter of tuning: this asks "is there
a face", and action animation is full of characters with no face in frame --
backs, hands, cropped heads, motion smear. Big Mom's face at full frame width
is missed; two hands filling the screen are missed.

`action_box` and `layout_for` remain useful where boxes DO exist. Do not build
a presence gate on `presence_fraction` or `effect_fraction`; `is_effect` in
particular fired on 0 of 19 labelled debris frames, because One Piece rubble
sits at mid luma, not blown out.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_MODEL = Path(__file__).resolve().parents[3] / "models" / "yunet.onnx"

#: Anime faces score far below photographic ones. Measured on these clips, a
#: 0.6 floor found almost nothing; 0.25 finds faces a human sees.
FACE_CONF = 0.25
#: A face box scaled to an approximate body box: anime figures run roughly
#: 5-7 heads tall, and the torso is what a crop must keep.
BODY_W_PER_FACE = 3.0
BODY_H_PER_FACE = 5.0
#: §2's bars.
MIN_PRESENCE_FRAC = 0.60
MAX_EFFECT_FRAC = 0.25
#: §1's magnification ceiling.
MAX_MAGNIFICATION = 1.8


@dataclass(frozen=True)
class Presence:
    t: float
    n_faces: int
    boxes: tuple[tuple[float, float, float, float], ...]   # x,y,w,h in source px
    luma: float
    is_effect: bool          # a flash / blown-out frame with nobody in it

    @property
    def has_fighter(self) -> bool:
        return self.n_faces >= 1


@lru_cache(maxsize=4)
def _detector(w: int, h: int):
    import cv2

    if not _MODEL.exists():
        raise RuntimeError(f"yunet model missing at {_MODEL}")
    return cv2.FaceDetectorYN.create(str(_MODEL), "", (w, h), FACE_CONF, 0.3, 5000)


def _frame(path: Path, t: float, w: int = 640) -> np.ndarray | None:
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
         "-vf", f"scale={w}:-2", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        capture_output=True, timeout=120)
    buf = r.stdout
    if not buf:
        return None
    h = len(buf) // (w * 3)
    if h <= 0:
        return None
    return np.frombuffer(buf[: w * h * 3], dtype=np.uint8).reshape(h, w, 3)


def presence_at(path: Path, t: float) -> Presence:
    img = _frame(path, t)
    if img is None:
        return Presence(t, 0, (), 0.0, False)
    h, w = img.shape[:2]
    det = _detector(w, h)
    det.setInputSize((w, h))
    _, faces = det.detect(img)
    boxes = tuple((float(f[0]), float(f[1]), float(f[2]), float(f[3]))
                  for f in (faces if faces is not None else []))
    luma = float(img.mean())
    # An "effect frame" is bright and empty: the flash, the shockwave, the
    # debris. These are the frames the old window scorer liked best.
    is_effect = (not boxes) and (luma > 170.0 or luma < 25.0)
    return Presence(t, len(boxes), boxes, luma, is_effect)


def scan(path: Path, *, start: float = 0.0, end: float | None = None,
         stride_s: float = 0.4) -> list[Presence]:
    from genlab_core.still.reference import duration_s

    end = end if end is not None else duration_s(path)
    out, t = [], start
    while t < end:
        out.append(presence_at(path, t))
        t += stride_s
    return out


def presence_fraction(scans: list[Presence]) -> float:
    return sum(1 for p in scans if p.has_fighter) / len(scans) if scans else 0.0


def effect_fraction(scans: list[Presence]) -> float:
    return sum(1 for p in scans if p.is_effect) / len(scans) if scans else 0.0


def action_box(scans: list[Presence], frame_w: int, frame_h: int,
               pad: float = 0.10) -> tuple[float, float, float, float] | None:
    """The union of every fighter box seen, padded. None when nobody appeared.

    §1: a fight has two subjects and a single-subject crop drops one. The box
    is the union, expanded from face to approximate body, so the crop is
    chosen to KEEP people rather than to centre the frame.
    """
    xs: list[float] = []
    ys: list[float] = []
    xe: list[float] = []
    ye: list[float] = []
    for p in scans:
        for (x, y, w, h) in p.boxes:
            cx, cy = x + w / 2, y + h / 2
            bw, bh = w * BODY_W_PER_FACE, h * BODY_H_PER_FACE
            xs.append(cx - bw / 2)
            xe.append(cx + bw / 2)
            ys.append(cy - bh * 0.25)      # head is near the top of the body
            ye.append(cy + bh * 0.75)
    if not xs:
        return None
    x0, x1 = max(0.0, min(xs)), min(float(frame_w), max(xe))
    y0, y1 = max(0.0, min(ys)), min(float(frame_h), max(ye))
    px, py = (x1 - x0) * pad, (y1 - y0) * pad
    return (max(0.0, x0 - px), max(0.0, y0 - py),
            min(float(frame_w), x1 + px), min(float(frame_h), y1 + py))


def layout_for(box: tuple[float, float, float, float] | None,
               frame_w: int, frame_h: int) -> tuple[str, float]:
    """('box'|'band', magnification). §1's decision, per shot.

    The band is not a fallback for failure — it is the correct layout when the
    action is wider than a 9:16 crop can hold without dropping a fighter.
    """
    if box is None:
        return "band", 1.0
    bw = box[2] - box[0]
    need = (frame_h * 9 / 16) / max(bw, 1.0)     # to fit box width into 9:16
    if need <= MAX_MAGNIFICATION and bw > 0:
        return "box", max(1.0, min(need, MAX_MAGNIFICATION))
    return "band", 1.0


# ── ANIME-PEAK-07 §2: windows score on characters, not effects ───────────


@dataclass(frozen=True)
class FighterWindow:
    start_s: float
    end_s: float
    presence: float
    effect: float
    impact_t: float | None
    reaction_t: float | None

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    @property
    def ok(self) -> bool:
        return (self.presence >= MIN_PRESENCE_FRAC and self.effect <= MAX_EFFECT_FRAC
                and self.reaction_t is not None)

    def row(self) -> str:
        r = f"{self.reaction_t:.1f}s" if self.reaction_t is not None else "none"
        return (f"  {self.start_s:6.2f}-{self.end_s:6.2f}s ({self.duration_s:4.1f}s)  "
                f"present {self.presence:4.0%}  effect {self.effect:4.0%}  "
                f"reaction {r}  {'OK' if self.ok else 'below bar'}")


def pick_window(path: Path, impact_t: float, *, scans: list[Presence] | None = None,
                min_s: float = 8.0, max_s: float = 14.0,
                windup_min: float = 2.0, windup_max: float = 4.0) -> FighterWindow:
    """A window built around the impact that actually CONTAINS the fighters.

    The marked impact is the CENTRE, not the whole. Measured on the five pilot
    reels, windows centred on the impact ran 11-61% fighter presence: an
    impact frame is a flash, and a window built symmetrically around one is
    mostly flash, shockwave and debris. The fix is to extend until a face
    comes back — the reaction shot is what makes the hit read.

    Search is over (wind-up, tail) pairs, scored on presence, with the
    constraint that a reaction face must appear after the impact.
    """
    from genlab_core.still.reference import duration_s

    total = duration_s(path)
    scans = scans or scan(path, stride_s=0.4)
    by_t = sorted(scans, key=lambda p: p.t)

    def window_stats(a: float, b: float):
        seg = [p for p in by_t if a <= p.t <= b]
        if not seg:
            return 0.0, 1.0, None
        after = [p for p in seg if p.t > impact_t and p.has_fighter]
        return (presence_fraction(seg), effect_fraction(seg),
                after[0].t if after else None)

    best: FighterWindow | None = None
    wu = windup_min
    while wu <= windup_max + 1e-6:
        a = max(0.0, impact_t - wu)
        tail = min_s - wu
        while a + wu + tail <= total and (wu + tail) <= max_s:
            b = min(total, impact_t + tail)
            pres, eff, react = window_stats(a, b)
            cand = FighterWindow(a, b, pres, eff, impact_t, react)
            # Prefer presence, then a reaction, then the shorter window.
            key = (round(pres, 3), react is not None, -(b - a))
            if best is None or key > (round(best.presence, 3), best.reaction_t is not None,
                                      -(best.end_s - best.start_s)):
                best = cand
            tail += 1.0
        wu += 1.0
    assert best is not None
    logger.info("[fighters] window for impact %.2fs: %s", impact_t, best.row())
    return best


def best_window_in_clip(path: Path, impacts: list[float], *,
                        stride_s: float = 0.4) -> FighterWindow:
    """The best presence-scoring window across every marked impact."""
    scans = scan(path, stride_s=stride_s)
    cands = [pick_window(path, t, scans=scans) for t in impacts]
    return max(cands, key=lambda w: (w.ok, w.presence))


def cover_frame(path: Path, window: FighterWindow, *,
                stride_s: float = 0.2) -> float | None:
    """§6 — the sharpest frame in the WIND-UP with a face and sane luma.

    Not the flash. Zoro's near-white cover and Luffy's red smear came from
    picking the brightest moment, which is the one with nobody in it.
    """
    from genlab_core.still.pv import frame_sharpness

    end = window.impact_t if window.impact_t is not None else window.end_s
    best: tuple[float, float] | None = None
    t = window.start_s
    while t < end:
        p = presence_at(path, t)
        if p.has_fighter and 60.0 <= p.luma <= 200.0:
            sh = frame_sharpness(path, t)
            if best is None or sh > best[0]:
                best = (sh, t)
        t += stride_s
    return best[1] if best else None
