"""Gen Lab universal video compositor — single source of truth for visual standards.

All niches (gaming, ai_creators, sports, anime, ...) call this module for rendered
video output.  Niche identity comes from the VisualConfig passed at instantiation;
no niche-specific code should exist here.

Visual standard enforced:
    ┌──────────────────────────────────────────┐
    │  TOP BAR (12 %)  │ logo │  hook text     │  ← solid black
    ├──────────────────────────────────────────┤
    │                                          │
    │          CONTENT  (source clips)         │  ← fills remaining
    │                                          │
    ├──────────────────────────────────────────┤
    │           BOTTOM BAR (18 %)              │  ← solid black, reserved for platform UI
    └──────────────────────────────────────────┘

Usage:
    from genlab_core.media.video_compositor import VideoCompositor, VisualConfig

    cfg = VisualConfig(
        niche_id="gaming",
        logo_path=Path("assets/CriticalRush-Logo.png"),
        accent_color="#FF4500",
    )
    comp = VideoCompositor(cfg)
    vertical = comp.compose_vertical(clips, "This changes everything", out / "reel.mp4")
    landscape = comp.derive_landscape(vertical, out / "landscape.mp4")
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

import yaml
from pydantic import BaseModel

if TYPE_CHECKING:
    from genlab_core.media.sandbox_runner import SandboxedFFmpegRunner

from genlab_core.media.ffmpeg import get_ffmpeg_binary
from genlab_core.media.ffmpeg_utils import (
    concat,
    escape_drawtext,
    probe_video_metadata,
    run_ffmpeg,
)

logger = logging.getLogger(__name__)

# ── Canvas constants ──────────────────────────────────────────────────────────
VERTICAL_WIDTH = 1080
VERTICAL_HEIGHT = 1920
LANDSCAPE_WIDTH_FB = 1920
LANDSCAPE_HEIGHT_FB = 1080
LANDSCAPE_WIDTH_X = 1280
LANDSCAPE_HEIGHT_X = 720

# ── Layout constants ──────────────────────────────────────────────────────────
LOGO_LEFT_MARGIN = 24
LOGO_TEXT_GAP = 24
TEXT_RIGHT_MARGIN = 24
# Average character width as a fraction of font size (sans-serif approximation).
_FONT_CHAR_WIDTH_RATIO = 0.55
# Default logo aspect ratio when probe fails (3:2 — common for horizontal logos).
_DEFAULT_LOGO_ASPECT = 1.5

# ── Encoding defaults (matches existing ffmpeg_utils FINAL_VIDEO_PARAMS) ─────
_ENCODE_ARGS = [
    "-c:v", "libx264", "-preset", "medium", "-crf", "18",
    "-pix_fmt", "yuv420p",
    "-colorspace", "bt709",
    "-color_primaries", "bt709",
    "-color_trc", "bt709",
    "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
    "-movflags", "+faststart",
]
_LANDSCAPE_ENCODE_ARGS = [
    "-c:v", "libx264", "-preset", "medium", "-crf", "20",
    "-pix_fmt", "yuv420p",
    "-colorspace", "bt709",
    "-color_primaries", "bt709",
    "-color_trc", "bt709",
    "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
    "-movflags", "+faststart",
]
_FFMPEG_TIMEOUT = 300


# ══════════════════════════════════════════════════════════════════════════════
# VisualConfig — per-niche identity
# ══════════════════════════════════════════════════════════════════════════════


class VisualConfig(BaseModel):
    """Per-niche visual identity.  Loaded from ``niches/*/config/visuals.yaml``.

    Layout defaults sourced from ``genlab_core.video.standards.LayoutStandard``.
    """

    niche_id: str
    logo_path: Path
    accent_color: str
    top_bar_height_pct: float = 0.12   # LayoutStandard.top_bar_pct
    bottom_bar_height_pct: float = 0.18  # LayoutStandard.bottom_bar_pct
    logo_height_px: int = 60           # LayoutStandard.logo_height
    logo_x_offset: int = 24
    hook_font_size: int = 32           # LayoutStandard.hook_font_size
    hook_x_offset: int = 24
    hook_max_lines: int = 2
    bar_color: List[int] = [0, 0, 0]
    hook_color: List[int] = [255, 255, 255]
    landscape_mode: str = "blurred_pillarbox"
    smart_crop: bool = False
    smart_crop_min_aspect: float = 1.2
    platforms_vertical: List[str] = ["instagram", "youtube", "tiktok", "threads"]
    platforms_landscape: List[str] = ["facebook", "twitter"]


def load_visual_config(visuals_yaml: Path, niche_root: Path | None = None) -> VisualConfig:
    """Load a VisualConfig from a ``visuals.yaml`` file.

    If *niche_root* is provided, ``logo_path`` in the YAML is resolved relative
    to *niche_root*.  Otherwise it is resolved relative to the YAML file's
    parent directory.
    """
    with open(visuals_yaml) as f:
        raw = yaml.safe_load(f)

    base = niche_root or visuals_yaml.parent
    logo = Path(raw["logo_path"])
    if not logo.is_absolute():
        logo = (base / logo).resolve()
    raw["logo_path"] = logo

    return VisualConfig(**raw)


# ══════════════════════════════════════════════════════════════════════════════
# VideoCompositor
# ══════════════════════════════════════════════════════════════════════════════


class VideoCompositor:
    """Gen Lab universal video compositor.

    Enforces visual standards across all niches:
    - Sandwich pattern: black bar (top 12%) + content + black bar (bottom 18%)
    - Logo: niche logo PNG at top-left inside top black bar, vertically centered
    - Hook: bold white text in top black bar, right of logo, max 2 lines
    - Platform aspect ratio: 9:16 for IG/YT/TikTok/Threads, 16:9 for FB/X
    - Landscape conversion: blurred pillarbox for 9:16→16:9 (never crop)
    - Vertical conversion: blurred letterbox for 16:9→9:16 with centered overlay

    Niche identity comes from the VisualConfig passed at instantiation.
    No niche-specific code should exist in this class.
    """

    def __init__(
        self,
        config: VisualConfig,
        sandbox_runner: Optional["SandboxedFFmpegRunner"] = None,
    ) -> None:
        self._config = config
        self._canvas_w = VERTICAL_WIDTH
        self._canvas_h = VERTICAL_HEIGHT
        self._sandbox_runner = sandbox_runner

    def _run_ffmpeg_cmd(
        self, cmd: list[str], *, timeout: int = _FFMPEG_TIMEOUT, label: str = "ffmpeg"
    ) -> subprocess.CompletedProcess:
        """Execute an FFmpeg command locally or inside a sandbox.

        If a sandbox_runner was provided at init, routes the command through
        the sandbox.  Otherwise, falls back to local subprocess.run().
        """
        if self._sandbox_runner is not None:
            result = self._sandbox_runner.run_ffmpeg_sync(cmd, timeout=timeout)
            result.check(label)
            # Return a CompletedProcess-like for backward compat with callers
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=result.stdout, stderr=result.stderr
            )

        try:
            return run_ffmpeg(cmd, timeout=timeout, fallback_preset="fast")
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"FFmpeg failed [{label}] (exit {exc.returncode}):\n"
                + (exc.stderr or "")[-2000:]
            ) from exc

    # ── Public API ────────────────────────────────────────────────────────────

    def compose_vertical(
        self,
        source_clips: list[Path],
        hook_text: str,
        output_path: Path,
        smart_crop: bool | None = None,
    ) -> Path:
        """Assemble the 9:16 master video with full sandwich treatment.

        The sandwich pattern is:
          [TOP BAR: 12 % height, solid black]
            - Logo: niche PNG, height=60 px, positioned left margin 24 px,
                    vertically centered in bar
            - Hook text: right of logo, 24 px margin from logo right edge,
                         bold, white, 32 px, max 2 lines, vertically centered
          [CONTENT: source clips assembled with crossfade, fills remaining height]
          [BOTTOM BAR: 18 % height, solid black]
            - Reserved for platform UI (Instagram handle, YouTube subscribe prompt)
            - No Gen Lab text in bottom bar (platform overlays go here)

        Parameters
        ----------
        smart_crop : bool | None
            Pre-crop landscape clips to face/motion centre before sandwich.
            ``None`` (default) defers to ``VisualConfig.smart_crop``.
        """
        if not self._config.logo_path.exists():
            raise FileNotFoundError(
                f"Logo not found: {self._config.logo_path}. "
                f"Each niche must provide a logo PNG at the configured logo_path. "
                f"Expected file for niche '{self._config.niche_id}'."
            )

        if not source_clips:
            raise ValueError("At least one source clip is required")

        for clip in source_clips:
            if not clip.exists():
                raise FileNotFoundError(f"Source clip not found: {clip}")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Step 0: optionally pre-crop landscape clips (face/motion-aware)
        do_smart_crop = smart_crop if smart_crop is not None else self._config.smart_crop
        crop_dir: str | None = None

        if do_smart_crop:
            source_clips, crop_dir = self._smart_crop_clips(source_clips)

        try:
            # Step 1: concatenate multiple clips (single clip skips this)
            assembled_clip, temp_dir = self._assemble_clips(source_clips)

            try:
                # Step 2: build the full filter_complex and run FFmpeg
                self._render_sandwich(assembled_clip, hook_text, output_path)
            finally:
                if temp_dir is not None:
                    shutil.rmtree(temp_dir, ignore_errors=True)
        finally:
            if crop_dir is not None:
                shutil.rmtree(crop_dir, ignore_errors=True)

        return output_path

    def derive_landscape(
        self,
        vertical_master: Path,
        output_path: Path,
        width: int = LANDSCAPE_WIDTH_FB,
        height: int = LANDSCAPE_HEIGHT_FB,
    ) -> Path:
        """Derive 16:9 variant from the 9:16 master for Facebook and X.

        Method: blurred pillarbox (NEVER crop).
        - Background: scale vertical_master to fill *width*×*height*, apply
                      Gaussian blur sigma=20
        - Foreground: scale vertical_master to fit height=*height*,
                      center horizontally over blurred background
        - The sandwich bars are preserved in the foreground layer
        """
        if not vertical_master.exists():
            raise FileNotFoundError(
                f"Vertical master not found: {vertical_master}"
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)

        filter_complex = (
            f"[0:v]split[bg][fg];"
            f"[bg]scale={width}:{height}"
            f":force_original_aspect_ratio=increase,"
            f"crop={width}:{height},gblur=sigma=20[blurred];"
            f"[fg]scale=-2:{height}[sharp];"
            f"[blurred][sharp]overlay=(W-w)/2:(H-h)/2[out]"
        )

        ffmpeg = get_ffmpeg_binary()
        cmd = [
            ffmpeg, "-y",
            "-i", str(vertical_master),
            "-filter_complex", filter_complex,
            "-map", "[out]", "-map", "0:a?",
            *_LANDSCAPE_ENCODE_ARGS,
            str(output_path),
        ]

        self._run_ffmpeg_cmd(cmd, label="landscape")
        return output_path

    # ── Filter builders (the ONLY place these filters are defined) ────────────

    def _build_sandwich_filter(self, config: VisualConfig) -> str:
        """Generate the FFmpeg filtergraph string for the sandwich overlay.

        This is the ONLY place in the entire codebase where the sandwich
        filter is defined.  Gaming, sports, anime, movies all call this
        same method.
        """
        cw, ch = self._canvas_w, self._canvas_h
        # Round bar heights to even — FFmpeg scale rounds to even via
        # force_original_aspect_ratio, and odd content_h causes
        # "Padded dimensions cannot be smaller than input dimensions".
        top_h = int(ch * config.top_bar_height_pct) & ~1
        bot_h = int(ch * config.bottom_bar_height_pct) & ~1
        content_h = ch - top_h - bot_h

        # 1. Scale source to fit inside the content area
        # 2. Pad to content area dimensions (center)
        # 3. Pad to full canvas (content placed below top bar)
        # 4. Draw explicit black bars (ensures solid coverage)
        return (
            f"[0:v]scale={cw}:{content_h}"
            f":force_original_aspect_ratio=decrease,"
            f"pad={cw}:{content_h}:(ow-iw)/2:(oh-ih)/2:black,"
            f"pad={cw}:{ch}:0:{top_h}:black,"
            f"drawbox=x=0:y=0:w={cw}:h={top_h}:color=black:t=fill,"
            f"drawbox=x=0:y={ch - bot_h}:w={cw}:h={bot_h}:color=black:t=fill"
        )

    def _overlay_logo(self, config: VisualConfig) -> str:
        """Generate the FFmpeg logo overlay expression.

        Logo path comes from config.logo_path (niche-specific asset).
        Position: x=24, y=(top_bar_height - logo_height)/2
        Always within the top black bar.  Never in the content area.
        """
        top_h = int(self._canvas_h * config.top_bar_height_pct)
        logo_y = (top_h - config.logo_height_px) // 2
        return f"overlay=x={LOGO_LEFT_MARGIN}:y={logo_y}"

    def _overlay_hook_text(self, hook: str, config: VisualConfig) -> str:
        """Generate the drawtext expression for the hook overlay.

        Font: bold, white (#FFFFFF), size 32 px.
        Position: x=logo_right_edge + 24, y centered in top bar.
        Max width: canvas_width - logo_right_edge - 48 (24 px each side).
        Line wrap: automatic at max_width.
        Max lines: 2.  Truncate with ellipsis if longer.
        """
        top_h = int(self._canvas_h * config.top_bar_height_pct)
        logo_w = self._get_logo_width_px(config)
        text_x = LOGO_LEFT_MARGIN + logo_w + LOGO_TEXT_GAP
        max_text_w = self._canvas_w - text_x - TEXT_RIGHT_MARGIN

        wrapped = self._wrap_hook(
            hook, config.hook_font_size, max_text_w, config.hook_max_lines,
        )
        escaped = escape_drawtext(wrapped)

        line_h = int(config.hook_font_size * 1.3)
        num_lines = wrapped.count("\n") + 1
        text_block_h = num_lines * line_h
        text_y = max(0, (top_h - text_block_h) // 2)

        return (
            f"drawtext=text='{escaped}'"
            f":fontsize={config.hook_font_size}"
            f":fontcolor=white"
            f":x={text_x}:y={text_y}"
            f":line_spacing={int(config.hook_font_size * 0.3)}"
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _smart_crop_clips(
        self, clips: list[Path],
    ) -> tuple[list[Path], str | None]:
        """Pre-crop landscape clips using face/motion detection.

        Returns (possibly-modified clips list, temp_dir_or_None).
        Portrait/square clips pass through unchanged.
        """
        from genlab_core.media.smart_crop import SmartCropper

        cropper = SmartCropper(
            min_landscape_aspect=self._config.smart_crop_min_aspect,
            sandbox_runner=self._sandbox_runner,
        )
        return cropper.pre_crop_clips(clips)

    def _assemble_clips(
        self, clips: list[Path],
    ) -> tuple[Path, str | None]:
        """Concatenate clips if >1.  Returns (assembled_path, temp_dir_or_None)."""
        if len(clips) == 1:
            return clips[0], None

        temp_dir = tempfile.mkdtemp(prefix="genlab_concat_")
        assembled = str(Path(temp_dir) / "assembled.mp4")
        success = concat(
            [str(p) for p in clips], assembled, temp_dir=temp_dir,
        )
        if not success:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise RuntimeError("Failed to concatenate source clips")
        return Path(assembled), temp_dir

    def _render_sandwich(
        self, source: Path, hook_text: str, output: Path,
    ) -> None:
        """Run the full sandwich render: scale → bars → logo → hook text."""
        cfg = self._config

        sandwich = self._build_sandwich_filter(cfg)
        logo_overlay = self._overlay_logo(cfg)
        hook_filter = self._overlay_hook_text(hook_text, cfg)

        filter_complex = (
            f"{sandwich}[sandwich];"
            f"[1:v]scale=-1:{cfg.logo_height_px}[logo];"
            f"[sandwich][logo]{logo_overlay}[branded];"
            f"[branded]{hook_filter}[out]"
        )

        ffmpeg = get_ffmpeg_binary()
        cmd = [
            ffmpeg, "-y",
            "-i", str(source),
            "-i", str(cfg.logo_path),
            "-filter_complex", filter_complex,
            "-map", "[out]", "-map", "0:a?",
            *_ENCODE_ARGS,
            str(output),
        ]

        self._run_ffmpeg_cmd(cmd, label="sandwich")

    def _get_logo_width_px(self, config: VisualConfig) -> int:
        """Probe logo to get its scaled width at ``logo_height_px``."""
        try:
            meta = probe_video_metadata(config.logo_path)
            if meta and meta.get("width") and meta.get("height"):
                aspect = meta["width"] / meta["height"]
                return int(config.logo_height_px * aspect)
        except Exception:
            pass
        return int(config.logo_height_px * _DEFAULT_LOGO_ASPECT)

    @staticmethod
    def _wrap_hook(
        text: str,
        font_size: int,
        max_width_px: int,
        max_lines: int,
    ) -> str:
        """Word-wrap hook text to fit pixel width, truncate with ellipsis."""
        char_w = font_size * _FONT_CHAR_WIDTH_RATIO
        chars_per_line = max(10, int(max_width_px / char_w))

        lines = textwrap.wrap(text, width=chars_per_line)
        if not lines:
            return text

        if len(lines) > max_lines:
            lines = lines[:max_lines]
            last = lines[-1]
            if len(last) > chars_per_line - 3:
                last = last[: chars_per_line - 3].rstrip() + "..."
            else:
                last = last.rstrip() + "..."
            lines[-1] = last

        return "\n".join(lines)
