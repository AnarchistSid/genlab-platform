"""Pipeline stage: Filter stories without downloaded clips before LLM writing.

Runs between DownloadTopVideos and Writing stages.
Sets story["_skip_llm"] = True for stories with no valid clip.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MIN_CLIP_SIZE_BYTES = 100 * 1024  # 100 KB default


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

            has_valid_clip = (
                clip_entry.get("success", False)
                and clip_entry.get("clip_path")
            )

            # Fallback: check story-level media (gaming ExtractGamingMedia style)
            if not has_valid_clip:
                local_path = story.get("local_path", "")
                media_clip = (story.get("media") or {}).get("clip")
                if local_path and Path(local_path).exists():
                    has_valid_clip = True
                    logger.debug("VideoGate: found clip via story.local_path for '%s'", story_id[:16])
                elif media_clip and media_clip.get("file_path"):
                    clip_file = media_clip["file_path"]
                    if Path(clip_file).exists():
                        has_valid_clip = True
                        story["local_path"] = clip_file
                        logger.debug("VideoGate: found clip via story.media.clip for '%s'", story_id[:16])

            # Check if clip file exists and is large enough
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
                            story_id[:16], p.stat().st_size,
                        )

            if has_valid_clip:
                # Set master_path for VMAF gate (validate_videos compares rendered vs master)
                if clip_path:
                    story.setdefault("media", {})["master_path"] = clip_path
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
