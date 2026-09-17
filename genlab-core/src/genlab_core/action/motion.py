"""The canonical motion metric. One implementation, one unit.

WHY THIS FILE EXISTS
--------------------
`classifier.measure()` takes `motion_fn` injected and, until now, nothing
supplied it. So `ACTION_MOTION_MIN = 6.0` was a number without a unit: an
ffmpeg scene-score reading of real UFC footage lands at 0.20-0.31, two orders
away, which says nothing about the clip and everything about measuring a
different quantity. A threshold whose unit is undefined cannot be calibrated,
and a corpus labelled against it would bake the confusion into fixtures.

THE UNIT
--------
**Mean absolute luma difference between consecutive frames, in 8-bit levels
(0-255), sampled at quarter scale, normalised to a per-second rate at 30 fps.**

Each part earns its place:

* *Luma, not RGB* -- a colour cast moving across frame is not motion.
* *Absolute difference* -- direction is irrelevant; a limb entering and leaving
  should both count.
* *Consecutive frames* -- the alternative, difference against a running mean,
  scores a locked-off shot of a waving flag the same as a whip pan.
* *Quarter scale* -- an 11x cost saving that also suppresses sensor noise and
  compression mosquito noise, which at full scale put a static shot at ~1.5
  levels of pure measurement floor.
* *Per second at 30 fps* -- the same physical movement measured on 60 fps
  footage produces half the per-FRAME difference, because the frames are half
  as far apart in time. Without this a 60 fps clip reads as half as energetic
  as the identical 30 fps clip. This is the normalisation the metric most needs
  and the one easiest to leave out.

The value is a RATE, so the scale of the number is "levels of change per second
of footage", and it is comparable across clips of different length and fps.
"""

from __future__ import annotations

import logging
import subprocess

import numpy as np

logger = logging.getLogger(__name__)

SCALE_DIVISOR = 4  # quarter scale
REFERENCE_FPS = 30.0  # the rate the unit is normalised to
MAX_SAMPLE_SECONDS = 20.0  # a long clip is characterised, not exhaustively read


def _probe(path: str) -> tuple[int, int, float]:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,avg_frame_rate",
            "-of",
            "csv=p=0",
            path,
        ],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not out:
        raise ValueError(f"ffprobe returned nothing for {path}")
    w, h, rate = out.split(",")[:3]
    num, _, den = rate.partition("/")
    fps = float(num) / float(den or 1) if den else float(num)
    return int(w), int(h), (fps or REFERENCE_FPS)


def motion_energy(path: str, start_s: float = 0.0, duration_s: float | None = None) -> float:
    """Levels of frame-to-frame luma change per second of footage.

    Returns 0.0 for an unreadable or single-frame clip -- a clip that cannot be
    measured is not a clip with no motion, but STILL is the safe verdict and the
    caller sees the reason in the log.
    """
    try:
        w, h, fps = _probe(path)
    except Exception:
        logger.warning("motion_energy: cannot probe %s", path, exc_info=True)
        return 0.0

    sw, sh = max(w // SCALE_DIVISOR, 16), max(h // SCALE_DIVISOR, 16)
    dur = min(duration_s if duration_s is not None else MAX_SAMPLE_SECONDS, MAX_SAMPLE_SECONDS)
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-v", "error"]
    if start_s:
        cmd += ["-ss", f"{start_s:.3f}"]
    cmd += [
        "-i",
        path,
        "-t",
        f"{dur:.3f}",
        "-vf",
        f"scale={sw}:{sh}",
        "-pix_fmt",
        "gray",
        "-f",
        "rawvideo",
        "-",
    ]
    raw = subprocess.run(cmd, capture_output=True).stdout
    n = len(raw) // (sw * sh)
    if n < 2:
        logger.warning("motion_energy: %s yielded %d frame(s)", path, n)
        return 0.0

    frames = np.frombuffer(raw[: n * sw * sh], np.uint8).reshape(n, sh, sw).astype(np.float32)
    per_frame = float(np.abs(np.diff(frames, axis=0)).mean())
    # Per SECOND at the reference rate: the same movement on 60 fps footage
    # yields half the per-frame difference, because the frames are half as far
    # apart in time.
    return per_frame * (fps / REFERENCE_FPS)


def motion_fn(path: str) -> float:
    """The injectable the classifier expects."""
    return motion_energy(path)
