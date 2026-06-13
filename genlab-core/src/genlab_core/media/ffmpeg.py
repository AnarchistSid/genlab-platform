"""
genlab_core.media.ffmpeg — Video rendering with per-platform quality specs.

What this module owns:
  * `Platform` enum — canonical platform identifiers used across the
    publisher (YouTube, Instagram, TikTok, Facebook, X std/premium, Threads)
  * `RenderSpec` — validated Pydantic model for FFmpeg encoding params
  * `PLATFORM_SPECS` — per-platform encoding specs (codec, CRF, preset)
  * `get_ffmpeg_binary()` / `get_ffprobe_binary()` — binary locators
  * `resolve_twitter_spec()` — runtime tier check (standard 720p vs premium 1080p)

What this module no longer owns (DEAD #1, 2026-06-13):
  The lossless-FFV1-master-then-transcode pipeline (``render_master`` +
  ``transcode_for_platforms`` + their sync wrappers and GPU detection
  helpers) was aspirational architecture that never shipped. Production
  has always been libx264 single-pass with no master stage — see
  ``publishing/transcode.py`` for the actual encode path. The dead code
  was removed in commit DEAD #1 along with ``MASTER_SPEC``, ``HWAccel``,
  ``detect_hw_accel``, and ``_apply_hw_accel`` (none of which had any
  non-test callers).
"""

from __future__ import annotations

import functools
import logging
import os
import shutil
import sys
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# -- Platform enum -------------------------------------------------------------


class Platform(StrEnum):
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    FACEBOOK = "facebook"
    X_STD = "x_standard"  # Standard account: 720p cap
    X_PREMIUM = "x_premium"  # Premium account: 1080p
    THREADS = "threads"


# -- RenderSpec: validated encoding parameters ---------------------------------


class RenderSpec(BaseModel):
    """
    Fully validated FFmpeg encoding parameters.

    Using Pydantic serves two purposes:
    1. Prevents argument injection -- all values are validated before
       they are interpolated into FFmpeg command strings.
    2. Makes encoding decisions explicit and auditable in code/config
       rather than scattered across ad-hoc subprocess calls.
    """

    codec: str = "libx264"
    width: int = 1080
    height: int = 1920
    fps: str | int = 30  # 'source' preserves input FPS
    audio_codec: str = "aac"
    audio_bitrate: str = "256k"
    audio_sample_rate: int = 48000
    crf: int | None = Field(default=18, ge=0, le=63)  # None for lossless
    preset: str | None = "medium"
    # H.264/H.265 specific
    maxrate: str | None = None  # e.g. "12M" for TikTok ceiling
    bufsize: str | None = None  # e.g. "24M" paired with maxrate
    # Color space -- always force bt709 for web delivery
    colorspace: str = "bt709"
    color_primaries: str = "bt709"
    color_transfer: str = "bt709"
    # Safe-zone padding (Facebook safe zones: 14% top, 35% bottom)
    safe_zone_top_pct: float = 0.0
    safe_zone_bottom_pct: float = 0.0

    def to_output_args(self) -> list[str]:
        """Convert spec to FFmpeg output arguments list."""
        args = ["-c:v", self.codec]

        # CRF (None for FFV1 lossless)
        if self.crf is not None:
            args += ["-crf", str(self.crf)]
            if self.preset:
                args += ["-preset", self.preset]

        # Frame rate
        if self.fps != "source":
            args += ["-r", str(self.fps)]

        # Resolution
        vf_parts = [f"scale={self.width}:{self.height}"]

        # Safe zone padding (Facebook)
        if self.safe_zone_top_pct > 0 or self.safe_zone_bottom_pct > 0:
            top_px = int(self.height * self.safe_zone_top_pct)
            bot_px = int(self.height * self.safe_zone_bottom_pct)
            vf_parts.append(f"pad={self.width}:{self.height + top_px + bot_px}:0:{top_px}")

        if vf_parts:
            args += ["-vf", ",".join(vf_parts)]

        # Bitrate ceiling (TikTok)
        if self.maxrate:
            args += [
                "-maxrate",
                self.maxrate,
                "-bufsize",
                self.bufsize or self.maxrate,
            ]

        # H.265 Apple compatibility tag
        if self.codec == "libx265":
            args += ["-tag:v", "hvc1"]

        # Color space -- always explicit to prevent washed-out transcode artifacts
        args += [
            "-colorspace",
            self.colorspace,
            "-color_primaries",
            self.color_primaries,
            "-color_trc",
            self.color_transfer,
        ]

        # Audio
        args += [
            "-c:a",
            self.audio_codec,
            "-b:a",
            self.audio_bitrate,
            "-ar",
            str(self.audio_sample_rate),
        ]

        return args


# -- Per-platform specs --------------------------------------------------------
# Constants, not YAML, because they encode platform technical requirements.
# Business rules (tone, hashtags, CTA) live in YAML. Technical constraints here.

# CRF + maxrate/bufsize tuned per platform's published spec.
#
# Lessons from 2026-05-20 publish failures: CRF alone is not enough.
# Without a maxrate ceiling, high-motion source (sports, gaming) can
# encode at 8-15 Mbps which IG/Threads/Facebook reject during upload
# (container processing errors 2207082/2207085, "reduce data" errors).
# Each platform's max-accepted bitrate is sourced from their developer
# docs as of 2026:
#   IG Reels:   ≤5 Mbps recommended, ~30MB sweet spot for 60s
#   Threads:    follows IG specs (same backend)
#   Facebook:   ≤6 Mbps recommended
#   YouTube:    re-encodes everything; cap looser (≤8 Mbps)
#   TikTok:     accepts up to 12 Mbps for Reels-style
PLATFORM_SPECS: dict[Platform, RenderSpec] = {
    # Preset = "fast" across all platforms. 2026-05-21 forensics:
    # "medium" preset hit the 300s subprocess timeout on the 2 vCPU
    # Hetzner VPS for 60s anime reels, dropping us into the "using
    # original" fallback path that ships uncapped-bitrate uploads
    # (IG/FB/Threads then reject them silently). "fast" cuts encode
    # time ~2x with negligible quality loss at the CRF values we use,
    # and all platforms re-encode their incoming videos anyway.
    Platform.YOUTUBE: RenderSpec(
        codec="libx264",  # x265 OOMs on 4GB VPS; x264 is safe and YouTube re-encodes anyway
        fps="source",  # 60fps gaming preserved through to YouTube
        audio_bitrate="320k",
        crf=20,
        preset="fast",  # was "medium" — hit 300s timeout on long reels
        maxrate="8M",
        bufsize="16M",
    ),
    Platform.INSTAGRAM: RenderSpec(
        codec="libx264",
        fps=30,
        audio_bitrate="192k",
        crf=22,
        preset="fast",
        maxrate="4M",
        bufsize="8M",
    ),
    Platform.TIKTOK: RenderSpec(
        codec="libx264",
        fps=30,
        audio_bitrate="192k",
        crf=20,
        preset="fast",
        maxrate="6M",
        bufsize="12M",
    ),
    Platform.FACEBOOK: RenderSpec(
        codec="libx264",
        fps=30,
        audio_bitrate="192k",
        crf=22,
        preset="fast",
        maxrate="5M",
        bufsize="10M",
        safe_zone_top_pct=0.14,
        safe_zone_bottom_pct=0.35,
    ),
    Platform.X_STD: RenderSpec(
        codec="libx264",
        width=720,
        height=1280,
        fps=30,
        audio_bitrate="128k",
        crf=22,
        preset="fast",
        maxrate="3M",
        bufsize="6M",
    ),
    Platform.X_PREMIUM: RenderSpec(
        codec="libx264",
        width=1080,
        height=1920,
        fps=30,
        audio_bitrate="192k",
        crf=20,
        preset="fast",
        maxrate="5M",
        bufsize="10M",
    ),
    Platform.THREADS: RenderSpec(
        codec="libx264",
        fps=30,
        audio_bitrate="192k",
        crf=22,
        preset="fast",
        maxrate="4M",
        bufsize="8M",
    ),
}


# -- FFmpeg binary discovery ---------------------------------------------------


@functools.lru_cache(maxsize=1)
def get_ffmpeg_binary() -> str:
    """
    Find FFmpeg binary using a prioritised discovery chain.

    Priority:
      1. FFMPEG_BINARY env var (explicit override)
      2. Conda environment: sys.prefix/bin/ffmpeg
      3. System PATH via shutil.which
    """
    if env := os.environ.get("FFMPEG_BINARY"):
        return env
    conda_path = os.path.join(sys.prefix, "bin", "ffmpeg")
    if os.path.isfile(conda_path):
        return conda_path
    if which := shutil.which("ffmpeg"):
        return which
    raise RuntimeError(
        "FFmpeg not found. Install: brew install ffmpeg (macOS) or "
        "apt-get install ffmpeg (Linux), or set FFMPEG_BINARY env var."
    )


@functools.lru_cache(maxsize=1)
def get_ffprobe_binary() -> str:
    """Find ffprobe binary (same discovery logic as ffmpeg)."""
    if env := os.environ.get("FFPROBE_BINARY"):
        return env
    conda_path = os.path.join(sys.prefix, "bin", "ffprobe")
    if os.path.isfile(conda_path):
        return conda_path
    if which := shutil.which("ffprobe"):
        return which
    raise RuntimeError("ffprobe not found. Install FFmpeg (includes ffprobe).")


# -- X/Twitter tier detection --------------------------------------------------


def resolve_twitter_spec(niche_id: str, backlog_client) -> RenderSpec:
    """
    Return the correct X/Twitter RenderSpec based on account tier.

    Standard accounts: 720p cap. Premium accounts: 1080p delivery.
    """
    try:
        account = backlog_client.get_platform_account(niche_id, "twitter")
        tier = account.get("tier", "standard")
        if tier == "premium":
            logger.info("[TWITTER] Premium tier detected -- using 1080p spec")
            return PLATFORM_SPECS[Platform.X_PREMIUM]
    except Exception as e:
        logger.debug(
            "[TWITTER] Could not determine tier: %s -- defaulting to standard",
            e,
        )
    return PLATFORM_SPECS[Platform.X_STD]


# -- Core rendering functions: REMOVED in DEAD #1 (2026-06-13) ---------------
#
# The lossless-master-then-transcode pipeline was aspirational architecture
# that never shipped — production has always been libx264 single-pass via
# ``publishing/transcode.py``. Removed: ``render_master`` /
# ``transcode_for_platforms`` (+ sync wrappers + private helpers
# ``_encode_single``, ``_encode_h264_tee``, ``_run_ffmpeg``,
# ``_verify_output``). Their tests (``test_ffmpeg_transcode_sync.py``)
# went with them.
#
# If you find yourself wanting to add a master/transcode pipeline back,
# read ``publishing/transcode.py`` first and decide whether the new code
# belongs there or whether the actual production path needs upgrading.
