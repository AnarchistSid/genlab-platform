"""FrameDrift writing strategy.

Inherits shared LLM + template-fallback writing from BaseWritingStrategy.
Anime uses the default ``_build_caption()`` and ``_write_story_template()``
from the base class — no niche-specific overrides needed.
"""

from __future__ import annotations

from pathlib import Path

from genlab_core.strategies import BaseWritingStrategy
from genlab_core.strategies.base_writing import (  # noqa: F401 — re-export for tests
    _build_extra_instructions,
)

NICHE_ROOT = Path(__file__).resolve().parent.parent


def _story_to_video_dict(story: dict, clip_index: dict | None = None) -> dict:
    """Convert a pipeline story dict to the video dict expected by write_video_content.

    Backward-compatible module-level function.
    """
    story_id = story.get("story_id", "")
    clip_info = {}
    if clip_index:
        clip_info = clip_index.get("clips", {}).get(story_id, {})

    return {
        "video_id": clip_info.get("video_id", story.get("video_id", story_id)),
        "title": story.get("title", ""),
        "channel_name": story.get("source", ""),
        "view_count": story.get("view_count", 0),
        "view_velocity": story.get("view_velocity", 0),
        "age_hours": story.get("age_hours", 1),
        "description_snippet": story.get("summary", "")[:300],
        "tags": story.get("tags", []),
    }


class AnimeWritingStrategy(BaseWritingStrategy):
    """Generate written content for anime stories."""

    def __init__(self) -> None:
        super().__init__(niche_id="anime", niche_root=NICHE_ROOT)
