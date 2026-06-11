"""Pipeline stage: Validate rendered videos meet platform specs.

Checks each rendered video against Instagram Reels / YouTube Shorts specs:
  - Dimensions: 1080x1920 (9:16) required
  - Codec: H.264 (libx264)
  - Pixel format: yuv420p
  - Color space: bt709
  - Audio: AAC, 48kHz stereo
  - Duration: 15-60 seconds
  - File size: < 100 MB

VMAF gate (enabled by default):
  - Compares rendered variant against master using Netflix VMAF metric
  - Threshold: >= 85
  - On failure: re-encode at CRF-3 (minimum CRF 12), then reject if still failing
  - Disable via niche_config: video_validation.run_vmaf: false

Auto-fix attempts re-encoding for codec/pixel format/color space mismatches.
R-39 (PR #138) extended ``_can_fix`` to also handle ``too_long`` (trim
via ``-t SPEC.max_duration``) and ``no_audio_stream`` (mux a silent
``anullsrc`` stereo bed at 48 kHz).

The ``no_audio_stream`` autofix is the render-time half of the
video-first source-audio policy — see ``docs/audio-policy.md`` for why
we mux silence rather than synthesize TTS narration over silent
sources. TL;DR: TTS-over-silent would sound AI-generated; silent-mux
satisfies the platform's "audio stream must exist" requirement at zero
quality risk.

Non-fatal: invalid videos are flagged but don't crash the pipeline.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from genlab_core.media.ffmpeg import get_ffmpeg_binary, get_ffprobe_binary
from genlab_core.media.video_validator import check_vmaf
from genlab_core.pipeline.stage_context import StageContext

logger = logging.getLogger(__name__)

# Default CRF used for initial encode (matches PLATFORM_SPECS instagram default)
_DEFAULT_CRF = 15
# Minimum CRF for VMAF re-encode attempts (lower CRF = higher quality / bigger file)
_MIN_REENCODE_CRF = 12
# CRF reduction step on VMAF failure
_CRF_STEP = 3

# Platform specs
SPEC = {
    "target_width": 1080,
    "target_height": 1920,
    "codec": "h264",
    "pix_fmt": "yuv420p",
    "color_space": "bt709",
    "audio_codec": "aac",
    "audio_sample_rate": 48000,
    "audio_channels": 2,
    "min_duration": 15.0,
    "max_duration": 60.0,
    "max_file_size_mb": 100,
}


class ValidateVideos:
    """Validate rendered videos against platform specifications.

    Reads: context['stories'], context['blueprints']
    Writes: context['stories'][*]['media']['video_validation'],
            context['run_stats']['video_validation']
    """

    def execute(self, context: StageContext) -> StageContext:
        stories = context.get("stories", [])
        if not stories:
            logger.info("[ValidateVideos] No stories to validate")
            return context

        config = context.get("niche_config", {})
        auto_fix = config.get("video_validation", {}).get("auto_fix", True)
        run_vmaf = config.get("video_validation", {}).get("run_vmaf", True)

        passed = 0
        failed = 0
        fixed = 0
        skipped = 0
        vmaf_skipped = (
            0  # R-07: separate counter for VMAF-skipped (still passes spec, gate did NOT run)
        )

        for story in stories:
            media = story.get("media", {})
            video_path = media.get("rendered_path", "")

            if not video_path or not Path(video_path).exists():
                skipped += 1
                continue

            try:
                probe = self._probe(Path(video_path))
                if not probe:
                    media["video_validation"] = {"valid": False, "error": "probe_failed"}
                    failed += 1
                    continue

                issues = self._check(probe)

                if not issues:
                    # Spec checks passed — now run VMAF gate if enabled
                    if run_vmaf:
                        vmaf_result, skip_reason = self._run_vmaf_gate(media, video_path)
                        if vmaf_result == "pass":
                            media["video_validation"] = {"valid": True, "issues": []}
                            if skip_reason:
                                # R-07: gate fail-opened (no master, infra
                                # missing, or log unreadable). Stamp the
                                # reason so downstream (dashboard,
                                # run_report) can see the absence of a
                                # real quality verdict.
                                media["video_validation"]["vmaf_skipped"] = True
                                media["video_validation"]["vmaf_skip_reason"] = skip_reason
                                vmaf_skipped += 1
                            passed += 1
                        elif vmaf_result == "reencoded":
                            media["video_validation"] = {
                                "valid": True,
                                "issues": [],
                                "vmaf_reencoded": True,
                            }
                            passed += 1
                            fixed += 1
                        else:
                            media["video_validation"] = {
                                "valid": False,
                                "issues": ["vmaf_below_threshold"],
                            }
                            failed += 1
                    else:
                        media["video_validation"] = {"valid": True, "issues": []}
                        passed += 1
                elif auto_fix and self._can_fix(issues):
                    fixed_path = self._fix(Path(video_path), probe, issues)
                    if fixed_path:
                        media["rendered_path"] = str(fixed_path)
                        media["video_validation"] = {
                            "valid": True,
                            "auto_fixed": True,
                            "fixed_issues": issues,
                        }
                        fixed += 1
                        passed += 1
                    else:
                        media["video_validation"] = {"valid": False, "issues": issues}
                        failed += 1
                else:
                    media["video_validation"] = {"valid": False, "issues": issues}
                    failed += 1

            except Exception:
                logger.exception(
                    "[ValidateVideos] Error validating %s",
                    story.get("story_id", "unknown"),
                )
                media["video_validation"] = {"valid": False, "error": "exception"}
                failed += 1

        logger.info(
            "[ValidateVideos] %d passed (%d auto-fixed, %d vmaf-skipped), %d failed, %d skipped",
            passed,
            fixed,
            vmaf_skipped,
            failed,
            skipped,
        )

        context.setdefault("run_stats", {})["video_validation"] = {
            "passed": passed,
            "failed": failed,
            "fixed": fixed,
            "skipped": skipped,
            "vmaf_skipped": vmaf_skipped,
        }

        return context

    def _run_vmaf_gate(self, media: dict[str, Any], video_path: str) -> tuple[str, str | None]:
        """Run VMAF check with one re-encode attempt on failure.

        Returns:
            Tuple of ``(state, skip_reason)``.

            * ``state``: "pass", "reencoded", or "fail".
            * ``skip_reason``: ``None`` when VMAF produced a real verdict;
              a string identifier when ``state == "pass"`` only because
              the gate could not actually run (R-07). Distinguishes a
              real pass from a fail-open pass for observability.
        """
        master_path = media.get("master_path", "")
        if not master_path or not Path(master_path).exists():
            # R-25: VMAF measures encode quality between two videos of the SAME
            # content at the SAME dimensions, so it needs a LOSSLESS master of
            # the COMPOSITE reel. The pipeline doesn't produce one (the raw
            # source clip was a different framing/resolution — a meaningless
            # reference that triggered wasted re-encodes), so the gate skips
            # cleanly instead of running garbage. Fail-open: an absent gate
            # must not block an otherwise spec-valid reel.
            logger.info(
                "[ValidateVideos] No same-framing lossless master available — skipping VMAF gate"
            )
            return "pass", "no_master"

        # Defense-in-depth (R-25): even if a master is supplied, VMAF across
        # mismatched dimensions is meaningless and would trigger a wasted
        # re-encode. Only run when the reference matches the rendered reel.
        master_dims = self._video_dims(self._probe(Path(master_path)))
        rendered_dims = self._video_dims(self._probe(Path(video_path)))
        if master_dims and rendered_dims and master_dims != rendered_dims:
            logger.warning(
                "[ValidateVideos] VMAF master %dx%d != rendered %dx%d — skipping "
                "(invalid reference; cannot measure encode quality across reframes)",
                master_dims[0],
                master_dims[1],
                rendered_dims[0],
                rendered_dims[1],
            )
            return "pass", "dim_mismatch"

        vmaf_ok, score = check_vmaf(Path(master_path), Path(video_path), "rendered")
        if vmaf_ok:
            if score == 0.0:
                # R-07: ``check_vmaf`` fail-opens with score 0.0 when libvmaf
                # is missing or the VMAF log was unparseable. The gate did
                # NOT produce a real quality verdict — record the skip so
                # ops sees a non-zero ``vmaf_skipped`` count and the dashboard
                # shows ``vmaf_skipped: true`` next to the post.
                logger.warning(
                    "[ValidateVideos] VMAF fail-opened (score=0.0) — gate did NOT verify quality"
                )
                return "pass", "infra_or_log_unreadable"
            logger.info("[ValidateVideos] VMAF passed: %.1f", score)
            return "pass", None

        logger.warning(
            "[ValidateVideos] VMAF %.1f < 85 — attempting re-encode at CRF-%d",
            score,
            _CRF_STEP,
        )

        # Re-encode at lower CRF
        reencoded = self._vmaf_reencode(Path(video_path), current_crf=_DEFAULT_CRF)
        if reencoded is None:
            logger.error("[ValidateVideos] VMAF re-encode failed")
            return "fail", None

        # Check VMAF again on the re-encoded file
        vmaf_ok_2, score_2 = check_vmaf(Path(master_path), reencoded, "reencoded")
        if vmaf_ok_2:
            logger.info("[ValidateVideos] VMAF passed after re-encode: %.1f", score_2)
            media["rendered_path"] = str(reencoded)
            return "reencoded", None

        logger.error(
            "[ValidateVideos] VMAF still failing after re-encode: %.1f — rejecting",
            score_2,
        )
        return "fail", None

    @staticmethod
    def _probe(path: Path) -> dict[str, Any] | None:
        """Probe video with ffprobe, return metadata dict."""
        ffprobe = get_ffprobe_binary()
        cmd = [
            ffprobe,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return None
            return json.loads(result.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            return None

    @staticmethod
    def _video_dims(probe: dict[str, Any] | None) -> tuple[int, int] | None:
        """Extract (width, height) of the first video stream, or None."""
        if not probe:
            return None
        for stream in probe.get("streams", []):
            if stream.get("codec_type") == "video":
                w = int(stream.get("width", 0))
                h = int(stream.get("height", 0))
                return (w, h) if w and h else None
        return None

    @staticmethod
    def _check(probe: dict[str, Any]) -> list[str]:
        """Check probe data against spec, return list of issues."""
        issues: list[str] = []

        # Find video stream
        video_stream = None
        audio_stream = None
        for stream in probe.get("streams", []):
            if stream.get("codec_type") == "video" and not video_stream:
                video_stream = stream
            elif stream.get("codec_type") == "audio" and not audio_stream:
                audio_stream = stream

        if not video_stream:
            issues.append("no_video_stream")
            return issues

        # Dimensions — must be exactly 1080x1920
        width = int(video_stream.get("width", 0))
        height = int(video_stream.get("height", 0))
        if width != SPEC["target_width"] or height != SPEC["target_height"]:
            issues.append(f"wrong_dimensions:{width}x{height}")

        # Codec — must be H.264
        codec = video_stream.get("codec_name", "")
        if codec != SPEC["codec"]:
            issues.append(f"wrong_codec:{codec}")

        # Pixel format
        pix_fmt = video_stream.get("pix_fmt", "")
        if pix_fmt != SPEC["pix_fmt"]:
            issues.append(f"wrong_pix_fmt:{pix_fmt}")

        # Color space — must be bt709
        color_space = video_stream.get("color_space", "")
        if color_space and color_space != SPEC["color_space"]:
            issues.append(f"wrong_color_space:{color_space}")

        # Audio checks
        if not audio_stream:
            issues.append("no_audio_stream")
        else:
            # Audio codec — must be AAC
            audio_codec = audio_stream.get("codec_name", "")
            if audio_codec != SPEC["audio_codec"]:
                issues.append(f"wrong_audio_codec:{audio_codec}")

            # Audio sample rate — must be 48kHz
            sample_rate = int(audio_stream.get("sample_rate", 0))
            if sample_rate != SPEC["audio_sample_rate"]:
                issues.append(f"wrong_sample_rate:{sample_rate}")

            # Audio channels — must be stereo (2)
            channels = int(audio_stream.get("channels", 0))
            if channels != SPEC["audio_channels"]:
                issues.append(f"wrong_audio_channels:{channels}")

        # Duration — must be 15-60 seconds
        fmt = probe.get("format", {})
        duration = float(fmt.get("duration", 0))
        if duration < SPEC["min_duration"]:
            issues.append(f"too_short:{duration:.1f}s")
        elif duration > SPEC["max_duration"]:
            issues.append(f"too_long:{duration:.1f}s")

        # File size
        size_mb = int(fmt.get("size", 0)) / (1024 * 1024)
        if size_mb > SPEC["max_file_size_mb"]:
            issues.append(f"too_large:{size_mb:.1f}MB")

        return issues

    @staticmethod
    def _can_fix(issues: list[str]) -> bool:
        """Determine if issues are auto-fixable via re-encoding.

        R-39: ``too_long`` and ``no_audio_stream`` were previously
        non-fixable, so a 90-second composite or a silent source got
        rendered, then validated, then silently dropped at the
        ``valid: False`` gate. Both are recoverable with a single
        ffmpeg pass — trim with ``-t SPEC.max_duration`` and/or mux
        in a silent ``anullsrc`` audio bed."""
        fixable = {
            "wrong_codec",
            "wrong_pix_fmt",
            "wrong_color_space",
            "wrong_audio_codec",
            "wrong_sample_rate",
            "wrong_audio_channels",
            # R-39 additions:
            "too_long",  # trim from end via ``-t SPEC.max_duration``
            "no_audio_stream",  # mux a silent anullsrc bed in
        }
        return all(any(issue.startswith(f) for f in fixable) for issue in issues)

    @staticmethod
    def _fix(
        path: Path,
        probe: dict[str, Any],
        issues: list[str],
    ) -> Path | None:
        """Re-encode to fix codec/pix_fmt/color_space/audio/duration issues.

        R-39 added two new fix paths:
          * ``too_long`` → ``-t SPEC.max_duration`` trims from end.
            The hook lives in the first ~3 seconds, so cutting the
            tail keeps the content that wins the algorithm.
          * ``no_audio_stream`` → ``-f lavfi -i anullsrc=...`` muxes
            in a silent stereo bed at 48kHz. Platforms reject videos
            without an audio stream even if the audio is silent; a
            real source that ships without audio is still publishable
            once we add the bed (the publish-then-drop loop was
            R-39's exact concern).
        """
        ffmpeg = get_ffmpeg_binary()
        out = path.with_stem(f"{path.stem}_fixed")

        needs_silence = any(i == "no_audio_stream" for i in issues)
        needs_trim = any(i.startswith("too_long") for i in issues)

        cmd = [ffmpeg, "-y"]

        if needs_silence:
            # Input 0: silent stereo bed. Input 1: source video.
            # Map video from input 1, audio from input 0. ``-shortest``
            # caps the output at the video length so the silence
            # doesn't extend past the visual.
            cmd += [
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-i",
                str(path),
                "-map",
                "1:v:0",
                "-map",
                "0:a",
                "-shortest",
            ]
        else:
            cmd += ["-i", str(path)]

        cmd += [
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
        ]

        if needs_trim:
            # Trim from start; keeps the hook. ``-t`` lands AFTER the
            # codec args so it applies to the OUTPUT length, not the
            # decoder seek.
            cmd += ["-t", str(SPEC["max_duration"])]

        cmd += [str(out)]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode == 0 and out.exists():
                return out
            return None
        except subprocess.TimeoutExpired:
            return None

    @staticmethod
    def _vmaf_reencode(
        path: Path,
        current_crf: int = _DEFAULT_CRF,
    ) -> Path | None:
        """Re-encode at CRF - _CRF_STEP (minimum _MIN_REENCODE_CRF) to improve VMAF.

        Returns the path to the re-encoded file, or None if FFmpeg fails.
        """
        new_crf = max(_MIN_REENCODE_CRF, current_crf - _CRF_STEP)
        out = path.with_stem(f"{path.stem}_vmaf_fixed")
        ffmpeg = get_ffmpeg_binary()

        logger.info(
            "[ValidateVideos] VMAF re-encode: CRF %d → %d for %s",
            current_crf,
            new_crf,
            path.name,
        )

        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(path),
            "-c:v",
            "libx264",
            "-crf",
            str(new_crf),
            "-preset",
            "slow",
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-c:a",
            "aac",
            "-b:a",
            "320k",
            "-ar",
            "48000",
            "-movflags",
            "+faststart",
            str(out),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode == 0 and out.exists():
                return out
            logger.warning(
                "[ValidateVideos] VMAF re-encode ffmpeg exited %d",
                result.returncode,
            )
            return None
        except subprocess.TimeoutExpired:
            logger.warning("[ValidateVideos] VMAF re-encode timed out")
            return None
