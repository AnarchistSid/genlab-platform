"""Gaming-specific FFmpeg utilities.

Re-exports shared functions from genlab_core.media.ffmpeg_utils and adds
gaming-specific transition handling (hard_cut, zoom_impact, glitch, etc.).
"""

from __future__ import annotations

import logging
import shutil
import subprocess  # noqa: F401 — tests patch ffmpeg_gaming.subprocess.run
from typing import Any

from genlab_core.media.ffmpeg import get_ffmpeg_binary
from genlab_core.media.ffmpeg_utils import (  # noqa: F401 — backward compat re-exports
    XFADE_MAP as _BASE_XFADE_MAP,
)
from genlab_core.media.ffmpeg_utils import (
    concat,
    escape_drawtext,
    get_duration,
    probe_media,
)

# Gaming quality encoding args (bt709 color space, CRF 17)
_QUALITY_ARGS = [
    "-c:v", "libx264", "-preset", "slow",
    "-crf", "17",
    "-pix_fmt", "yuv420p",
    "-color_primaries", "bt709",
    "-color_trc", "bt709",
    "-colorspace", "bt709",
    "-c:a", "aac", "-b:a", "192k",
    "-movflags", "+faststart",
]

logger = logging.getLogger(__name__)

# Gaming-specific transitions (extend the base map)
# hard_cut = None signals "no xfade filter, just concat"
XFADE_MAP: dict[str, str | None] = {
    **_BASE_XFADE_MAP,
    "hard_cut": None,
    "zoom_impact": "smoothup",
    "glitch": "pixelize",
    "white_flash": "fadewhite",
    "chromatic_aberration": "distance",
}


def normalize_clip(
    input_path: str,
    output_path: str,
    target_width: int = 1080,
    target_height: int = 1920,
    target_fps: int | None = None,
    crf: str = "17",
) -> bool:
    """Gaming-specific normalize: bt709 color space + quality encoding."""
    ffmpeg = get_ffmpeg_binary()
    info = probe_media(input_path) or {}
    video = info.get("video", {})
    video.get("width", target_width)
    video.get("height", target_height)

    vf = f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black"

    cmd = [ffmpeg, "-y", "-i", input_path, "-vf", vf]
    if target_fps:
        cmd.extend(["-r", str(target_fps)])
    cmd.extend(_QUALITY_ARGS)
    cmd.append(output_path)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def add_text_overlay(
    input_path: str,
    output_path: str,
    overlays: list[dict] | None = None,
) -> bool:
    """Gaming-specific text overlay with bt709 quality encoding."""
    if not overlays:
        shutil.copy2(input_path, output_path)
        return True

    ffmpeg = get_ffmpeg_binary()
    filters = []
    for ov in overlays:
        text = escape_drawtext(ov.get("text", ""))
        x = ov.get("x", "(w-text_w)/2")
        y = ov.get("y", 50)
        size = ov.get("fontsize", 36)
        filters.append(
            f"drawtext=text='{text}':fontsize={size}:fontcolor=white"
            f":x={x}:y={y}:enable='between(t,{ov.get('start', 0)},{ov.get('end', 9999)})'"
            f":shadowcolor=black@0.5:shadowx=2:shadowy=2"
        )

    vf = ",".join(filters) if filters else "null"
    cmd = [ffmpeg, "-y", "-i", input_path, "-vf", vf]
    cmd.extend(_QUALITY_ARGS)
    cmd.append(output_path)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def build_xfade_transition(
    transition_name: str = "fade",
    duration: float = 0.5,
    offset: float = 0.0,
) -> str | None:
    """Gaming-aware xfade builder. Returns None for hard_cut (no transition)."""
    ffmpeg_name = XFADE_MAP.get(transition_name)
    if ffmpeg_name is None and transition_name in XFADE_MAP:
        return None  # explicit hard_cut
    if ffmpeg_name is None:
        ffmpeg_name = "fade"  # unknown → fallback
    return f"xfade=transition={ffmpeg_name}:duration={duration}:offset={offset}"


def concat_with_transitions(
    clip_paths: list[str],
    output_path: str,
    transitions: list[dict[str, Any]] | None = None,
    temp_dir: str | None = None,
) -> bool:
    """Concatenate clips with xfade transitions.

    Gaming-specific: supports hard_cut, zoom_impact, glitch, etc.
    Falls back to simple concat when all transitions are hard_cut or None.
    """
    if not clip_paths:
        return False

    if len(clip_paths) == 1:
        shutil.copy2(clip_paths[0], output_path)
        return True

    transitions = transitions or []

    # If all transitions are hard_cut, use simple concat
    if all(
        t.get("type") == "hard_cut" or t.get("type") is None
        for t in transitions
    ):
        return concat(clip_paths, output_path, temp_dir=temp_dir)

    # Build xfade filter graph
    ffmpeg = get_ffmpeg_binary()
    n = len(clip_paths)
    durations = [get_duration(p) for p in clip_paths]

    # Build input args
    input_args: list[str] = []
    for p in clip_paths:
        input_args.extend(["-i", p])

    # Build filter_complex
    filter_parts: list[str] = []
    current_offset = 0.0

    for i in range(n - 1):
        t = transitions[i] if i < len(transitions) else {"type": "fade", "duration": 0.5}
        t_type = t.get("type", "fade")
        t_dur = t.get("duration", 0.5)

        xfade_str = build_xfade_transition(t_type, duration=t_dur, offset=0.0)

        if xfade_str is None:
            # hard_cut: use minimal overlap to keep xfade chain valid
            fake_dur = 0.001
            offset = current_offset + durations[i] - fake_dur
            xfade_filter = f"xfade=transition=fade:duration={fake_dur}:offset={offset}"
            current_offset = offset
        else:
            offset = current_offset + durations[i] - t_dur
            xfade_filter = f"xfade=transition={XFADE_MAP.get(t_type, 'fade')}:duration={t_dur}:offset={offset}"
            current_offset = offset

        if i == 0:
            src = "[0:v][1:v]"
        else:
            src = f"[v{i}][{i + 1}:v]"

        if i < n - 2:
            out = f"[v{i + 1}]"
        else:
            out = "[vout]"

        filter_parts.append(f"{src}{xfade_filter}{out}")

    filter_complex = ";".join(filter_parts)

    # Check if clips have audio
    audio_info = probe_media(clip_paths[0])
    has_audio = audio_info and "audio" in audio_info

    if has_audio and n > 1:
        # Merge audio tracks with amerge/amix
        audio_inputs = "".join(f"[{i}:a]" for i in range(n))
        filter_complex += f";{audio_inputs}amix=inputs={n}:duration=longest[aout]"
        map_args = ["-map", "[vout]", "-map", "[aout]"]
    else:
        map_args = ["-map", "[vout]"]

    cmd = [
        ffmpeg, "-y",
        *input_args,
        "-filter_complex", filter_complex,
        *map_args,
        *_QUALITY_ARGS,
        output_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
