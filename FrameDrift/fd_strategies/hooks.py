"""FrameDrift hook generation strategy.

Inherits shared hook generation from BaseHookStrategy. Provides anime-specific:
- ``_classify_story()`` — trend-cycle-aware + content-type routing
- ``_substitute_placeholders()`` — {title}, {studio}, {voice_actor}, etc.
"""

from __future__ import annotations

import re
from pathlib import Path

from genlab_core.strategies import BaseHookStrategy

NICHE_ROOT = Path(__file__).resolve().parent.parent

# Trend cycle -> preferred categories (fallback)
_CYCLE_CATEGORIES: dict[str, list[str]] = {
    "emerging": ["trend_emerging"],
    "peak": ["anime_premiere", "voice_actor_trigger"],
    "declining": ["default"],
    "unknown": ["default"],
}


class AnimeHookStrategy(BaseHookStrategy):
    """Generate trend-cycle-aware hooks for anime content."""

    _title_fallback_label = "Anime moment"

    def __init__(self) -> None:
        super().__init__(niche_id="anime", niche_root=NICHE_ROOT)

    def _classify_story(self, story: dict) -> str:
        """Determine story category. Content type takes priority over cycle stage."""
        is_creator = story.get("is_creator_spotlight", False)
        is_release = story.get("is_new_release", False)
        is_collab = story.get("is_collab", False)
        is_event = story.get("is_event_coverage", False)

        if is_creator:
            return "voice_actor_trigger"
        if is_release:
            return "anime_premiere"
        if is_collab:
            return "studio_collab"
        if is_event:
            return "manga_release"

        cycle = story.get("trend_cycle_stage", "unknown")
        preferred = _CYCLE_CATEGORIES.get(cycle, ["default"])
        return preferred[0]

    def _substitute_placeholders(self, formula: str, story: dict) -> str:
        title = story.get("title", "")
        trend = self._truncate_at_word(story.get("trend_name", "this trend"), 40)
        studio = story.get("studio_mentioned", "the studio")
        creator = story.get("creator_name", "the creator")

        subs = {
            "title": self._truncate_at_word(title, 40) if title else "this anime",
            "studio": studio,
            "voice_actor": creator,
            "episode": "the latest",
            "item": self._truncate_at_word(title, 30),
            "season": "this season",
            "studio1": studio,
            "studio2": "the collab studio",
            "trend_name": trend,
            "announcement": self._truncate_at_word(title, 50) if title else "this news",
        }

        result = formula
        for key, value in subs.items():
            result = result.replace(f"{{{key}}}", value)

        # Remove any remaining unreplaced {placeholder} patterns
        result = re.sub(r"\{[a-z_]+\}", "", result)
        result = re.sub(r"\s+", " ", result).strip().rstrip(" .,!?-:")
        return result
