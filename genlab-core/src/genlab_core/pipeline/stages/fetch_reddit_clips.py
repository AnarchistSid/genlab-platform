"""Pipeline stage: fetch trending video posts from Reddit per niche.

Reads the niche's ``sources.yaml`` for a ``reddit:`` block describing
which subreddits to pull. Adds discovered story dicts to
``context["stories"]`` (deduped against existing entries by
``source_url``). Soft-fails — a Reddit outage doesn't break the run.

Design notes:
- Runs AFTER ``FetchTrendingVideos`` so YouTube candidates land first
  and Reddit fills in. The shared story_id (SHA-256 of URL +
  published_date) handles dedup naturally.
- Quota-free: Reddit JSON has no API key requirement, so adding this
  stage doesn't compete with YouTube's 10k/day budget.
- 2026-05-21: introduced after the YouTube SABR experiment took out
  every yt-dlp download for a day → 0/10 sports clips → all 11 sports
  blueprints failed QC. Reddit pulls give us a second leg to stand on.
"""

from __future__ import annotations

import logging
from typing import Any

from genlab_core.pipeline.models import FetcherStage, merge_stories
from genlab_core.pipeline.stage_context import StageContext

logger = logging.getLogger(__name__)


class FetchRedditClips(FetcherStage):
    """Pipeline stage that augments context['stories'] with Reddit video posts.

    Reads:  context['sources_config']['reddit']
    Writes: context['stories'] (append) + run_stats.reddit_stories_found
    """

    # P1 phase-2, 2026-06-19 — EMITTED_SOURCES is empty because Reddit emits
    # a dynamic ``reddit:<subreddit>`` prefix pattern (one source per
    # subreddit name configured in sources.yaml). Downstream consumers
    # (FilterGamingStories) handle this via ``source.startswith("reddit:")``
    # rather than the producer registry. Leaving the set empty is correct —
    # the FetcherStage mixin is for the merge_stories validation, not the
    # source-registry contribution.
    EMITTED_SOURCES = frozenset()

    def execute(self, context: StageContext) -> StageContext:
        niche_id = context.get("niche_id", "")
        if not niche_id:
            return context

        sources_config = context.get("sources_config", {})
        reddit_cfg = sources_config.get("reddit", {})
        if reddit_cfg.get("enabled") is False:
            return context

        subreddits = reddit_cfg.get("subreddits", [])
        if not subreddits:
            return context

        listing = reddit_cfg.get("listing", "top")
        time_window = reddit_cfg.get("time_window", "day")
        per_sub_limit = int(reddit_cfg.get("per_sub_limit", 15))

        # Late import — keeps the pipeline runner import-time cheap and
        # makes the module testable without touching the network.
        from genlab_core.media.fetch_reddit_clips import fetch_for_niche

        try:
            stories = fetch_for_niche(
                niche_id=niche_id,
                subreddits=subreddits,
                listing=listing,
                time_window=time_window,
                per_sub_limit=per_sub_limit,
            )
        except Exception as exc:
            logger.warning(
                "[RedditClips] fetch failed for niche=%s: %s — pipeline "
                "continues with existing stories only",
                niche_id,
                exc,
            )
            return context

        if not stories:
            return context

        # Dedup against stories already in the context. YouTube-trending
        # stage runs first, so a Reddit post linking to a YT video that
        # was already pulled by FetchTrendingVideos shouldn't create a
        # duplicate.
        existing = context.get("stories", []) or []
        existing_urls = {s.get("source_url") for s in existing}
        new_stories = [
            s for s in stories if s.get("source_url") and s["source_url"] not in existing_urls
        ]
        # P1 phase-2: intent-revealing merge. Schema-validates each entry;
        # ``StoryCandidate`` extra='allow' preserves Reddit-specific keys.
        merge_stories(context, new_stories)

        run_stats = context.setdefault("run_stats", {})
        run_stats["reddit_stories_found"] = len(new_stories)
        run_stats.setdefault("source_breakdown", {})["reddit"] = len(new_stories)

        logger.info(
            "[RedditClips] niche=%s added %d new stories (%d total Reddit "
            "candidates after pre-existing dedup)",
            niche_id,
            len(new_stories),
            len(stories),
        )
        return context

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        return self.execute(context)
