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
import os
from typing import Any, Callable

from genlab_core.media.trending_video_fetcher import FetchTrendingVideos
from genlab_core.pipeline.models import collect_emitted_sources
from genlab_core.pipeline.stages.fetch_reddit_clips import FetchRedditClips
from genlab_core.pipeline.stages.fetch_steam_trailers import FetchSteamTrailers
from genlab_core.pipeline.stages.fetch_twitch_clips import FetchTwitchClips
from niches.gaming.stages.fetch_gaming_stories import FetchGamingStories

logger = logging.getLogger(__name__)

# P1 phase 4 COMPLETE, 2026-06-19 — trust list is now ENTIRELY DERIVED from
# the actual producer stages via FetcherStage.EMITTED_SOURCES. The legacy
# hardcoded fallback set is EMPTY — every gaming-pipeline fetcher declares
# its source values via the registry. Adding a new fetcher to the gaming
# pipeline now requires zero edits here. PR #360's "trust list drift from
# producers" bug class is structurally prevented. Contract tests pin the
# relationship at CI.
#
# Migrated fetchers (all phases): FetchTwitchClips, FetchSteamTrailers,
# FetchRedditClips (empty EMITTED_SOURCES — Reddit emits ``reddit:<subreddit>``
# prefix pattern handled separately below), FetchGamingStories (local),
# FetchTrendingVideos (4 source values).
_REGISTRY_TRUSTED_SOURCES = collect_emitted_sources(
    [
        FetchTwitchClips,
        FetchSteamTrailers,
        FetchRedditClips,
        FetchGamingStories,
        FetchTrendingVideos,
    ]
)

# Empty after phase 4. Kept as a documented anchor for future fetcher
# additions that can't yet declare EMITTED_SOURCES (e.g. a new fetcher
# whose source values are dynamic). Adding entries here without a TODO to
# migrate them into a real FetcherStage is the smell that bit us in PR #360.
_LEGACY_HARDCODED_SOURCES: frozenset[str] = frozenset()

_TRUSTED_GAMING_SOURCES = _REGISTRY_TRUSTED_SOURCES | _LEGACY_HARDCODED_SOURCES

# FetchRedditClips uses the prefixed pattern "reddit:<subreddit>" rather than
# a fixed source value (one source per subreddit), so the registry can't
# enumerate them. Detect via prefix. When FetchRedditClips becomes a
# FetcherStage it can expose this prefix as a class attribute too.
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
        niche_config = context.get("niche_config", {})

        # 2026-08-11 Option A: game-name cooldown. Gaming's local fetchers
        # emit ``title = game_name`` without video_id (see Option C
        # StoryCandidate bypass), so video_id_dedup silently no-ops and the
        # same top-trending games (LoL x10, Fortnite x7, Rust x3) surface
        # every fetch cycle. The cooldown rejects any signal-only gaming
        # candidate whose title matches a game published/scheduled within
        # the window. Config: video_sourcing.game_name_cooldown_days
        # (default 0 = disabled; gaming sets 7 in niche.yaml). Video-
        # bearing stories (populated video_id) bypass the cooldown —
        # video_id_dedup handles them and a real YouTube video that
        # happens to be titled "Rust" shouldn't be blocked on title alone.
        cooldown_days = int(
            niche_config.get("video_sourcing", {}).get("game_name_cooldown_days", 0)
        )
        recent_titles_lower: set[str] = set()
        if cooldown_days > 0:
            recent_titles_lower = self._recent_gaming_titles(cooldown_days)

        filtered = []
        rejected = []
        cooldown_rejected_titles: list[str] = []

        for story in stories:
            if not self._is_gaming_content(story):
                rejected.append(story["title"])
                continue
            # Cooldown only applies to signal-only stories (no video_id).
            # Video-bearing stories have video_id_dedup as their key.
            if cooldown_days > 0 and not story.get("video_id"):
                title_lower = (story.get("title") or "").strip().lower()
                if title_lower and title_lower in recent_titles_lower:
                    cooldown_rejected_titles.append(story["title"])
                    continue
            filtered.append(story)

        # Sort by score descending, take top N (config-driven, default 5).
        #
        # 2026-06-28 — was hardcoded `[:5]`. Gaming's daily zero_blueprints
        # alerts traced to a small surviving-candidate pool: filter passes 5
        # → enrich loses ~2 to IGDB failures → 3 reach writing/rendering →
        # push_to_backlog dedup blocks any whose URL/title was published in
        # the last url_dedup_ttl_days. When the top-3 trending happen to be
        # already-published this week, the day ends with 0 blueprints. Raising
        # the cap gives dedup more survivors to choose from without changing
        # the publish cap (still 1/day at the publisher stage). Cost:
        # ~2x render CPU on starvation-prone days (~5 extra min on the 4 GB
        # Hetzner VPS); render only runs when sources line up so the actual
        # marginal cost is small.
        filtered.sort(key=lambda s: s.get("score", 0), reverse=True)
        filter_top_n = niche_config.get("video_sourcing", {}).get("filter_top_n", 5)
        top_stories = filtered[:filter_top_n]

        context["stories"] = top_stories
        context.setdefault("run_stats", {})["filter"] = {
            "input_count": len(stories),
            "passed": len(filtered),
            "selected": len(top_stories),
            "rejected": len(rejected),
            "rejected_titles": rejected[:5],
            "cooldown_rejected": len(cooldown_rejected_titles),
            "cooldown_rejected_titles": cooldown_rejected_titles[:10],
        }

        cooldown_note = ""
        if cooldown_rejected_titles:
            cooldown_note = f" (game-name cooldown: {len(cooldown_rejected_titles)})"
        logger.info(
            "[FILTER] %d → %d stories after gaming filter (rejected: %d%s)",
            len(stories),
            len(top_stories),
            len(rejected),
            cooldown_note,
        )
        return context

    def _recent_gaming_titles(self, cooldown_days: int) -> set[str]:
        """Return the set of lowercased titles of gaming blueprints active
        within the last ``cooldown_days``.

        "Active" = status in PUBLISHED / VISUAL_READY / READY_TO_PUBLISH
        / SCHEDULED (any state that IS or WILL be a live reel). Uses
        ``updated_at`` as the recency signal so PUBLISHED rows drop out of
        the cooldown set N days after publish and newly-scheduled rows
        block for N days from their scheduling time.

        Wrapped by ``_safe_recent_gaming_titles`` in tests + in production
        so DB blips fail-open (empty set + WARN log per rule #19). The
        raw method is the module boundary that tests monkeypatch when
        they want to inject a synthetic recent-set.
        """
        return self._safe_recent_gaming_titles(cooldown_days)

    def _safe_recent_gaming_titles(
        self,
        cooldown_days: int,
        *,
        _query: Callable[[int], set[str]] | None = None,
    ) -> set[str]:
        """Fail-open shim around the DB query. If the query raises, log
        WARN and return empty set (no cooldown enforced this run).

        The ``_query`` kwarg exists for test injection — production code
        never passes it; tests pass a mock query that raises so the
        fail-open branch is exercised.
        """
        query = _query if _query is not None else self._query_recent_titles
        try:
            return query(cooldown_days)
        except Exception as exc:  # noqa: BLE001 — fail-open by design
            logger.warning(
                "[FILTER] game-name cooldown query failed (%s) — cooldown "
                "disabled for this run. Better to occasionally publish a "
                "repeat than to silent-block gaming on a DB blip.",
                exc,
            )
            return set()

    def _query_recent_titles(self, cooldown_days: int) -> set[str]:
        """Direct DB query. Extracted so tests can monkey-patch it via the
        ``_query`` param of ``_safe_recent_gaming_titles`` without needing
        a mock BacklogClient."""
        dsn = os.environ.get("DATABASE_URL", "").strip()
        if not dsn:
            logger.warning(
                "[FILTER] DATABASE_URL unset — game-name cooldown disabled"
            )
            return set()

        import psycopg  # local import — keeps the module import-time cheap

        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT LOWER(title)
                    FROM blueprints
                    WHERE niche_id = 'gaming'
                      AND status IN (
                          'PUBLISHED',
                          'VISUAL_READY',
                          'READY_TO_PUBLISH',
                          'SCHEDULED'
                      )
                      AND updated_at > NOW() - make_interval(days => %s)
                    """,
                    (cooldown_days,),
                )
                return {row[0] for row in cur.fetchall() if row[0]}

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
