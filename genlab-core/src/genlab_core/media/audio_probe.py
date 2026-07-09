"""genlab_core.media.audio_probe — Detect and extract audio from video clips.

Used by per-channel visual render strategies to decide:
  - Path A: clip has meaningful speech audio → extract + Whisper sync captions
  - Path B: clip is silent / music-only → generate TTS narration instead

Two public functions:
  has_meaningful_audio(clip_path)  — ffprobe stream check + volumedetect silence gate
  extract_audio_track(clip_path)   — WAV extraction at 16kHz mono (Whisper's native format)
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path

from genlab_core.media.ffmpeg import get_ffmpeg_binary, get_ffprobe_binary

logger = logging.getLogger(__name__)


# ── Public API ───────────────────────────────────────────────────────────────


def has_meaningful_audio(
    clip_path: Path | str,
    silence_threshold_db: float = -40,
) -> bool:
    """Check whether a video clip contains audible (non-silent) audio.

    Steps:
      1. ffprobe to verify an audio stream exists
      2. FFmpeg volumedetect to measure mean volume
      3. Compare mean volume against threshold

    Args:
        clip_path: Path to the video file.
        silence_threshold_db: Mean volume (dB) below which audio is
            considered silent.  Default -40 dB catches music beds,
            background noise, and near-silence.

    Returns:
        True if the clip has an audio stream with mean volume above the
        threshold, False otherwise (including on any error).
    """
    clip_path = Path(clip_path)

    # Step 1 — does an audio stream exist at all?
    stream = _probe_audio_stream(clip_path)
    if stream is None:
        # Task #633 (2026-07-09 late evening): elevated DEBUG → INFO.
        # Missing audio stream isn't necessarily a bug (some source
        # clips are truly silent-video), but tonight's motion_compositor
        # investigation raised the hypothesis that a music_mood
        # transform sometimes produces zero-audio output that then
        # breaks concat with -22 EINVAL. Making this INFO so
        # operators grepping journal after a concat failure can
        # correlate "audio_probe reported no audio" against
        # motion_compositor's "Nothing was written into output file".
        logger.info("[audio_probe] No audio stream in %s", clip_path.name)
        return False

    # Step 2 — measure volume
    mean_vol = _measure_volume(clip_path)
    if mean_vol is None:
        # Task #633: same rationale as above. Measurement-failed
        # correlates with motion_compositor concat -22 EINVAL
        # hypothesis C (corrupted audio after music_mood stage).
        logger.info(
            "[audio_probe] Could not measure volume for %s (ffmpeg volumedetect "
            "returned no mean_volume — file may have corrupted audio stream)",
            clip_path.name,
        )
        return False

    # Step 3 — compare against threshold
    is_meaningful = mean_vol > silence_threshold_db
    logger.debug(
        "[audio_probe] %s mean_volume=%.1f dB threshold=%.1f → %s",
        clip_path.name,
        mean_vol,
        silence_threshold_db,
        "speech" if is_meaningful else "silent",
    )
    return is_meaningful


def extract_audio_track(
    clip_path: Path | str,
    output_path: Path | str,
) -> Path | None:
    """Extract audio from a video clip as 16kHz mono WAV (Whisper format).

    Args:
        clip_path: Path to the source video file.
        output_path: Destination path for the extracted WAV.

    Returns:
        output_path as a Path on success, None on failure.
    """
    clip_path = Path(clip_path)
    output_path = Path(output_path)

    try:
        ffmpeg = get_ffmpeg_binary()
    except RuntimeError:
        logger.warning("[audio_probe] FFmpeg not found — cannot extract audio")
        return None

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(clip_path),
        "-vn",  # drop video
        "-acodec",
        "pcm_s16le",  # 16-bit signed little-endian
        "-ar",
        "16000",  # 16 kHz sample rate (Whisper native)
        "-ac",
        "1",  # mono
        str(output_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0:
            logger.warning(
                "[audio_probe] Audio extraction failed (exit %d) for %s",
                result.returncode,
                clip_path.name,
            )
            return None
        return output_path
    except Exception as exc:
        logger.warning("[audio_probe] Audio extraction error for %s: %s", clip_path.name, exc)
        return None


# ── Internal helpers ─────────────────────────────────────────────────────────


def _probe_audio_stream(clip_path: Path) -> dict | None:
    """Use ffprobe to check for an audio stream.

    Returns:
        A dict of stream metadata if an audio stream exists, None otherwise.
    """
    try:
        ffprobe = get_ffprobe_binary()
    except RuntimeError:
        return None

    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=codec_name,channels,sample_rate",
        "-of",
        "json",
        str(clip_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        return streams[0] if streams else None
    except Exception:
        return None


_MEAN_VOLUME_RE = re.compile(r"mean_volume:\s*([-\d.]+)\s*dB")


def _measure_volume(clip_path: Path) -> float | None:
    """Use FFmpeg volumedetect filter to measure mean volume in dB.

    Returns:
        Mean volume in dB (negative float), or None on failure.
    """
    try:
        ffmpeg = get_ffmpeg_binary()
    except RuntimeError:
        return None

    cmd = [
        ffmpeg,
        "-i",
        str(clip_path),
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        # volumedetect outputs to stderr
        match = _MEAN_VOLUME_RE.search(result.stderr)
        if match:
            return float(match.group(1))
        return None
    except Exception:
        return None
