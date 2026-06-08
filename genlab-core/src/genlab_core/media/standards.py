"""Canonical video standards for all Gen Lab channels.

This module defines *intent-level* contracts — what quality, frame geometry,
and layout every rendered video must satisfy. The encoding-quality fields
(``codec``, ``crf``, ``preset``, ``max_bitrate_kbps``) **derive** from
:data:`genlab_core.media.ffmpeg.PLATFORM_SPECS` at import time so the two
modules cannot drift (audit M-2). The frame and layout standards remain
hand-defined here because they have no counterpart in the FFmpeg layer.

Usage::

    from genlab_core.video.standards import get_standard, Platform

    std = get_standard(Platform.INSTAGRAM)
    assert std.video.codec == "libx264"
    assert std.frame.width == 1080
    assert std.layout.top_bar_pct == 0.12

Configuration sprawl note (audit M-2)
-------------------------------------
``genlab-core/config/platform_encode_specs.yaml`` exists as an
*intended* override layer but is currently NOT wired into production —
the loader ``video_compositor.load_platform_encode_overrides()`` is
called only by tests. If an operator decides to activate it, the right
wire-in point is ``ffmpeg.py`` module load (apply the YAML to
``PLATFORM_SPECS`` so this module's derived values pick the overrides
up automatically). Until then, the YAML is documentation of *possible*
tunings, not active configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from genlab_core.media.ffmpeg import PLATFORM_SPECS
from genlab_core.media.ffmpeg import Platform as _FFmpegPlatform

# ── Platform enum (mirrors media.ffmpeg.Platform, kept here to avoid coupling) ─


class Platform(StrEnum):
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    FACEBOOK = "facebook"
    X_STANDARD = "x_standard"
    X_PREMIUM = "x_premium"
    THREADS = "threads"


# Bridge between standards.Platform (intent-level) and the lower-level
# ffmpeg.Platform enum so derivation from PLATFORM_SPECS stays one-way.
_TO_FFMPEG: dict[Platform, _FFmpegPlatform] = {
    Platform.YOUTUBE: _FFmpegPlatform.YOUTUBE,
    Platform.INSTAGRAM: _FFmpegPlatform.INSTAGRAM,
    Platform.TIKTOK: _FFmpegPlatform.TIKTOK,
    Platform.FACEBOOK: _FFmpegPlatform.FACEBOOK,
    Platform.X_STANDARD: _FFmpegPlatform.X_STD,
    Platform.X_PREMIUM: _FFmpegPlatform.X_PREMIUM,
    Platform.THREADS: _FFmpegPlatform.THREADS,
}


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class VideoStandard:
    """Encoding quality contract for a platform."""

    codec: str
    crf: int
    preset: str
    color_space: str = "bt709"
    vmaf_floor: int = 85
    max_bitrate_kbps: int | None = None


@dataclass(frozen=True)
class FrameStandard:
    """Frame geometry contract."""

    width: int = 1080
    height: int = 1920
    fps: int = 30
    aspect_ratio: str = "9:16"


@dataclass(frozen=True)
class LayoutStandard:
    """Visual layout contract (sandwich pattern)."""

    top_bar_pct: float = 0.12
    bottom_bar_pct: float = 0.18
    hook_font_size: int = 32
    logo_height: int = 60
    pillarbox_blur: bool = True


@dataclass(frozen=True)
class PlatformStandard:
    """Complete standard for one platform (video + frame + layout)."""

    platform: Platform
    video: VideoStandard
    frame: FrameStandard
    layout: LayoutStandard


# ── Per-platform frame + layout (hand-defined; no FFmpeg counterpart) ─────────

_VERTICAL_FRAME = FrameStandard()  # 1080×1920 @ 30fps, 9:16
_VERTICAL_FRAME_SOURCE_FPS = FrameStandard(fps=60)  # YouTube preserves source FPS
_LANDSCAPE_FRAME_FB = FrameStandard(width=1920, height=1080, aspect_ratio="16:9")
_LANDSCAPE_FRAME_X_STD = FrameStandard(width=1280, height=720, aspect_ratio="16:9")
_LANDSCAPE_FRAME_X_PREM = FrameStandard(width=1920, height=1080, aspect_ratio="16:9")

_DEFAULT_LAYOUT = LayoutStandard()

# Per-platform frame mapping. The video standard is DERIVED from
# PLATFORM_SPECS at import time below, so codec/crf/preset/bitrate can
# never drift between standards.py and ffmpeg.py.
_FRAMES: dict[Platform, FrameStandard] = {
    Platform.INSTAGRAM: _VERTICAL_FRAME,
    Platform.YOUTUBE: _VERTICAL_FRAME_SOURCE_FPS,
    Platform.TIKTOK: _VERTICAL_FRAME,
    Platform.FACEBOOK: _LANDSCAPE_FRAME_FB,
    Platform.X_STANDARD: _LANDSCAPE_FRAME_X_STD,
    Platform.X_PREMIUM: _LANDSCAPE_FRAME_X_PREM,
    Platform.THREADS: _VERTICAL_FRAME,
}


def _video_from_spec(platform: Platform) -> VideoStandard:
    """Build a :class:`VideoStandard` from the matching
    :data:`PLATFORM_SPECS` entry. Single source of truth for codec /
    crf / preset / max_bitrate — they live in ffmpeg.py and standards
    reads them from there. Audit M-2 — closes the three-way drift
    surface (was: PLATFORM_SPECS + _STANDARDS + dead YAML)."""
    spec = PLATFORM_SPECS[_TO_FFMPEG[platform]]
    # ``spec.crf`` is Optional in RenderSpec to allow lossless masters;
    # PlatformStandard demands an int. Every shipping platform sets a
    # CRF, so default to 22 if a future entry drops it.
    crf_value = spec.crf if spec.crf is not None else 22
    # ``spec.maxrate`` is a string like "12M"; convert to kbps int if
    # present, leave as None otherwise (the per-platform default).
    max_bitrate_kbps: int | None = None
    if getattr(spec, "maxrate", None):
        max_bitrate_kbps = _parse_bitrate_kbps(spec.maxrate)
    return VideoStandard(
        codec=spec.codec,
        crf=crf_value,
        preset=spec.preset or "medium",
        max_bitrate_kbps=max_bitrate_kbps,
    )


def _parse_bitrate_kbps(s: str) -> int | None:
    """Parse FFmpeg-style bitrate strings (``"12M"``, ``"800k"``, ``"5000000"``)
    into an integer kilobit-per-second value. Returns ``None`` on parse failure
    — the caller falls back to the per-platform default."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        if s[-1].upper() == "M":
            return int(float(s[:-1]) * 1000)
        if s[-1].upper() == "K":
            return int(float(s[:-1]))
        # Bare integer — assume bits per second.
        return int(s) // 1000
    except (ValueError, IndexError):
        return None


_STANDARDS: dict[Platform, PlatformStandard] = {
    platform: PlatformStandard(
        platform=platform,
        video=_video_from_spec(platform),
        frame=_FRAMES[platform],
        layout=_DEFAULT_LAYOUT,
    )
    for platform in Platform
}


def get_standard(platform: Platform) -> PlatformStandard:
    """Return the canonical standard for *platform*.

    Raises ``KeyError`` if the platform is not defined.
    """
    return _STANDARDS[platform]


def all_standards() -> dict[Platform, PlatformStandard]:
    """Return a copy of the full standards registry."""
    return dict(_STANDARDS)
