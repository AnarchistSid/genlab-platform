"""Landscape variant derivation + visual config loading.

Module-history note (post-DEAD #1 + ARCH #45, 2026-06-13):

This module used to contain the ``VideoCompositor`` class — a parallel
sandwich-render path that competed with the live ``FrameCompositor``.
DEAD #1 removed the dead ``compose_vertical`` chain; ARCH #45 then
collapsed the residual class into a single stateless function
``derive_landscape()``. The 5 niches now have ONE compositor system,
not two:

  * ``frame_compositor.FrameCompositor.compose()`` —
    canonical 9:16 vertical render with sandwich-pattern branding.
    Owns: layout-case dispatch, hook drawtext, logo overlay, branding
    text, source-clip trimming. Used by all 5 niches.

  * ``video_compositor.derive_landscape()`` —
    one-shot 9:16 → 16:9 derivation for Facebook/X delivery.
    Method: blurred pillarbox (never crop the subject). Optional
    sandbox routing via ``sandbox_runner`` kwarg.

What still lives here besides ``derive_landscape``:

  * ``VisualConfig`` + ``load_visual_config`` — Pydantic config model
    for visuals.yaml. Used by tools that need to read the niche's
    visual settings without instantiating the full ``ChannelBranding``
    (FrameCompositor's heavier-weight equivalent).
  * ``load_platform_encode_overrides`` — YAML reader for
    ``platform_encode_specs.yaml`` overrides. Currently INACTIVE in
    production (per CLAUDE.md M-2); kept for the eventual revival.
  * ``_get_encode_args`` / ``_get_landscape_encode_args`` — per-platform
    FFmpeg encode-arg builders. Consumed by ``derive_landscape``.

Usage:
    from genlab_core.media.video_compositor import derive_landscape

    derive_landscape(
        vertical_master=Path("/tmp/run/reel.mp4"),
        output_path=Path("/tmp/run/reel_landscape.mp4"),
        platform="facebook",
        sandbox_runner=runner,  # optional, defense-in-depth
    )
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


def _run_ffmpeg_with_optional_sandbox(
    cmd: list[str],
    *,
    sandbox_runner: SandboxedFFmpegRunner | None,
    timeout: int = _FFMPEG_TIMEOUT,
    label: str = "ffmpeg",
) -> subprocess.CompletedProcess:
    """Execute an FFmpeg command, optionally routed through a sandbox.

    Centralizes the sandbox-routing logic that the ARCH #45 merge
    extracted from ``VideoCompositor._run_ffmpeg_cmd``. Callers that
    need sandbox isolation pass a ``sandbox_runner``; callers that
    don't pass ``None`` and the command runs locally via ``run_ffmpeg``.
    """
    if sandbox_runner is not None:
        result = sandbox_runner.run_ffmpeg_sync(cmd, timeout=timeout)
        result.check(label)
        # Return a CompletedProcess-like for backward compat with callers.
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=result.stdout, stderr=result.stderr
        )

    try:
        return run_ffmpeg(cmd, timeout=timeout, fallback_preset="fast")
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"FFmpeg failed [{label}] (exit {exc.returncode}):\n" + (exc.stderr or "")[-2000:]
        ) from exc


def derive_landscape(
    vertical_master: Path,
    output_path: Path,
    *,
    width: int = LANDSCAPE_WIDTH_FB,
    height: int = LANDSCAPE_HEIGHT_FB,
    platform: str = "facebook",
    sandbox_runner: SandboxedFFmpegRunner | None = None,
) -> Path:
    """Derive a 16:9 variant from a 9:16 vertical master.

    Used for Facebook/X delivery where landscape aspect is preferred.
    The 5 niches render vertical via ``FrameCompositor.compose`` (the
    sandwich-pattern owner); this function consumes that output and
    produces the wide variant via blurred pillarbox — NEVER cropping
    the subject.

    Visual recipe:
        - Background: scale vertical_master to fill width×height with
          ``force_original_aspect_ratio=increase``, then ``gblur=sigma=20``
        - Foreground: scale vertical_master to ``height=height`` keeping
          its 9:16 aspect, then center horizontally over the blurred bg
        - The FrameCompositor sandwich bars and overlays survive in the fg

    Parameters
    ----------
    vertical_master:
        Path to the 9:16 master produced by ``FrameCompositor.compose()``.
    output_path:
        Where to write the 16:9 variant. Parent dir is created if missing.
    width, height:
        Output dimensions. Defaults match Facebook's 1920×1080 spec.
        Pass ``LANDSCAPE_WIDTH_X``/``LANDSCAPE_HEIGHT_X`` for X/Twitter's
        1280×720.
    platform:
        Used by ``_get_landscape_encode_args`` to pick CRF/preset/maxrate.
    sandbox_runner:
        Optional ``SandboxedFFmpegRunner``. When provided, the FFmpeg
        command runs inside the sandbox (the gaming pipeline does this
        for defense-in-depth). When ``None``, runs locally.

    ARCH #45 history: this was ``VideoCompositor.derive_landscape``,
    the only live method on the otherwise-empty post-DEAD-#1 class.
    Collapsing the class into a stateless function matches the actual
    usage (one-shot transformation, no state worth carrying across calls).
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

    _run_ffmpeg_with_optional_sandbox(cmd, sandbox_runner=sandbox_runner, label="landscape")
    return output_path


# ── VideoCompositor class REMOVED in ARCH #45 (2026-06-13) ────────────────────
#
# After DEAD #1 collapsed the compose_vertical chain, the class held only
# derive_landscape() and a private FFmpeg-runner helper. Two methods don't
# justify a class — and FrameCompositor (the live sandwich-render owner
# for all 5 niches) had its own parallel surface. Collapsing to module-level
# functions removes the false impression of "parallel compositor systems"
# while keeping the sandbox-routing semantics intact via the
# ``sandbox_runner`` parameter on derive_landscape().
#
# Migration: callers that did
#     comp = VideoCompositor(cfg, sandbox_runner=runner)
#     comp.derive_landscape(vert, land)
# now do
#     from genlab_core.media.video_compositor import derive_landscape
#     derive_landscape(vert, land, sandbox_runner=runner)
