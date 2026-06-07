"""Per-platform video transcode for the multi-platform publisher.

Single public entry point: :func:`transcode_for_platform` takes a source
video and a platform name, returns a path to a platform-tuned variant
(or the original on failure, fail-open). Wraps ffmpeg via
``PLATFORM_SPECS`` from :mod:`genlab_core.media.ffmpeg` and the
optional ``platform_encode_specs.yaml`` for per-platform duration trims.

Lives in its own module so the multi-platform publisher orchestrator
(:mod:`genlab_core.publishing.publish_all_platforms`) stays focused on
the orchestration flow. Extracted in the refactor-#9 decomposition
(PR 2/N). Re-exported from the orchestrator for backwards-compatible
imports (notably ``test_encode_concurrency`` which imports the
underscore-prefixed names from there).

Concurrency model
-----------------
The publisher fans out to up to 5 platforms in parallel; each call to
:func:`transcode_for_platform` spawns its own ffmpeg subprocess. Without
the :data:`ENCODE_SEMAPHORE` cap that would mean up to 5 concurrent
encodes on the 2-vCPU/4GB box, the realised OOM mechanism (R-03/R-54).
The module-level semaphore (default size 1, tunable via
``GENLAB_MAX_CONCURRENT_ENCODES``) bounds that across the entire
process — the semaphore lives here, with the function that owns the
guarantee, rather than in the orchestrator.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path

import yaml

from genlab_core.media.ffmpeg import PLATFORM_SPECS, Platform, get_ffmpeg_binary

logger = logging.getLogger(__name__)

# Map legacy platform names to the ffmpeg ``Platform`` enum used by
# ``PLATFORM_SPECS``. Kept in sync with ``platform_status.PLATFORM_ID_MAP``
# at the level of which platform names exist; the two maps target
# different downstream registries (platform-client lookups vs ffmpeg specs).
PLATFORM_MAP: dict[str, Platform] = {
    "youtube": Platform.YOUTUBE,
    "instagram": Platform.INSTAGRAM,
    "facebook": Platform.FACEBOOK,
    "twitter": Platform.X_STD,
    "threads": Platform.THREADS,
    "tiktok": Platform.TIKTOK,
}

# R-54/R-03: serialise ffmpeg encodes across the publisher's parallel
# per-platform fan-out (up to 5 concurrent transcodes otherwise). Cap is
# tunable via ``GENLAB_MAX_CONCURRENT_ENCODES`` on larger hosts; default
# 1 (= serial) suits the 2-vCPU/4GB Hetzner box this runs on.
MAX_CONCURRENT_ENCODES: int = max(
    1, int(os.environ.get("GENLAB_MAX_CONCURRENT_ENCODES", "1") or "1")
)
ENCODE_SEMAPHORE: threading.BoundedSemaphore = threading.BoundedSemaphore(MAX_CONCURRENT_ENCODES)


def _platform_max_duration(platform: str) -> int | None:
    """Read the per-platform ``max_seconds`` trim from
    ``config/platform_encode_specs.yaml`` if present, else return None.

    The yaml file is optional — most niches don't override the
    PLATFORM_SPECS defaults. The two candidate paths cover both the
    repo-root deploy layout and the installed-package layout."""
    candidates = [
        Path(__file__).resolve().parent.parent / "config" / "platform_encode_specs.yaml",
        Path(__file__).resolve().parent.parent.parent.parent
        / "config"
        / "platform_encode_specs.yaml",
    ]
    try:
        for path in candidates:
            if path.exists():
                with open(path) as f:
                    cfg = yaml.safe_load(f) or {}
                durations = cfg.get("platform_durations", {}).get(platform, {})
                return durations.get("max_seconds")
    except Exception as exc:
        # Non-critical: fall through to no trim. Logged at DEBUG because
        # platform_encode_specs.yaml is optional.
        logger.debug("[transcode] Could not load platform_encode_specs for %s: %s", platform, exc)
    return None


def transcode_for_platform(source: Path, platform: str) -> Path:
    """Transcode ``source`` for ``platform`` using ``PLATFORM_SPECS``.

    Returns the platform variant path. If the platform is unknown, or
    the transcode fails for any reason (subprocess error, timeout,
    binary missing), returns the original ``source`` path — fail-open,
    because uploading the original is preferable to dropping the post,
    even though it risks platform rejection on bitrate/container
    constraints (IG/Threads/FB are the strict ones).

    Idempotent: a previously-encoded variant (recognisable by the
    ``_{platform}`` suffix and size > 10KB) is reused, not re-encoded.
    """
    plat_enum = PLATFORM_MAP.get(platform)
    if plat_enum is None or plat_enum not in PLATFORM_SPECS:
        return source

    spec = PLATFORM_SPECS[plat_enum]
    variant_path = source.parent / f"{source.stem}_{plat_enum.value}{source.suffix}"

    # Skip if variant already exists and is recent.
    if variant_path.exists() and variant_path.stat().st_size > 10240:
        return variant_path

    try:
        ffmpeg = get_ffmpeg_binary()
        max_duration = _platform_max_duration(platform)

        cmd = [ffmpeg, "-y", "-i", str(source)]
        # Apply duration trim if source exceeds platform max (trim from end, keep hook).
        if max_duration:
            cmd.extend(["-t", str(max_duration)])
        cmd.extend(["-c:v", spec.codec])
        if spec.crf is not None:
            cmd.extend(["-crf", str(spec.crf)])
        # Use spec preset (was hardcoded to "slow" — overrode per-platform tuning
        # and made the 2-CPU VPS painfully slow on every transcode).
        cmd.extend(["-preset", spec.preset or "medium"])
        # Apply bitrate ceiling when configured. Critical for IG/Threads/FB
        # which reject high-bitrate uploads with container-processing errors.
        # Without maxrate, CRF=15-22 on motion-heavy source produces 8-15
        # Mbps files that platforms refuse.
        if spec.maxrate:
            cmd.extend(["-maxrate", spec.maxrate, "-bufsize", spec.bufsize or spec.maxrate])
        if spec.width and spec.height:
            cmd.extend(["-vf", f"scale={spec.width}:{spec.height}"])
        cmd.extend(
            [
                "-colorspace",
                "bt709",
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-c:a",
                "aac",
                "-b:a",
                spec.audio_bitrate or "192k",
                "-ar",
                "48000",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(variant_path),
            ]
        )
        # 600s timeout (was 300) gives "fast" preset enough headroom for the
        # longest 60s reels on the 2 vCPU Hetzner VPS. The combination of slow
        # CPU + "medium" preset + max_duration=60 was reliably tripping the old
        # 300s ceiling and dropping us into the "using original" fallback that
        # ships uncapped-bitrate uploads — IG/FB/Threads then silently reject
        # them. Once the box gets a faster CPU or HW accel, drop this back.
        # R-54: hold the encode semaphore so the parallel per-platform fan-out
        # never runs >MAX_CONCURRENT_ENCODES ffmpeg processes at once (OOM).
        with ENCODE_SEMAPHORE:
            subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        dur_msg = f", trimmed to {max_duration}s" if max_duration else ""
        logger.info(
            "[publish] Transcoded %s for %s (%s CRF %s%s)",
            source.name,
            platform,
            spec.codec,
            spec.crf,
            dur_msg,
        )
        return variant_path
    except subprocess.TimeoutExpired as exc:
        # Surface timeout distinctly so the dashboard alerting can treat it
        # differently from a genuine encode error (timeout usually means
        # upstream is fine but needs more CPU).
        logger.warning(
            "[publish] Transcode TIMEOUT for %s/%s after %ds — using original "
            "(uncapped bitrate may be rejected by platform)",
            platform,
            source.name,
            getattr(exc, "timeout", 600),
        )
        return source
    except Exception as exc:
        logger.warning(
            "[publish] Transcode failed for %s/%s: %s — using original",
            platform,
            source.name,
            exc,
        )
        return source
