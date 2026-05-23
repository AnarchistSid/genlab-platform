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
from typing import Any

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

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        clip_index = context.get("clip_index", {})
        clips = clip_index.get("clips", {})
        stories = context.get("stories", [])

        passed = 0
        skipped = 0

        for story in stories:
            story_id = story.get("story_id", "")
            clip_entry = clips.get(story_id, {})

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

        logger.info("VideoGate: %d passed, %d skipped", passed, skipped)
        context.setdefault("run_stats", {})["video_gate"] = {
            "passed": passed,
            "skipped": skipped,
        }
        return context
