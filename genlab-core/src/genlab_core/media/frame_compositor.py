"""Frame compositor for Gen Lab video reels.

Implements the canonical two-case frame layout for all 5 channels.
This is the ONLY place that frame composition logic lives.
All channels call this. Zero per-channel compositor divergence allowed.

THE LOCKED SPEC
───────────────
Canvas: 1080 x 1920 (9:16 portrait, always)

TOP BAR (both cases):
  Solid black rectangle painted ON TOP of the frame.
  Height: 180px (y=0 to y=180)
  Logo: 48px tall, x=30, vertically centred (y_center=90)
  Channel name: 24px bold white, x=logo_right+14, y~82
  Handle: 17px regular #AAAAAA, below name

CASE 1 -- Native 9:16 source (aspect ~ 0.56):
  Video fills full 1080x1920 canvas (scaled/cropped).
  NO bottom safe zone -- video runs edge-to-edge.
  Top bar painted ON TOP of video.
  Hook text drawn over video, y~200-280.

CASE 2 -- 16:9 source (landscape, aspect > 1.0):
  Black canvas. Video scaled to 1080px wide -> 608px tall.
  Video centered: y_start=656, y_end=1264.
  Hook text centered in black zone: y=180 to y=656 (476px).
  Bottom black zone: y=1264 to y=1920 (656px, satisfies all platform safe zones).

CASE 3 -- Other (square, unusual vertical):
  Scale to fit, pillarbox/letterbox with black.
  Same top bar and hook zone treatment as Case 2.

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

logger = logging.getLogger(__name__)

# -------------------------------------------------------------
# Canvas constants -- LOCKED. Do not make these configurable.
# -------------------------------------------------------------
CANVAS_W = 1080
CANVAS_H = 1920

TOP_BAR_H = 180          # px -- height of black top bar
LOGO_H    = 48           # px -- logo height inside bar
LOGO_X    = 30           # px -- logo left edge
LOGO_Y    = int((TOP_BAR_H - LOGO_H) / 2)   # vertically centred in bar = 66
NAME_X    = LOGO_X       # resolved to LOGO_X + logo_width + 14 at render time
NAME_Y    = 82           # px -- channel name baseline
HANDLE_Y  = NAME_Y + 22  # px -- handle text baseline

# Hook text zone (Case 1 -- painted over video)
HOOK_Y_NATIVE = 220      # px -- vertical centre of hook text for 9:16 clips

# 16:9 derived constants
VIDEO_W_LANDSCAPE = CANVAS_W                          # 1080
VIDEO_H_LANDSCAPE = int(CANVAS_W * 9 / 16)            # 608
VIDEO_Y_LANDSCAPE = int((CANVAS_H - VIDEO_H_LANDSCAPE) / 2)   # 656
HOOK_Y_LANDSCAPE  = int(TOP_BAR_H + (VIDEO_Y_LANDSCAPE - TOP_BAR_H) / 2)  # ~418

# Aspect ratio decision boundary
# Native 9:16 = 0.5625. We call anything under 0.65 "portrait" (Case 1/3).
# 16:9 = 1.778. Anything over 0.9 is "landscape" (Case 2).
PORTRAIT_THRESHOLD  = 0.65   # w/h < this -> portrait (Case 1 if ~9:16, else Case 3)
LANDSCAPE_THRESHOLD = 0.90   # w/h > this -> landscape -> Case 2

FONT_NAME_SIZE    = 24    # px
FONT_HANDLE_SIZE  = 17    # px
FONT_HOOK_SIZE    = 40    # px -- hook text on video
HOOK_MAX_CHARS    = 60    # characters -- enforced upstream, checked here too

# Colours
BLACK = "0x000000"
WHITE = "0xFFFFFF"
GREY_HANDLE = "0xAAAAAA"


# -------------------------------------------------------------
# Config
# -------------------------------------------------------------

@dataclass
class ChannelBranding:
    """Per-channel branding loaded from visuals.yaml."""
    channel_name: str           # e.g. "CriticalRush"
    handle: str                 # e.g. "@CriticalRush"
    accent_color: str           # e.g. "#00FF88" (for future tinted elements)
    logo_path: str              # absolute or relative path to logo PNG
    niche_id: str               # e.g. "gaming"

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
    is_portrait: bool         # aspect_ratio < PORTRAIT_THRESHOLD
    is_landscape: bool        # aspect_ratio > LANDSCAPE_THRESHOLD
    is_native_9_16: bool      # close to 9:16 portrait

    @property
    def layout_case(self) -> int:
        """1 = native 9:16, 2 = landscape 16:9, 3 = other"""
        if self.is_native_9_16:
            return 1
        if self.is_landscape:
            return 2
        return 3


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

    is_portrait = ar < PORTRAIT_THRESHOLD
    is_landscape = ar > LANDSCAPE_THRESHOLD
    # Native 9:16: aspect between 0.50 and 0.65 (portrait phone video)
    is_native_9_16 = 0.50 <= ar <= PORTRAIT_THRESHOLD

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
        preset: str = "slow",
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
            preset: FFmpeg preset (slow for best quality).
            force_fps: Output frame rate.

        Returns:
            output_path on success.

        Raises:
            RuntimeError: if FFmpeg fails.
        """
        # Validate hook length
        if len(hook_text) > HOOK_MAX_CHARS:
            logger.warning(
                f"Hook '{hook_text[:30]}...' is {len(hook_text)} chars -- truncating to {HOOK_MAX_CHARS}"
            )
            hook_text = hook_text[:HOOK_MAX_CHARS - 3] + "..."

        # Probe source
        info = probe_video(source_video_path)
        logger.info(
            f"[{self.branding.niche_id}] Source: {info.width}x{info.height} "
            f"ar={info.aspect_ratio:.3f} -> Case {info.layout_case} "
            f"({'native 9:16' if info.layout_case == 1 else '16:9' if info.layout_case == 2 else 'other'})"
        )

        # Ensure output directory exists
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        if info.layout_case == 1:
            ffmpeg_cmd = self._build_cmd_case1(
                source_video_path, hook_text, output_path,
                info, duration_seconds, trim_start, crf, preset, force_fps,
            )
        elif info.layout_case == 2:
            ffmpeg_cmd = self._build_cmd_case2(
                source_video_path, hook_text, output_path,
                info, duration_seconds, trim_start, crf, preset, force_fps,
            )
        else:
            # Case 3: scale to fit, black bars, same treatment as Case 2
            ffmpeg_cmd = self._build_cmd_case3(
                source_video_path, hook_text, output_path,
                info, duration_seconds, trim_start, crf, preset, force_fps,
            )

        logger.info(f"[{self.branding.niche_id}] Running FFmpeg: {' '.join(ffmpeg_cmd[:8])}...")
        result = subprocess.run(
            ffmpeg_cmd,
            capture_output=True, text=True, timeout=300,
        )

        if result.returncode != 0:
            logger.error(f"FFmpeg failed:\n{result.stderr[-2000:]}")
            raise RuntimeError(f"FFmpeg composition failed: {result.stderr[-500:]}")

        logger.info(f"[{self.branding.niche_id}] Rendered -> {output_path}")
        return output_path

    # --- Case 1: Native 9:16 -- video fills full canvas ----------------

    def _build_cmd_case1(
        self, src, hook, out, info, duration, trim_start, crf, preset, fps
    ) -> list[str]:
        """Native 9:16 clip: video IS the canvas. Top bar + hook painted on top."""

        logo_path = self.branding.logo_path
        channel_name = self.branding.channel_name
        handle = self.branding.handle
        safe_hook = self._escape_drawtext(hook)
        safe_name = self._escape_drawtext(channel_name)
        safe_handle = self._escape_drawtext(handle)

        # Duration flags
        dur_flags = self._duration_flags(duration)
        trim_flag = ["-ss", str(trim_start)] if trim_start > 0 else []

        logo_x = LOGO_X
        logo_y = LOGO_Y
        name_x = f"{logo_x}+{LOGO_H}+14"   # logo_x + logo_width + 14
        name_y = NAME_Y
        handle_y = HANDLE_Y
        hook_y = HOOK_Y_NATIVE
        bar_h = TOP_BAR_H

        font_bold, font_reg, font_hook = self._resolve_fonts()

        has_logo = logo_path and os.path.exists(logo_path)

        if has_logo:
            filtergraph = (
                # Input 0 = source video, scale to fill 1080x1920
                f"[0:v]scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
                f"crop={CANVAS_W}:{CANVAS_H}[base];"
                # Input 1 = logo, scale to LOGO_H
                f"[1:v]scale=-1:{LOGO_H}[logo];"
                # Paint black top bar on base
                f"[base]drawbox=x=0:y=0:w={CANVAS_W}:h={bar_h}:color=black:t=fill[barred];"
                # Overlay logo
                f"[barred][logo]overlay={logo_x}:{logo_y}[withlogo];"
                # Draw channel name
                f"[withlogo]drawtext=fontfile='{font_bold}':text='{safe_name}':"
                f"fontsize={FONT_NAME_SIZE}:fontcolor=white:x={name_x}:y={name_y}[withname];"
                # Draw handle
                f"[withname]drawtext=fontfile='{font_reg}':text='{safe_handle}':"
                f"fontsize={FONT_HANDLE_SIZE}:fontcolor=0xAAAAAA:x={name_x}:y={handle_y}[withhandle];"
                # Draw hook text (centred horizontally, at hook_y)
                f"[withhandle]drawtext=fontfile='{font_hook}':text='{safe_hook}':"
                f"fontsize={FONT_HOOK_SIZE}:fontcolor=white:"
                f"x=(w-text_w)/2:y={hook_y}:"
                f"shadowcolor=black:shadowx=2:shadowy=2[out]"
            )
            inputs = ["-i", src, "-i", logo_path]
        else:
            filtergraph = (
                f"[0:v]scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
                f"crop={CANVAS_W}:{CANVAS_H}[base];"
                f"[base]drawbox=x=0:y=0:w={CANVAS_W}:h={bar_h}:color=black:t=fill[barred];"
                f"[barred]drawtext=fontfile='{font_bold}':text='{safe_name}':"
                f"fontsize={FONT_NAME_SIZE}:fontcolor=white:x={LOGO_X}:y={name_y}[withname];"
                f"[withname]drawtext=fontfile='{font_reg}':text='{safe_handle}':"
                f"fontsize={FONT_HANDLE_SIZE}:fontcolor=0xAAAAAA:x={LOGO_X}:y={handle_y}[withhandle];"
                f"[withhandle]drawtext=fontfile='{font_hook}':text='{safe_hook}':"
                f"fontsize={FONT_HOOK_SIZE}:fontcolor=white:"
                f"x=(w-text_w)/2:y={hook_y}:"
                f"shadowcolor=black:shadowx=2:shadowy=2[out]"
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

    # --- Case 2: 16:9 clip -- centred on black canvas -----------------

    def _build_cmd_case2(
        self, src, hook, out, info, duration, trim_start, crf, preset, fps
    ) -> list[str]:
        """16:9 clip: scale to 1080 wide, centre on 1080x1920 black canvas."""

        logo_path = self.branding.logo_path
        channel_name = self.branding.channel_name
        handle = self.branding.handle
        safe_hook = self._escape_drawtext(hook)
        safe_name = self._escape_drawtext(channel_name)
        safe_handle = self._escape_drawtext(handle)

        dur_flags = self._duration_flags(duration)
        trim_flag = ["-ss", str(trim_start)] if trim_start > 0 else []

        bar_h     = TOP_BAR_H
        vid_h     = VIDEO_H_LANDSCAPE     # 608
        vid_y     = VIDEO_Y_LANDSCAPE     # 656
        hook_y    = HOOK_Y_LANDSCAPE      # ~418 -- centred in hook zone
        logo_x    = LOGO_X
        logo_y    = LOGO_Y
        name_x    = f"{logo_x}+{LOGO_H}+14"
        name_y    = NAME_Y
        handle_y  = HANDLE_Y

        font_bold, font_reg, font_hook = self._resolve_fonts()

        has_logo = logo_path and os.path.exists(logo_path)

        if has_logo:
            filtergraph = (
                # Black canvas 1080x1920
                f"color=black:{CANVAS_W}x{CANVAS_H}:rate={fps}[canvas];"
                # Source video scaled to 1080 wide
                f"[0:v]scale={CANVAS_W}:{vid_h}[scaled];"
                # Place video at y=vid_y
                f"[canvas][scaled]overlay=0:{vid_y}[base];"
                # Logo
                f"[1:v]scale=-1:{LOGO_H}[logo];"
                # Top bar is already black (canvas) -- draw name/handle directly
                f"[base][logo]overlay={logo_x}:{logo_y}[withlogo];"
                f"[withlogo]drawtext=fontfile='{font_bold}':text='{safe_name}':"
                f"fontsize={FONT_NAME_SIZE}:fontcolor=white:x={name_x}:y={name_y}[withname];"
                f"[withname]drawtext=fontfile='{font_reg}':text='{safe_handle}':"
                f"fontsize={FONT_HANDLE_SIZE}:fontcolor=0xAAAAAA:x={name_x}:y={handle_y}[withhandle];"
                # Hook text in hook zone (centred horizontally, centred vertically in zone)
                f"[withhandle]drawtext=fontfile='{font_hook}':text='{safe_hook}':"
                f"fontsize={FONT_HOOK_SIZE}:fontcolor=white:"
                f"x=(w-text_w)/2:y={hook_y}:"
                f"shadowcolor=black:shadowx=2:shadowy=2[out]"
            )
            inputs = ["-i", src, "-i", logo_path]
        else:
            filtergraph = (
                f"color=black:{CANVAS_W}x{CANVAS_H}:rate={fps}[canvas];"
                f"[0:v]scale={CANVAS_W}:{vid_h}[scaled];"
                f"[canvas][scaled]overlay=0:{vid_y}[base];"
                f"[base]drawtext=fontfile='{font_bold}':text='{safe_name}':"
                f"fontsize={FONT_NAME_SIZE}:fontcolor=white:x={LOGO_X}:y={name_y}[withname];"
                f"[withname]drawtext=fontfile='{font_reg}':text='{safe_handle}':"
                f"fontsize={FONT_HANDLE_SIZE}:fontcolor=0xAAAAAA:x={LOGO_X}:y={handle_y}[withhandle];"
                f"[withhandle]drawtext=fontfile='{font_hook}':text='{safe_hook}':"
                f"fontsize={FONT_HOOK_SIZE}:fontcolor=white:"
                f"x=(w-text_w)/2:y={hook_y}:"
                f"shadowcolor=black:shadowx=2:shadowy=2[out]"
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

    # --- Case 3: Other aspect ratios ----------------------------------

    def _build_cmd_case3(
        self, src, hook, out, info, duration, trim_start, crf, preset, fps
    ) -> list[str]:
        """Scale to fit with black pillarbox/letterbox, then same overlay as Case 2."""

        logo_path = self.branding.logo_path
        channel_name = self.branding.channel_name
        handle = self.branding.handle
        safe_hook = self._escape_drawtext(hook)
        safe_name = self._escape_drawtext(channel_name)
        safe_handle = self._escape_drawtext(handle)

        dur_flags = self._duration_flags(duration)
        trim_flag = ["-ss", str(trim_start)] if trim_start > 0 else []

        bar_h    = TOP_BAR_H
        logo_x   = LOGO_X
        logo_y   = LOGO_Y
        name_x   = f"{logo_x}+{LOGO_H}+14"
        name_y   = NAME_Y
        handle_y = HANDLE_Y
        # Hook y: below top bar, with some padding
        hook_y   = bar_h + 60

        font_bold, font_reg, font_hook = self._resolve_fonts()

        has_logo = logo_path and os.path.exists(logo_path)

        scale_pad = (
            f"[0:v]scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=decrease,"
            f"pad={CANVAS_W}:{CANVAS_H}:(ow-iw)/2:(oh-ih)/2:black[padded]"
        )

        if has_logo:
            filtergraph = (
                f"{scale_pad};"
                f"[1:v]scale=-1:{LOGO_H}[logo];"
                f"[padded]drawbox=x=0:y=0:w={CANVAS_W}:h={bar_h}:color=black:t=fill[barred];"
                f"[barred][logo]overlay={logo_x}:{logo_y}[withlogo];"
                f"[withlogo]drawtext=fontfile='{font_bold}':text='{safe_name}':"
                f"fontsize={FONT_NAME_SIZE}:fontcolor=white:x={name_x}:y={name_y}[withname];"
                f"[withname]drawtext=fontfile='{font_reg}':text='{safe_handle}':"
                f"fontsize={FONT_HANDLE_SIZE}:fontcolor=0xAAAAAA:x={name_x}:y={handle_y}[withhandle];"
                f"[withhandle]drawtext=fontfile='{font_hook}':text='{safe_hook}':"
                f"fontsize={FONT_HOOK_SIZE}:fontcolor=white:"
                f"x=(w-text_w)/2:y={hook_y}:"
                f"shadowcolor=black:shadowx=2:shadowy=2[out]"
            )
            inputs = ["-i", src, "-i", logo_path]
        else:
            filtergraph = (
                f"{scale_pad};"
                f"[padded]drawbox=x=0:y=0:w={CANVAS_W}:h={bar_h}:color=black:t=fill[barred];"
                f"[barred]drawtext=fontfile='{font_bold}':text='{safe_name}':"
                f"fontsize={FONT_NAME_SIZE}:fontcolor=white:x={LOGO_X}:y={name_y}[withname];"
                f"[withname]drawtext=fontfile='{font_reg}':text='{safe_handle}':"
                f"fontsize={FONT_HANDLE_SIZE}:fontcolor=0xAAAAAA:x={LOGO_X}:y={handle_y}[withhandle];"
                f"[withhandle]drawtext=fontfile='{font_hook}':text='{safe_hook}':"
                f"fontsize={FONT_HOOK_SIZE}:fontcolor=white:"
                f"x=(w-text_w)/2:y={hook_y}:"
                f"shadowcolor=black:shadowx=2:shadowy=2[out]"
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
        """Escape text for FFmpeg drawtext filter."""
        return (
            text
            .replace("\\", "\\\\")
            .replace("'", "\\'")
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
