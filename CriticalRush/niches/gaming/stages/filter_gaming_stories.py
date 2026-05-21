"""Pipeline stage: Filter non-gaming noise from fetched stories.

Runs AFTER FETCH, BEFORE ENRICH. Pure logic, no LLM calls.
Steam/Twitch stories always pass. RSS stories are keyword-filtered.

Usage:
    stage = FilterGamingStories()
    context = stage.execute(context)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


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
        # Steam/Twitch stories are always gaming content
        if story.get("source") in ("steam_spike", "twitch_trending"):
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
