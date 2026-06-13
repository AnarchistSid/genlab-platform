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
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel

if TYPE_CHECKING:
    from genlab_core.media.sandbox_runner import SandboxedFFmpegRunner

from genlab_core.media.ffmpeg import PLATFORM_SPECS, Platform, RenderSpec, get_ffmpeg_binary
from genlab_core.media.ffmpeg_utils import run_ffmpeg

logger = logging.getLogger(__name__)

# ── Canvas constants ──────────────────────────────────────────────────────────
# VERTICAL_WIDTH/HEIGHT removed in DEAD #1 — only consumed by the deleted
# sandwich-render chain. derive_landscape uses LANDSCAPE_* defaults.
LANDSCAPE_WIDTH_FB = 1920
LANDSCAPE_HEIGHT_FB = 1080
LANDSCAPE_WIDTH_X = 1280
LANDSCAPE_HEIGHT_X = 720

# ── Layout constants REMOVED in DEAD #1 (2026-06-13) ─────────────────────────
# LOGO_LEFT_MARGIN / LOGO_TEXT_GAP / TEXT_RIGHT_MARGIN /
# _FONT_CHAR_WIDTH_RATIO / _DEFAULT_LOGO_ASPECT were only consumed by the
# deleted sandwich-render chain. derive_landscape doesn't need them.

# ── Encoding ─────────────────────────────────────────────────────────────────
_FFMPEG_TIMEOUT = 300

# Map of platform name strings → Platform enum for lookup.
_PLATFORM_ALIAS: dict[str, Platform] = {
    "instagram": Platform.INSTAGRAM,
    "youtube": Platform.YOUTUBE,
    "tiktok": Platform.TIKTOK,
    "facebook": Platform.FACEBOOK,
    "threads": Platform.THREADS,
    "x_standard": Platform.X_STD,
    "x_premium": Platform.X_PREMIUM,
}

# Runtime cache for YAML overrides (populated lazily by load_platform_encode_overrides).
_override_specs: dict[str, RenderSpec] = {}


def load_platform_encode_overrides(yaml_path: Path) -> dict[str, RenderSpec]:
    """Load per-platform encode spec overrides from a YAML config file.

    The YAML maps platform name → partial RenderSpec fields.  Only fields
    present in the YAML are overridden; everything else inherits from the
    built-in PLATFORM_SPECS in ffmpeg.py.

    Returns a dict of platform name → merged RenderSpec.  Returns an empty
    dict if the file is missing or empty (fail-open: code defaults win).
    """
    if not yaml_path.exists():
        logger.debug("No platform encode overrides at %s — using built-in specs", yaml_path)
        return {}

    try:
        with open(yaml_path) as f:
            raw = yaml.safe_load(f)
    except Exception:
        logger.warning("Failed to parse %s — using built-in specs", yaml_path, exc_info=True)
        return {}

    if not raw or not isinstance(raw, dict):
        return {}

    result: dict[str, RenderSpec] = {}
    for platform_name, overrides in raw.items():
        enum_key = _PLATFORM_ALIAS.get(platform_name)
        if enum_key is None:
            logger.debug("Ignoring unknown platform '%s' in encode overrides", platform_name)
            continue
        if not isinstance(overrides, dict):
            continue
        # Start from the built-in spec and override specified fields
        base = PLATFORM_SPECS[enum_key]
        merged = base.model_copy(update=overrides)
        result[platform_name] = merged

    if result:
        logger.info(
            "Loaded platform encode overrides for: %s",
            ", ".join(sorted(result)),
        )

    return result


def _get_encode_args(platform: str = "instagram") -> list[str]:
    """Build FFmpeg output args for *platform* using PLATFORM_SPECS.

    Falls back to Instagram spec for unknown platforms.  Adds pix_fmt and
    movflags which RenderSpec.to_output_args() does not include (they are
    container-level flags, not codec parameters).
    """
    # Check YAML overrides first, then built-in specs
    if platform in _override_specs:
        spec = _override_specs[platform]
    else:
        enum_key = _PLATFORM_ALIAS.get(platform, Platform.INSTAGRAM)
        spec = PLATFORM_SPECS.get(enum_key, PLATFORM_SPECS[Platform.INSTAGRAM])

    args = spec.to_output_args()

    # Append pix_fmt (always yuv420p for web delivery) — not in RenderSpec
    args += ["-pix_fmt", "yuv420p"]

    # bt709 color space tagging (web standard)
    args += ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]

    # Append movflags for progressive download — not in RenderSpec
    args += ["-movflags", "+faststart"]

    return args


def _get_landscape_encode_args(platform: str = "facebook") -> list[str]:
    """Build FFmpeg output args for landscape variants (Facebook, X)."""
    return _get_encode_args(platform)


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
    top_bar_height_pct: float = 0.12  # LayoutStandard.top_bar_pct
    bottom_bar_height_pct: float = 0.18  # LayoutStandard.bottom_bar_pct
    logo_height_px: int = 60  # LayoutStandard.logo_height
    logo_x_offset: int = 24
    hook_font_size: int = 32  # LayoutStandard.hook_font_size
    hook_x_offset: int = 24
    hook_max_lines: int = 2
    bar_color: list[int] = [0, 0, 0]
    hook_color: list[int] = [255, 255, 255]
    landscape_mode: str = "blurred_pillarbox"
    smart_crop: bool = False
    smart_crop_min_aspect: float = 1.2
    platforms_vertical: list[str] = ["instagram", "youtube", "tiktok", "threads"]
    platforms_landscape: list[str] = ["facebook", "twitter"]


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
        sandbox_runner: SandboxedFFmpegRunner | None = None,
    ) -> None:
        self._config = config
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
                f"FFmpeg failed [{label}] (exit {exc.returncode}):\n" + (exc.stderr or "")[-2000:]
            ) from exc

    # ── Public API ────────────────────────────────────────────────────────────

    # compose_vertical() + private helpers REMOVED in DEAD #1 (2026-06-13).
    # All 5 niches render via FrameCompositor (see [[task #45]]), so the
    # sandwich-render path in VideoCompositor was dead. Only derive_landscape
    # remains — gaming uses it to produce 16:9 variants from the vertical
    # master for Facebook/X.

    def derive_landscape(
        self,
        vertical_master: Path,
        output_path: Path,
        width: int = LANDSCAPE_WIDTH_FB,
        height: int = LANDSCAPE_HEIGHT_FB,
        platform: str = "facebook",
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
            raise FileNotFoundError(f"Vertical master not found: {vertical_master}")

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
            ffmpeg,
            "-y",
            "-i",
            str(vertical_master),
            "-filter_complex",
            filter_complex,
            "-map",
            "[out]",
            "-map",
            "0:a?",
            *_get_landscape_encode_args(platform),
            str(output_path),
        ]

        self._run_ffmpeg_cmd(cmd, label="landscape")
        return output_path

    # ── Filter builders + internal helpers REMOVED in DEAD #1 (2026-06-13) ───
    #
    # _build_sandwich_filter / _overlay_logo / _overlay_hook_text were
    # called only by _render_sandwich. _smart_crop_clips / _assemble_clips /
    # _render_sandwich / _get_logo_width_px / _wrap_hook were called only by
    # compose_vertical. With compose_vertical gone, the whole chain became
    # unreachable. The FrameCompositor path owns sandwich rendering for all
    # 5 niches today; see [[task #45]] for the architectural merge between
    # FrameCompositor and the remaining VideoCompositor surface.
