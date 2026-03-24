"""Canonical video standards for all Gen Lab channels.

This module defines *intent-level* contracts — what quality, frame geometry,
and layout every rendered video must satisfy.  These are upstream of FFmpeg
encoding parameters (``genlab_core.media.ffmpeg.PLATFORM_SPECS``) and the
compositor layout (``genlab_core.media.video_compositor.VisualConfig``).

Usage::

    from genlab_core.video.standards import get_standard, Platform

    std = get_standard(Platform.INSTAGRAM)
    assert std.video.codec == "libx264"
    assert std.frame.width == 1080
    assert std.layout.top_bar_pct == 0.12
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# ── Platform enum (mirrors media.ffmpeg.Platform, kept here to avoid coupling) ─


class Platform(str, Enum):
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    FACEBOOK = "facebook"
    X_STANDARD = "x_standard"
    X_PREMIUM = "x_premium"
    THREADS = "threads"


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


# ── Per-platform definitions ─────────────────────────────────────────────────

_VERTICAL_FRAME = FrameStandard()  # 1080×1920 @ 30fps, 9:16
_VERTICAL_FRAME_SOURCE_FPS = FrameStandard(fps=60)  # YouTube preserves source FPS
_LANDSCAPE_FRAME_FB = FrameStandard(width=1920, height=1080, aspect_ratio="16:9")
_LANDSCAPE_FRAME_X_STD = FrameStandard(width=1280, height=720, aspect_ratio="16:9")
_LANDSCAPE_FRAME_X_PREM = FrameStandard(width=1920, height=1080, aspect_ratio="16:9")

_DEFAULT_LAYOUT = LayoutStandard()

_STANDARDS: dict[Platform, PlatformStandard] = {
    Platform.INSTAGRAM: PlatformStandard(
        platform=Platform.INSTAGRAM,
        video=VideoStandard(codec="libx264", crf=15, preset="slow"),
        frame=_VERTICAL_FRAME,
        layout=_DEFAULT_LAYOUT,
    ),
    Platform.YOUTUBE: PlatformStandard(
        platform=Platform.YOUTUBE,
        video=VideoStandard(codec="libx265", crf=18, preset="slow"),
        frame=_VERTICAL_FRAME_SOURCE_FPS,
        layout=_DEFAULT_LAYOUT,
    ),
    Platform.TIKTOK: PlatformStandard(
        platform=Platform.TIKTOK,
        video=VideoStandard(
            codec="libx264", crf=15, preset="slow", max_bitrate_kbps=12_000,
        ),
        frame=_VERTICAL_FRAME,
        layout=_DEFAULT_LAYOUT,
    ),
    Platform.FACEBOOK: PlatformStandard(
        platform=Platform.FACEBOOK,
        video=VideoStandard(codec="libx264", crf=17, preset="medium"),
        frame=_LANDSCAPE_FRAME_FB,
        layout=_DEFAULT_LAYOUT,
    ),
    Platform.X_STANDARD: PlatformStandard(
        platform=Platform.X_STANDARD,
        video=VideoStandard(codec="libx264", crf=20, preset="medium"),
        frame=_LANDSCAPE_FRAME_X_STD,
        layout=_DEFAULT_LAYOUT,
    ),
    Platform.X_PREMIUM: PlatformStandard(
        platform=Platform.X_PREMIUM,
        video=VideoStandard(codec="libx264", crf=17, preset="slow"),
        frame=_LANDSCAPE_FRAME_X_PREM,
        layout=_DEFAULT_LAYOUT,
    ),
    Platform.THREADS: PlatformStandard(
        platform=Platform.THREADS,
        video=VideoStandard(codec="libx264", crf=15, preset="slow"),
        frame=_VERTICAL_FRAME,
        layout=_DEFAULT_LAYOUT,
    ),
}


def get_standard(platform: Platform) -> PlatformStandard:
    """Return the canonical standard for *platform*.

    Raises ``KeyError`` if the platform is not defined.
    """
    return _STANDARDS[platform]


def all_standards() -> dict[Platform, PlatformStandard]:
    """Return a copy of the full standards registry."""
    return dict(_STANDARDS)
