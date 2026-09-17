"""§1 — score an ACTION candidate's framing BEFORE the template touches it.

Four packets of work went into rescuing a wide hard-cam clip that was
structurally the wrong input for the ACTION template. The measurement that would
have rejected it is twelve frames and one matte pass -- about two minutes. This
makes it an ingest step instead of a post-mortem.

The number that separates a usable clip from an unusable one is the TORSO-BOX
HEIGHT at native crop. Subject height, subject area and head height all failed to
separate them (see the shot-size work); torso box at NATIVE crop does, because it
asks "how close is the camera", not "how much of the frame does a person occupy".
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

TORSO_FRAC = 0.58
MAG_FILL, MAG_HI = 0.58, 3.0

# Target canvas for every ACTION reel (CLAUDE.md: 1080x1920, 9:16).
TARGET_W, TARGET_H = 1080, 1920

# The widest magnification that still FILLS the canvas from a 1920x1080 source.
# This has now bitten three times (clamp lower bound, score normalisation, and
# the UFC run reporting "1.30x needed" for a crop that would letterbox), so it
# is named rather than spelled 1.3 or 1.7778 at each site.
FULL_BLEED_FLOOR_1080P = TARGET_H / 1080.0  # 1.7778


def full_bleed_floor(src_h: int) -> float:
    """Widest magnification that still fills 1080x1920 from a ``src_h``-tall source.

    The tightest full-bleed crop takes the FULL source height and
    ``src_h * 9/16`` of its width; scaling that to 1080 wide is ``1920 / src_h``.
    Anything wider letterboxes, so no ACTION shot can ever run below this.

    It is a FUNCTION, not the constant, because the floor moves with the source:
    1080p gives 1.778x but a 720p source needs 2.667x just to fill the frame --
    which is itself the signal that a 720p source is a poor ACTION input. A
    hardcoded 1.7778 would silently permit an unreachable magnification there,
    the same defect this constant exists to prevent.
    """
    return TARGET_H / float(max(src_h, 1))


# Floors proposed from measured clips:
#   reference VFX edit  torso 0.465 @ 1.31x     UFC official  0.474 @ 1.30x
#   WWE hard-cam        torso 0.172 @ 1.78x  (18/380 frames pinned at the cap)
TORSO_FLOOR = 0.30
MAG_CEILING = 1.8


@dataclass(frozen=True)
class SourceScore:
    torso_box_frac: float
    subject_area_frac: float
    mag_needed: float
    frames_measured: int
    frames_at_cap: int
    # Defaults to the 1080p floor so existing 5-arg construction keeps working.
    mag_floor: float = FULL_BLEED_FLOOR_1080P

    @property
    def action_source_score(self) -> float:
        """0..1. Higher is a better ACTION input. Torso box dominates; the
        magnification the template would need is the penalty term."""
        t = float(np.clip(self.torso_box_frac / 0.47, 0.0, 1.0))
        # Normalise over [mag_floor, MAG_HI] -- the range that is actually
        # reachable. Normalising over [1.3, 3.0] while clamping at 1.778 capped
        # the magnification term at 0.719, so a perfectly-framed clip could not
        # score 1.0 on it.
        span = max(MAG_HI - self.mag_floor, 1e-6)
        m = float(np.clip((MAG_HI - self.mag_needed) / span, 0.0, 1.0))
        return round(0.75 * t + 0.25 * m, 4)

    @property
    def action_eligible(self) -> bool:
        return self.torso_box_frac >= TORSO_FLOOR and self.mag_needed <= MAG_CEILING

    @property
    def verdict(self) -> str:
        return "ACTION" if self.action_eligible else "TALK/STILL — not ACTION"


def sample_frames(path: str, n: int = 12, skip_s: float = 30.0) -> list:
    """n frames spread across the clip, avoiding titles at either end."""
    dur = float(
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                path,
            ],
            capture_output=True,
            text=True,
        ).stdout.strip()
        or 0.0
    )
    lo, hi = (skip_s, dur - skip_s) if dur > 3 * skip_s else (0.0, max(dur, 1.0))
    return [lo + (hi - lo) * i / max(n - 1, 1) for i in range(n)]


def score(
    path: str,
    matte_fn: Callable[[np.ndarray], np.ndarray],
    frame_fn: Callable[[str, float], np.ndarray],
    times: Sequence[float] | None = None,
) -> SourceScore:
    """`matte_fn` is a saliency matte (birefnet); `frame_fn` grabs one frame."""
    times = list(times if times is not None else sample_frames(path))
    torsos, areas, mags, cap = [], [], [], 0
    floor = FULL_BLEED_FLOOR_1080P
    for t in times:
        rgb = frame_fn(path, t)
        m = matte_fn(rgb) > 0.5
        if m.sum() < 400:
            continue
        floor = full_bleed_floor(m.shape[0])
        ys, xs = np.nonzero(m)
        bh = ys.max() - ys.min() + 1
        ty1 = ys.min() + int(bh * TORSO_FRAC)
        sel = ys <= ty1
        tw = float(xs[sel].max() - xs[sel].min() + 1)
        torsos.append(int(bh * TORSO_FRAC) / m.shape[0])
        areas.append(float(m.mean()))
        mg = float(np.clip(1080.0 * MAG_FILL / max(tw, 1.0), floor, MAG_HI))
        mags.append(mg)
        cap += 1 if mg >= MAG_HI - 1e-6 else 0
    if not torsos:
        return SourceScore(0.0, 0.0, MAG_HI, len(times), len(times), floor)
    return SourceScore(
        torso_box_frac=round(float(np.mean(torsos)), 4),
        subject_area_frac=round(float(np.mean(areas)), 4),
        mag_needed=round(float(np.mean(mags)), 3),
        frames_measured=len(torsos),
        frames_at_cap=cap,
        mag_floor=floor,
    )
