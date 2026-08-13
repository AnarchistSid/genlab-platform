"""Pipeline stage: Extract gaming media clips for stories.

Slots into the EXTRACT_MEDIA pipeline stage. Lazy-initializes the
GamingClipSourcer on first use to avoid import-time failures.

Usage:
    stage = ExtractGamingMedia()
    context = stage.execute(context)
"""

from __future__ import annotations

import logging
import time
from datetime import UTC
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

INTER_STORY_DELAY_SECONDS = 2


class ExtractGamingMedia:
    """Extract gaming clips for each story in the pipeline context."""

    def __init__(self):
        self._sourcer = None

    def _get_sourcer(self):
        """Lazy-initialize the clip sourcer on first use."""
        if self._sourcer is None:
            from niches.gaming.tools.clip_sourcer import GamingClipSourcer

            project_root = Path(__file__).resolve().parent.parent.parent.parent
            self._sourcer = GamingClipSourcer.from_config(project_root)
        return self._sourcer

    def _source_clip_for_story(
        self, story: dict[str, Any], project_root: Path
    ) -> dict[str, Any] | None:
        """Source a clip for a single story. Returns clip dict or None.

        Prefers `canonical_name` (from IGDB enrichment) over the raw
        story title when passing to ClipSourcer. 2026-08-13 discovery:
        raw RSS/trending titles carry marketing suffixes ("- Official
        Trailer | PS5 Games", "The Art of Aircraft Trailer") that make
        YT search return 0 hits. IGDB fuzzy matches the actual game
        name (e.g., "ACE COMBAT 8" instead of the verbose original) so
        the YT tier of ClipSourcer can find general gameplay clips
        rather than needing an exact-verbose-title match.

        Fall back to story['title'] when IGDB enrichment didn't run OR
        didn't find a match (canonical_name is None).
        """
        sourcer = self._get_sourcer()
        # Prefer IGDB canonical name for the YT search; keep raw title
        # for logs so operator sees the mapping.
        raw_title = story.get("title", "")
        canonical_name = story.get("canonical_name") or ""
        game_title = canonical_name or raw_title
        if canonical_name and canonical_name.lower() != raw_title.lower():
            logger.info(
                "[ExtractGamingMedia] IGDB canonical: %r -> %r",
                raw_title[:80], canonical_name,
            )
        steam_app_id = story.get("steam_app_id")
        igdb_game_id = story.get("igdb_game_id")

        result = sourcer.source_clip(
            game_title=game_title,
            steam_app_id=steam_app_id,
            igdb_game_id=igdb_game_id,
            project_root=project_root,
        )
        if result:
            return result.model_dump()
        return None

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        """Run clip sourcing for all stories in context."""
        flags = context.get("feature_flags", {})
        if not flags.get("use_gaming_clip_sourcer", False):
            logger.info("[ExtractGamingMedia] use_gaming_clip_sourcer=False, skipping")
            return context

        stories = context.get("stories", [])
        if not stories:
            logger.info("[ExtractGamingMedia] No stories to process")
            return context

        project_root = Path(__file__).resolve().parent.parent.parent.parent

        stats = {
            "total_stories": len(stories),
            "clips_sourced": 0,
            "clips_failed": 0,
            "tiers_used": {},
        }

        for i, story in enumerate(stories):
            title = story.get("title", f"story_{i}")
            try:
                clip = self._source_clip_for_story(story, project_root)
                if clip:
                    story.setdefault("media", {})["clip"] = clip
                    stats["clips_sourced"] += 1
                    tier = clip.get("source_tier", "unknown")
                    stats["tiers_used"][tier] = stats["tiers_used"].get(tier, 0) + 1

                    # Promote clip metadata to story top level for scoring stage
                    story["local_path"] = clip.get("file_path", "")
                    story["duration_seconds"] = clip.get("duration_seconds", 0)
                    story["resolution_height"] = clip.get("height", 0)
                    story["resolution_width"] = clip.get("width", 0)
                    story["fps"] = clip.get("fps", 0.0)
                    if not story.get("fetched_at"):
                        from datetime import datetime

                        story["fetched_at"] = datetime.now(UTC).isoformat()

                    logger.info(
                        "[ExtractGamingMedia] %s -> %s clip sourced (%ds, %dx%d, %.0ffps)",
                        title,
                        clip.get("source_tier"),
                        clip.get("duration_seconds", 0),
                        clip.get("width", 0),
                        clip.get("height", 0),
                        clip.get("fps", 0),
                    )
                else:
                    story.setdefault("media", {})["clip"] = None
                    stats["clips_failed"] += 1
                    logger.info("[ExtractGamingMedia] %s -> no clip found", title)
            except Exception as e:
                story.setdefault("media", {})["clip"] = None
                stats["clips_failed"] += 1
                logger.warning("[ExtractGamingMedia] %s -> error: %s", title, e)

            # Rate limit between stories
            if i < len(stories) - 1:
                time.sleep(INTER_STORY_DELAY_SECONDS)

        context.setdefault("run_stats", {})["clip_sourcing"] = stats
        logger.info(
            "[ExtractGamingMedia] Done: %d/%d clips sourced",
            stats["clips_sourced"],
            stats["total_stories"],
        )
        return context
