"""Frame compositor for Gen Lab video reels.

Implements the canonical three-case frame layout for all 5 channels.
This is the ONLY place that frame composition logic lives.
All channels call this. Zero per-channel compositor divergence allowed.

THE LOCKED SPEC
───────────────
Canvas: 1080 x 1920 (9:16 portrait, always)

LANDSCAPE (source aspect ratio >= 1.33):
  Video 1080x608, VERTICALLY CENTERED at y=656.
  Branding above video: logo(60px) + channel name + handle + hook text
  (44px bold white, max 2 lines) + accent line (6px).
  Black fill above branding and below video.

PORTRAIT (source aspect ratio <= 0.75):
  Clean full-screen video — fills entire 1080x1920 canvas.
  NO branding, NO hook overlay, NO accent line burned into video.
  Branding comes from post caption text only.

SQUARE (source aspect ratio 0.75 to 1.33):
  Video 1080x1080, VERTICALLY CENTERED at y=420.
  Branding above video: logo(60px) + channel name + handle + hook text
  (44px bold white, max 2 lines) + accent line (6px).
  Black fill above branding and below video.

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

# Layout A: Landscape — video VERTICALLY CENTERED, branding above it
L_VIDEO_H = 608               # 16:9 at 1080w = 608h
L_VIDEO_Y = (CANVAS_H - L_VIDEO_H) // 2   # 656 — centered vertically
L_HOOK_Y = L_VIDEO_Y - 120    # hook text above video with 16px clear gap
L_LOGO_Y = L_HOOK_Y - 150     # logo/name above hook with breathing room

# Layout B: Portrait — clean full-screen video, NO branding/hook overlay
# Portrait videos fill the entire 1080x1920 canvas with zero overlays.
# Branding comes from the caption/post text, not burned into the video.

# Layout C: Square — video vertically centered, branding above
S_VIDEO_H = 1080              # 1080x1080 square video
S_VIDEO_Y = (CANVAS_H - S_VIDEO_H) // 2   # 420 — centered vertically
S_HOOK_Y = S_VIDEO_Y - 120
S_LOGO_Y = S_HOOK_Y - 150

# Shared branding
LOGO_SIZE = 60
LOGO_X = 45
NAME_FONT_SIZE = 24
NAME_X = 120
HANDLE_FONT_SIZE = 17
HANDLE_X = 120
HANDLE_OPACITY = 0.70

# Hook text — CENTER-ALIGNED horizontally
HOOK_FONT_SIZE = 44
HOOK_LINE_H = 52
HOOK_MAX_LINES = 2
HOOK_MAX_CHARS_LINE = 35
SHADOW_OFFSET = 2
SHADOW_OPACITY = 0.50

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
    def from_visuals_yaml(cls, path: str) -> ChannelBranding:
        """Load branding from a channel's visuals.yaml."""
        yaml_path = Path(path).resolve()
        niche_root = yaml_path.parent.parent  # config/ -> niche_root/

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

        # Resolve logo path — relative paths are resolved against niche_root
        raw_logo = (
            fl_branding.get("logo_path")
            or branding.get("logo_path")
            or branding.get("logo", "")
        )
        if raw_logo and not Path(raw_logo).is_absolute():
            raw_logo = str((niche_root / raw_logo).resolve())

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
            logo_path=raw_logo,
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
    def from_visuals_yaml(cls, visuals_yaml_path: str) -> FrameCompositor:
        branding = ChannelBranding.from_visuals_yaml(visuals_yaml_path)
        return cls(branding)

    def compose(
        self,
        source_video_path: str,
        hook_text: str,
        output_path: str,
        duration_seconds: float | None = None,
        trim_start: float = 0.0,
        crf: int = 15,
        preset: str | None = None,
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

        # Sub-item D: Skip first 10% of long clips (intros, logos, "hey guys")
        if info.duration_seconds > 45 and trim_start == 0:
            skip = info.duration_seconds * 0.10
            trim_start = round(skip, 1)
            logger.debug(
                "[%s] Skipping first %.1fs (10%%) of %.1fs clip for stronger opening",
                self.branding.niche_id, trim_start, info.duration_seconds,
            )

        # Enforce minimum duration (15s for reels)
        effective_duration = info.duration_seconds - trim_start
        if duration_seconds:
            effective_duration = min(effective_duration, duration_seconds)
        if effective_duration < 15:
            logger.warning(
                f"[{self.branding.niche_id}] Source clip too short: {effective_duration:.1f}s "
                f"(minimum 15s) — {source_video_path}"
            )

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
        return lines[:HOOK_MAX_LINES]  # max 2 lines

    # --- Accent color helper ----------------------------------------------

    def _accent_hex(self) -> str:
        """Strip '#' from accent_color for FFmpeg."""
        return self.branding.accent_color.lstrip("#").lower()

    # --- Shared: build center-aligned hook drawtext chain --------------------

    def _build_hook_filters(self, hook: str, hook_y: int, font_hook: str, prev_label: str) -> tuple[str, str]:
        """Build FFmpeg drawtext filters for center-aligned hook text.

        Returns (filter_chain_str, final_label).
        """
        hook_lines = self._wrap_hook(hook)
        if not hook_lines:
            # Empty hook — pass through to keep the filtergraph connected
            return f"[{prev_label}]copy[withhook];", "withhook"
        num_lines = len(hook_lines)
        filters = ""
        for i, line in enumerate(hook_lines):
            safe_line = self._escape_drawtext(line)
            line_y = hook_y + i * HOOK_LINE_H
            out_label = f"hook{i}" if i < num_lines - 1 else "withhook"
            filters += (
                f"[{prev_label}]drawtext=fontfile='{font_hook}':text='{safe_line}':"
                f"fontsize={HOOK_FONT_SIZE}:fontcolor=white:"
                f"x=(w-text_w)/2:y={line_y}:"
                f"shadowcolor=black@{SHADOW_OPACITY}:shadowx={SHADOW_OFFSET}:shadowy={SHADOW_OFFSET}"
                f"[{out_label}];"
            )
            prev_label = out_label
        return filters, "withhook"

    # --- Shared: build branding filters (logo + name + handle) ------------

    def _build_branding_filters(self, font_bold: str, font_reg: str,
                                 logo_y: int, base_label: str) -> tuple[str, str]:
        """Build FFmpeg filters for logo + channel name + handle.

        Returns (filter_chain_str, final_label).
        """
        safe_name = self._escape_drawtext(self.branding.channel_name)
        safe_handle = self._escape_drawtext(self.branding.handle)
        name_y = logo_y + 12
        handle_y = logo_y + 36

        has_logo = self.branding.logo_path and os.path.exists(self.branding.logo_path)

        if has_logo:
            filters = (
                # Logo scaled to target size
                f"[1:v]scale={LOGO_SIZE}:{LOGO_SIZE}[logo];"
                # Subtle white backing behind logo for visibility on black
                f"[{base_label}]drawbox=x={LOGO_X-4}:y={logo_y-4}:"
                f"w={LOGO_SIZE+8}:h={LOGO_SIZE+8}:"
                f"color=white@0.12:t=fill[withbg];"
                # Overlay logo on top of backing
                f"[withbg][logo]overlay={LOGO_X}:{logo_y}[withlogo];"
                f"[withlogo]drawtext=fontfile='{font_bold}':text='{safe_name}':"
                f"fontsize={NAME_FONT_SIZE}:fontcolor=white:x={NAME_X}:y={name_y}[withname];"
                f"[withname]drawtext=fontfile='{font_reg}':text='{safe_handle}':"
                f"fontsize={HANDLE_FONT_SIZE}:fontcolor=white@{HANDLE_OPACITY}:"
                f"x={HANDLE_X}:y={handle_y}[withhandle];"
            )
        else:
            filters = (
                f"[{base_label}]drawtext=fontfile='{font_bold}':text='{safe_name}':"
                f"fontsize={NAME_FONT_SIZE}:fontcolor=white:x={LOGO_X}:y={name_y}[withname];"
                f"[withname]drawtext=fontfile='{font_reg}':text='{safe_handle}':"
                f"fontsize={HANDLE_FONT_SIZE}:fontcolor=white@{HANDLE_OPACITY}:"
                f"x={LOGO_X}:y={handle_y}[withhandle];"
            )
        return filters, "withhandle"

    # --- Layout A: Landscape (ar >= 1.33) ---------------------------------

    def _build_cmd_landscape(
        self, src, hook, out, info, duration, trim_start, crf, preset, fps
    ) -> list[str]:
        """Landscape clip: branding header + center-aligned hook + flush video."""
        dur_flags = self._duration_flags(duration)
        trim_flag = ["-ss", str(trim_start)] if trim_start > 0 else []
        font_bold, font_reg, font_hook = self._resolve_fonts()
        has_logo = self.branding.logo_path and os.path.exists(self.branding.logo_path)

        # Video scaled to canvas width, top-aligned (no centering gap)
        video_filter = (
            f"color=black:{CANVAS_W}x{CANVAS_H}:rate={fps}[canvas];"
            f"[0:v]scale={CANVAS_W}:{L_VIDEO_H}:force_original_aspect_ratio=decrease,"
            f"pad={CANVAS_W}:{L_VIDEO_H}:(ow-iw)/2:0:black[scaled];"
            f"[canvas][scaled]overlay=0:{L_VIDEO_Y}[base];"
        )

        branding, _ = self._build_branding_filters(font_bold, font_reg, L_LOGO_Y, "base")
        hooks, _ = self._build_hook_filters(hook, L_HOOK_Y, font_hook, "withhandle")

        # Sub-item C: Subtle brightness flash in first 0.07s (pattern interrupt)
        flash = "[withhook]eq=brightness='if(lt(t,0.07),0.08,0)':eval=frame[out]"
        filtergraph = f"{video_filter}{branding}{hooks}{flash}"
        inputs = ["-i", src] + (["-i", self.branding.logo_path] if has_logo else [])

        return (
            ["ffmpeg", "-y"] + trim_flag + inputs + dur_flags
            + ["-filter_complex", filtergraph, "-map", "[out]", "-map", "0:a?"]
            + self._output_flags(crf, preset, fps) + [out]
        )

    # --- Layout B: Portrait (ar <= 0.75) ----------------------------------

    def _build_cmd_portrait(
        self, src, hook, out, info, duration, trim_start, crf, preset, fps
    ) -> list[str]:
        """Portrait clip: clean full-screen video, NO branding or hook overlay.

        Portrait sources (9:16) fill the entire 1080x1920 canvas.
        No logo, no text, no gradient — just the video.
        """
        dur_flags = self._duration_flags(duration)
        trim_flag = ["-ss", str(trim_start)] if trim_start > 0 else []

        filtergraph = (
            f"[0:v]scale={CANVAS_W}:{CANVAS_H}:"
            f"force_original_aspect_ratio=increase,"
            f"crop={CANVAS_W}:{CANVAS_H}[out]"
        )

        return (
            ["ffmpeg", "-y"] + trim_flag + ["-i", src] + dur_flags
            + ["-filter_complex", filtergraph, "-map", "[out]", "-map", "0:a?"]
            + self._output_flags(crf, preset, fps) + [out]
        )

    # --- Layout C: Square (0.75 < ar < 1.33) ------------------------------

    def _build_cmd_square(
        self, src, hook, out, info, duration, trim_start, crf, preset, fps
    ) -> list[str]:
        """Square clip: same branding header as landscape, 1080x1080 video below."""
        dur_flags = self._duration_flags(duration)
        trim_flag = ["-ss", str(trim_start)] if trim_start > 0 else []
        font_bold, font_reg, font_hook = self._resolve_fonts()
        has_logo = self.branding.logo_path and os.path.exists(self.branding.logo_path)

        video_filter = (
            f"color=black:{CANVAS_W}x{CANVAS_H}:rate={fps}[canvas];"
            f"[0:v]scale={CANVAS_W}:{S_VIDEO_H}:force_original_aspect_ratio=decrease,"
            f"pad={CANVAS_W}:{S_VIDEO_H}:(ow-iw)/2:0:black[scaled];"
            f"[canvas][scaled]overlay=0:{S_VIDEO_Y}[base];"
        )

        branding, _ = self._build_branding_filters(font_bold, font_reg, S_LOGO_Y, "base")
        hooks, _ = self._build_hook_filters(hook, S_HOOK_Y, font_hook, "withhandle")

        # Sub-item C: Subtle brightness flash in first 0.07s (pattern interrupt)
        flash = "[withhook]eq=brightness='if(lt(t,0.07),0.08,0)':eval=frame[out]"
        filtergraph = f"{video_filter}{branding}{hooks}{flash}"
        inputs = ["-i", src] + (["-i", self.branding.logo_path] if has_logo else [])

        return (
            ["ffmpeg", "-y"] + trim_flag + inputs + dur_flags
            + ["-filter_complex", filtergraph, "-map", "[out]", "-map", "0:a?"]
            + self._output_flags(crf, preset, fps) + [out]
        )

    # --- Overlay branding on already-composed video ----------------------

    def overlay_branding(
        self,
        source_video_path: str,
        hook_text: str,
        output_path: str,
        duration_seconds: float | None = None,
        crf: int = 15,
        preset: str | None = None,
    ) -> str:
        """Burn logo + channel name + handle + hook onto an already-composed video.

        Unlike compose(), this does NOT re-layout the video — it overlays
        branding elements directly on top. Use for compilation videos that
        are already 1080x1920 but need channel branding burned in.

        Uses the LANDSCAPE branding positions (logo+name above center,
        hook text below) since compilations use landscape-style centering.
        """
        ff = self.branding.ffmpeg
        if preset is None:
            preset = ff.preset

        if len(hook_text) > HOOK_MAX_CHARS:
            hook_text = hook_text[:HOOK_MAX_CHARS - 3] + "..."

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        font_bold, font_reg, font_hook = self._resolve_fonts()
        has_logo = self.branding.logo_path and os.path.exists(self.branding.logo_path)
        dur_flags = self._duration_flags(duration_seconds)

        # Build branding filters on top of the source video
        branding, _ = self._build_branding_filters(
            font_bold, font_reg, L_LOGO_Y, "base"
        )
        hooks, _ = self._build_hook_filters(hook_text, L_HOOK_Y, font_hook, "withhandle")
        flash = "[withhook]eq=brightness='if(lt(t,0.07),0.08,0)':eval=frame[out]"

        # Source video becomes [base] directly (no re-layout)
        video_filter = "[0:v]copy[base];"
        filtergraph = f"{video_filter}{branding}{hooks}{flash}"

        inputs = ["-i", source_video_path]
        if has_logo:
            inputs += ["-i", self.branding.logo_path]

        cmd = (
            ["ffmpeg", "-y"] + inputs + dur_flags
            + ["-filter_complex", filtergraph, "-map", "[out]", "-map", "0:a?"]
            + self._output_flags(crf, preset, 30) + [output_path]
        )

        logger.info(
            f"[{self.branding.niche_id}] Overlaying branding on compilation: "
            f"{' '.join(cmd[:8])}..."
        )
        try:
            run_ffmpeg(cmd, timeout=ff.timeout_seconds, fallback_preset=ff.fallback_preset)
        except subprocess.CalledProcessError as exc:
            logger.error(f"FFmpeg branding overlay failed:\n{(exc.stderr or '')[-2000:]}")
            raise RuntimeError(
                f"FFmpeg branding overlay failed: {(exc.stderr or '')[-500:]}"
            ) from exc

        logger.info(f"[{self.branding.niche_id}] Branded -> {output_path}")
        return output_path

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
            .replace("%", "%%")
            .replace(":", "\\:")
            .replace("[", "\\[")
            .replace("]", "\\]")
            .replace(",", "\\,")
            .replace(";", "\\;")
        )

    @staticmethod
    def _duration_flags(duration: float | None) -> list[str]:
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
        """Return (bold, regular, hook) font paths.

        Prefers Inter (brand font in genlab-core/assets/fonts/).
        Falls back to system fonts if Inter is not available.
        Inter Variable supports all weights — used for bold, regular, and hook.
        """
        # Brand font: Inter (variable font — supports all weights via FFmpeg)
        gc_root = Path(__file__).resolve().parents[4]  # genlab-core/
        inter = gc_root / "genlab-core" / "assets" / "fonts" / "Inter.ttf"
        if inter.exists():
            inter_str = str(inter)
            return inter_str, inter_str, inter_str

        # macOS fallback
        mac_bold = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
        mac_reg = "/System/Library/Fonts/Supplemental/Arial.ttf"
        if os.path.exists(mac_bold):
            return mac_bold, mac_reg, mac_bold

        # Linux fallback
        linux_bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        linux_reg = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        return linux_bold, linux_reg, linux_bold
