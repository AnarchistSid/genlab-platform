"""SpliceReel writing strategy.

Inherits shared LLM + template-fallback writing from BaseWritingStrategy.
Overrides ``_build_caption()`` for film-specific franchise hashtag handling and
``_story_to_video_dict()`` to prefer ``film_title`` over ``title``.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

from genlab_core.strategies import BaseWritingStrategy
from genlab_core.strategies.base_writing import (  # noqa: F401 — re-export for tests
    _build_extra_instructions,
)

NICHE_ROOT = Path(__file__).resolve().parent.parent


def _story_to_video_dict(story: dict, clip_index: dict | None = None) -> dict:
    """Convert a pipeline story dict to the video dict expected by write_video_content.

    Backward-compatible module-level function. Prefers ``film_title`` over ``title``.
    """
    story_id = story.get("story_id", "")
    clip_info = {}
    if clip_index:
        clip_info = clip_index.get("clips", {}).get(story_id, {})

    return {
        "video_id": clip_info.get("video_id", story.get("video_id", story_id)),
        "title": story.get("film_title", "") or story.get("title", ""),
        "channel_name": story.get("source", ""),
        "view_count": story.get("view_count", 0),
        "view_velocity": story.get("view_velocity", 0),
        "age_hours": story.get("age_hours", 1),
        "description_snippet": story.get("summary", "")[:300],
        "tags": story.get("tags", []),
    }


class MovieWritingStrategy(BaseWritingStrategy):
    """Generate written content for film stories."""

    def __init__(self) -> None:
        super().__init__(niche_id="movies", niche_root=NICHE_ROOT)

    def _story_to_video_dict(
        self, story: dict, clip_index: dict | None = None
    ) -> dict:
        """Use ``film_title`` as the primary title if available."""
        video = super()._story_to_video_dict(story, clip_index)
        video["title"] = story.get("film_title", "") or story.get("title", "")
        return video

    def _build_caption(self, story: dict) -> str:
        """Build a film caption with franchise hashtag support."""
        self._ensure_config()

        title = story.get("film_title", "") or story.get("title", "")
        summary = story.get("summary", "")
        hook = story.get("content", {}).get("hook", "")

        captions_config = (self._templates or {}).get("captions", {})
        cta_library = captions_config.get("cta_library", [])
        hashtag_pool = captions_config.get("hashtag_pool", [])
        hashtags_per_post = captions_config.get("hashtags_per_post", 4)

        parts: list[str] = []
        if hook:
            parts.append(hook)
        elif title:
            parts.append(title)

        if summary:
            parts.append(summary[:200])

        if cta_library:
            parts.append(random.choice(cta_library))

        # Franchise hashtags
        franchise = story.get("franchise", "")
        franchise_tags = captions_config.get("franchise_hashtags", {})
        selected_tags: list[str] = []
        if franchise and franchise in franchise_tags:
            selected_tags.append(franchise_tags[franchise])
            hashtags_per_post -= 1

        if hashtag_pool:
            remaining = random.sample(hashtag_pool, min(hashtags_per_post, len(hashtag_pool)))
            selected_tags.extend(remaining)

        if selected_tags:
            parts.append(" ".join(selected_tags))

        target_length = captions_config.get("target_length", 300)
        caption = "\n\n".join(parts)
        return caption[:target_length] if len(caption) > target_length else caption

    def _write_story_template(self, story: dict) -> dict:
        """Generate content for a single story using templates — film-specific."""
        content = story.get("content", {})
        caption = self._build_caption(story)
        content["caption"] = caption
        content["written"] = True

        film_title = story.get("film_title", "") or story.get("title", "")

        hashtags = re.findall(r"#\w+", caption)
        content["instagram"] = {"caption": caption, "hashtags": hashtags}
        content["youtube"] = {"title": film_title[:40], "description": caption}
        content["x_twitter"] = {"tweet": caption[:280]}
        content["facebook"] = {"caption": caption[:300]}
        content["tiktok"] = {"caption": caption[:2200]}
        content["threads"] = {"caption": caption[:500]}

        story["content"] = content
        return story
