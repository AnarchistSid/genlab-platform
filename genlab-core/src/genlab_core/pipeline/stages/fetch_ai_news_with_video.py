"""Pipeline stage: fetch AI news headlines + pair each with a YouTube
video via search. Adds candidates to ``context['stories']``.

Flag-gated OFF by default. Enable with:

    GENLAB_AI_NEWS_WITH_VIDEO_ENABLED=1

Reads config from ``context['sources_config']['ai_news_rss_with_video']``
in the niche's sources.yaml — a list of {url, name} feed configs plus
optional ``per_feed_limit`` (default 3).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from genlab_core.pipeline.models import FetcherStage, merge_stories
from genlab_core.pipeline.stage_context import StageContext

logger = logging.getLogger(__name__)


class FetchAINewsWithVideo(FetcherStage):
    """Pipeline stage that adds AI-news-driven YT video stories.

    Reads:  context['sources_config']['ai_news_rss_with_video']
    Writes: context['stories'] (append) + run_stats.ai_news_stories_found
    """

    EMITTED_SOURCES = frozenset()  # dynamic: ainewsyt:<feed_domain>

    def execute(self, context: StageContext) -> StageContext:
        if os.environ.get(
            "GENLAB_AI_NEWS_WITH_VIDEO_ENABLED", ""
        ).strip().lower() not in ("1", "true", "yes", "on"):
            logger.debug(
                "[AINewsWithVideo] GENLAB_AI_NEWS_WITH_VIDEO_ENABLED not set, "
                "skipping"
            )
            return context

        niche_id = context.get("niche_id", "")
        if not niche_id:
            return context

        sources_config = context.get("sources_config", {})
        cfg = sources_config.get("ai_news_rss_with_video", {})
        if cfg.get("enabled") is False:
            return context

        rss_feeds = cfg.get("feeds", [])
        if not rss_feeds:
            return context

        per_feed_limit = int(cfg.get("per_feed_limit", 3))

        from genlab_core.media.fetch_ai_news_with_video import fetch_for_niche

        try:
            stories = fetch_for_niche(
                niche_id=niche_id,
                rss_feeds=rss_feeds,
                per_feed_limit=per_feed_limit,
            )
        except Exception as exc:
            logger.warning(
                "[AINewsWithVideo] fetch failed for niche=%s: %s",
                niche_id, exc,
            )
            return context

        if not stories:
            return context

        # Dedup against existing stories in the context by source_url
        existing = context.get("stories", []) or []
        existing_urls = {s.get("source_url") for s in existing}
        new_stories = [
            s for s in stories
            if s.get("source_url") and s["source_url"] not in existing_urls
        ]
        merge_stories(context, new_stories)

        run_stats = context.setdefault("run_stats", {})
        run_stats["ai_news_stories_found"] = len(new_stories)
        run_stats.setdefault("source_breakdown", {})["ai_news_with_video"] = len(new_stories)

        logger.info(
            "[AINewsWithVideo] niche=%s added %d new stories (%d total "
            "candidates after pre-existing dedup)",
            niche_id, len(new_stories), len(stories),
        )
        return context

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        return self.execute(context)
