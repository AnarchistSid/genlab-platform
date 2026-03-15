"""Shared FFmpeg utility functions — probe, trim, concat, encode, filter builders.

Lower-level operations that both Content Scraper and CriticalRush use.
For platform-specific rendering (RenderSpec, transcode_for_platforms),
see genlab_core.media.ffmpeg.

Binary discovery uses get_ffmpeg_binary()/get_ffprobe_binary() from
genlab_core.media.ffmpeg for consistent FFmpeg location across all code.

All functions are sync (subprocess.run) because both agents' pipelines
are sync. The async rendering pipeline lives in genlab_core.media.ffmpeg.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from genlab_core.media.ffmpeg import get_ffmpeg_binary, get_ffprobe_binary

logger = logging.getLogger(__name__)

# ── Instagram Reels spec constants ────────────────────────────────
REEL_WIDTH = 1080
REEL_HEIGHT = 1920
REEL_FPS = 30
REEL_MAX_DURATION = 900  # Instagram Reels max 15 min
FFMPEG_TIMEOUT = 120     # Default subprocess timeout

# ── Landscape (16:9) spec constants ──────────────────────────────
LANDSCAPE_WIDTH = 1920
LANDSCAPE_HEIGHT = 1080
LANDSCAPE_FPS = 30

# ── Final encode params (Instagram competitive spec) ─────────────
FINAL_VIDEO_PARAMS: list[str] = [
    "-c:v", "libx264",
    "-profile:v", "high",
    "-level:v", "4.1",
    "-preset", "slow",
    "-crf", "17",
    "-maxrate", "25M",
    "-bufsize", "50M",
    "-pix_fmt", "yuv420p",
    "-r", "30",
    "-g", "60",
    "-bf", "2",
    "-colorspace", "bt709",
    "-color_primaries", "bt709",
    "-color_trc", "bt709",
]

FINAL_AUDIO_PARAMS: list[str] = [
    "-c:a", "aac",
    "-b:a", "256k",
    "-ar", "48000",
    "-ac", "2",
]

# ── Lossless intermediate params ─────────────────────────────────
LOSSLESS_VIDEO_PARAMS: list[str] = [
    "-c:v", "ffv1",
    "-level", "3",
    "-threads", "4",
]

LOSSLESS_AUDIO_PARAMS: list[str] = [
    "-c:a", "pcm_s16le",
]


# ══════════════════════════════════════════════════════════════════
# Probe functions
# ══════════════════════════════════════════════════════════════════


def check_ffmpeg() -> bool:
    """Verify FFmpeg is installed and accessible."""
    try:
        ffmpeg = get_ffmpeg_binary()
        result = subprocess.run(
            [ffmpeg, "-version"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def check_ffprobe() -> bool:
    """Verify ffprobe is installed (usually ships with FFmpeg)."""
    try:
        ffprobe = get_ffprobe_binary()
        result = subprocess.run(
            [ffprobe, "-version"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def probe_video_duration(video_path: Path | str) -> float:
    """Get video/audio duration in seconds via ffprobe.

    Returns 0.0 if the file cannot be probed.
    """
    try:
        ffprobe = get_ffprobe_binary()
    except RuntimeError:
        return 0.0

    cmd = [
        ffprobe, "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(video_path),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            dur = float(data.get("format", {}).get("duration", 0))
            if dur <= 0:
                logger.warning(
                    "probe_video_duration: non-positive duration (%.2f) for %s",
                    dur, video_path,
                )
            return dur
    except (subprocess.TimeoutExpired, json.JSONDecodeError,
            FileNotFoundError, ValueError):
        pass
    return 0.0


def probe_video_metadata(video_path: Path | str) -> Dict[str, Any]:
    """Get full video metadata: duration, width, height, fps, codec.

    Returns empty dict on failure.
    """
    try:
        ffprobe = get_ffprobe_binary()
    except RuntimeError:
        return {}

    cmd = [
        ffprobe, "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(video_path),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return {}
        data = json.loads(result.stdout)

        video_stream: Dict[str, Any] = {}
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                video_stream = stream
                break

        duration = float(data.get("format", {}).get("duration", 0))
        width = int(video_stream.get("width", 0))
        height = int(video_stream.get("height", 0))

        fps = 0.0
        rfr = video_stream.get("r_frame_rate", "0/1")
        if "/" in str(rfr):
            num, den = str(rfr).split("/")
            if int(den) > 0:
                fps = int(num) / int(den)
        else:
            fps = float(rfr) if rfr else 0.0

        return {
            "duration": duration,
            "width": width,
            "height": height,
            "fps": round(fps, 2),
            "aspect_ratio": width / height if height > 0 else 0,
            "codec": video_stream.get("codec_name", ""),
            "format_name": data.get("format", {}).get("format_name", ""),
        }
    except (subprocess.TimeoutExpired, json.JSONDecodeError,
            FileNotFoundError, ValueError, ZeroDivisionError):
        return {}


def probe_media(path: str | Path) -> dict | None:
    """Probe a media file and return structured metadata.

    Returns dict with keys: video, audio, duration, file_size_bytes.
    Returns None on failure. More detailed than probe_video_metadata().
    """
    try:
        ffprobe = get_ffprobe_binary()
    except RuntimeError:
        return None

    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "quiet",
                "-print_format", "json",
                "-show_streams", "-show_format",
                str(path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
        video_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
            {},
        )
        audio_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "audio"),
            {},
        )
        fmt = data.get("format", {})

        fps = 0.0
        r_frame_rate = video_stream.get("r_frame_rate", "0/1")
        if "/" in str(r_frame_rate):
            num, den = str(r_frame_rate).split("/")
            fps = float(num) / float(den) if float(den) > 0 else 0.0
        else:
            fps = float(r_frame_rate) if r_frame_rate else 0.0

        return {
            "video": {
                "width": int(video_stream.get("width", 0)),
                "height": int(video_stream.get("height", 0)),
                "fps": round(fps, 2),
                "codec": video_stream.get("codec_name", ""),
                "duration": float(video_stream.get("duration", 0) or 0),
            },
            "audio": {
                "sample_rate": int(audio_stream.get("sample_rate", 0) or 0),
                "channels": int(audio_stream.get("channels", 0) or 0),
                "codec": audio_stream.get("codec_name", ""),
            },
            "duration": float(
                video_stream.get("duration", 0)
                or fmt.get("duration", 0)
                or 0
            ),
            "file_size_bytes": int(fmt.get("size", 0) or 0),
        }
    except (FileNotFoundError, subprocess.TimeoutExpired,
            json.JSONDecodeError) as e:
        logger.warning("probe_media error for %s: %s", path, e)
        return None


def probe_raw_ffprobe(video_path: Path | str) -> Optional[Dict]:
    """Run ffprobe and return the raw parsed JSON output.

    For compliance checking that needs direct access to all stream fields.
    Most callers should use probe_video_metadata() or probe_media() instead.
    """
    try:
        ffprobe = get_ffprobe_binary()
    except RuntimeError:
        return None

    cmd = [
        ffprobe, "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(video_path),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15,
        )
        if proc.returncode == 0:
            return json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError,
            FileNotFoundError):
        pass
    return None


def get_duration(path: str | Path) -> float:
    """Get video duration in seconds. Returns 0.0 on failure."""
    return probe_video_duration(path)


def get_resolution(path: str | Path) -> tuple[int, int]:
    """Get video resolution as (width, height). Returns (0, 0) on failure."""
    info = probe_media(str(path))
    if info:
        return (info["video"]["width"], info["video"]["height"])
    return (0, 0)


def get_fps(path: str | Path) -> float:
    """Get video frame rate. Returns 0.0 on failure."""
    info = probe_media(str(path))
    return info["video"]["fps"] if info else 0.0


# ══════════════════════════════════════════════════════════════════
# Text escaping and filter builders
# ══════════════════════════════════════════════════════════════════


def escape_drawtext(text: str) -> str:
    """Escape text for FFmpeg drawtext filter (filter_complex mode).

    Escapes: backslash, colon, semicolon, square brackets.
    Single quotes are replaced with Unicode RIGHT SINGLE QUOTATION MARK
    (U+2019) — visually identical but won't terminate the drawtext value
    delimiter in filter_complex strings.
    """
    text = text.replace("\\", "\\\\\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "\u2019")
    text = text.replace(";", "\\;")
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    return text


def _escape_drawtext_simple(text: str) -> str:
    """Escape text for FFmpeg drawtext filter (simple -vf mode)."""
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "\\'")
    text = text.replace(":", "\\:")
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    return text


def position_to_xy(
    position: str,
    margin_x: int = 0,
    margin_y: int = 0,
) -> tuple[str, str]:
    """Convert named position to FFmpeg drawtext x/y expressions."""
    positions = {
        "bottom_left": (str(margin_x), f"(h-text_h-{margin_y})"),
        "bottom_right": (f"(w-text_w-{margin_x})", f"(h-text_h-{margin_y})"),
        "top_left": (str(margin_x), str(margin_y)),
        "top_right": (f"(w-text_w-{margin_x})", str(margin_y)),
        "center": ("(w-text_w)/2", "(h-text_h)/2"),
        "bottom_center": ("(w-text_w)/2", f"(h-text_h-{margin_y})"),
    }
    return positions.get(position, ("0", "0"))


def build_scale_crop(
    src_width: int,
    src_height: int,
    target_width: int = 1080,
    target_height: int = 1920,
    mode: str = "crop",
) -> str:
    """Build filter string for aspect ratio conversion to target dimensions.

    Modes:
        - "crop": center crop then scale (default)
        - "pillarbox": returns blurred background filter (complex filter)
    """
    if mode == "pillarbox":
        tw, th = target_width, target_height
        return (
            f"[0:v]scale={tw}:{th}:force_original_aspect_ratio=increase,"
            f"crop={tw}:{th},boxblur=20[bg];"
            f"[0:v]scale={tw}:{th}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
        )

    src_ratio = src_width / max(src_height, 1)
    target_ratio = target_width / max(target_height, 1)

    if abs(src_ratio - target_ratio) < 0.05:
        return f"scale={target_width}:{target_height}"

    if src_ratio > target_ratio:
        crop_w = int(src_height * target_width / target_height)
        crop_x = (src_width - crop_w) // 2
        return f"crop={crop_w}:{src_height}:{crop_x}:0,scale={target_width}:{target_height}"
    else:
        crop_h = int(src_width * target_height / target_width)
        crop_y = (src_height - crop_h) // 2
        return f"crop={src_width}:{crop_h}:0:{crop_y},scale={target_width}:{target_height}"


def build_drawtext(
    text: str,
    font_file: str,
    size: int,
    color: str,
    x: str,
    y: str,
    start: float,
    end: float,
    stroke_color: str | None = None,
    stroke_width: int = 0,
) -> str:
    """Build a drawtext filter string for -vf mode."""
    escaped = _escape_drawtext_simple(text)
    parts = [
        f"drawtext=text='{escaped}'",
        f"fontfile={font_file}",
        f"fontsize={size}",
        f"fontcolor={color}",
        f"x={x}",
        f"y={y}",
        f"enable='between(t,{start},{end})'",
    ]
    if stroke_color and stroke_width > 0:
        parts.append(f"bordercolor={stroke_color}")
        parts.append(f"borderw={stroke_width}")
    return ":".join(parts)


def build_loudnorm_filter(
    target_i: float = -14.0,
    target_tp: float = -1.5,
    target_lra: float = 11.0,
) -> str:
    """Build loudnorm audio filter string."""
    return f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}"


# ══════════════════════════════════════════════════════════════════
# FFmpeg runner with preset fallback
# ══════════════════════════════════════════════════════════════════


def _swap_preset(cmd: list[str], new_preset: str) -> list[str]:
    """Replace -preset value in an FFmpeg command list."""
    cmd = list(cmd)  # copy
    try:
        idx = cmd.index("-preset")
        cmd[idx + 1] = new_preset
    except (ValueError, IndexError):
        # No -preset found — insert before output file (last arg)
        cmd = cmd[:-1] + ["-preset", new_preset, cmd[-1]]
    return cmd


def run_ffmpeg(
    cmd: list[str],
    timeout: int = FFMPEG_TIMEOUT,
    fallback_preset: str = "fast",
) -> subprocess.CompletedProcess:
    """Run FFmpeg with timeout and preset fallback.

    If the command times out (likely -preset slow on long clips),
    swaps to fallback_preset and retries with 180s timeout.
    """
    try:
        return subprocess.run(
            cmd, timeout=timeout, check=True,
            capture_output=True, text=True,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "FFmpeg timed out after %ds, retrying with -preset %s",
            timeout, fallback_preset,
        )
        fallback_cmd = _swap_preset(cmd, fallback_preset)
        return subprocess.run(
            fallback_cmd, timeout=180, check=True,
            capture_output=True, text=True,
        )


# ══════════════════════════════════════════════════════════════════
# Basic video operations
# ══════════════════════════════════════════════════════════════════


def trim(
    input_path: str,
    output_path: str,
    start: float,
    end: float,
    timeout: int = FFMPEG_TIMEOUT,
) -> bool:
    """Trim video from start to end seconds (stream copy). Returns True on success."""
    try:
        ffmpeg = get_ffmpeg_binary()
        result = subprocess.run(
            [
                ffmpeg, "-y",
                "-ss", str(start),
                "-to", str(end),
                "-i", input_path,
                "-c", "copy",
                "-avoid_negative_ts", "make_zero",
                output_path,
            ],
            capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode == 0
    except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("trim failed for %s: %s", input_path, e)
        return False


def concat(
    input_paths: list[str],
    output_path: str,
    temp_dir: str | None = None,
    timeout: int = FFMPEG_TIMEOUT,
) -> bool:
    """Concatenate video files using concat demuxer (stream copy). Returns True on success."""
    if not input_paths:
        return False

    temp_dir = temp_dir or tempfile.gettempdir()
    filelist_path = Path(temp_dir) / "concat_list.txt"

    try:
        ffmpeg = get_ffmpeg_binary()
        with open(filelist_path, "w") as f:
            for p in input_paths:
                f.write(f"file '{p}'\n")

        result = subprocess.run(
            [
                ffmpeg, "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(filelist_path),
                "-c", "copy",
                output_path,
            ],
            capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode == 0
    except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("concat failed: %s", e)
        return False
    finally:
        filelist_path.unlink(missing_ok=True)


def reencode(
    input_path: str,
    output_path: str,
    width: int,
    height: int,
    fps: int,
    bitrate_kbps: int,
    audio_sample_rate: int = 48000,
    audio_channels: int = 2,
    audio_bitrate_kbps: int | None = None,
    timeout: int = FFMPEG_TIMEOUT,
) -> bool:
    """Re-encode video with specified parameters. Returns True on success."""
    try:
        ffmpeg = get_ffmpeg_binary()
        cmd = [
            ffmpeg, "-y",
            "-i", input_path,
            "-vf", f"scale={width}:{height},format=yuv420p",
            "-r", str(fps),
            "-c:v", "libx264",
            "-b:v", f"{bitrate_kbps}k",
            "-c:a", "aac",
            "-ar", str(audio_sample_rate),
            "-ac", str(audio_channels),
        ]
        if audio_bitrate_kbps is not None:
            cmd.extend(["-b:a", f"{audio_bitrate_kbps}k"])
        cmd.extend(["-movflags", "+faststart", output_path])

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode == 0
    except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("reencode failed for %s: %s", input_path, e)
        return False


def get_loudness(path: str | Path) -> dict | None:
    """Measure audio loudness using ffmpeg loudnorm filter.

    Returns dict with input_i, input_tp, input_lra (as floats).
    Returns None on failure.
    """
    try:
        ffmpeg = get_ffmpeg_binary()
        result = subprocess.run(
            [
                ffmpeg,
                "-i", str(path),
                "-af", "loudnorm=print_format=json",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=60,
        )

        stderr = result.stderr
        json_match = re.search(r'\{[^}]*"input_i"[^}]*\}', stderr, re.DOTALL)
        if not json_match:
            return None

        data = json.loads(json_match.group())
        return {
            "input_i": float(data.get("input_i", 0)),
            "input_tp": float(data.get("input_tp", 0)),
            "input_lra": float(data.get("input_lra", 0)),
            "input_thresh": float(data.get("input_thresh", 0)),
        }
    except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired,
            json.JSONDecodeError, ValueError) as e:
        logger.warning("get_loudness failed for %s: %s", path, e)
        return None


def loudnorm(
    input_path: str,
    output_path: str,
    target_lufs: float = -14.0,
    timeout: int = FFMPEG_TIMEOUT,
) -> bool:
    """Apply loudness normalization. Returns True on success."""
    af = build_loudnorm_filter(target_i=target_lufs)
    try:
        ffmpeg = get_ffmpeg_binary()
        result = subprocess.run(
            [
                ffmpeg, "-y",
                "-i", input_path,
                "-af", af,
                "-c:v", "copy",
                "-movflags", "+faststart",
                output_path,
            ],
            capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode == 0
    except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("loudnorm failed for %s: %s", input_path, e)
        return False


# ══════════════════════════════════════════════════════════════════
# CDN / publishing operations
# ══════════════════════════════════════════════════════════════════


def split_video_segments(
    video_path: Path | str,
    max_segment_seconds: float = 59.0,
    output_dir: Optional[Path | str] = None,
) -> list[str]:
    """Split a video into segments using keyframe-aligned stream copy.

    Returns list of output file paths (sorted by part number).
    """
    video_path = Path(video_path)
    if not video_path.exists():
        logger.error("split_video_segments: file not found: %s", video_path)
        return []

    duration = probe_video_duration(video_path)
    if duration <= max_segment_seconds:
        return [str(video_path)]

    out_dir = Path(output_dir) if output_dir else video_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = video_path.stem

    for stale in out_dir.glob(f"{stem}_part*"):
        try:
            stale.unlink()
        except OSError:
            pass

    pattern = str(out_dir / f"{stem}_part%02d.mp4")
    try:
        ffmpeg = get_ffmpeg_binary()
        proc = subprocess.run(
            [
                ffmpeg, "-y", "-i", str(video_path),
                "-f", "segment",
                "-segment_time", str(int(max_segment_seconds)),
                "-reset_timestamps", "1",
                "-map", "0:v:0", "-map", "0:a?",
                "-c", "copy",
                pattern,
            ],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0:
            logger.error("split_video_segments failed: %s", proc.stderr[-500:])
            return []
    except (RuntimeError, subprocess.TimeoutExpired):
        return []

    part_pattern = re.compile(re.escape(stem) + r"_part\d+\.mp4$")
    segments = sorted(
        p for p in out_dir.glob(f"{stem}_part*.mp4")
        if part_pattern.search(p.name)
    )
    paths = [str(s) for s in segments]
    logger.info(
        "Split %s (%.1fs) into %d segments",
        video_path.name, duration, len(paths),
    )
    return paths


def reencode_for_cdn(
    video_path: Path | str,
    max_size_mb: float = 20.0,
    target_bitrate_kbps: int = 0,
    audio_bitrate_kbps: int = 128,
) -> str:
    """Re-encode a video to fit under a CDN upload size limit.

    If the file is already under max_size_mb, returns the original path.
    Auto-calculates bitrate from target size and duration. Retries once
    at reduced bitrate if first attempt overshoots.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        logger.error("reencode_for_cdn: file not found: %s", video_path)
        return str(video_path)

    size_mb = video_path.stat().st_size / (1024 * 1024)
    if size_mb <= max_size_mb:
        return str(video_path)

    duration = probe_video_duration(video_path)
    if duration <= 0:
        duration = 60.0

    if target_bitrate_kbps <= 0:
        usable_mb = max_size_mb * 0.95
        target_bitrate_kbps = int(
            (usable_mb * 8 * 1024) / duration - audio_bitrate_kbps
        )

    MIN_BITRATE_KBPS = 400
    target_bitrate_kbps = max(target_bitrate_kbps, MIN_BITRATE_KBPS)

    logger.info(
        "reencode_for_cdn: %s is %.1fMB (limit %.1fMB) — re-encoding at %d kbps",
        video_path.name, size_mb, max_size_mb, target_bitrate_kbps,
    )

    out_path = video_path.with_stem(video_path.stem + "_cdn")

    def _encode(v_kbps: int) -> bool:
        try:
            ffmpeg = get_ffmpeg_binary()
            cmd = [
                ffmpeg, "-y", "-i", str(video_path),
                "-map", "0:v:0", "-map", "0:a:0?",
                "-c:v", "libx264",
                "-profile:v", "high",
                "-preset", "medium",
                "-b:v", f"{v_kbps}k",
                "-maxrate", f"{int(v_kbps * 1.5)}k",
                "-bufsize", f"{v_kbps * 2}k",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", f"{audio_bitrate_kbps}k",
                "-movflags", "+faststart",
                str(out_path),
            ]
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600,
            )
            if proc.returncode != 0:
                logger.error("reencode_for_cdn failed: %s", proc.stderr[-500:])
                return False
            return True
        except (RuntimeError, subprocess.TimeoutExpired):
            Path(out_path).unlink(missing_ok=True)
            return False

    current_kbps = target_bitrate_kbps

    for attempt in range(1, 3):
        if not _encode(current_kbps):
            return str(video_path)

        new_size_mb = out_path.stat().st_size / (1024 * 1024)
        if new_size_mb <= max_size_mb:
            break

        overshoot = new_size_mb / max_size_mb
        current_kbps = max(int(current_kbps / overshoot * 0.85), MIN_BITRATE_KBPS)

    return str(out_path)
