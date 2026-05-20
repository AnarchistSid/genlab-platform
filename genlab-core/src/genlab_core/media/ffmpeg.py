"""
genlab_core.media.ffmpeg — Video rendering with per-platform quality specs.

Architecture:
  1. render_master() produces one FFV1 lossless intermediate
  2. transcode_for_platforms() derives all platform variants from master
     using the two-group transcode tree (H.265 pass + H.264 tee pass)

Why FFV1 as the intermediate codec:
  FFV1 is mathematically lossless. Every platform variant is transcoded
  from a perfect original. The current approach (H.264 CRF 18 as intermediate)
  means every platform variant suffers a second lossy encode compounding the
  first. FFV1 masters are larger (3-5x H.264) but they are temporary and
  evicted after all variants are confirmed uploaded.

Why H.265 only for YouTube:
  Instagram and TikTok technically accept H.265, but Android HEVC decoder
  compatibility gaps cause playback failures or forced software decoding.
  H.264 CRF 15 at slow preset produces a bitrate roughly equivalent to
  H.265 CRF 22. The H.265 win is exclusively on YouTube where their
  AV1/VP9 transcoder specifically benefits from HEVC source quality.

Why preserve FPS in master (especially important for CriticalRush):
  Gaming footage is commonly captured at 60 FPS. Forcing 30 FPS at the
  master stage throws away half the temporal information permanently.
  FPS decisions happen per-platform in the variant stage, not the master.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# -- Platform enum -------------------------------------------------------------


class Platform(str, Enum):
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
            vf_parts.append(
                f"pad={self.width}:{self.height + top_px + bot_px}:0:{top_px}"
            )

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

MASTER_SPEC = RenderSpec(
    codec="ffv1",
    fps="source",
    audio_codec="pcm_s24le",
    audio_bitrate="lossless",
    crf=None,
    preset=None,
)

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
    Platform.YOUTUBE: RenderSpec(
        codec="libx264",  # x265 OOMs on 4GB VPS; x264 is safe and YouTube re-encodes anyway
        fps="source",  # 60fps gaming preserved through to YouTube
        audio_bitrate="320k",
        crf=20,
        preset="medium",  # slow + x264 is diminishing returns for YouTube
        maxrate="8M",
        bufsize="16M",
    ),
    Platform.INSTAGRAM: RenderSpec(
        codec="libx264",
        fps=30,
        audio_bitrate="192k",
        crf=22,
        preset="medium",
        maxrate="4M",
        bufsize="8M",
    ),
    Platform.TIKTOK: RenderSpec(
        codec="libx264",
        fps=30,
        audio_bitrate="192k",
        crf=20,
        preset="medium",
        maxrate="6M",
        bufsize="12M",
    ),
    Platform.FACEBOOK: RenderSpec(
        codec="libx264",
        fps=30,
        audio_bitrate="192k",
        crf=22,
        preset="medium",
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
        preset="medium",
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
        preset="medium",
        maxrate="5M",
        bufsize="10M",
    ),
    Platform.THREADS: RenderSpec(
        codec="libx264",
        fps=30,
        audio_bitrate="192k",
        crf=22,
        preset="medium",
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


# -- GPU hardware detection ----------------------------------------------------


@dataclass
class HWAccel:
    """Available hardware acceleration for this machine."""

    h264_encoder: str = "libx264"
    h265_encoder: str = "libx265"
    speedup_factor: float = 1.0


@functools.lru_cache(maxsize=1)
def detect_hw_accel() -> HWAccel:
    """
    Probe FFmpeg for available hardware encoders.

    Falls back gracefully to CPU if no GPU acceleration is found.
    CRF+2 on GPU hardware encoders produces perceptual quality
    equivalent to CRF on CPU slow.
    """
    try:
        result = subprocess.run(
            [get_ffmpeg_binary(), "-encoders", "-v", "quiet"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        encoders = result.stdout

        if "h264_nvenc" in encoders:
            logger.info("GPU: NVIDIA CUDA (h264_nvenc / hevc_nvenc)")
            return HWAccel(
                h264_encoder="h264_nvenc",
                h265_encoder="hevc_nvenc",
                speedup_factor=6.0,
            )
        if "h264_videotoolbox" in encoders:
            logger.info(
                "GPU: Apple Silicon (h264_videotoolbox / hevc_videotoolbox)"
            )
            return HWAccel(
                h264_encoder="h264_videotoolbox",
                h265_encoder="hevc_videotoolbox",
                speedup_factor=4.0,
            )
        if "h264_amf" in encoders:
            logger.info("GPU: AMD AMF (h264_amf / hevc_amf)")
            return HWAccel(
                h264_encoder="h264_amf",
                h265_encoder="hevc_amf",
                speedup_factor=4.0,
            )
    except Exception as e:
        logger.debug("GPU probe failed: %s -- using CPU", e)

    logger.info("GPU: none detected -- using CPU libx264 / libx265")
    return HWAccel()


def _apply_hw_accel(spec: RenderSpec, hw: HWAccel) -> RenderSpec:
    """
    Return a copy of spec with hardware-accelerated encoder substituted.

    Applies the CRF+2 adjustment for perceptual parity on GPU encoders.
    """
    if hw.speedup_factor == 1.0:
        return spec

    adjusted = spec.model_copy(deep=True)
    if spec.codec == "libx264":
        adjusted.codec = hw.h264_encoder
        if adjusted.crf is not None:
            adjusted.crf = min(51, adjusted.crf + 2)
    elif spec.codec == "libx265":
        adjusted.codec = hw.h265_encoder
        if adjusted.crf is not None:
            adjusted.crf = min(63, adjusted.crf + 2)
    return adjusted


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


# -- Core rendering functions -------------------------------------------------


async def render_master(source: Path, output: Path) -> Path:
    """
    Render a lossless FFV1 master from the source video.

    The master preserves the source frame rate and uses lossless audio.
    It is the common ancestor for all platform variants.

    Raises:
        RuntimeError: If FFmpeg fails or output fails verification.
    """
    ffmpeg = get_ffmpeg_binary()
    args = [
        ffmpeg,
        "-y",
        "-i",
        str(source),
        *MASTER_SPEC.to_output_args(),
        str(output),
    ]
    logger.info("[RENDER] Creating FFV1 master: %s", output.name)
    await _run_ffmpeg(args, label="master")
    if not await _verify_output(output):
        raise RuntimeError(f"Master output failed verification: {output}")
    logger.info(
        "[RENDER] Master created: %s (%.1f MB)",
        output.name,
        output.stat().st_size / 1e6,
    )
    return output


async def transcode_for_platforms(
    master: Path,
    platforms: list[Platform],
    output_dir: Path,
    use_gpu: bool = True,
) -> dict[Platform, Path]:
    """
    Derive all platform variants from a lossless master.

    Uses the two-group transcode tree:
      - H.265 targets (YouTube): one encode pass per target
      - H.264 targets (everything else): one tee-muxer pass for all
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    hw = detect_hw_accel() if use_gpu else HWAccel()

    h265_platforms = [
        p for p in platforms if PLATFORM_SPECS[p].codec == "libx265"
    ]
    h264_platforms = [
        p for p in platforms if PLATFORM_SPECS[p].codec == "libx264"
    ]

    results: dict[Platform, Path] = {}

    for platform in h265_platforms:
        spec = _apply_hw_accel(PLATFORM_SPECS[platform], hw)
        out = output_dir / f"{platform.value}.mp4"
        logger.info("[RENDER] Encoding %s (H.265 pass)", platform.value)
        await _encode_single(master, spec, out)
        results[platform] = out

    if h264_platforms:
        logger.info(
            "[RENDER] Encoding %d H.264 variants (tee pass)",
            len(h264_platforms),
        )
        outputs = await _encode_h264_tee(
            master, h264_platforms, output_dir, hw
        )
        results.update(outputs)

    for platform, path in results.items():
        if not await _verify_output(path):
            raise RuntimeError(
                f"Platform variant failed verification: {platform} -> {path}"
            )

    return results


async def _encode_single(
    master: Path, spec: RenderSpec, output: Path
) -> None:
    """Encode one variant from the master."""
    ffmpeg = get_ffmpeg_binary()
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(master),
        *spec.to_output_args(),
        str(output),
    ]
    await _run_ffmpeg(cmd, label=output.stem)


async def _encode_h264_tee(
    master: Path,
    platforms: list[Platform],
    output_dir: Path,
    hw: HWAccel,
) -> dict[Platform, Path]:
    """
    Encode multiple H.264 variants in one decode pass using FFmpeg tee muxer.

    One decode of the master simultaneously feeds all H.264 encoders.
    """
    if len(platforms) == 1:
        spec = _apply_hw_accel(PLATFORM_SPECS[platforms[0]], hw)
        out = output_dir / f"{platforms[0].value}.mp4"
        await _encode_single(master, spec, out)
        return {platforms[0]: out}

    outputs: dict[Platform, Path] = {}
    tee_parts = []

    for platform in platforms:
        spec = _apply_hw_accel(PLATFORM_SPECS[platform], hw)
        out = output_dir / f"{platform.value}.mp4"
        outputs[platform] = out
        args_str = ":".join(spec.to_output_args())
        tee_parts.append(f"[{args_str}]{out}")

    ffmpeg = get_ffmpeg_binary()
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(master),
        "-f",
        "tee",
        "|".join(tee_parts),
    ]
    await _run_ffmpeg(cmd, label="h264_tee")
    return outputs


async def _run_ffmpeg(cmd: list[str], label: str = "ffmpeg") -> None:
    """Run an FFmpeg command asynchronously and raise on non-zero exit."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        preexec_fn=os.setpgrp if hasattr(os, "setpgrp") else None,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"FFmpeg failed [{label}] (exit {proc.returncode}):\n"
            + stderr.decode(errors="replace")[-2000:]
        )


async def _verify_output(path: Path) -> bool:
    """
    Verify FFmpeg output has non-zero duration via ffprobe.

    FFmpeg can exit with code 0 but produce a corrupt or zero-duration
    file when the input has issues or disk is full.
    """
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        ffprobe = get_ffprobe_binary()
        proc = await asyncio.create_subprocess_exec(
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        duration = float(stdout.decode().strip() or "0")
        return proc.returncode == 0 and duration > 0
    except Exception as e:
        logger.warning("Output verification failed for %s: %s", path, e)
        return False


# ── Sync wrappers ─────────────────────────────────────────────────────
# The async functions above use asyncio.create_subprocess_exec for parallel
# FFmpeg execution. These sync wrappers handle the event loop safely,
# avoiding conflicts with async_bridge's persistent loop.


def render_master_sync(source: Path, output: Path) -> Path:
    """Sync wrapper for render_master — safe to call from pipeline stages."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        # Already in an async context — use run_coroutine_threadsafe
        import concurrent.futures
        future = asyncio.run_coroutine_threadsafe(render_master(source, output), loop)
        return future.result(timeout=600)
    else:
        return asyncio.run(render_master(source, output))


def transcode_for_platforms_sync(
    master: Path, output_dir: Path, platforms: list[Platform] | None = None,
) -> dict[str, Path]:
    """Sync wrapper for transcode_for_platforms — safe to call from pipeline stages."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        import concurrent.futures
        future = asyncio.run_coroutine_threadsafe(
            transcode_for_platforms(master, output_dir, platforms), loop,
        )
        return future.result(timeout=600)
    else:
        return asyncio.run(transcode_for_platforms(master, output_dir, platforms))
