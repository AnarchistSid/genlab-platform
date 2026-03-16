"""Frame compositor for Gen Lab video reels.

Implements the canonical three-case frame layout for all 5 channels.
This is the ONLY place that frame composition logic lives.
All channels call this. Zero per-channel compositor divergence allowed.

THE LOCKED SPEC
───────────────
Canvas: 1080 x 1920 (9:16 portrait, always)

LANDSCAPE (source aspect ratio >= 1.33):
  y=0-310:    Solid black top bar. Logo(60px x=45) + channel name(24px x=120 y=322)
              + handle(17px x=120 y=346)
  y=310-370:  Name row (60px)  [solid black]
  y=380-460:  Hook text zone (80px, 44px bold white, vertically centered,
              max 2 lines)  [solid black]
  y=460-466:  Accent line (6px, channel accent color)
  y=466-1074: VIDEO 1080x608 — ZERO overlays on video
  y=1074-1920: Solid black (846px)

PORTRAIT (source aspect ratio <= 0.75):
  y=0-310:    Solid black top bar. Logo(60px x=45) + channel name + handle
  y=310-370:  Name row (60px)  [solid black]
  y=380-460:  Hook text zone (80px, 44px bold white, vertically centered)  [solid black]
  y=460-466:  Accent line (6px, channel accent color)
  y=466-1466: VIDEO 1080x1000 — ZERO overlays on video (letterboxed if needed)
  y=1466-1920: Solid black (454px)

SQUARE (source aspect ratio 0.75 to 1.33):
  y=0-310:    Solid black top bar. Logo(60px x=45) + channel name + handle
  y=310-370:  Name row (60px)  [solid black]
  y=380-460:  Hook text zone (80px, 44px bold white, vertically centered)  [solid black]
  y=460-466:  Accent line (6px, channel accent color)
  y=466-1546: VIDEO 1080x1080 — ZERO overlays on video
  y=1546-1920: Solid black (374px)

Usage:
    comp = FrameCompositor.from_visuals_yaml("path/to/visuals.yaml")
    output_path = comp.compose(
        source_video_path="input.mp4",
        hook_text="Bam Adebayo just dropped 83",
        output_path="output.mp4",
        duration_seconds=30,
    )
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from genlab_core.media.ffmpeg_utils import run_ffmpeg

logger = logging.getLogger(__name__)

# -------------------------------------------------------------
# Canvas constants -- LOCKED. Do not make these configurable.
# -------------------------------------------------------------
CANVAS_W = 1080
CANVAS_H = 1920

# Aspect ratio thresholds
LANDSCAPE_THRESHOLD = 1.33   # w/h >= this -> landscape
PORTRAIT_THRESHOLD = 0.75    # w/h <= this -> portrait
# Between 0.75 and 1.33 = SQUARE

# Layout A: Landscape
L_TOP_BAR_H = 310
L_NAME_ROW_Y = 310
L_NAME_ROW_H = 60
L_HOOK_ZONE_Y = 380
L_HOOK_ZONE_H = 80
L_ACCENT_Y = 460
L_ACCENT_H = 6
L_VIDEO_Y = 466
L_VIDEO_H = 608
L_BOTTOM_H = 846

# Layout B: Portrait (sandwich layout — same structure as landscape/square)
P_TOP_BAR_H = 310
P_NAME_ROW_Y = 310
P_NAME_ROW_H = 60
P_HOOK_ZONE_Y = 380
P_HOOK_ZONE_H = 80
P_ACCENT_Y = 460
P_ACCENT_H = 6
P_VIDEO_Y = 466
P_VIDEO_H = 1000
P_BOTTOM_H = 454

# Layout C: Square
S_TOP_BAR_H = 310
S_NAME_ROW_Y = 310
S_NAME_ROW_H = 60
S_HOOK_ZONE_Y = 380
S_HOOK_ZONE_H = 80
S_ACCENT_Y = 460
S_ACCENT_H = 6
S_VIDEO_Y = 466
S_VIDEO_H = 1080
S_BOTTOM_H = 374

# Shared text
LOGO_SIZE = 60
LOGO_X = 45
LOGO_Y = 310          # logo top aligned with name-row start (y=310)
NAME_FONT_SIZE = 24
NAME_X = 120
NAME_Y = 322
HANDLE_FONT_SIZE = 17
HANDLE_X = 120
HANDLE_Y = 346
HANDLE_OPACITY = 0.70
HOOK_FONT_SIZE = 44
HOOK_LINE_H = 52
HOOK_MAX_LINES = 2
HOOK_MAX_CHARS_LINE = 35
SHADOW_OFFSET = 2
SHADOW_OPACITY = 0.50
HOOK_X = 45

HOOK_MAX_CHARS = 60  # enforced upstream, checked here too


# -------------------------------------------------------------
# Config
# -------------------------------------------------------------

@dataclass
class FFmpegConfig:
    """FFmpeg render settings from visuals.yaml."""
    preset: str = "slow"
    fallback_preset: str = "fast"
    timeout_seconds: int = 120


@dataclass
class ChannelBranding:
    """Per-channel branding loaded from visuals.yaml."""
    channel_name: str           # e.g. "CriticalRush"
    handle: str                 # e.g. "@CriticalRush"
    accent_color: str           # e.g. "#00FF88" (for accent line)
    logo_path: str              # absolute or relative path to logo PNG
    niche_id: str               # e.g. "gaming"
    ffmpeg: FFmpegConfig = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.ffmpeg is None:
            self.ffmpeg = FFmpegConfig()

    @classmethod
    def from_visuals_yaml(cls, path: str) -> "ChannelBranding":
        """Load branding from a channel's visuals.yaml."""
        with open(path) as f:
            cfg = yaml.safe_load(f)

        # Support both flat and nested structures
        v = cfg.get("visuals", cfg)
        branding = v.get("branding", v)

        # Also check frame_layout.branding (Sprint 56 structure)
        fl = cfg.get("frame_layout", {})
        fl_branding = fl.get("branding", {})

        # FFmpeg render config
        ff_cfg = cfg.get("ffmpeg", {})
        ffmpeg = FFmpegConfig(
            preset=ff_cfg.get("preset", "slow"),
            fallback_preset=ff_cfg.get("fallback_preset", "fast"),
            timeout_seconds=int(ff_cfg.get("timeout_seconds", 120)),
        )

        return cls(
            channel_name=(
                fl_branding.get("channel_name")
                or branding.get("channel_name")
                or branding.get("name", "")
            ),
            handle=(
                fl_branding.get("handle")
                or branding.get("handle")
                or branding.get("instagram_handle", "")
            ),
            accent_color=(
                fl_branding.get("accent_color")
                or branding.get("accent_color", "#FFFFFF")
            ),
            logo_path=(
                fl_branding.get("logo_path")
                or branding.get("logo_path")
                or branding.get("logo", "")
            ),
            niche_id=(
                fl_branding.get("niche_id")
                or branding.get("niche_id")
                or cfg.get("niche_id", "")
            ),
            ffmpeg=ffmpeg,
        )


# -------------------------------------------------------------
# Source video probe
# -------------------------------------------------------------

@dataclass
class VideoInfo:
    width: int
    height: int
    duration_seconds: float
    fps: float
    aspect_ratio: float       # width / height
    is_portrait: bool         # aspect_ratio <= PORTRAIT_THRESHOLD
    is_landscape: bool        # aspect_ratio >= LANDSCAPE_THRESHOLD
    is_native_9_16: bool      # kept for backward compat; True when portrait

    @property
    def layout_case(self) -> str:
        """'landscape', 'portrait', or 'square'."""
        if self.aspect_ratio >= LANDSCAPE_THRESHOLD:
            return "landscape"
        elif self.aspect_ratio <= PORTRAIT_THRESHOLD:
            return "portrait"
        return "square"


def probe_video(path: str) -> VideoInfo:
    """Use ffprobe to get video dimensions and duration."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", "-select_streams", "v:0", path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {result.stderr}")

    data = json.loads(result.stdout)
    stream = data["streams"][0]

    w = int(stream["width"])
    h = int(stream["height"])
    ar = w / h

    # Duration: try stream first, then format
    dur_str = stream.get("duration")
    if not dur_str:
        cmd2 = ["ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", path]
        r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=30)
        if r2.returncode == 0:
            fmt = json.loads(r2.stdout).get("format", {})
            dur_str = fmt.get("duration", "0")
    duration = float(dur_str or 0)

    # FPS
    fps_str = stream.get("r_frame_rate", "30/1")
    try:
        num, den = fps_str.split("/")
        fps = float(num) / float(den)
    except Exception:
        fps = 30.0

    is_portrait = ar <= PORTRAIT_THRESHOLD
    is_landscape = ar >= LANDSCAPE_THRESHOLD
    is_native_9_16 = is_portrait  # backward compat

    return VideoInfo(
        width=w, height=h, duration_seconds=duration,
        fps=fps, aspect_ratio=ar,
        is_portrait=is_portrait, is_landscape=is_landscape,
        is_native_9_16=is_native_9_16,
    )


# -------------------------------------------------------------
# Frame compositor
# -------------------------------------------------------------

class FrameCompositor:
    """Composes a Gen Lab video reel from a source clip + channel branding.

    Always outputs 1080x1920 H.264/AAC video with the locked frame spec.
    Call compose() to render.
    """

    def __init__(self, branding: ChannelBranding):
        self.branding = branding

    @classmethod
    def from_visuals_yaml(cls, visuals_yaml_path: str) -> "FrameCompositor":
        branding = ChannelBranding.from_visuals_yaml(visuals_yaml_path)
        return cls(branding)

    def compose(
        self,
        source_video_path: str,
        hook_text: str,
        output_path: str,
        duration_seconds: Optional[float] = None,
        trim_start: float = 0.0,
        crf: int = 15,
        preset: Optional[str] = None,
        force_fps: int = 30,
    ) -> str:
        """Render a reel with the canonical frame layout.

        Args:
            source_video_path: Path to the downloaded source clip.
            hook_text: Hook text to overlay (<=60 chars).
            output_path: Where to write the output .mp4.
            duration_seconds: Clip duration cap (None = use full clip).
            trim_start: Start trimming from this offset in seconds.
            crf: H.264 CRF (15 for Instagram quality, 17 for FB, 18 for YT).
            preset: FFmpeg preset. Defaults to visuals.yaml ffmpeg.preset.
            force_fps: Output frame rate.

        Returns:
            output_path on success.

        Raises:
            RuntimeError: if FFmpeg fails.
        """
        # Use config defaults from visuals.yaml
        ff = self.branding.ffmpeg
        if preset is None:
            preset = ff.preset
        # Validate hook length
        if len(hook_text) > HOOK_MAX_CHARS:
            logger.warning(
                f"Hook '{hook_text[:30]}...' is {len(hook_text)} chars -- truncating to {HOOK_MAX_CHARS}"
            )
            hook_text = hook_text[:HOOK_MAX_CHARS - 3] + "..."

        # Probe source
        info = probe_video(source_video_path)
        case = info.layout_case
        logger.info(
            f"[{self.branding.niche_id}] Source: {info.width}x{info.height} "
            f"ar={info.aspect_ratio:.3f} -> {case}"
        )

        # Ensure output directory exists
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        builder = {
            "landscape": self._build_cmd_landscape,
            "portrait": self._build_cmd_portrait,
            "square": self._build_cmd_square,
        }[case]
        ffmpeg_cmd = builder(
            source_video_path, hook_text, output_path,
            info, duration_seconds, trim_start, crf, preset, force_fps,
        )

        logger.info(f"[{self.branding.niche_id}] Running FFmpeg ({preset}): {' '.join(ffmpeg_cmd[:8])}...")
        try:
            result = run_ffmpeg(
                ffmpeg_cmd,
                timeout=ff.timeout_seconds,
                fallback_preset=ff.fallback_preset,
            )
        except subprocess.CalledProcessError as exc:
            logger.error(f"FFmpeg failed:\n{(exc.stderr or '')[-2000:]}")
            raise RuntimeError(
                f"FFmpeg composition failed: {(exc.stderr or '')[-500:]}"
            ) from exc

        logger.info(f"[{self.branding.niche_id}] Rendered -> {output_path}")
        return output_path

    # --- Hook text wrapping -----------------------------------------------

    @staticmethod
    def _wrap_hook(text: str, max_chars: int = HOOK_MAX_CHARS_LINE) -> list[str]:
        """Word-wrap hook text into lines of at most max_chars characters."""
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            if current and len(current) + 1 + len(word) > max_chars:
                lines.append(current)
                current = word
            else:
                current = f"{current} {word}".strip()
        if current:
            lines.append(current)
        return lines[:HOOK_MAX_LINES]  # max 3 lines

    # --- Accent color helper ----------------------------------------------

    def _accent_hex(self) -> str:
        """Strip '#' from accent_color for FFmpeg."""
        return self.branding.accent_color.lstrip("#").lower()

    # --- Layout A: Landscape (ar >= 1.33) ---------------------------------

    def _build_cmd_landscape(
        self, src, hook, out, info, duration, trim_start, crf, preset, fps
    ) -> list[str]:
        """Landscape clip: video at y=466, hook text zone above, accent line separator."""

        logo_path = self.branding.logo_path
        channel_name = self.branding.channel_name
        handle = self.branding.handle
        safe_name = self._escape_drawtext(channel_name)
        safe_handle = self._escape_drawtext(handle)
        accent = self._accent_hex()

        dur_flags = self._duration_flags(duration)
        trim_flag = ["-ss", str(trim_start)] if trim_start > 0 else []

        font_bold, font_reg, font_hook = self._resolve_fonts()

        has_logo = logo_path and os.path.exists(logo_path)

        # Wrap hook text and compute vertical centering within hook zone
        hook_lines = self._wrap_hook(hook)
        num_lines = len(hook_lines)
        total_text_h = num_lines * HOOK_LINE_H
        hook_zone_center_y = L_HOOK_ZONE_Y + L_HOOK_ZONE_H // 2
        hook_start_y = hook_zone_center_y - total_text_h // 2

        # Build hook drawtext chain
        hook_filters = ""
        prev_label = "withhandle"
        for i, line in enumerate(hook_lines):
            safe_line = self._escape_drawtext(line)
            line_y = hook_start_y + i * HOOK_LINE_H
            out_label = f"hook{i}" if i < num_lines - 1 else "withhook"
            hook_filters += (
                f"[{prev_label}]drawtext=fontfile='{font_hook}':text='{safe_line}':"
                f"fontsize={HOOK_FONT_SIZE}:fontcolor=white:"
                f"x=(w-text_w)/2:y={line_y}:"
                f"shadowcolor=black@{SHADOW_OPACITY}:shadowx={SHADOW_OFFSET}:shadowy={SHADOW_OFFSET}"
                f"[{out_label}];"
            )
            prev_label = out_label

        if has_logo:
            filtergraph = (
                # Black canvas 1080x1920
                f"color=black:{CANVAS_W}x{CANVAS_H}:rate={fps}[canvas];"
                # Source video scaled to fit 1080x608 (maintain AR, pad)
                f"[0:v]scale={CANVAS_W}:{L_VIDEO_H}:force_original_aspect_ratio=decrease,"
                f"pad={CANVAS_W}:{L_VIDEO_H}:(ow-iw)/2:(oh-ih)/2:black[scaled];"
                # Place video at y=656
                f"[canvas][scaled]overlay=0:{L_VIDEO_Y}[base];"
                # Logo scaled to 56px
                f"[1:v]scale={LOGO_SIZE}:{LOGO_SIZE}[logo];"
                # Accent line at y=650
                f"[base]drawbox=x=0:y={L_ACCENT_Y}:w={CANVAS_W}:h={L_ACCENT_H}:"
                f"color=0x{accent}:t=fill[accented];"
                # Overlay logo
                f"[accented][logo]overlay={LOGO_X}:{LOGO_Y}[withlogo];"
                # Channel name
                f"[withlogo]drawtext=fontfile='{font_bold}':text='{safe_name}':"
                f"fontsize={NAME_FONT_SIZE}:fontcolor=white:x={NAME_X}:y={NAME_Y}[withname];"
                # Handle
                f"[withname]drawtext=fontfile='{font_reg}':text='{safe_handle}':"
                f"fontsize={HANDLE_FONT_SIZE}:fontcolor=white@{HANDLE_OPACITY}:"
                f"x={HANDLE_X}:y={HANDLE_Y}[withhandle];"
                # Hook lines
                f"{hook_filters}"
                # Final label rename
                f"[withhook]null[out]"
            )
            inputs = ["-i", src, "-i", logo_path]
        else:
            filtergraph = (
                f"color=black:{CANVAS_W}x{CANVAS_H}:rate={fps}[canvas];"
                f"[0:v]scale={CANVAS_W}:{L_VIDEO_H}:force_original_aspect_ratio=decrease,"
                f"pad={CANVAS_W}:{L_VIDEO_H}:(ow-iw)/2:(oh-ih)/2:black[scaled];"
                f"[canvas][scaled]overlay=0:{L_VIDEO_Y}[base];"
                f"[base]drawbox=x=0:y={L_ACCENT_Y}:w={CANVAS_W}:h={L_ACCENT_H}:"
                f"color=0x{accent}:t=fill[accented];"
                f"[accented]drawtext=fontfile='{font_bold}':text='{safe_name}':"
                f"fontsize={NAME_FONT_SIZE}:fontcolor=white:x={LOGO_X}:y={NAME_Y}[withname];"
                f"[withname]drawtext=fontfile='{font_reg}':text='{safe_handle}':"
                f"fontsize={HANDLE_FONT_SIZE}:fontcolor=white@{HANDLE_OPACITY}:"
                f"x={LOGO_X}:y={HANDLE_Y}[withhandle];"
                f"{hook_filters}"
                f"[withhook]null[out]"
            )
            inputs = ["-i", src]

        cmd = (
            ["ffmpeg", "-y"]
            + trim_flag
            + inputs
            + dur_flags
            + ["-filter_complex", filtergraph, "-map", "[out]", "-map", "0:a?"]
            + self._output_flags(crf, preset, fps)
            + [out]
        )
        return cmd

    # --- Layout B: Portrait (ar <= 0.75) ----------------------------------

    def _build_cmd_portrait(
        self, src, hook, out, info, duration, trim_start, crf, preset, fps
    ) -> list[str]:
        """Portrait clip: sandwich layout — black top bar with logo+hook, video in middle, black bottom.

        Video is scaled to fit within 1080xP_VIDEO_H maintaining aspect ratio, centered
        vertically with black letterbox bars if needed.  Same structure as landscape/square.
        """

        logo_path = self.branding.logo_path
        channel_name = self.branding.channel_name
        handle = self.branding.handle
        safe_name = self._escape_drawtext(channel_name)
        safe_handle = self._escape_drawtext(handle)
        accent = self._accent_hex()

        dur_flags = self._duration_flags(duration)
        trim_flag = ["-ss", str(trim_start)] if trim_start > 0 else []

        font_bold, font_reg, font_hook = self._resolve_fonts()

        has_logo = logo_path and os.path.exists(logo_path)

        # Wrap hook text and compute vertical centering within hook zone
        hook_lines = self._wrap_hook(hook)
        num_lines = len(hook_lines)
        total_text_h = num_lines * HOOK_LINE_H
        hook_zone_center_y = P_HOOK_ZONE_Y + P_HOOK_ZONE_H // 2
        hook_start_y = hook_zone_center_y - total_text_h // 2

        # Build hook drawtext chain
        hook_filters = ""
        prev_label = "withhandle"
        for i, line in enumerate(hook_lines):
            safe_line = self._escape_drawtext(line)
            line_y = hook_start_y + i * HOOK_LINE_H
            out_label = f"hook{i}" if i < num_lines - 1 else "withhook"
            hook_filters += (
                f"[{prev_label}]drawtext=fontfile='{font_hook}':text='{safe_line}':"
                f"fontsize={HOOK_FONT_SIZE}:fontcolor=white:"
                f"x=(w-text_w)/2:y={line_y}:"
                f"shadowcolor=black@{SHADOW_OPACITY}:shadowx={SHADOW_OFFSET}:shadowy={SHADOW_OFFSET}"
                f"[{out_label}];"
            )
            prev_label = out_label

        if has_logo:
            filtergraph = (
                # Black canvas 1080x1920
                f"color=black:{CANVAS_W}x{CANVAS_H}:rate={fps}[canvas];"
                # Source video scaled to fit 1080xP_VIDEO_H (maintain AR, letterbox)
                f"[0:v]scale={CANVAS_W}:{P_VIDEO_H}:force_original_aspect_ratio=decrease,"
                f"pad={CANVAS_W}:{P_VIDEO_H}:(ow-iw)/2:(oh-ih)/2:black[scaled];"
                # Place video at y=P_VIDEO_Y
                f"[canvas][scaled]overlay=0:{P_VIDEO_Y}[base];"
                # Logo scaled to LOGO_SIZE px
                f"[1:v]scale={LOGO_SIZE}:{LOGO_SIZE}[logo];"
                # Accent line at y=P_ACCENT_Y
                f"[base]drawbox=x=0:y={P_ACCENT_Y}:w={CANVAS_W}:h={P_ACCENT_H}:"
                f"color=0x{accent}:t=fill[accented];"
                # Overlay logo
                f"[accented][logo]overlay={LOGO_X}:{LOGO_Y}[withlogo];"
                # Channel name
                f"[withlogo]drawtext=fontfile='{font_bold}':text='{safe_name}':"
                f"fontsize={NAME_FONT_SIZE}:fontcolor=white:x={NAME_X}:y={NAME_Y}[withname];"
                # Handle
                f"[withname]drawtext=fontfile='{font_reg}':text='{safe_handle}':"
                f"fontsize={HANDLE_FONT_SIZE}:fontcolor=white@{HANDLE_OPACITY}:"
                f"x={HANDLE_X}:y={HANDLE_Y}[withhandle];"
                # Hook lines
                f"{hook_filters}"
                # Final label rename
                f"[withhook]null[out]"
            )
            inputs = ["-i", src, "-i", logo_path]
        else:
            filtergraph = (
                f"color=black:{CANVAS_W}x{CANVAS_H}:rate={fps}[canvas];"
                f"[0:v]scale={CANVAS_W}:{P_VIDEO_H}:force_original_aspect_ratio=decrease,"
                f"pad={CANVAS_W}:{P_VIDEO_H}:(ow-iw)/2:(oh-ih)/2:black[scaled];"
                f"[canvas][scaled]overlay=0:{P_VIDEO_Y}[base];"
                f"[base]drawbox=x=0:y={P_ACCENT_Y}:w={CANVAS_W}:h={P_ACCENT_H}:"
                f"color=0x{accent}:t=fill[accented];"
                f"[accented]drawtext=fontfile='{font_bold}':text='{safe_name}':"
                f"fontsize={NAME_FONT_SIZE}:fontcolor=white:x={LOGO_X}:y={NAME_Y}[withname];"
                f"[withname]drawtext=fontfile='{font_reg}':text='{safe_handle}':"
                f"fontsize={HANDLE_FONT_SIZE}:fontcolor=white@{HANDLE_OPACITY}:"
                f"x={LOGO_X}:y={HANDLE_Y}[withhandle];"
                f"{hook_filters}"
                f"[withhook]null[out]"
            )
            inputs = ["-i", src]

        cmd = (
            ["ffmpeg", "-y"]
            + trim_flag
            + inputs
            + dur_flags
            + ["-filter_complex", filtergraph, "-map", "[out]", "-map", "0:a?"]
            + self._output_flags(crf, preset, fps)
            + [out]
        )
        return cmd

    # --- Layout C: Square (0.75 < ar < 1.33) ------------------------------

    def _build_cmd_square(
        self, src, hook, out, info, duration, trim_start, crf, preset, fps
    ) -> list[str]:
        """Square-ish clip: video at y=466 (1080x1080), hook in 80px zone, accent line."""

        logo_path = self.branding.logo_path
        channel_name = self.branding.channel_name
        handle = self.branding.handle
        safe_name = self._escape_drawtext(channel_name)
        safe_handle = self._escape_drawtext(handle)
        accent = self._accent_hex()

        dur_flags = self._duration_flags(duration)
        trim_flag = ["-ss", str(trim_start)] if trim_start > 0 else []

        font_bold, font_reg, font_hook = self._resolve_fonts()

        has_logo = logo_path and os.path.exists(logo_path)

        # Wrap hook text and compute vertical centering within hook zone (80px)
        hook_lines = self._wrap_hook(hook)
        num_lines = len(hook_lines)
        total_text_h = num_lines * HOOK_LINE_H
        hook_zone_center_y = S_HOOK_ZONE_Y + S_HOOK_ZONE_H // 2
        hook_start_y = hook_zone_center_y - total_text_h // 2

        # Build hook drawtext chain
        hook_filters = ""
        prev_label = "withhandle"
        for i, line in enumerate(hook_lines):
            safe_line = self._escape_drawtext(line)
            line_y = hook_start_y + i * HOOK_LINE_H
            out_label = f"hook{i}" if i < num_lines - 1 else "withhook"
            hook_filters += (
                f"[{prev_label}]drawtext=fontfile='{font_hook}':text='{safe_line}':"
                f"fontsize={HOOK_FONT_SIZE}:fontcolor=white:"
                f"x=(w-text_w)/2:y={line_y}:"
                f"shadowcolor=black@{SHADOW_OPACITY}:shadowx={SHADOW_OFFSET}:shadowy={SHADOW_OFFSET}"
                f"[{out_label}];"
            )
            prev_label = out_label

        if has_logo:
            filtergraph = (
                # Black canvas 1080x1920
                f"color=black:{CANVAS_W}x{CANVAS_H}:rate={fps}[canvas];"
                # Source video scaled to fit 1080x1080 (maintain AR, pad)
                f"[0:v]scale={CANVAS_W}:{S_VIDEO_H}:force_original_aspect_ratio=decrease,"
                f"pad={CANVAS_W}:{S_VIDEO_H}:(ow-iw)/2:(oh-ih)/2:black[scaled];"
                # Place video at y=503
                f"[canvas][scaled]overlay=0:{S_VIDEO_Y}[base];"
                # Logo scaled to 56px
                f"[1:v]scale={LOGO_SIZE}:{LOGO_SIZE}[logo];"
                # Accent line at y=160
                f"[base]drawbox=x=0:y={S_ACCENT_Y}:w={CANVAS_W}:h={S_ACCENT_H}:"
                f"color=0x{accent}:t=fill[accented];"
                # Overlay logo
                f"[accented][logo]overlay={LOGO_X}:{LOGO_Y}[withlogo];"
                # Channel name
                f"[withlogo]drawtext=fontfile='{font_bold}':text='{safe_name}':"
                f"fontsize={NAME_FONT_SIZE}:fontcolor=white:x={NAME_X}:y={NAME_Y}[withname];"
                # Handle
                f"[withname]drawtext=fontfile='{font_reg}':text='{safe_handle}':"
                f"fontsize={HANDLE_FONT_SIZE}:fontcolor=white@{HANDLE_OPACITY}:"
                f"x={HANDLE_X}:y={HANDLE_Y}[withhandle];"
                # Hook lines
                f"{hook_filters}"
                # Final label rename
                f"[withhook]null[out]"
            )
            inputs = ["-i", src, "-i", logo_path]
        else:
            filtergraph = (
                f"color=black:{CANVAS_W}x{CANVAS_H}:rate={fps}[canvas];"
                f"[0:v]scale={CANVAS_W}:{S_VIDEO_H}:force_original_aspect_ratio=decrease,"
                f"pad={CANVAS_W}:{S_VIDEO_H}:(ow-iw)/2:(oh-ih)/2:black[scaled];"
                f"[canvas][scaled]overlay=0:{S_VIDEO_Y}[base];"
                f"[base]drawbox=x=0:y={S_ACCENT_Y}:w={CANVAS_W}:h={S_ACCENT_H}:"
                f"color=0x{accent}:t=fill[accented];"
                f"[accented]drawtext=fontfile='{font_bold}':text='{safe_name}':"
                f"fontsize={NAME_FONT_SIZE}:fontcolor=white:x={LOGO_X}:y={NAME_Y}[withname];"
                f"[withname]drawtext=fontfile='{font_reg}':text='{safe_handle}':"
                f"fontsize={HANDLE_FONT_SIZE}:fontcolor=white@{HANDLE_OPACITY}:"
                f"x={LOGO_X}:y={HANDLE_Y}[withhandle];"
                f"{hook_filters}"
                f"[withhook]null[out]"
            )
            inputs = ["-i", src]

        cmd = (
            ["ffmpeg", "-y"]
            + trim_flag
            + inputs
            + dur_flags
            + ["-filter_complex", filtergraph, "-map", "[out]", "-map", "0:a?"]
            + self._output_flags(crf, preset, fps)
            + [out]
        )
        return cmd

    # --- Helpers -------------------------------------------------------

    @staticmethod
    def _escape_drawtext(text: str) -> str:
        """Escape text for FFmpeg drawtext filter.

        Single quotes cannot be reliably escaped inside drawtext values
        delimited by '...' in a filter_complex string -- FFmpeg treats \\'
        as end-of-value.  Replace with Unicode RIGHT SINGLE QUOTATION MARK
        (U+2019) which is visually identical and harmless.
        """
        return (
            text
            .replace("\\", "\\\\")
            .replace("'", "\u2019")      # curly quote -- safe inside '...'
            .replace(":", "\\:")
            .replace("[", "\\[")
            .replace("]", "\\]")
            .replace(",", "\\,")
        )

    @staticmethod
    def _duration_flags(duration: Optional[float]) -> list[str]:
        if duration is not None and duration > 0:
            return ["-t", str(duration)]
        return []

    @staticmethod
    def _output_flags(crf: int, preset: str, fps: int) -> list[str]:
        return [
            "-c:v", "libx264",
            "-crf", str(crf),
            "-preset", preset,
            "-r", str(fps),
            "-c:a", "aac",
            "-b:a", "320k",
            "-ar", "48000",
            "-ac", "2",
            "-pix_fmt", "yuv420p",
            "-colorspace", "bt709",
            "-color_primaries", "bt709",
            "-color_trc", "bt709",
            "-movflags", "+faststart",
        ]

    @staticmethod
    def _resolve_fonts() -> tuple[str, str, str]:
        """Return (bold, regular, hook) font paths for the current platform."""
        mac_bold = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
        mac_reg = "/System/Library/Fonts/Supplemental/Arial.ttf"
        if os.path.exists(mac_bold):
            return mac_bold, mac_reg, mac_bold
        # Linux fallback
        linux_bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        linux_reg = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        return linux_bold, linux_reg, linux_bold
