"""Pipeline stage: Filter stories without downloaded clips before LLM writing.

Runs between DownloadTopVideos and Writing stages.
Sets story["_skip_llm"] = True for stories with no valid clip.

Also rejects clips whose contents are visually empty (auto-generated article
preview cards, static slideshow images set to video, talking-head podcast
audio with a single frame). These ship through yt-dlp as valid mp4 files
but have tiny video bitrates and effectively zero motion. CLAUDE.md
explicitly bans text-only renders; this gate is where we catch them.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from genlab_core.pipeline.stage_context import StageContext

logger = logging.getLogger(__name__)

_MIN_CLIP_SIZE_BYTES = 100 * 1024  # 100 KB default

# Thresholds for the visual-content gate (D-cluster: text-only render fix).
#
# Real footage clips downloaded from YouTube/Twitch typically clock 500-2000
# kbps video stream bitrate even at 480p.  An ESPN auto-generated article
# preview card we saw in production was 26 kbps video bitrate — 20x lower
# than the next-worst real clip.  150 kbps gives us a 3x margin to the
# lowest legitimate clip we've observed (~510 kbps), so this threshold
# rejects synthetic content without false-positives on real footage.
#
# bytes_per_sec is the defense-in-depth signal when ffprobe can't surface
# a clean stream bitrate (some containers report bitrate as 0 even with
# real content).  40 KB/s catches the same Schmitt clip (20 KB/s) while
# leaving headroom for compressed 480p footage (~70-130 KB/s).
_MIN_VIDEO_BITRATE_BPS = 150_000
_MIN_BYTES_PER_SEC = 40 * 1024


def _probe_video_quality(path: Path) -> dict[str, float]:
    """Return ``{bitrate_bps, duration_sec, bytes_per_sec}`` for ``path``.

    All keys present even on failure (zero values).  Self-contained so the
    gate doesn't need to import ffmpeg_utils — keeps the failure mode
    obvious if ffprobe is missing.
    """
    result = {"bitrate_bps": 0.0, "duration_sec": 0.0, "bytes_per_sec": 0.0}
    try:
        size_bytes = path.stat().st_size
    except OSError:
        return result

    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_streams",
                "-show_format",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return result
        data = json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError, OSError):
        return result

    duration = 0.0
    try:
        duration = float(data.get("format", {}).get("duration", 0) or 0)
    except (TypeError, ValueError):
        duration = 0.0

    video_bitrate = 0.0
    for stream in data.get("streams", []):
        if stream.get("codec_type") != "video":
            continue
        # ffprobe reports stream bitrate when the container has it.
        try:
            video_bitrate = float(stream.get("bit_rate", 0) or 0)
        except (TypeError, ValueError):
            video_bitrate = 0.0
        break

    result["bitrate_bps"] = video_bitrate
    result["duration_sec"] = duration
    result["bytes_per_sec"] = (size_bytes / duration) if duration > 0 else 0.0
    return result


class VideoGate:
    """Mark stories without a downloaded video clip so Writing can skip them."""

    def execute(self, context: StageContext) -> StageContext:
        clip_index = context.get("clip_index", {})
        clips = clip_index.get("clips", {})
        stories = context.get("stories", [])

        # C3 (2026-06-30): snapshot the input list BEFORE any mutation
        # so per-content_type drop accounting compares like-for-like.
        # Shallow copy is enough — content_type is read from each dict's
        # surface, not mutated by the gate.
        before_stories_snapshot = list(stories)

        passed = 0
        skipped = 0

        for story in stories:
            # 2026-06-23 production outage fix — gaming + sports pipelines
            # were crashing here when fetchers (e.g. FetchTrendingVideos)
            # populated stories with ``story_id=None`` explicitly.
            # ``dict.get(key, default)`` returns the default ONLY when the
            # key is ABSENT — when the key exists with value None, get()
            # returns None. The downstream ``story_id[:16]`` slices at
            # lines 125/133/150 then crashed with
            # ``'NoneType' object is not subscriptable``, and fail_mode=
            # continue meant the pipeline kept running with stories that
            # lacked the ``_skip_llm`` gate, ending in PushToBacklog
            # producing 0 blueprints despite render running for 6+ minutes.
            # Coerce ``None`` → ``""`` so the slicing is safe.
            story_id = story.get("story_id") or ""
            clip_entry = clips.get(story_id, {}) if story_id else {}

            has_valid_clip = clip_entry.get("success", False) and clip_entry.get("clip_path")

            # Fallback: check story-level media (gaming ExtractGamingMedia style)
            if not has_valid_clip:
                local_path = story.get("local_path", "")
                media_clip = (story.get("media") or {}).get("clip")
                if local_path and Path(local_path).exists():
                    has_valid_clip = True
                    logger.debug(
                        "VideoGate: found clip via story.local_path for '%s'", story_id[:16]
                    )
                elif media_clip and media_clip.get("file_path"):
                    clip_file = media_clip["file_path"]
                    if Path(clip_file).exists():
                        has_valid_clip = True
                        story["local_path"] = clip_file
                        logger.debug(
                            "VideoGate: found clip via story.media.clip for '%s'", story_id[:16]
                        )

            # Check if clip file exists and is large enough
            clip_path = ""
            if has_valid_clip:
                clip_path = (
                    clip_entry.get("clip_path")
                    or story.get("local_path", "")
                    or (story.get("media") or {}).get("clip", {}).get("file_path", "")
                )
                if clip_path:
                    p = Path(clip_path)
                    if p.exists() and p.stat().st_size < _MIN_CLIP_SIZE_BYTES:
                        has_valid_clip = False
                        logger.warning(
                            "VideoGate: clip for %s too small (%d bytes)",
                            story_id[:16],
                            p.stat().st_size,
                        )

            # Visual-content gate: reject clips whose stream bitrate and/or
            # bytes-per-second are below thresholds.  These are typically
            # auto-generated article preview cards, podcast-audio-with-image,
            # or slideshow content masquerading as video.  Without this check
            # they would yt-dlp cleanly and FrameCompositor would happily
            # re-encode the same text-only frames into a "rendered reel" with
            # the channel branding overlay — exactly what CLAUDE.md bans.
            visual_reject_reason = ""
            if has_valid_clip and clip_path:
                p = Path(clip_path)
                if p.exists():
                    metrics = _probe_video_quality(p)
                    br = metrics["bitrate_bps"]
                    bps = metrics["bytes_per_sec"]
                    if br > 0 and br < _MIN_VIDEO_BITRATE_BPS:
                        has_valid_clip = False
                        visual_reject_reason = (
                            f"low_video_bitrate ({int(br)} bps < "
                            f"{_MIN_VIDEO_BITRATE_BPS} threshold)"
                        )
                    elif bps > 0 and bps < _MIN_BYTES_PER_SEC:
                        has_valid_clip = False
                        visual_reject_reason = (
                            f"low_bytes_per_sec ({int(bps)} B/s < {_MIN_BYTES_PER_SEC} threshold)"
                        )
                    if visual_reject_reason:
                        logger.warning(
                            "VideoGate: clip for '%s' rejected — %s "
                            "(likely text-only or static content)",
                            story.get("title", "")[:60],
                            visual_reject_reason,
                        )
                        # Mark in clip_index so render strategies fall through
                        # to the no-video path instead of compositing garbage.
                        if story_id in clips:
                            clips[story_id]["success"] = False
                            clips[story_id]["error"] = visual_reject_reason

            if has_valid_clip:
                # R-25: do NOT set master_path to the raw downloaded clip. VMAF
                # measures encode quality between two videos of the SAME content
                # at the SAME dimensions — but the raw clip is the unbranded,
                # original-aspect-ratio source (e.g. 1920x1080), while the
                # rendered reel is a branded, cropped/padded 1080x1920 composite.
                # Diffing those produced a meaningless score that triggered a
                # wasted CRF-12 re-encode every run. A real VMAF gate needs a
                # lossless master of the COMPOSITE (not produced today), so we
                # leave master_path unset and validate_videos skips VMAF rather
                # than running garbage.
                passed += 1
            else:
                story["_skip_llm"] = True
                skipped += 1
                logger.info(
                    "VideoGate: no valid clip for '%s' — skipping LLM",
                    story.get("title", "")[:50],
                )

        # RENDER #6 fix (2026-06-13): drop _skip_llm stories from the active
        # pipeline list. Pre-fix, VideoGate set the flag but kept the story in
        # context["stories"] — every downstream stage (WritingStrategy, Hook-
        # Strategy, AffiliateMatch, QCGates, ViralityScoring, VisualRender, ...)
        # then iterated over them. Writing correctly skipped (the for-loop's
        # `if story.get("_skip_llm"): continue`), but HookStrategy generated
        # hooks for them anyway (it doesn't check the flag) and QC then ran on
        # stories with hook + empty caption → "Missing required field: caption/
        # body" failures. The 40% sports QC pass rate today (2 of 5 failing)
        # was entirely VideoGate-skipped stories pretending to be real candidates.
        #
        # Side effect: the run_report's `qc.failed` count now reflects only true
        # quality failures, not "we never had a video for this in the first
        # place" structural skips. Operators reading the dashboard will see the
        # real signal.
        if skipped > 0:
            context["stories"] = [s for s in stories if not s.get("_skip_llm")]
            logger.info(
                "VideoGate: dropped %d clipless stories from active pipeline; remaining=%d",
                skipped,
                len(context["stories"]),
            )

        logger.info("VideoGate: %d passed, %d skipped", passed, skipped)
        context.setdefault("run_stats", {})["video_gate"] = {
            "passed": passed,
            "skipped": skipped,
        }

        # C3 (2026-06-30): per-content_type drop accounting. "after"
        # reflects context["stories"] post _skip_llm pruning (the
        # active list downstream stages will iterate). Fail-OPEN.
        metrics = context.get("metrics")
        if metrics is not None:
            try:
                metrics.record_filter_drops(
                    self.__class__.__name__,
                    before_stories_snapshot,
                    context.get("stories", []),
                )
            except Exception as exc:  # noqa: BLE001 — observability is never critical-path
                logger.debug("VideoGate: record_filter_drops failed: %s", exc)

        # 2026-06-22 — VideoGate writes to BOTH sinks (sibling PRs
        # landed together as Month-1 Week-1 trio). They serve different
        # timelines + consumers:
        #
        # 1. reasoning_trace (in-memory, within-run) → consumed
        #    immediately by next stage / auto_approval_gate
        # 2. decision_traces (on-disk JSONL, within-run audit log) →
        #    consumed by debug + offline analysis. Operators query via:
        #    cat <run_dir>/decision_traces.jsonl | jq '.stage=="VideoGate"'
        #
        # Both record the SAME logical decision; the dual-write is
        # intentional. Same shape (stage, decision, confidence, reasons,
        # metadata) so consumers don't have to translate.
        from genlab_core.observability.decision_trace import record_decision
        from genlab_core.pipeline.reasoning_trace import append_trace

        total = passed + skipped
        drop_rate = (skipped / total) if total > 0 else 0.0
        confidence = max(0.0, 1.0 - drop_rate)  # 100% drop = 0 conf
        decision = "warning" if drop_rate >= 0.5 else "info"
        reasons_line = f"passed={passed}, skipped={skipped}, drop_rate={drop_rate:.0%}"
        metadata = {
            "passed": passed,
            "skipped": skipped,
            "drop_rate": round(drop_rate, 3),
        }
        append_trace(
            context,
            stage="VideoGate",
            decision=decision,
            confidence=confidence,
            reasons=[reasons_line],
            metadata=metadata,
        )
        record_decision(
            context,
            stage="VideoGate",
            decision=decision,
            reason=reasons_line,
            confidence=confidence,
            metadata=metadata,
        )
        return context
