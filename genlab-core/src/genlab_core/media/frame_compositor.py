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
from typing import TYPE_CHECKING

import yaml

from genlab_core.media.ffmpeg_utils import run_ffmpeg

if TYPE_CHECKING:
    # ARCH #45 phase 2: sandbox routing is optional; the type-check-only
    # import avoids a hard runtime dep on the sandbox module for callers
    # that don't use it.
    from genlab_core.media.sandbox_runner import SandboxedFFmpegRunner

logger = logging.getLogger(__name__)

# -------------------------------------------------------------
# Canvas constants -- LOCKED. Do not make these configurable.
# -------------------------------------------------------------
CANVAS_W = 1080
CANVAS_H = 1920

# Aspect ratio thresholds
LANDSCAPE_THRESHOLD = 1.33  # w/h >= this -> landscape
PORTRAIT_THRESHOLD = 0.75  # w/h <= this -> portrait
# Between 0.75 and 1.33 = SQUARE

# Layout A: Landscape — video VERTICALLY CENTERED, branding above it
L_VIDEO_H = 608  # 16:9 at 1080w = 608h
L_VIDEO_Y = (CANVAS_H - L_VIDEO_H) // 2  # 656 — centered vertically
L_HOOK_Y = L_VIDEO_Y - 120  # hook text above video with 16px clear gap
L_LOGO_Y = L_HOOK_Y - 150  # logo/name above hook with breathing room

# Layout B: Portrait — clean full-screen video, NO branding/hook overlay
# Portrait videos fill the entire 1080x1920 canvas with zero overlays.
# Branding comes from the caption/post text, not burned into the video.

# Layout C: Square — video vertically centered, branding above
S_VIDEO_H = 1080  # 1080x1080 square video
S_VIDEO_Y = (CANVAS_H - S_VIDEO_H) // 2  # 420 — centered vertically
S_HOOK_Y = S_VIDEO_Y - 120
S_LOGO_Y = S_HOOK_Y - 150

# Shared branding
LOGO_SIZE = 60
LOGO_X = 45
# Layout B (portrait): the logo is overlaid directly ON the full-screen video
# (R-26 — portrait reels previously shipped with NO branding at all). Top-left,
# clear of the platform UI safe zone, with a subtle dark backing for contrast.
P_LOGO_X = 45
P_LOGO_Y = 70
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
    """FFmpeg render settings from visuals.yaml.

    Defaults tuned 2026-05-20 after discovering production masters
    were running 8-15 Mbps for motion-heavy content (sports, gaming),
    causing downstream IG/Threads/FB upload failures. Defaults now
    cap the master at 6 Mbps with maxrate — still ample quality for
    the per-platform transcode step to work from, but small enough
    to ship without re-compression.
    """

    preset: str = "medium"
    fallback_preset: str = "fast"
    timeout_seconds: int = 600
    # Bitrate ceiling for the master render (per-platform transcode
    # at publish time may apply tighter caps). Empty string = no cap
    # (legacy behavior; produces 8-15 Mbps on motion-heavy source).
    maxrate: str = "6M"
    bufsize: str = "12M"


@dataclass
class ChannelBranding:
    """Per-channel branding loaded from visuals.yaml."""

    channel_name: str  # e.g. "CriticalRush"
    handle: str  # e.g. "@CriticalRush"
    accent_color: str  # e.g. "#00FF88" (for accent line)
    logo_path: str  # absolute or relative path to logo PNG
    niche_id: str  # e.g. "gaming"
    ffmpeg: FFmpegConfig = None  # type: ignore[assignment]

    # RENDER #4 (2026-06-13): portrait branding richness — opt-in per
    # niche so the operator can dial in the aesthetic without code changes.
    # All three default False so existing portrait renders are byte-
    # identical to pre-RENDER-#4 (logo only, no text). YAML shape:
    #
    #     branding:
    #       portrait_branding:
    #         show_name: true
    #         show_handle: true
    #         show_hook: true
    #
    # Each is independent — operator can ship logo+name (handle off) or
    # logo+hook (name + handle off) as a creative-test variant.
    portrait_show_name: bool = False
    portrait_show_handle: bool = False
    portrait_show_hook: bool = False

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
            preset=ff_cfg.get("preset", "medium"),
            fallback_preset=ff_cfg.get("fallback_preset", "fast"),
            timeout_seconds=int(ff_cfg.get("timeout_seconds", 600)),
            maxrate=str(ff_cfg.get("maxrate", "6M") or ""),
            bufsize=str(ff_cfg.get("bufsize", "12M") or ""),
        )

        # Resolve logo path — relative paths are resolved against niche_root
        raw_logo = (
            fl_branding.get("logo_path") or branding.get("logo_path") or branding.get("logo", "")
        )
        if raw_logo and not Path(raw_logo).is_absolute():
            raw_logo = str((niche_root / raw_logo).resolve())

        # RENDER #4: read the per-niche portrait branding flags. Block
        # defaults to {} so a missing visuals.yaml `portrait_branding:` key
        # produces the safe default (all flags False).
        portrait_block = (
            fl_branding.get("portrait_branding")
            or branding.get("portrait_branding")
            or {}
        )
        if not isinstance(portrait_block, dict):
            portrait_block = {}

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
                fl_branding.get("accent_color") or branding.get("accent_color", "#FFFFFF")
            ),
            logo_path=raw_logo,
            niche_id=(
                fl_branding.get("niche_id") or branding.get("niche_id") or cfg.get("niche_id", "")
            ),
            ffmpeg=ffmpeg,
            portrait_show_name=bool(portrait_block.get("show_name", False)),
            portrait_show_handle=bool(portrait_block.get("show_handle", False)),
            portrait_show_hook=bool(portrait_block.get("show_hook", False)),
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
    aspect_ratio: float  # width / height
    is_portrait: bool  # aspect_ratio <= PORTRAIT_THRESHOLD
    is_landscape: bool  # aspect_ratio >= LANDSCAPE_THRESHOLD
    is_native_9_16: bool  # kept for backward compat; True when portrait

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
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        "-select_streams",
        "v:0",
        path,
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
        cmd2 = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path]
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
        width=w,
        height=h,
        duration_seconds=duration,
        fps=fps,
        aspect_ratio=ar,
        is_portrait=is_portrait,
        is_landscape=is_landscape,
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

    def __init__(
        self,
        branding: ChannelBranding,
        sandbox_runner: SandboxedFFmpegRunner | None = None,
    ):
        """Construct a compositor for one channel.

        Parameters
        ----------
        branding:
            Per-channel visual config (logo, accent, font, etc.)
        sandbox_runner:
            ARCH #45 phase 2 (2026-06-13): when provided, every FFmpeg
            call goes through the sandboxed runner instead of executing
            locally. Matches the sandbox plumbing already present on
            ``derive_landscape``. When ``None`` (default), behavior is
            unchanged — FFmpeg runs locally via ``run_ffmpeg``. Gaming
            opts in; other niches may follow once the perf/isolation
            trade-off is settled (see task #51).
        """
        self.branding = branding
        self._sandbox_runner = sandbox_runner

    @classmethod
    def from_visuals_yaml(
        cls,
        visuals_yaml_path: str,
        sandbox_runner: SandboxedFFmpegRunner | None = None,
    ) -> FrameCompositor:
        branding = ChannelBranding.from_visuals_yaml(visuals_yaml_path)
        return cls(branding, sandbox_runner=sandbox_runner)

    def _run_ffmpeg(
        self,
        cmd: list[str],
        *,
        timeout: int,
        fallback_preset: str | None,
        label: str = "frame_compositor",
    ) -> None:
        """Execute an FFmpeg command, optionally inside the sandbox.

        Centralized so both ``compose`` (sandwich render) and
        ``overlay_branding`` (compilation branding) share the same
        sandbox-aware path. ARCH #45 phase 2.
        """
        if self._sandbox_runner is not None:
            result = self._sandbox_runner.run_ffmpeg_sync(cmd, timeout=timeout)
            # The runner's check() raises CalledProcessError on non-zero
            # exit, matching the contract of ``run_ffmpeg`` so existing
            # callers' try/except logic doesn't change.
            result.check(label)
            return
        # Local execution — historical default for all 5 niches.
        run_ffmpeg(cmd, timeout=timeout, fallback_preset=fallback_preset)

    def compose(
        self,
        source_video_path: str,
        hook_text: str,
        output_path: str,
        duration_seconds: float | None = None,
        trim_start: float = 0.0,
        crf: int = 20,
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
            crf: H.264 CRF (20 default — high quality, paired with the
                 maxrate cap from visuals.yaml. Was 15 historically, which
                 produced 8-15 Mbps masters for motion-heavy content and
                 broke downstream IG/Threads upload.)
            preset: FFmpeg preset. Defaults to visuals.yaml ffmpeg.preset.
            force_fps: Output frame rate.

        Returns:
            output_path on success.

        Raises:
            RuntimeError: if FFmpeg fails.
        """
        # R-26: the channel logo is a STRICT, mandatory invariant — a reel
        # without it must NEVER silently ship. Previously a missing logo file
        # degraded to text-only branding on landscape/square (and portrait had
        # no branding at all) yet still returned success → VISUAL_READY. Fail
        # LOUD instead so the dark-day alerting (R-65/R-01) surfaces it rather
        # than an unbranded reel reaching publish.
        if not (self.branding.logo_path and os.path.exists(self.branding.logo_path)):
            raise RuntimeError(
                f"[{self.branding.niche_id}] Channel logo missing or not found "
                f"(logo_path={self.branding.logo_path!r}) — refusing to render an "
                "unbranded reel; every reel MUST carry the channel logo."
            )

        # Use config defaults from visuals.yaml
        ff = self.branding.ffmpeg
        if preset is None:
            preset = ff.preset
        # Validate hook length
        if len(hook_text) > HOOK_MAX_CHARS:
            logger.warning(
                f"Hook '{hook_text[:30]}...' is {len(hook_text)} chars -- truncating to {HOOK_MAX_CHARS}"
            )
            hook_text = hook_text[: HOOK_MAX_CHARS - 3] + "..."

        # Probe source
        info = probe_video(source_video_path)
        case = info.layout_case

        # Sub-item D: Skip first 10% of long clips (intros, logos, "hey guys")
        if info.duration_seconds > 45 and trim_start == 0:
            skip = info.duration_seconds * 0.10
            trim_start = round(skip, 1)
            logger.debug(
                "[%s] Skipping first %.1fs (10%%) of %.1fs clip for stronger opening",
                self.branding.niche_id,
                trim_start,
                info.duration_seconds,
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
            source_video_path,
            hook_text,
            output_path,
            info,
            duration_seconds,
            trim_start,
            crf,
            preset,
            force_fps,
        )

        logger.info(
            f"[{self.branding.niche_id}] Running FFmpeg ({preset}): {' '.join(ffmpeg_cmd[:8])}..."
        )
        try:
            self._run_ffmpeg(
                ffmpeg_cmd,
                timeout=ff.timeout_seconds,
                fallback_preset=ff.fallback_preset,
                label="compose",
            )
        except subprocess.CalledProcessError as exc:
            logger.error(f"FFmpeg failed:\n{(exc.stderr or '')[-2000:]}")
            raise RuntimeError(f"FFmpeg composition failed: {(exc.stderr or '')[-500:]}") from exc

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

    def _build_hook_filters(
        self, hook: str, hook_y: int, font_hook: str, prev_label: str
    ) -> tuple[str, str]:
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

    def _build_branding_filters(
        self, font_bold: str, font_reg: str, logo_y: int, base_label: str
    ) -> tuple[str, str]:
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
                f"[{base_label}]drawbox=x={LOGO_X - 4}:y={logo_y - 4}:"
                f"w={LOGO_SIZE + 8}:h={LOGO_SIZE + 8}:"
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
            ["ffmpeg", "-y"]
            + trim_flag
            + inputs
            + dur_flags
            + ["-filter_complex", filtergraph, "-map", "[out]", "-map", "0:a?"]
            + self._output_flags(crf, preset, fps)
            + [out]
        )

    # --- Layout B: Portrait (ar <= 0.75) ----------------------------------

    def _build_cmd_portrait(
        self, src, hook, out, info, duration, trim_start, crf, preset, fps
    ) -> list[str]:
        """Portrait clip: full-screen video with the channel logo overlaid.

        Portrait sources (9:16) fill the entire 1080x1920 canvas. R-26: the
        logo is overlaid top-left (these reels previously shipped with zero
        branding, violating the "every reel has the logo" invariant). A
        subtle dark backing keeps the logo legible over bright footage. The
        logo file is guaranteed present by compose()'s guard.

        RENDER #4 (2026-06-13): name / handle / hook overlays are opt-in
        per-niche via visuals.yaml `portrait_branding:` flags. Default is
        logo only (current behavior). When the operator turns a flag on
        the corresponding drawtext joins the filtergraph after the logo
        overlay — order: name → handle → hook.
        """
        dur_flags = self._duration_flags(duration)
        trim_flag = ["-ss", str(trim_start)] if trim_start > 0 else []
        font_bold, font_reg, font_hook = self._resolve_fonts()

        # ── Base: logo over the full-bleed video ──────────────────────────
        # Final label is overwritten as each opt-in overlay appends. The
        # last filter in the chain produces [out].
        filtergraph = (
            f"[0:v]scale={CANVAS_W}:{CANVAS_H}:"
            f"force_original_aspect_ratio=increase,"
            f"crop={CANVAS_W}:{CANVAS_H}[vid];"
            f"[1:v]scale={LOGO_SIZE}:{LOGO_SIZE}[logo];"
            f"[vid]drawbox=x={P_LOGO_X - 4}:y={P_LOGO_Y - 4}:"
            f"w={LOGO_SIZE + 8}:h={LOGO_SIZE + 8}:color=black@0.30:t=fill[vidbg];"
            f"[vidbg][logo]overlay={P_LOGO_X}:{P_LOGO_Y}"
        )
        # Track which label feeds the next filter so each opt-in overlay
        # can be inserted independently without re-wiring the others.
        current_label = "withlogo"
        filtergraph += f"[{current_label}];"

        # ── Opt-in: channel name (RENDER #4) ──────────────────────────────
        # Sits to the right of the logo, vertically aligned with its top
        # edge. Bold font (matches landscape/square treatment). Subtle
        # shadow keeps it legible on bright frames.
        if self.branding.portrait_show_name and self.branding.channel_name:
            safe_name = self._escape_drawtext(self.branding.channel_name)
            name_x = P_LOGO_X + LOGO_SIZE + 16
            name_y = P_LOGO_Y + 8
            next_label = "withname"
            filtergraph += (
                f"[{current_label}]drawtext=fontfile='{font_bold}':text='{safe_name}':"
                f"fontsize={NAME_FONT_SIZE}:fontcolor=white:"
                f"x={name_x}:y={name_y}:"
                f"shadowcolor=black@{SHADOW_OPACITY}:shadowx={SHADOW_OFFSET}:shadowy={SHADOW_OFFSET}"
                f"[{next_label}];"
            )
            current_label = next_label

        # ── Opt-in: handle (RENDER #4) ────────────────────────────────────
        # Below the name (or below the logo if name is off). Slightly
        # transparent — secondary information.
        if self.branding.portrait_show_handle and self.branding.handle:
            safe_handle = self._escape_drawtext(self.branding.handle)
            handle_x = P_LOGO_X + LOGO_SIZE + 16
            handle_y = P_LOGO_Y + 8 + (NAME_FONT_SIZE + 6 if self.branding.portrait_show_name else 0)
            next_label = "withhandle"
            filtergraph += (
                f"[{current_label}]drawtext=fontfile='{font_reg}':text='{safe_handle}':"
                f"fontsize={HANDLE_FONT_SIZE}:fontcolor=white@{HANDLE_OPACITY}:"
                f"x={handle_x}:y={handle_y}:"
                f"shadowcolor=black@{SHADOW_OPACITY}:shadowx={SHADOW_OFFSET}:shadowy={SHADOW_OFFSET}"
                f"[{next_label}];"
            )
            current_label = next_label

        # ── Opt-in: hook (RENDER #4) ──────────────────────────────────────
        # Center-aligned horizontally, sits ~120px below the logo block.
        # Reuses _wrap_hook + the existing HOOK_FONT_SIZE / shadow values
        # for visual consistency with landscape/square layouts.
        if self.branding.portrait_show_hook and hook:
            hook_y = P_LOGO_Y + LOGO_SIZE + 80
            hook_lines = self._wrap_hook(hook)
            for i, line in enumerate(hook_lines):
                safe_line = self._escape_drawtext(line)
                line_y = hook_y + i * HOOK_LINE_H
                next_label = (
                    f"hook{i}" if i < len(hook_lines) - 1 else "withhook"
                )
                filtergraph += (
                    f"[{current_label}]drawtext=fontfile='{font_hook}':text='{safe_line}':"
                    f"fontsize={HOOK_FONT_SIZE}:fontcolor=white:"
                    f"x=(w-text_w)/2:y={line_y}:"
                    f"shadowcolor=black@{SHADOW_OPACITY}:shadowx={SHADOW_OFFSET}:shadowy={SHADOW_OFFSET}"
                    f"[{next_label}];"
                )
                current_label = next_label

        # Re-label the final node as [out] — the rest of compose() expects
        # that name. Using copy keeps it filter-graph-valid without
        # producing a re-encoded extra pass.
        filtergraph += f"[{current_label}]copy[out]"

        return (
            ["ffmpeg", "-y"]
            + trim_flag
            + ["-i", src, "-i", self.branding.logo_path]
            + dur_flags
            + ["-filter_complex", filtergraph, "-map", "[out]", "-map", "0:a?"]
            + self._output_flags(crf, preset, fps)
            + [out]
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
            ["ffmpeg", "-y"]
            + trim_flag
            + inputs
            + dur_flags
            + ["-filter_complex", filtergraph, "-map", "[out]", "-map", "0:a?"]
            + self._output_flags(crf, preset, fps)
            + [out]
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
            hook_text = hook_text[: HOOK_MAX_CHARS - 3] + "..."

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        font_bold, font_reg, font_hook = self._resolve_fonts()
        has_logo = self.branding.logo_path and os.path.exists(self.branding.logo_path)
        dur_flags = self._duration_flags(duration_seconds)

        # Build branding filters on top of the source video
        branding, _ = self._build_branding_filters(font_bold, font_reg, L_LOGO_Y, "base")
        hooks, _ = self._build_hook_filters(hook_text, L_HOOK_Y, font_hook, "withhandle")
        flash = "[withhook]eq=brightness='if(lt(t,0.07),0.08,0)':eval=frame[out]"

        # Source video becomes [base] directly (no re-layout)
        video_filter = "[0:v]copy[base];"
        filtergraph = f"{video_filter}{branding}{hooks}{flash}"

        inputs = ["-i", source_video_path]
        if has_logo:
            inputs += ["-i", self.branding.logo_path]

        cmd = (
            ["ffmpeg", "-y"]
            + inputs
            + dur_flags
            + ["-filter_complex", filtergraph, "-map", "[out]", "-map", "0:a?"]
            + self._output_flags(crf, preset, 30)
            + [output_path]
        )

        logger.info(
            f"[{self.branding.niche_id}] Overlaying branding on compilation: {' '.join(cmd[:8])}..."
        )
        try:
            self._run_ffmpeg(
                cmd,
                timeout=ff.timeout_seconds,
                fallback_preset=ff.fallback_preset,
                label="overlay_branding",
            )
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

        Newlines and tabs are stripped entirely — FFmpeg drawtext doesn't
        support multi-line text in filter_complex strings, and leaving them
        in can break the filter graph parser. Hooks are supposed to be
        single-line anyway (≤60 chars) so this is belt-and-suspenders.
        """
        # Strip control characters first (defense-in-depth against
        # adversarial input — LLM hooks shouldn't have them, but scraped
        # text might).
        text = "".join(c for c in text if ord(c) >= 32 or c == " ")
        return (
            text.replace("\\", "\\\\")
            .replace("'", "\u2019")  # curly quote -- safe inside '...'
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

    def _output_flags(self, crf: int, preset: str, fps: int) -> list[str]:
        flags = [
            "-c:v",
            "libx264",
            "-crf",
            str(crf),
            "-preset",
            preset,
            "-r",
            str(fps),
        ]
        # Master bitrate ceiling from visuals.yaml ffmpeg.maxrate.
        # Empty string disables (legacy uncapped behavior).
        maxrate = (self.branding.ffmpeg.maxrate or "").strip()
        bufsize = (self.branding.ffmpeg.bufsize or "").strip()
        if maxrate:
            flags.extend(["-maxrate", maxrate, "-bufsize", bufsize or maxrate])
        flags.extend(
            [
                "-c:a",
                "aac",
                "-b:a",
                "320k",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-pix_fmt",
                "yuv420p",
                "-colorspace",
                "bt709",
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-movflags",
                "+faststart",
            ]
        )
        return flags

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
