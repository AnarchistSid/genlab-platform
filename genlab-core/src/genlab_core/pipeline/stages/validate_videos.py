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
import os
import subprocess
from pathlib import Path
from typing import Any

from genlab_core.media import audio_loudness
from genlab_core.media.ffmpeg import get_ffmpeg_binary, get_ffprobe_binary
from genlab_core.media.video_validator import check_vmaf
from genlab_core.pipeline.stage_context import StageContext

logger = logging.getLogger(__name__)


# Lever G3 wire (2026-06-22): post-render frame-ensemble vision QC.
# Opt-in via GENLAB_RENDER_QC_ENABLED=1. See _run_render_qc docstring
# for the full contract. Defined here at module top so the validation
# class below can call it without circular-import concerns.
def _run_render_qc(media: dict, story: dict, video_path: str) -> None:
    """Lever G3 wire helper: invoke render_qc + stash result on media.

    Opt-in via ``GENLAB_RENDER_QC_ENABLED=1``. When disabled, this is a
    no-op (zero overhead because ``render_qc.qc_render`` itself
    short-circuits on the env flag — we duplicate the check here to
    avoid the import cost in the hot path when disabled).

    The result lands under ``media["video_validation"]["render_qc"]``
    with the structured ``RenderQCReport`` fields. Fail-open: any
    ``qc_render`` failure means no record is added (caller proceeds).

    Pure side-effect mutation of ``media`` dict. Returns None.
    """
    if os.environ.get("GENLAB_RENDER_QC_ENABLED", "0") != "1":
        return

    try:
        from genlab_core.media.render_qc import qc_render
    except ImportError:
        return

    blueprint_id = str(story.get("blueprint_id") or story.get("candidate_id") or "")
    hook_text = str(story.get("hook") or story.get("title") or "")
    niche_id = str(story.get("niche_id") or "")

    report = qc_render(
        video_path,
        blueprint_id=blueprint_id,
        hook_text=hook_text,
        niche_id=niche_id,
    )
    if report is None:
        # qc_render fail-opened (disabled, no API key, ffmpeg missing,
        # judges all returned None, etc.) — no record added
        return

    # Stash structured report onto media for dashboard + downstream
    # consumers. We KEEP this separate from video_validation.valid so a
    # bad render_qc verdict (publishable=False) does NOT block publish
    # in this PR — surfacing the signal is step 1; gating publication
    # on it is a follow-up PR after operators calibrate the threshold.
    validation = media.setdefault("video_validation", {})
    validation["render_qc"] = {
        "frames_judged": report.frames_judged,
        "min_quality_score": report.min_quality_score,
        "avg_quality_score": report.avg_quality_score,
        "unique_issues": list(report.unique_issues),
        "publishable": report.publishable,
        "recommendation": report.recommendation,
    }
    logger.info(
        "[validate_videos] render_qc bp=%s min=%.1f avg=%.1f issues=%s rec=%s",
        blueprint_id,
        report.min_quality_score,
        report.avg_quality_score,
        ",".join(report.unique_issues) or "none",
        report.recommendation,
    )


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
    # Loudness is measured on the FINAL ASSEMBLED asset, not mid-pipeline.
    # post_render_transform normalises the transformed body, but the intro is
    # prepended and captions burned AFTERWARDS -- so anything appended after
    # that pass is unnormalised. Measured 2026-09-10 on the first narrated
    # candidate: body -13.91 LUFS / -1.31 dBTP (correct), appended intro
    # +3.42 dBTP (clipping), in the first three seconds where the hook lives.
    # Third instance of the append-after-normalize class.
    "target_lufs": audio_loudness.TARGET_LUFS,
    "lufs_tolerance": 1.0,
    "max_true_peak": audio_loudness.GATE_MAX_TRUE_PEAK_DBTP,
}

# PUBLISH-03 §2 (2026-09-15): the 0.5 dB margin this used to hardcode was too
# small. loudnorm overshoots its own TP target by a measured 0.6 dB in
# single-pass mode, which is how a movies render reached the gate at -0.93
# dBTP against a -1.0 ceiling. The margin now lives in audio_loudness beside
# the measurement that sets it, and the PRODUCER applies the same limiter so
# this repair path stops being the only thing standing between loudnorm and
# the gate.
_LIMITER_CEILING = audio_loudness.LIMITER_CEILING_LINEAR


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

                # --- final-asset loudness gate (2026-09-10) ---------------
                # This stage is the LAST to touch the asset, which is the only
                # place the measurement is meaningful. post_render_transform
                # normalises the transformed body, then the intro is prepended
                # and captions burned on top -- so a mid-pipeline pass leaves
                # everything appended after it unnormalised. Repair in place if
                # we can; block if we cannot. A clipping asset must not reach a
                # platform, and this is the last gate before it does.
                loud_issues = self._check_loudness(Path(video_path))
                if loud_issues and auto_fix and "loudness_unmeasured" not in loud_issues:
                    logger.info(
                        "[ValidateVideos] final-asset loudness out of spec (%s) — "
                        "re-normalising assembled asset",
                        ",".join(loud_issues),
                    )
                    relouded = self._fix_loudness(Path(video_path), loud_issues)
                    if relouded is not None:
                        remaining = self._check_loudness(relouded)
                        if remaining:
                            # Re-normalising did not bring it into spec. Keep the
                            # original path and let the issue block: silently
                            # publishing the half-fixed file would be worse.
                            loud_issues = remaining
                        else:
                            video_path = str(relouded)
                            media["rendered_path"] = video_path
                            probe = self._probe(relouded) or probe
                            issues = self._check(probe)
                            loud_issues = []
                            fixed += 1
                            logger.info(
                                "[ValidateVideos] final-asset loudness repaired -> %s",
                                relouded.name,
                            )
                if loud_issues:
                    issues = issues + loud_issues

                if not issues:
                    # Lever G3 wire (2026-06-22): post-render frame-ensemble
                    # vision QC. Opt-in via GENLAB_RENDER_QC_ENABLED=1.
                    # Internally calls G2's judge_frame on 3 frames (start/
                    # middle/end). Result is recorded under
                    # media["video_validation"]["render_qc"] for the
                    # dashboard + future LLM-judge layer to consume.
                    # Fail-quiet — any error here NEVER blocks publication,
                    # which is the existing behavior of this stage.
                    try:
                        _run_render_qc(media, story, video_path)
                    except Exception as exc:  # noqa: BLE001 — fail-open
                        logger.debug("[validate_videos] render_qc error: %s", exc)

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

            except Exception as exc:
                logger.exception(
                    "[ValidateVideos] Error validating %s",
                    story.get("story_id", "unknown"),
                )
                # 2026-07-14 audit: prior value ``"error": "exception"``
                # was a generic string that lost the actual failure
                # detail. Dashboard operators saw "validation failed"
                # with no root cause. Persist exception type + message
                # so ``blueprints.error_message`` (populated downstream
                # from this dict) carries diagnostic info.
                media["video_validation"] = {
                    "valid": False,
                    "error": "exception",
                    "error_type": exc.__class__.__name__,
                    "error_message": str(exc)[:500],
                }
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

        # 2026-07-23: emit decision trace — 6th stage in this session's
        # symmetric trace-emission rollout (VideoGate + ViralityScoring
        # + QCGates + PreDownloadDedup + AutoApprovalGate already).
        # Motivating pattern: when a run reports blueprints=0 with
        # video_validation.failed>0, the specific failure reasons
        # currently live only in per-blueprint logger.warning lines
        # that get journal-rotated away. Trace metadata carries the
        # aggregate counts so operators can filter for validation
        # blockers post-hoc.
        try:
            from genlab_core.observability.decision_trace import record_decision
            from genlab_core.pipeline.reasoning_trace import append_trace

            total_attempts = passed + failed + fixed
            # WARN when validation had ANY failures (even 1) OR when
            # everything was skipped (nothing to validate = precursor
            # to zero_blueprints).
            if failed > 0:
                trace_decision = "warning"
            elif total_attempts == 0 and skipped > 0:
                trace_decision = "warning"
            else:
                trace_decision = "info"

            reasons_line = (
                f"passed={passed}, failed={failed}, fixed={fixed}, "
                f"skipped={skipped}, vmaf_skipped={vmaf_skipped}"
            )
            metadata = {
                "passed": passed,
                "failed": failed,
                "fixed": fixed,
                "skipped": skipped,
                "vmaf_skipped": vmaf_skipped,
            }
            append_trace(
                context,
                stage="ValidateVideos",
                decision=trace_decision,
                confidence=1.0,
                reasons=[reasons_line],
                metadata=metadata,
            )
            record_decision(
                context,
                stage="ValidateVideos",
                decision=trace_decision,
                reason=reasons_line,
                confidence=1.0,
                metadata=metadata,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning(
                "[ValidateVideos] trace emission failed: %s",
                exc,
                exc_info=True,
            )

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
    def _measure_loudness(path: Path) -> tuple[float, float] | None:
        """Integrated loudness and true peak of an asset, via ffmpeg loudnorm.

        Returns ``(lufs, dbtp)`` or None when measurement fails. ffprobe cannot
        answer this -- loudness needs a decode pass, which is why it was never
        part of the spec check that runs off probe data alone.
        """
        import json as _json
        import re as _re
        import subprocess

        try:
            proc = subprocess.run(
                ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                 "-af", audio_loudness.loudnorm_filter() + ":print_format=json",
                 "-f", "null", "-"],
                capture_output=True, text=True, timeout=300,
            )
            blob = _re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr, _re.S)
            if not blob:
                logger.warning(
                    "[ValidateVideos] loudness measurement produced no JSON for %s", path.name
                )
                return None
            d = _json.loads(blob.group(0))
            return float(d["input_i"]), float(d["input_tp"])
        except Exception as exc:
            logger.warning(
                "[ValidateVideos] loudness measurement failed for %s: %s", path.name, exc
            )
            return None

    @classmethod
    def _check_loudness(cls, path: Path) -> list[str]:
        """Gate the final asset on loudness. Never fabricates a pass.

        A measurement failure returns ``loudness_unmeasured`` rather than an
        empty list: a gate that silently reports clean when it could not run is
        the failure mode this gate exists to close.
        """
        measured = cls._measure_loudness(path)
        if measured is None:
            return ["loudness_unmeasured"]
        lufs, dbtp = measured
        issues: list[str] = []
        if dbtp > SPEC["max_true_peak"]:
            issues.append(f"true_peak_over:{dbtp:+.2f}dBTP")
        if abs(lufs - SPEC["target_lufs"]) > SPEC["lufs_tolerance"]:
            issues.append(f"loudness_off:{lufs:.2f}LUFS")
        return issues

    @staticmethod
    def _fix_loudness(path: Path, issues: list[str] | None = None) -> Path | None:
        """Repair the FINAL assembled asset, video stream untouched.

        **Apply only the correction the measurement asked for.** Running both
        stages unconditionally is what broke this: measured 2026-09-12 on three
        real sports renders, every one entered at I=-14.5x (in spec) with
        TP=-0.72 (over by 0.28 dB), and left at I=-15.83/-15.98/-16.07 — a
        passing integrated loudness converted into a failing one while fixing
        the peak. The gate then blocked all three as ``loudness_off``, and the
        error named the symptom the repair had introduced.

        The mechanism: ``loudnorm`` targeting -14 from -14.5 applies about
        +0.5 dB, which pushes material into the limiter that follows, and the
        limiter's gain reduction then drags integrated loudness down ~1.3 LU
        with nothing to restore it. Neither filter is wrong; running the pair
        when only the peak was out of spec is.

        Measured on the same three assets with the limiter ALONE, no loudnorm
        (ceiling sweep -1.3 to -2.0 dBFS, all passing):

            ceiling -1.5 dBFS -> I=-14.62/-14.60/-14.58, TP=-1.20  PASS

        so the existing ceiling is retained unchanged; only the chain is now
        conditional.

        Two properties of the tooling that make this non-obvious and are worth
        recording, both measured the same day:

        * ``alimiter`` limits SAMPLE peaks, not TRUE peaks. Limiting to
          -1.2 dBFS yielded -0.95 dBTP — still over the -1.0 gate.
        * The AAC re-encode regenerates overshoot. A flat -0.38 dB attenuation
          moved true peak only 0.13 dB (-0.72 -> -0.85), not the 0.38 dB the
          arithmetic predicts. Plain attenuation therefore cannot be trusted to
          hit a true-peak target; a limiter with headroom under the gate can.

        The ceiling stays 0.5 dB below the gate (-1.5 dBFS for a -1.0 dBTP
        ceiling) because limiting to exactly the threshold lands on it: with
        ``limit=0.891`` an earlier asset measured -0.98 dBTP, failing ``> -1.0``
        by two hundredths.

        ``-c:v copy`` matters: the video has already been encoded, captioned
        and validated for colour. Re-encoding it to repair audio would risk a
        second-generation quality loss and could invalidate the bt709 tags this
        same stage just checked.
        """
        import subprocess

        issues = issues or []
        peak_over = any(i.startswith("true_peak_over") for i in issues)
        loud_off = any(i.startswith("loudness_off") for i in issues)
        # No issue list supplied (legacy callers) → repair both, old behaviour.
        if not peak_over and not loud_off:
            peak_over = loud_off = True

        # PUBLISH-04 (2026-09-15): loudnorm runs ALWAYS, not only for
        # loudness_off. It is the one true-peak-aware stage in the chain --
        # alimiter caps SAMPLE peaks and inter-sample peaks ride well above
        # them, the more so the harder it is working.
        #
        # A true-peak-only repair therefore could not fix a true-peak problem.
        # Measured on 25e4ed6ced2810fe_reel_with_intro.mp4, an anime asset that
        # arrived clipping at +0.63 dBTP:
        #
        #   chain                       I (LUFS)   TP (dBTP)
        #   input                        -14.08      +0.63     fail
        #   limiter only  (old)          -14.17      -0.63     FAIL   <- still
        #   loudnorm + limiter (new)     -14.30      -2.23     pass
        #
        # Limiting to -2.0 dBFS sample peak left 1.37 dB of inter-sample
        # overshoot. All 4 anime renders on the 08:13Z fire failed this way:
        # "0 passed (0 auto-fixed), 4 failed" with a repair attempted on each.
        #
        # Why this was not caught by the movies/ai_creators repairs earlier
        # today: those arrived at -0.93 and -0.64 dBTP, close enough that the
        # limiter alone cleared the gate. The margin measured there (0.3-0.45
        # dB) was overshoot on loudnorm output, not on heavily-limited output,
        # and did not generalise to a clipping input.
        #
        # `peak_over` / `loud_off` are still computed: they drive the log line
        # so the reason for a repair stays visible.
        # PUBLISH-05 (2026-09-15): TWO-PASS, and this corrects PUBLISH-04.
        #
        # Making loudnorm unconditional fixed true-peak failures and introduced
        # loudness failures, because the call was SINGLE-pass — the mode whose
        # own warning says the target "may miss by >1 LU". It missed by 1.8:
        #
        #   input        -14.57 LUFS  -0.72 dBTP   fail (true peak)
        #   single-pass  -15.83 LUFS  -2.61 dBTP   FAIL (loudness_off)  <- traded
        #   two-pass     -14.40 LUFS  -1.80 dBTP   passes both
        #
        # All 4 sports renders on the 09:31Z fire failed this way. PUBLISH-04's
        # anime case passed (-14.08 -> -14.30) only because single-pass happened
        # to land there; one passing sample again described a narrower case than
        # it appeared to.
        #
        # The analysis pass measures the actual asset, so linear mode can hit
        # both targets instead of predicting them. Fail-open to single-pass with
        # a WARNING when analysis fails — degraded is better than no repair, but
        # it must not be silent (rule #19).
        measured = None
        try:
            from genlab_core.media.post_render_transform import _measure_loudness

            measured = _measure_loudness(
                path,
                audio_loudness.TARGET_LUFS,
                audio_loudness.TARGET_LRA,
                audio_loudness.NORMALISE_TARGET_TRUE_PEAK_DBTP,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ValidateVideos] loudness analysis raised: %s", exc)
        if not measured:
            logger.warning(
                "[ValidateVideos] loudness analysis pass failed for %s — falling "
                "back to single-pass, which can miss integrated loudness by >1 LU "
                "and may swap a true_peak_over failure for a loudness_off one",
                path.name,
            )
        chain: list[str] = [
            audio_loudness.loudnorm_filter(measured=measured),
            # Always end on the limiter: loudnorm can itself raise peaks.
            audio_loudness.limiter_filter(),
        ]

        out = path.with_name(f"{path.stem}_ln{path.suffix}")
        logger.info(
            "[ValidateVideos] loudness repair for %s — issues=%s chain=%s",
            path.name, ",".join(issues) or "(none)", "+".join(chain),
        )
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(path),
                 "-c:v", "copy",
                 "-af", ",".join(chain),
                 "-c:a", "aac", "-b:a", "192k",
                 "-ar", str(SPEC["audio_sample_rate"]),
                 "-ac", str(SPEC["audio_channels"]), str(out)],
                capture_output=True, text=True, timeout=600, check=True,
            )
            return out if out.exists() and out.stat().st_size > 0 else None
        except Exception as exc:
            logger.warning("[ValidateVideos] final loudnorm failed for %s: %s", path.name, exc)
            return None

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
