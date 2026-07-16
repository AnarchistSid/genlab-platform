"""Highlight moment detection — consumer of the highlight_moment dimension.

Given a source video, picks a highlight window (start_s, end_s) using
one of four methods chosen by the bandit selector:

  * ``first_frame`` — always use the first ``window_s`` seconds
    (baseline for the bandit — no detection cost)
  * ``audio_peak`` — FFmpeg silencedetect to identify LOUD stretches;
    pick the middle of the longest non-silent stretch
  * ``motion_peak`` — FFmpeg scene detection to find high-motion
    transitions; pick the window centered on the biggest scene change
  * ``llm_pick`` — Claude vision on sampled frames (deferred; returns
    None so the arm falls back to first_frame at runtime)

Design decision: FFmpeg-only, no heavy audio/vision dependencies
The alternatives (librosa, opencv, open-scene-detection) each add
50-200 MB of packages that don't fit the 4 GB Hetzner VPS budget. The
FFmpeg-based detectors are less precise but exercise the same signal
axes — silence for audio, scene changes for motion.

Reward attribution (PR 6): highlight_moment → hold_avg_3s_15s.

Related sprint context:
- PR 4 (#668) — selector picks method
- PR 15 (upcoming) — wires HighlightDetector into pipeline
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HighlightWindow:
    """A (start_s, end_s) time range within the source video."""

    start_s: float
    end_s: float

    def duration(self) -> float:
        return max(0.0, self.end_s - self.start_s)

    def is_valid(self) -> bool:
        return self.duration() > 0.0


def use_first_frame(video_duration_s: float, window_s: float) -> HighlightWindow:
    """Trivial baseline — first ``window_s`` seconds.

    Clamped to video duration so a 4s clip with window_s=8 still
    returns a valid range.
    """
    end = min(window_s, video_duration_s)
    return HighlightWindow(start_s=0.0, end_s=end)


# --- Audio peak detection via silencedetect -----------------------------------
_SILENCE_START_RE = re.compile(r"silence_start:\s*([\d.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([\d.]+)")


def _parse_silence_windows(ffmpeg_stderr: str) -> list[tuple[float, float]]:
    """Parse silencedetect stderr → list of (silence_start, silence_end)."""
    starts = [float(m.group(1)) for m in _SILENCE_START_RE.finditer(ffmpeg_stderr)]
    ends = [float(m.group(1)) for m in _SILENCE_END_RE.finditer(ffmpeg_stderr)]
    # Zip pairs — a silence with no explicit end is dropped (stream ended
    # during silence).
    return list(zip(starts, ends, strict=False))


def _longest_non_silent(
    silence_windows: list[tuple[float, float]],
    video_duration_s: float,
) -> tuple[float, float]:
    """Compute the longest continuous non-silent stretch.

    Silence windows are (start, end). Non-silent stretches are the
    complements: (0, first_silence_start), (silence_end, next_silence_start),
    ..., (last_silence_end, video_duration).

    Returns the (start, end) of the longest such stretch.
    """
    if not silence_windows:
        # Entire video is non-silent
        return (0.0, video_duration_s)

    # Build non-silent stretches from silence gaps
    non_silent: list[tuple[float, float]] = []
    prev_end = 0.0
    for s_start, s_end in silence_windows:
        if s_start > prev_end:
            non_silent.append((prev_end, s_start))
        prev_end = s_end
    if prev_end < video_duration_s:
        non_silent.append((prev_end, video_duration_s))

    if not non_silent:
        # All-silent video — return first window as trivial fallback
        return (0.0, video_duration_s)

    # Longest by span
    return max(non_silent, key=lambda r: r[1] - r[0])


def detect_audio_peak(
    source_video_path: Path,
    video_duration_s: float,
    window_s: float,
    *,
    silence_threshold_db: int = -30,
    min_silence_s: float = 0.5,
    timeout_seconds: int = 60,
) -> HighlightWindow | None:
    """Detect audio-peak highlight via silencedetect.

    Runs ``ffmpeg -af silencedetect=noise=-30dB:d=0.5 -f null`` to
    get silence boundaries, then picks the middle of the longest
    non-silent stretch as highlight center. Window centered on that.

    Returns None on: source missing, ffmpeg unavailable, timeout,
    parse failure. Caller falls back to first_frame.
    """
    if not source_video_path.exists():
        return None

    from genlab_core.media.ffmpeg import get_ffmpeg_binary

    try:
        ffmpeg = get_ffmpeg_binary()
    except RuntimeError:
        return None

    cmd = [
        ffmpeg,
        "-i",
        str(source_video_path),
        "-af",
        f"silencedetect=noise={silence_threshold_db}dB:d={min_silence_s}",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds)
    except (subprocess.TimeoutExpired, OSError):
        return None

    # silencedetect writes to stderr regardless of returncode
    silence_windows = _parse_silence_windows(result.stderr)
    non_silent_start, non_silent_end = _longest_non_silent(silence_windows, video_duration_s)

    # Center highlight window in the longest non-silent stretch
    center = (non_silent_start + non_silent_end) / 2
    half = window_s / 2
    start = max(0.0, center - half)
    end = min(video_duration_s, start + window_s)
    # Re-anchor start if end got clamped
    start = max(0.0, end - window_s)
    return HighlightWindow(start_s=start, end_s=end)


# --- Motion peak detection via scenedetect ------------------------------------
_SHOWINFO_TIME_RE = re.compile(r"pts_time:\s*([\d.]+).+?scene:\s*([\d.]+)", re.DOTALL)


def _parse_scene_changes(ffmpeg_stderr: str) -> list[tuple[float, float]]:
    """Parse showinfo stderr → list of (timestamp, scene_score).

    ``select='gt(scene,X)',showinfo`` emits one line per selected
    frame with ``pts_time`` (seconds) and ``scene`` (change score).
    """
    matches = _SHOWINFO_TIME_RE.findall(ffmpeg_stderr)
    return [(float(t), float(s)) for t, s in matches]


def detect_motion_peak(
    source_video_path: Path,
    video_duration_s: float,
    window_s: float,
    *,
    scene_threshold: float = 0.3,
    timeout_seconds: int = 60,
) -> HighlightWindow | None:
    """Detect motion-peak highlight via scene-change filter.

    Runs ``ffmpeg -vf "select='gt(scene,0.3)',showinfo"`` to get
    timestamps of significant visual changes. Picks the window
    centered on the biggest scene change.

    Returns None on failure — caller falls back to first_frame.
    """
    if not source_video_path.exists():
        return None

    from genlab_core.media.ffmpeg import get_ffmpeg_binary

    try:
        ffmpeg = get_ffmpeg_binary()
    except RuntimeError:
        return None

    cmd = [
        ffmpeg,
        "-i",
        str(source_video_path),
        "-vf",
        f"select='gt(scene,{scene_threshold})',showinfo",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds)
    except (subprocess.TimeoutExpired, OSError):
        return None

    scenes = _parse_scene_changes(result.stderr)
    if not scenes:
        # No scene changes above threshold — signal too weak; skip
        return None

    # Pick the timestamp with the highest scene score
    peak_ts, _ = max(scenes, key=lambda ts_score: ts_score[1])

    half = window_s / 2
    start = max(0.0, peak_ts - half)
    end = min(video_duration_s, start + window_s)
    start = max(0.0, end - window_s)
    return HighlightWindow(start_s=start, end_s=end)


# --- LLM pick stub ------------------------------------------------------------


def detect_llm_pick(
    source_video_path: Path,
    video_duration_s: float,
    window_s: float,
) -> HighlightWindow | None:
    """LLM-vision-based highlight pick — DEFERRED to future PR.

    Returns None so the caller falls back to first_frame. The bandit
    arm still exists (registered in PR 3); it just isn't producing
    differentiated signal yet. When Claude vision on sampled frames
    ships, this function will be replaced.
    """
    logger.debug("[highlight_detector] llm_pick not implemented — arm falls back")
    _ = source_video_path, video_duration_s, window_s
    return None


# --- Dispatcher ---------------------------------------------------------------


def detect_highlight(
    source_video_path: Path,
    method: str,
    *,
    video_duration_s: float,
    window_s: float = 8.0,
    fallback_to_first_frame: bool = True,
) -> HighlightWindow | None:
    """Dispatch to the chosen detection method.

    Returns:
        HighlightWindow when detection succeeds OR falls back to
        first_frame (default). None only when
        ``fallback_to_first_frame=False`` AND the chosen method
        failed.

    Args:
        method: 'first_frame' | 'audio_peak' | 'motion_peak' | 'llm_pick'
        video_duration_s: source duration (from ffprobe).
        window_s: highlight window length (default 8s).
        fallback_to_first_frame: if True, failed detection returns
            first_frame result instead of None. Enables the "always
            get some window" contract most callers want.
    """
    if method == "first_frame":
        return use_first_frame(video_duration_s, window_s)

    result: HighlightWindow | None = None
    if method == "audio_peak":
        result = detect_audio_peak(source_video_path, video_duration_s, window_s)
    elif method == "motion_peak":
        result = detect_motion_peak(source_video_path, video_duration_s, window_s)
    elif method == "llm_pick":
        result = detect_llm_pick(source_video_path, video_duration_s, window_s)
    else:
        logger.warning("[highlight_detector] unknown method %r — fallback", method)

    if result is not None and result.is_valid():
        return result

    if fallback_to_first_frame:
        return use_first_frame(video_duration_s, window_s)
    return None


__all__ = [
    "HighlightWindow",
    "detect_audio_peak",
    "detect_highlight",
    "detect_llm_pick",
    "detect_motion_peak",
    "use_first_frame",
]
