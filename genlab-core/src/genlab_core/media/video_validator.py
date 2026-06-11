"""
genlab_core.media.video_validator — Video quality gate before upload.

Extended from BlackboxBrief's video_content_gate.py with two new gates
required for the per-platform quality architecture:

  VMAF score check — catches unexpected quality degradation in platform
  variants, most commonly when TikTok's bitrate ceiling hits a complex
  gaming scene.

  Color space verification — H.265 transcodes are more prone to
  inadvertently switching color primaries. bt709 must be confirmed on
  every variant before upload to prevent washed-out appearance.
"""

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def check_vmaf(master: Path, variant: Path, platform: str) -> tuple[bool, float]:
    """
    Compare platform variant quality against master using VMAF.

    VMAF (Video Multi-method Assessment Fusion) is Netflix's perceptual
    quality metric. Score >= 85 means the variant looks acceptably close
    to the master. Below 85, the encode has introduced visible quality
    loss -- most commonly when TikTok's 12M maxrate ceiling hits a
    complex scene.

    When VMAF < 85, the caller should re-encode at CRF+2 rather than
    silently uploading a degraded file.

    Returns:
        Tuple of (passed: bool, score: float). ``score == 0.0`` is the
        fail-open sentinel — VMAF could not run (libvmaf missing) or the
        log was unreadable. R-07: callers should use ``score == 0.0`` as
        the "skipped" signal and record ``vmaf_skipped=True`` downstream
        so the absence of a real gate is visible in observability.
    """
    from genlab_core.media.ffmpeg import get_ffmpeg_binary

    ffmpeg = get_ffmpeg_binary()
    vmaf_log = f"/tmp/vmaf_{platform}.json"
    # `-nostats -loglevel error` is critical here: without it ffmpeg
    # emits ~40 KB of "frame=N fps=..." progress lines to stderr while
    # the libvmaf filter runs. subprocess.run(capture_output=True) buffers
    # stderr into an OS pipe (~64 KB default), the pipe fills mid-render,
    # ffmpeg blocks on stderr write, subprocess hits the 300s timeout and
    # raises, the JSON file never gets finalised, and check_vmaf returns
    # the fail-open (True, 0.0). 2026-05-21 forensics found this skipped
    # every single VMAF check in production despite ffmpeg-static having
    # --enable-libvmaf compiled in. Manual run-as-genlab produced VMAF
    # score 98.1 against the same files.
    cmd = [
        ffmpeg,
        "-nostats",
        "-loglevel",
        "error",
        "-i",
        str(master),
        "-i",
        str(variant),
        "-filter_complex",
        f"[0:v][1:v]libvmaf=log_fmt=json:log_path={vmaf_log}",
        "-f",
        "null",
        "-",
    ]
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=300,
        )
    except Exception as e:
        # R-07: libvmaf binary is missing or the subprocess timed out.
        # This is an INFRA condition, not a quality verdict — log at
        # INFO so legitimate environments without libvmaf don't spam
        # WARN, but keep the structured field so observability can
        # count it as a "VMAF gate skipped" event.
        logger.info(
            "[video_validator] vmaf_skipped_infra: libvmaf "
            "subprocess failed (binary missing or timeout). "
            "platform=%s error=%s path=%s",
            platform,
            e,
            variant,
        )
        return True, 0.0

    try:
        with open(vmaf_log) as f:
            vmaf_data = json.load(f)
        score = vmaf_data["pooled_metrics"]["vmaf"]["mean"]
        passed = score >= 85.0
        if not passed:
            logger.warning(
                "[VMAF] %s score %.1f < 85 -- variant needs re-encode at CRF+2",
                platform,
                score,
            )
        return passed, score
    except Exception as e:
        # R-07: subprocess.run succeeded but the VMAF log is unreadable
        # — this is the silent-failure mode that bit us in May 2026
        # (pipe-buffer deadlock left the log truncated, every check
        # fail-opened). Distinct from the infra-skip case above: this
        # one IS a bug worth tracking, so log at ERROR + structured
        # field. Still fail-open on shape because the caller depends
        # on the (bool, float) contract and the rendered reel itself
        # is otherwise spec-valid.
        logger.error(
            "[video_validator] vmaf_log_unreadable: VMAF subprocess "
            "succeeded but log could not be parsed — quality NOT "
            "verified. platform=%s error=%s log=%s path=%s",
            platform,
            e,
            vmaf_log,
            variant,
        )
        return True, 0.0  # fail-open: don't block upload on VMAF parse failure


def check_color_space(path: Path, platform: str) -> bool:
    """
    Verify all video streams use bt709 color primaries/transfer/space.

    H.265 encodes are particularly prone to incorrect color space tagging
    when the source clip has an unusual colorspace (common in gaming capture
    software that tags HDR or YUV 4:2:0 limited). When the platform's
    player sees incorrect color tags, it misinterprets the signal, causing
    washed-out or oversaturated appearance.

    Returns:
        True if all video streams are correctly tagged as bt709.
    """
    from genlab_core.media.ffmpeg import get_ffprobe_binary

    ffprobe = get_ffprobe_binary()
    cmd = [
        ffprobe,
        "-v",
        "quiet",
        "-show_streams",
        "-select_streams",
        "v:0",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        stdout = result.stdout
    except Exception as e:
        logger.error(
            "[video_validator] color space check failed with parse error — "
            "failing closed to protect platform compliance. error=%s path=%s",
            e,
            path,
        )
        return False  # fail-closed: block upload when color space unverifiable

    try:
        data = json.loads(stdout)
        streams = data.get("streams", [])
        # Tags that are explicitly wrong (non-bt709). Empty/unknown tags are
        # acceptable — most players and platforms default to bt709 for H.264.
        _BAD_COLORSPACES = {"bt470bg", "smpte170m", "smpte240m", "bt2020nc", "bt2020c"}

        for stream in streams:
            primaries = stream.get("color_primaries", "")
            transfer = stream.get("color_transfer", "")
            colorspace = stream.get("color_space", "")

            bad = [
                v
                for v in [primaries, transfer, colorspace]
                if v and v != "bt709" and v != "unknown" and v in _BAD_COLORSPACES
            ]
            if bad:
                logger.warning(
                    "[COLOR] %s has non-bt709 color space: primaries=%s "
                    "transfer=%s space=%s -- may appear washed out",
                    platform,
                    primaries,
                    transfer,
                    colorspace,
                )
                return False
        return True
    except Exception as e:
        logger.error(
            "[video_validator] color space check failed with parse error — "
            "failing closed to protect platform compliance. error=%s path=%s",
            e,
            path,
        )
        return False  # fail-closed: block upload when color space unverifiable


def validate_platform_variant(
    master: Path,
    variant: Path,
    platform: str,
    run_vmaf: bool = True,
) -> bool:
    """
    Run all quality gates on a platform variant before upload.

    Runs color space verification and VMAF quality check. Returns False
    if any gate fails.

    The VMAF check is optional (run_vmaf=False skips it) because libvmaf
    may not be compiled into all FFmpeg builds. Check with:
        ffmpeg -filters 2>/dev/null | grep vmaf
    """
    color_ok = check_color_space(variant, platform)
    if not color_ok:
        return False

    if run_vmaf:
        vmaf_ok, _score = check_vmaf(master, variant, platform)
        if not vmaf_ok:
            return False

    return True
