"""ClutchWire writing strategy.

Inherits shared LLM + template-fallback writing from BaseWritingStrategy.
Overrides ``_build_caption()`` for sports-specific team/player handling and
``_story_to_video_dict()`` to preserve the base mapping.
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


class SportWritingStrategy(BaseWritingStrategy):
    """Generate written content for sports stories."""

    def __init__(self) -> None:
        super().__init__(niche_id="sports", niche_root=NICHE_ROOT)

    def _build_caption(self, story: dict) -> str:
        """Build a sports caption with team names and CTA."""
        self._ensure_config()

        title = story.get("title", "")
        summary = story.get("summary", "")
        teams = story.get("teams", [])
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

        if teams:
            parts.append(f"{'vs '.join(teams[:2])}")

        if cta_library:
            parts.append(random.choice(cta_library))

        if hashtag_pool:
            tags = random.sample(hashtag_pool, min(hashtags_per_post, len(hashtag_pool)))
            parts.append(" ".join(tags))

        target_length = captions_config.get("target_length", 300)
        caption = "\n\n".join(parts)
        return caption[:target_length] if len(caption) > target_length else caption

    def _write_story_template(self, story: dict) -> dict:
        """Generate content for a single story using templates — sports-specific."""
        content = story.get("content", {})
        caption = self._build_caption(story)
        content["caption"] = caption
        content["written"] = True

        platforms_config = (self._templates or {}).get("platforms", {})

        # Instagram — extract hashtags so push_to_backlog can persist them
        ig_config = platforms_config.get("instagram", {})
        hashtags = re.findall(r"#\w+", caption)
        content["instagram"] = {
            "caption": caption,
            "hashtags": hashtags,
            "hashtag_count": ig_config.get("hashtag_count", 4),
        }

        # YouTube
        yt_config = platforms_config.get("youtube", {})
        yt_formulas = (self._templates or {}).get("youtube", {}).get("title_formulas", [])
        yt_title = story.get("title", "")[:yt_config.get("title_max_chars", 40)]
        if yt_formulas:
            teams = story.get("teams", [])
            formula = random.choice(yt_formulas)
            try:
                yt_title = formula.format(
                    Player=teams[0] if teams else "This Player",
                    Team=teams[0] if teams else "This Team",
                )[:yt_config.get("title_max_chars", 40)]
            except (KeyError, IndexError):
                pass
        content["youtube"] = {"title": yt_title, "description": caption}

        # X/Twitter
        x_config = platforms_config.get("x_twitter", {})
        max_chars = x_config.get("max_tweet_chars", 280)
        content["x_twitter"] = {"tweet": caption[:max_chars]}

        # Facebook
        fb_config = platforms_config.get("facebook", {})
        content["facebook"] = {"caption": caption[:fb_config.get("caption_length_target", 300)]}

        # TikTok
        tk_config = platforms_config.get("tiktok", {})
        content["tiktok"] = {"caption": caption[:tk_config.get("max_caption_length", 2200)]}

        # Threads
        th_config = platforms_config.get("threads", {})
        content["threads"] = {"caption": caption[:th_config.get("max_caption_length", 500)]}

        story["content"] = content
        return story
