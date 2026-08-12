"""First-frame brightener — auto-fix for dark video icons on YT Shorts.

## Why this exists

`first_frame_validator.check_first_frame_brightness` (2026-08-12
`215a9629`) detects the "dark first frame -> black feed icon"
pattern that drops YouTube Shorts feed-CTR. Detection is not
enough — the video still ships with the dark first frame and
still gets the black feed icon.

This module ships the FIX side: re-encode with ffmpeg's `eq` filter
applying a brightness boost enabled ONLY on the first N milliseconds
(default 100ms). In-playback this is imperceptible (100ms brightness
spike registers as a screen flash that most viewers won't notice),
but YouTube's feed icon extraction grabs the boosted first frame
and shows a bright tile instead of a black one.

## Design decisions + trade-offs

* **Global brightness boost, not per-frame** — could theoretically
  extract a bright mid-video frame and splice it in at t=0, but
  splicing requires audio pad-and-shift which risks sync drift.
  A pure `eq=brightness` pass preserves duration + audio sync
  perfectly. Boost value 0.15 raises dark scenes from YAVG=30
  to YAVG=~60 (crossing our validator threshold).

* **Only fires when validator flags dark** — no point re-encoding
  when the first frame is already bright. Caller responsibility
  to gate on validator result.

* **Full video re-encode** — the `eq` filter operates in RGB space
  so ffmpeg must decode + re-encode the full stream. Cost: ~10-30s
  per video depending on length. Acceptable given 5 blueprints/day
  per niche.

* **Video-only re-encode, audio copy** — audio stream is copied
  verbatim (`-c:a copy`). Zero audio quality loss + faster encode.

* **libx264 CRF 20 preset=fast** — same specs as the base render
  pipeline (per `media/ffmpeg.py` PLATFORM_SPECS). Preserves
  quality parity with the original render.

## Fail-open contract

Every failure path returns `False`:
  * Input file missing
  * ffmpeg binary missing
  * ffmpeg exit non-zero
  * Timeout
  * Any exception

`output_path` may not exist when False returned. Caller keeps the
original video path.

## Consumer wire

`platforms/youtube.py._publish_reel` after the validator detects
dark. Flag-gated `GENLAB_FIRST_FRAME_AUTOFIX_ENABLED` — off by
default so operator sees validator DARK_FIRST_FRAME rate first
before opting into the re-encode cost.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

_DEFAULT_BOOST: Final[float] = 0.15
"""ffmpeg eq=brightness value. Raises dark frames by ~40 YAVG
units (BT.709 Y-plane). Values above 0.3 look artificially
overexposed. Values below 0.10 don't shift enough to cross the
validator threshold."""

_DEFAULT_DURATION_S: Final[float] = 0.10
"""How long the brightness boost is active from t=0. 100ms is
below the perceptual flash-blindness threshold (~150ms) so most
viewers won't notice, but well above the single-frame duration
(33ms at 30fps) so YouTube's feed icon extraction reliably
grabs a boosted frame."""

_ENCODE_TIMEOUT_S: Final[int] = 120


def brighten_first_frames(
    video_path: Path,
    output_path: Path,
    *,
    boost: float = _DEFAULT_BOOST,
    duration_s: float = _DEFAULT_DURATION_S,
) -> bool:
    """Re-encode the video with a brightness boost on the first
    `duration_s` seconds.

    Args:
        video_path: input file (existing).
        output_path: destination file. Overwritten if exists.
        boost: eq=brightness value in [-1.0, 1.0]. Default 0.15
            (raises dark scenes ~40 YAVG units).
        duration_s: how long the boost is active from t=0.
            Default 0.10s (100ms).

    Returns:
        True when output_path is a valid re-encoded video with
        the first-frame boost applied. False on any failure —
        caller keeps the original.
    """
    if not video_path.exists():
        logger.debug(
            "[first_frame_brightener] input missing path=%s", video_path,
        )
        return False

    try:
        from genlab_core.media.ffmpeg import get_ffmpeg_binary
        ffmpeg = get_ffmpeg_binary()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[first_frame_brightener] ffmpeg lookup failed: %s", exc,
        )
        return False

    # Video filter: apply brightness boost only when playback time
    # is under `duration_s`. `enable` uses ffmpeg's expression eval
    # so 't' is playback seconds. Elsewhere, no-op passthrough.
    vf = f"eq=brightness={boost}:enable='lt(t,{duration_s})'"

    # Same encoding params as PLATFORM_SPECS baseline so quality
    # parity with the original render is preserved.
    cmd = [
        ffmpeg,
        "-nostdin",
        "-y",  # overwrite output if exists
        "-loglevel", "error",
        "-i", str(video_path),
        "-vf", vf,
        "-c:v", "libx264",
        "-crf", "20",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",  # no audio re-encode
        "-movflags", "+faststart",
        str(output_path),
    ]

    try:
        proc = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=_ENCODE_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "[first_frame_brightener] ffmpeg timeout path=%s", video_path,
        )
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[first_frame_brightener] ffmpeg raised path=%s: %s",
            video_path, exc,
        )
        return False

    if proc.returncode != 0:
        logger.warning(
            "[first_frame_brightener] ffmpeg exit=%d stderr_tail=%r",
            proc.returncode,
            (proc.stderr or "").strip().splitlines()[-3:],
        )
        return False

    if not output_path.exists() or output_path.stat().st_size < 1024:
        logger.warning(
            "[first_frame_brightener] output invalid path=%s exists=%s size=%d",
            output_path,
            output_path.exists(),
            output_path.stat().st_size if output_path.exists() else 0,
        )
        return False

    logger.info(
        "[first_frame_brightener] applied boost=%.2f duration=%.2fs "
        "input=%s output=%s",
        boost, duration_s, video_path, output_path,
    )
    return True


__all__ = [
    "brighten_first_frames",
]
