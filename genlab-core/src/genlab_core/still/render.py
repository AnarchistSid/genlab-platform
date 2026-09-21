"""Turn a still into a moving shot, and shots into a reel.

Why this does not call ``media/pan_zoom.py``: that module transforms a VIDEO
(``PanZoomSpec.source_video_path``) and knows three patterns —
``ken_burns_slow``, ``punch_in``, ``dolly``. This path starts from an IMAGE
and needs directional pans that alternate, so the vocabularies do not
overlap. Worse, ``build_pan_zoom_filter`` FAILS OPEN on an unknown pattern:
passing "zoom_in" would have returned an empty filter and produced a
motionless still with nothing but a warning. ``assert_patterns_supported``
below makes that mismatch loud instead.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

W, H, FPS = 1080, 1920, 30


class UnknownPattern(ValueError):
    """A kit named a motion pattern this renderer does not implement."""


def _zoompan(expr_z: str, expr_x: str, expr_y: str, frames: int) -> str:
    """One output frame per input frame — ``d=1``, motion driven by ``n``.

    NOT ``d=frames``. The input is `-loop 1 -t <dur>`, so ffmpeg feeds
    dur x fps frames, and ``d=N`` expands EVERY ONE of them into N output
    frames: the two multiply. A 2.44 s shot rendered as 148.43 s before this.
    With ``d=1`` the output length is exactly the input length, and ``n`` (the
    input frame index) carries the motion.
    """
    return (
        f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,"
        f"crop={W * 2}:{H * 2},"
        f"zoompan=z='{expr_z}':x='{expr_x}':y='{expr_y}':d=1:s={W}x{H}:fps={FPS}"
    )


def build_motion_filter(pattern: str, duration_s: float, zoom_max: float) -> str:
    """The ``-vf`` chain for one still, for the whole shot duration."""
    frames = max(1, int(round(duration_s * FPS)))
    step = (zoom_max - 1.0) / frames
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if pattern == "zoom_in":
        return _zoompan(f"1.0+{step:.6f}*on", cx, cy, frames)
    if pattern == "zoom_out":
        return _zoompan(f"{zoom_max}-{step:.6f}*on", cx, cy, frames)
    if pattern == "pan_right":
        return _zoompan(f"{zoom_max}", f"(iw-iw/zoom)*on/{frames}", cy, frames)
    if pattern == "pan_left":
        return _zoompan(f"{zoom_max}", f"(iw-iw/zoom)*(1-on/{frames})", cy, frames)
    raise UnknownPattern(
        f"no motion filter for pattern {pattern!r}. Implemented: zoom_in, "
        "zoom_out, pan_right, pan_left. pan_zoom.py fails OPEN on an unknown "
        "pattern and would have rendered this still motionless."
    )


def assert_patterns_supported(kit: dict[str, Any]) -> None:
    """Every pattern the kit names must exist here. Loud, at load time."""
    for pat in kit["motion"]["ken_burns"]["patterns"]:
        build_motion_filter(pat, 3.0, kit["motion"]["ken_burns"]["zoom_max"])


def _grain_vignette(kit: dict[str, Any]) -> str:
    g = kit["motion"]["grain"]["opacity"]
    v = kit["motion"]["vignette"]["strength"]
    return f"noise=alls={max(1, int(g * 100))}:allf=t+u,vignette=PI/4*{v:.3f}"


@dataclass(frozen=True)
class Shot:
    index: int
    image: Path
    output: Path
    pattern: str
    duration_s: float


def render_shot(shot: Shot, kit: dict[str, Any], *, timeout_s: int = 180) -> bool:
    """One still -> one moving clip of exactly ``duration_s``."""
    zoom_max = kit["motion"]["ken_burns"]["zoom_max"]
    vf = f"{build_motion_filter(shot.pattern, shot.duration_s, zoom_max)},{_grain_vignette(kit)}"
    shot.output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-loop",
        "1",
        "-framerate",
        str(FPS),
        "-t",
        f"{shot.duration_s:.3f}",
        "-i",
        str(shot.image),
        "-vf",
        vf,
        "-r",
        str(FPS),
        "-c:v",
        "libx264",
        "-crf",
        "20",
        "-preset",
        "fast",
        "-pix_fmt",
        "yuv420p",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        str(shot.output),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    if proc.returncode != 0 or not shot.output.exists():
        logger.warning("[still] shot %d failed: %s", shot.index, proc.stderr[-300:])
        return False
    return True


def probe_duration(path: Path) -> float:
    return float(
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
