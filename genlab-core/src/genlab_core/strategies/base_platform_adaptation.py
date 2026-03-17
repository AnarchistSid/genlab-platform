"""Base platform adaptation strategy with shared URL-handling logic.

All 5 channels apply the same platform-specific URL rules:
- X/Twitter: links moved to first_reply, never in main tweet body
- Instagram: external URLs stripped from caption
- TikTok: external URLs stripped (rendered as unclickable text)

Subclasses only need to set ``niche_id`` and optionally override
individual ``_adapt_*`` methods for niche-specific behavior.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict

from .interfaces import PlatformAdaptationStrategy

logger = logging.getLogger(__name__)

# Shared URL pattern for link extraction across all channels.
_URL_PATTERN = re.compile(r"https?://\S+")


class BasePlatformAdaptationStrategy(PlatformAdaptationStrategy):
    """Shared platform adaptation logic.

    Parameters
    ----------
    niche_id:
        Unique identifier for the channel (e.g. ``"sports"``, ``"anime"``).
        Used for logging and multi-tenant compatibility.
    """

    def __init__(self, niche_id: str) -> None:
        self._niche_id = niche_id
        logger.info("[%s] %s initialized", self._niche_id, type(self).__name__)

    # ------------------------------------------------------------------
    # Per-platform adaptation methods — override in subclass if needed
    # ------------------------------------------------------------------

    def _adapt_x_twitter(self, content: Dict[str, Any], story: Dict[str, Any]) -> None:
        """X/Twitter: Move all links to first_reply. Never in main tweet."""
        tw = content.get("x_twitter", {})
        if not tw.get("tweet"):
            return

        tweet_text = tw["tweet"]
        urls = _URL_PATTERN.findall(tweet_text)

        if urls:
            cleaned = _URL_PATTERN.sub("", tweet_text).strip()
            cleaned = re.sub(r"\s{2,}", " ", cleaned)
            tw["tweet"] = cleaned

            source_url = story.get("source_url", "")
            all_urls = list(dict.fromkeys(urls + ([source_url] if source_url else [])))
            tw["first_reply"] = " ".join(all_urls)
            logger.info(
                "[%s] X/Twitter: moved %d URLs to first_reply",
                self._niche_id,
                len(all_urls),
            )
        elif story.get("source_url"):
            tw["first_reply"] = story["source_url"]

    def _adapt_instagram(self, content: Dict[str, Any]) -> None:
        """Instagram: Strip external URLs from caption."""
        ig = content.get("instagram", {})
        if ig.get("caption"):
            ig["caption"] = _URL_PATTERN.sub("", ig["caption"]).strip()

    def _adapt_tiktok(self, content: Dict[str, Any]) -> None:
        """TikTok: Strip URLs (render as unclickable text)."""
        tk = content.get("tiktok", {})
        if tk.get("caption"):
            tk["caption"] = _URL_PATTERN.sub("", tk["caption"]).strip()

    def _adapt_story(self, content: Dict[str, Any], story: Dict[str, Any]) -> None:
        """Apply all platform adaptations to a single story.

        Override this to add extra platform-specific logic (e.g. Facebook,
        Threads, YouTube) while keeping the default URL-cleaning chain.
        """
        self._adapt_x_twitter(content, story)
        self._adapt_instagram(content)
        self._adapt_tiktok(content)

    # ------------------------------------------------------------------
    # execute() — shared pipeline entry point
    # ------------------------------------------------------------------

    def execute(self, context: Any) -> Any:
        """Apply platform-specific adaptations to all stories."""
        stories = context.get("stories", [])
        adapted_count = 0

        for story in stories:
            content = story.get("content")
            if not content:
                continue

            self._adapt_story(content, story)
            adapted_count += 1

        context.setdefault("run_stats", {})["adapt"] = {
            "adapted": adapted_count,
            "total": len(stories),
        }
        logger.info(
            "[%s] PlatformAdaptation: adapted %d stories",
            self._niche_id,
            adapted_count,
        )
        return context
