"""First-frame brightness validator for the YouTube Shorts feed icon.

## Why this exists

YouTube Shorts displays the video's FIRST FRAME as the feed icon
before playback begins. When the first frame is dark/black (typical
of a fade-in composite or a hook overlay on black background), the
Shorts feed shows a mostly-black tile — which drops thumbnail-CTR
substantially (compares poorly against neighboring tiles that
show a bright, hook-visible frame).

This module extracts the first frame's average luminance via
ffmpeg's `signalstats` filter and returns a quality signal so the
pipeline can log-warn on dark first frames. Zero rejection today
(observability only) — operator sees the pattern first, then a
follow-up commit can wire a compositor fix that inserts a bright
poster-frame at t=0.

## Not doing today

  * Compositor fix (inserting a bright poster-frame) — separate ship
    once we know the dark-first-frame rate
  * Content-quality signal (is the first frame just "bright" or is
    it actually a compelling image?) — Vision-LLM territory, out
    of scope
  * Per-niche calibration — anime tends to open dark by design,
    might need a different threshold. Ship a single threshold first,
    measure per-niche rate, calibrate in follow-up.

## Fail-open

Every failure path returns `FirstFrameQuality(passed=True, ...)`:
  * ffmpeg not found
  * ffmpeg exit non-zero
  * Cannot parse YAVG from stderr
  * Any exception

Callers can trust that a False `passed` means we ACTUALLY measured
a dark first frame, not "the validator broke".

## Y-plane average (YAVG) interpretation

YAVG comes from BT.709 Y-plane in [16, 235]:
  * < 40   — essentially black (curtains, fade-in)
  * 40-70  — dark scene (night, dungeon, dim room)
  * 70-140 — normal
  * 140-200 — bright
  * > 200  — near-white (snow, overexposed)

Default threshold `_DARK_YAVG_THRESHOLD = 60` catches "video icon
will look like a black square in the Shorts feed" without false-
positiving on genuinely dim scenes that are still visible.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

_DARK_YAVG_THRESHOLD: Final[float] = 60.0
"""YAVG below this triggers a WARN. Chosen empirically:
  * Solid #000 -> YAVG ≈ 16 (min BT.709)
  * Dim room, subject barely visible -> YAVG ≈ 40-55
  * Normal indoor scene -> YAVG ≈ 80-120
60 sits above 'essentially unreadable' and below 'visible enough
to work as a feed icon'."""

_YAVG_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"lavfi\.signalstats\.YAVG=([0-9.]+)"
)


@dataclass(frozen=True)
class FirstFrameQuality:
    """First-frame validator result.

    Attributes
    ----------
    passed : bool
        True when the first frame is bright enough to work as a
        Shorts feed icon (YAVG >= threshold OR validator fail-open).
    yavg : float | None
        Measured Y-plane average, [16, 235] BT.709 range. None when
        the validator couldn't measure (ffmpeg failure, parse error).
    reason : str
        Short human-readable label. "ok" when passed; "dark" when
        below threshold; "measurement_failed:{detail}" when fail-open.
    """

    passed: bool
    yavg: float | None
    reason: str


def check_first_frame_brightness(video_path: Path) -> FirstFrameQuality:
    """Measure the first frame's brightness via ffmpeg signalstats.

    Args:
        video_path: absolute path to an existing MP4 (or any video
            format ffmpeg reads).

    Returns:
        FirstFrameQuality. Fail-open — validator errors return
        `passed=True` with `yavg=None` so callers never block on
        the validator itself failing.
    """
    if not video_path.exists():
        return FirstFrameQuality(
            passed=True,
            yavg=None,
            reason="measurement_failed:file_not_found",
        )

    try:
        from genlab_core.media.ffmpeg import get_ffmpeg_binary
        ffmpeg = get_ffmpeg_binary()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[first_frame_validator] ffmpeg lookup failed: %s", exc)
        return FirstFrameQuality(
            passed=True,
            yavg=None,
            reason="measurement_failed:ffmpeg_binary_missing",
        )

    # Extract first frame + run signalstats + print metadata to stderr.
    # `-vframes 1` limits to a single frame (near-instant).
    # `-f null -` discards the output (we only care about the metadata
    # printed via signalstats).
    cmd = [
        ffmpeg,
        "-nostdin",
        "-loglevel", "info",
        "-i", str(video_path),
        "-vframes", "1",
        "-vf", "signalstats,metadata=print",
        "-f", "null",
        "-",
    ]
    try:
        proc = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "[first_frame_validator] ffmpeg timeout path=%s", video_path,
        )
        return FirstFrameQuality(
            passed=True,
            yavg=None,
            reason="measurement_failed:timeout",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[first_frame_validator] ffmpeg raised path=%s: %s",
            video_path, exc,
        )
        return FirstFrameQuality(
            passed=True,
            yavg=None,
            reason=f"measurement_failed:{type(exc).__name__}",
        )

    if proc.returncode != 0:
        logger.debug(
            "[first_frame_validator] ffmpeg exit=%d path=%s",
            proc.returncode, video_path,
        )
        return FirstFrameQuality(
            passed=True,
            yavg=None,
            reason=f"measurement_failed:ffmpeg_exit_{proc.returncode}",
        )

    yavg = _parse_yavg(proc.stderr)
    if yavg is None:
        return FirstFrameQuality(
            passed=True,
            yavg=None,
            reason="measurement_failed:no_yavg_in_stderr",
        )

    passed = yavg >= _DARK_YAVG_THRESHOLD
    reason = "ok" if passed else "dark"
    return FirstFrameQuality(passed=passed, yavg=yavg, reason=reason)


def _parse_yavg(stderr: str) -> float | None:
    """Extract `lavfi.signalstats.YAVG=<float>` from ffmpeg stderr."""
    match = _YAVG_PATTERN.search(stderr or "")
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


def log_first_frame_signal(
    video_path: Path,
    *,
    niche_id: str,
    platform: str = "youtube",
) -> FirstFrameQuality:
    """Convenience wrapper that logs a WARN when the first frame is
    dark. Returns the quality result so callers can also act on it
    (though today no caller does — pure observability).

    Emits:
        [first_frame_validator] DARK_FIRST_FRAME niche=X platform=Y
        yavg=42.1 threshold=60 path=/tmp/...

    Operator grep after deploy:
        journalctl -u genlab-* --since '2h ago' | grep DARK_FIRST_FRAME
    """
    result = check_first_frame_brightness(video_path)
    if not result.passed and result.yavg is not None:
        logger.warning(
            "[first_frame_validator] DARK_FIRST_FRAME niche=%s platform=%s "
            "yavg=%.1f threshold=%.0f path=%s",
            niche_id,
            platform,
            result.yavg,
            _DARK_YAVG_THRESHOLD,
            video_path,
        )
    return result


__all__ = [
    "FirstFrameQuality",
    "check_first_frame_brightness",
    "log_first_frame_signal",
]
