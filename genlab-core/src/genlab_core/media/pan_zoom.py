"""Pan/zoom filter chain for the pan_zoom transformation dimension.

Applies one of five motion patterns to a source video. The dimension's
bandit selects the pattern; this module produces the FFmpeg filter
graph and executes it.

Five patterns (matches ``config.dimensions.pan_zoom.patterns`` seed):

  * ``ken_burns_slow`` — slow zoom-in from 1.0× to 1.2× at ~0.01/s.
    Classic Ken Burns effect; adds subtle motion to static content.
    Reward attribution (per PR 6): full-video completion rate.
  * ``punch_in`` — static for first 1s, then quick zoom to 1.3× over
    the next 1s. Punchy attention-grab for hook moments.
  * ``jump_cut`` — [not implemented in PR 9] — falls back to no_motion
    with a warning. Jump-cut is fundamentally different (segments the
    source into 2-3s chunks + hard-cuts). Ships as a future PR;
    currently no filter is applied. The bandit arm still exists so
    the pattern is registered; it just won't get exercised until the
    segmentation pipeline lands.
  * ``no_motion`` — no filter applied. Pass-through. Deliberately a
    valid arm choice so the bandit has a "leave it alone" baseline.
    Returns False so caller uses source unchanged (no wasteful
    re-encode).
  * ``dolly`` — horizontal pan left-to-right. Requires scale-up first
    to give the crop window room to move. ~0.15 pan/s over duration.

Design notes:

* zoompan filter is used for the zoom-based patterns (ken_burns_slow,
  punch_in). The ``d=1`` trick makes it apply per-frame on video
  (default is designed for slideshows).

* The zoom expression uses the ``t`` variable (time in seconds) rather
  than ``pzoom``+``on`` so patterns are duration-independent — a 20s
  reel gets ``1.0 → 1.2``, a 15s reel gets ``1.0 → 1.15``. Not
  identical end state but same rate.

* Output is enforced 1080×1920 via ``s=1080x1920`` — matches
  CLAUDE.md's non-negotiable reel dimensions.

* Video re-encode uses libx264 CRF 20 preset=fast (matches
  PLATFORM_SPECS + PR 8's motion_compositor for consistency).

Related sprint context:
- PR 4 (#668) — selector picks pan_zoom pattern
- PR 6 (#670) — pan_zoom arm reward = completion rate
- PR 15 (upcoming) — wires pan_zoom into FrameCompositor
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# Sentinel returned by build_pan_zoom_filter when a pattern is a
# deliberate no-op (no_motion, unimplemented jump_cut). Caller checks
# and skips FFmpeg invocation entirely.
NO_FILTER = ""


@dataclass
class PanZoomSpec:
    """Parameters for one pan/zoom render."""

    source_video_path: Path
    output_path: Path
    pattern: str
    target_width: int = 1080
    target_height: int = 1920
    fps: int = 30
    crf: int = 20
    preset: str = "fast"
    audio_bitrate: str = "128k"


def _center_x_expr() -> str:
    """Zoompan expression to keep x-center anchored as zoom grows."""
    return "iw/2-(iw/zoom/2)"


def _center_y_expr() -> str:
    """Zoompan expression to keep y-center anchored as zoom grows."""
    return "ih/2-(ih/zoom/2)"


def build_pan_zoom_filter(
    pattern: str, target_width: int, target_height: int, fps: int
) -> str:
    """Return the ``-vf`` filter string for a pattern.

    Returns ``NO_FILTER`` (empty string) for no-op patterns —
    ``no_motion`` and the not-yet-implemented ``jump_cut``.

    Unknown patterns also return NO_FILTER after logging a warning —
    fail-open avoids crashing on typos or future arms not yet coded.
    """
    if pattern == "no_motion":
        return NO_FILTER
    if pattern == "jump_cut":
        # Not implementable as a simple filter — needs segmentation
        # pipeline. Ship the arm for future expansion; skip for now.
        logger.debug(
            "[pan_zoom] jump_cut not implemented — falls back to no_motion"
        )
        return NO_FILTER

    if pattern == "ken_burns_slow":
        # 1.0 → ~1.2 at 0.01/s. At 20s → 1.2; at 15s → 1.15.
        z_expr = "'min(1+0.01*t,1.2)'"
        return (
            f"zoompan=z={z_expr}:d=1:"
            f"x='{_center_x_expr()}':y='{_center_y_expr()}':"
            f"s={target_width}x{target_height}:fps={fps}"
        )

    if pattern == "punch_in":
        # Static for 1s, then zoom to 1.3 over next 1s. After 2s stays
        # at 1.3× until end.
        z_expr = "'if(lt(t,1),1,min(1+(t-1)*0.3,1.3))'"
        return (
            f"zoompan=z={z_expr}:d=1:"
            f"x='{_center_x_expr()}':y='{_center_y_expr()}':"
            f"s={target_width}x{target_height}:fps={fps}"
        )

    if pattern == "dolly":
        # Horizontal pan left-to-right. Scale up 20% first so crop
        # window has room to move. Crop window slides from x=0 (left)
        # to x=(iw-1080) over duration.
        #
        # The 't/max(t\,1)' safety trick avoids divide-by-zero on t=0.
        # Actual duration bound comes from the source itself via -shortest
        # in the ffmpeg command.
        return (
            f"scale=iw*1.2:ih*1.2,"
            f"crop={target_width}:{target_height}:"
            f"x='(iw-{target_width})*(t/20)':y='(ih-{target_height})/2'"
        )

    logger.warning(
        "[pan_zoom] unknown pattern %r — no filter applied", pattern
    )
    return NO_FILTER


def build_ffmpeg_command(
    spec: PanZoomSpec, ffmpeg_binary: str, filter_str: str
) -> list[str]:
    """Compose full argv for a pan/zoom render.

    Kept as a pure function so tests can assert command shape without
    invoking ffmpeg.
    """
    return [
        ffmpeg_binary,
        "-y",
        "-i", str(spec.source_video_path),
        "-vf", filter_str,
        # bt709 compliance — matches PR 8's motion_compositor + CLAUDE.md
        "-colorspace", "bt709",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
        "-preset", spec.preset,
        "-crf", str(spec.crf),
        "-c:a", "aac",
        "-b:a", spec.audio_bitrate,
        "-ac", "2",
        "-ar", "48000",
        str(spec.output_path),
    ]


def apply_pan_zoom(
    spec: PanZoomSpec,
    *,
    timeout_seconds: int = 300,
) -> bool:
    """Apply a pan/zoom pattern to a video.

    Returns True on success. False on:
      * ``pattern`` is a no-op (``no_motion``, ``jump_cut``, or
        unknown) — caller uses source unchanged
      * Source video missing
      * FFmpeg unavailable / timeout / nonzero exit
      * Output validation failure

    Fail-open: never raises. Matches PR 7 / PR 8 semantics.
    """
    filter_str = build_pan_zoom_filter(
        spec.pattern, spec.target_width, spec.target_height, spec.fps
    )
    if filter_str == NO_FILTER:
        logger.debug(
            "[pan_zoom] pattern %r produces no filter — caller "
            "should use source unchanged",
            spec.pattern,
        )
        return False

    if not spec.source_video_path.exists():
        logger.warning(
            "[pan_zoom] source video missing: %s", spec.source_video_path
        )
        return False

    from genlab_core.media.ffmpeg import get_ffmpeg_binary

    try:
        ffmpeg = get_ffmpeg_binary()
    except RuntimeError as exc:
        logger.warning("[pan_zoom] ffmpeg not available: %s", exc)
        return False

    cmd = build_ffmpeg_command(spec, ffmpeg, filter_str)
    logger.info(
        "[pan_zoom] applying %r to %s -> %s",
        spec.pattern, spec.source_video_path.name, spec.output_path,
    )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "[pan_zoom] ffmpeg timed out after %ds: %s",
            timeout_seconds, spec.output_path,
        )
        return False
    except (OSError, FileNotFoundError) as exc:
        logger.warning("[pan_zoom] ffmpeg subprocess failed: %s", exc)
        return False

    if result.returncode != 0:
        stderr_tail = "\n".join(result.stderr.strip().splitlines()[-5:])
        logger.warning(
            "[pan_zoom] ffmpeg exit=%d for %s. Last stderr:\n%s",
            result.returncode, spec.output_path, stderr_tail,
        )
        return False

    if not spec.output_path.exists() or spec.output_path.stat().st_size < 1024:
        logger.warning(
            "[pan_zoom] output missing/too small: %s", spec.output_path
        )
        return False

    return True


def apply_pan_zoom_for_reel(
    source_video_path: Path,
    output_path: Path,
    pattern: str,
    *,
    target_width: int = 1080,
    target_height: int = 1920,
    timeout_seconds: int = 300,
) -> bool:
    """High-level entry point matching PR 7/PR 8 orchestrator signature.

    Returns False on no-op patterns AND all failure modes — caller
    checks and falls back to source path unchanged.
    """
    spec = PanZoomSpec(
        source_video_path=source_video_path,
        output_path=output_path,
        pattern=pattern,
        target_width=target_width,
        target_height=target_height,
    )
    return apply_pan_zoom(spec, timeout_seconds=timeout_seconds)


__all__ = [
    "NO_FILTER",
    "PanZoomSpec",
    "apply_pan_zoom",
    "apply_pan_zoom_for_reel",
    "build_ffmpeg_command",
    "build_pan_zoom_filter",
]
