"""Pipeline stage: Filter non-gaming noise from fetched stories.

Runs AFTER FETCH, BEFORE ENRICH. Pure logic, no LLM calls.

Trust model (2026-06-19 — fix for "useless content" symptom):
    Stories whose ``source`` is from a gaming-by-construction fetcher are
    auto-passed. The keyword filter is only consulted for the legacy ``rss``
    path, which is the one source that can legitimately carry non-gaming
    noise (general feeds where ``content_filter`` upstream may have let
    through a stray item).

    Why: YouTube trending (category=20 Gaming), Twitch (gaming-only
    platform), Reddit (subreddits listed in ``sources.yaml`` are gaming),
    Steam (gaming storefront) all produce gaming content by construction.
    Their titles are often emoji + streamer slang ("MAX UMBRA, MIN VOLUME 😈")
    that contain zero English gaming keywords — keyword-filtering them
    silently dropped legitimate gameplay clips.

Usage:
    stage = FilterGamingStories()
    context = stage.execute(context)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Source values from gaming-by-construction fetchers — auto-pass without
# consulting the keyword filter. Add new entries here as new gaming-specific
# fetchers come online. Anything NOT in this allowlist falls through to the
# generic keyword filter.
_TRUSTED_GAMING_SOURCES = frozenset(
    {
        # Local (FetchGamingStories)
        "steam_spike",
        "twitch_trending",
        # FetchTrendingVideos (YouTube category=20 Gaming for gaming niche)
        "youtube_trending",
        "youtube_rss",
        "youtube_playlist",
        "channel_subscription",
        "shared_pool",  # content_pool entries claimed by gaming niche
        # FetchTwitchClips (Twitch is a gaming-only platform)
        "twitch_clips",
        # FetchSteamTrailers (Steam is a gaming storefront)
        "steam_trailer",
    }
)

# FetchRedditClips uses the prefixed pattern "reddit:<subreddit>" rather than
# a fixed source value. Subreddits are configured in ``sources.yaml`` per
# niche, so anything that came back from Reddit on the gaming pipeline is
# gaming-by-config-curation. Detect via prefix.
_REDDIT_SOURCE_PREFIX = "reddit:"


class FilterGamingStories:
    """Remove non-gaming RSS noise and select top stories."""

    # Keywords that strongly suggest gaming content
    GAMING_SIGNALS = [
        "game",
        "gaming",
        "player",
        "dlc",
        "update",
        "patch",
        "release",
        "esports",
        "steam",
        "twitch",
        "ps5",
        "xbox",
        "nintendo",
        "pc",
        "trailer",
        "gameplay",
        "review",
        "launch",
        "early access",
        "free to play",
        "battle royale",
        "rpg",
        "fps",
        "mmo",
        "playstation",
        "switch",
        "mod",
        "speedrun",
        "multiplayer",
        "co-op",
        "open world",
        "sandbox",
        "indie",
        "studio",
        "developer",
        "publisher",
        "elden ring",
        "zelda",
        "mario",
        "resident evil",
        "final fantasy",
        "fortnite",
        "minecraft",
        "valorant",
        "overwatch",
        "league of legends",
        "dota",
        "counter-strike",
        "apex legends",
        "gta",
        "call of duty",
        "assassin's creed",
        "cyberpunk",
        "starfield",
        "diablo",
        "baldur's gate",
        "palworld",
        "helldivers",
    ]

    # Keywords that suggest non-gaming content (noise from general feeds)
    NON_GAMING_SIGNALS = [
        "deal",
        "discount",
        "sale",
        "t-mobile",
        "smartphone",
        "phone",
        "movie",
        "film",
        "tv show",
        "series",
        "streaming",
        "netflix",
        "toy",
        "merchandise",
        "comic",
        "book",
        "anime",
        "manga",
        "football",
        "basketball",
        "soccer",
        "nfl",
        "nba",
        "iphone",
        "android",
        "tablet",
        "laptop",
        "headphone",
        "speaker",
        "camera",
        "subscription",
        "coupon",
        "promo",
        "action figure",
        "game of thrones",
        "board game",
    ]

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        stories = context.get("stories", [])

        filtered = []
        rejected = []

        for story in stories:
            if self._is_gaming_content(story):
                filtered.append(story)
            else:
                rejected.append(story["title"])

        # Sort by score descending, take top 5
        filtered.sort(key=lambda s: s.get("score", 0), reverse=True)
        top_stories = filtered[:5]

        context["stories"] = top_stories
        context.setdefault("run_stats", {})["filter"] = {
            "input_count": len(stories),
            "passed": len(filtered),
            "selected": len(top_stories),
            "rejected": len(rejected),
            "rejected_titles": rejected[:5],
        }

        logger.info(
            "[FILTER] %d → %d stories after gaming filter (rejected: %d)",
            len(stories),
            len(top_stories),
            len(rejected),
        )
        return context

    def _is_gaming_content(self, story: dict[str, Any]) -> bool:
        # Trust all gaming-by-construction fetcher sources outright. The
        # keyword filter below is only meaningful for the legacy ``rss``
        # path where general news feeds can carry off-topic items.
        source = story.get("source", "")
        if source in _TRUSTED_GAMING_SOURCES or source.startswith(_REDDIT_SOURCE_PREFIX):
            return True

        title_lower = story.get("title", "").lower()
        summary_lower = story.get("summary", "").lower()
        combined = title_lower + " " + summary_lower

        # Count signals
        non_gaming_hits = sum(1 for kw in self.NON_GAMING_SIGNALS if kw in combined)
        gaming_hits = sum(1 for kw in self.GAMING_SIGNALS if kw in combined)

        # Strong non-gaming signal — reject outright
        if non_gaming_hits >= 2:
            return False

        # Weak non-gaming signal — only accept with strong gaming evidence
        if non_gaming_hits >= 1 and gaming_hits <= 1:
            return False

        # Accept if any gaming signal present
        return gaming_hits >= 1
