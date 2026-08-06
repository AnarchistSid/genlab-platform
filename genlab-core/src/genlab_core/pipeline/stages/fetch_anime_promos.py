"""Pipeline stage: Fetch anime promotional videos from Jikan + AniList.

Returns YouTube video IDs that feed into videos.list enrichment at 1 unit/50.
No authentication required. Combined 0 YouTube quota cost.

Sources:
    Jikan (MAL): /watch/promos — recently added PVs/trailers with YouTube IDs
    AniList GraphQL: seasonal airing anime with official trailer links
"""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime
from typing import Any

import requests

from genlab_core.pipeline.models import FetcherStage, merge_stories
from genlab_core.pipeline.stage_context import StageContext

logger = logging.getLogger(__name__)

# AniList GraphQL query for currently airing anime with trailers
_ANILIST_QUERY = """
query ($season: MediaSeason, $seasonYear: Int) {
  Page(perPage: 20) {
    media(
      season: $season, seasonYear: $seasonYear,
      format: TV, sort: POPULARITY_DESC,
      isAdult: false
    ) {
      title { romaji english }
      trailer { id site }
      popularity
      trending
      description(asHtml: false)
      genres
      studios(isMain: true) { nodes { name } }
    }
  }
}
"""

_SEASON_MAP = {
    1: "WINTER",
    2: "WINTER",
    3: "SPRING",
    4: "SPRING",
    5: "SPRING",
    6: "SUMMER",
    7: "SUMMER",
    8: "SUMMER",
    9: "FALL",
    10: "FALL",
    11: "FALL",
    12: "WINTER",
}


def _get_current_anime_season() -> tuple[str, int]:
    now = datetime.now(UTC)
    season = _SEASON_MAP[now.month]
    year = now.year
    if now.month == 12:
        year += 1  # December Winter belongs to next year's season
    return season, year


# base_writing._MIN_WRITABLE_CONTEXT_CHARS. Kept in sync so anime promos
# always give the writer LLM enough context to skip the thin-context guard.
_WRITER_MIN_CONTEXT_CHARS = 40


def _build_promo_summary(p: dict) -> str:
    """Assemble a writer-usable summary from AniList/Jikan promo metadata.

    AniList's Media has a rich `description` (200-2000 chars). Jikan's
    watch/promos endpoint has no synopsis field — fall back to a
    synthesized "trending anime PV: {title}" string. In both cases
    guarantee the writer's ≥40 char thin-context floor is cleared.
    """
    desc = (p.get("description") or "").strip()
    if len(desc) >= _WRITER_MIN_CONTEXT_CHARS:
        return desc[:500]
    title = (p.get("title") or "").strip()
    source = (p.get("source") or "").strip()
    genres = [str(g).strip() for g in (p.get("genres") or []) if str(g).strip()]
    studios = [str(s).strip() for s in (p.get("studios") or []) if str(s).strip()]
    parts: list[str] = []
    if title:
        source_label = "trending anime PV" if source == "jikan_promos" else "seasonal anime trailer"
        parts.append(f"{source_label}: {title}")
    if studios:
        parts.append(f"Studio: {', '.join(studios[:2])}")
    if genres:
        parts.append(f"Genres: {', '.join(genres[:5])}")
    synthesized = ". ".join(parts).strip()
    return (synthesized or desc)[:500]


def _fetch_jikan_promos(max_promos: int = 20) -> list[dict]:
    """Fetch latest anime promotional videos from Jikan (MAL)."""
    try:
        r = requests.get(
            "https://api.jikan.moe/v4/watch/promos",
            params={"page": 1},
            timeout=10,
        )
        if r.status_code == 429:
            logger.warning("[AnimePromos] Jikan rate limited, skipping")
            return []
        r.raise_for_status()
        results = []
        for promo in r.json().get("data", [])[:max_promos]:
            entry = promo.get("entry", {})
            trailer = promo.get("trailer", {})
            yt_id = trailer.get("youtube_id")
            if not yt_id:
                continue
            title = entry.get("title", "")
            results.append(
                {
                    "title": title,
                    "video_id": yt_id,
                    "url": f"https://www.youtube.com/watch?v={yt_id}",
                    "source_url": f"https://www.youtube.com/watch?v={yt_id}",
                    "mal_id": entry.get("mal_id"),
                    "source": "jikan_promos",
                    "_trending_video": True,
                }
            )
        return results
    except Exception as e:
        logger.warning("[AnimePromos] Jikan failed: %s", e)
        return []


def _fetch_anilist_trailers(max_results: int = 20) -> list[dict]:
    """Fetch currently airing anime with YouTube trailers from AniList."""
    season, year = _get_current_anime_season()
    try:
        r = requests.post(
            "https://graphql.anilist.co",
            json={"query": _ANILIST_QUERY, "variables": {"season": season, "seasonYear": year}},
            timeout=10,
        )
        r.raise_for_status()
        results = []
        for media in r.json().get("data", {}).get("Page", {}).get("media", [])[:max_results]:
            trailer = media.get("trailer") or {}
            if trailer.get("site") != "youtube":
                continue
            yt_id = trailer.get("id")
            if not yt_id:
                continue
            title_obj = media.get("title") or {}
            title = title_obj.get("english") or title_obj.get("romaji", "")
            # AniList descriptions embed HTML-like tags (<br>, <i>) even when
            # asHtml=false — strip them so writer sees clean prose.
            raw_desc = (media.get("description") or "").strip()
            clean_desc = re.sub(r"<[^>]+>", " ", raw_desc)
            clean_desc = re.sub(r"\s+", " ", clean_desc).strip()
            studios = [
                s.get("name", "")
                for s in (media.get("studios") or {}).get("nodes", [])
                if s.get("name")
            ]
            results.append(
                {
                    "title": title,
                    "video_id": yt_id,
                    "url": f"https://www.youtube.com/watch?v={yt_id}",
                    "source_url": f"https://www.youtube.com/watch?v={yt_id}",
                    "popularity": media.get("popularity", 0),
                    "trending": media.get("trending", 0),
                    "source": "anilist",
                    "_trending_video": True,
                    "description": clean_desc,
                    "genres": media.get("genres") or [],
                    "studios": studios,
                }
            )
        return sorted(results, key=lambda x: x.get("trending", 0), reverse=True)
    except Exception as e:
        logger.warning("[AnimePromos] AniList failed: %s", e)
        return []


class FetchAnimePromos(FetcherStage):
    """Pipeline stage: fetch anime PVs/trailers from Jikan + AniList.

    Adds YouTube video IDs to context for downstream enrichment.
    Runs before DownloadTopVideos — provides additional video candidates
    beyond YouTube RSS/playlist sources.

    Zero YouTube API quota cost (external APIs only).
    """

    # P1 phase-2, 2026-06-19 — declare both emitted source values for the
    # producer registry. Anime fetcher returns from two external APIs that
    # each carry a distinct ``source`` tag.
    EMITTED_SOURCES = frozenset({"jikan_promos", "anilist"})

    def execute(self, context: StageContext) -> StageContext:
        niche_id = context.get("niche_id", "")
        if niche_id != "anime":
            return context

        sources_config = context.get("sources_config", {})
        jikan_cfg = sources_config.get("jikan", {})
        anilist_cfg = sources_config.get("anilist", {})

        all_promos: list[dict] = []

        # Jikan promos
        if jikan_cfg.get("enabled", True):
            max_promos = jikan_cfg.get("max_promos", 20)
            promos = _fetch_jikan_promos(max_promos)
            all_promos.extend(promos)
            logger.info("[AnimePromos] Jikan: %d promos with YouTube IDs", len(promos))
            time.sleep(1)  # Respect Jikan rate limit

        # AniList trailers
        if anilist_cfg.get("enabled", True):
            max_results = anilist_cfg.get("max_results", 20)
            trailers = _fetch_anilist_trailers(max_results)
            all_promos.extend(trailers)
            logger.info("[AnimePromos] AniList: %d trailers with YouTube IDs", len(trailers))

        # Deduplicate by video_id
        seen: set[str] = set()
        unique: list[dict] = []
        for p in all_promos:
            vid = p.get("video_id", "")
            if vid and vid not in seen:
                seen.add(vid)
                unique.append(p)

        # Merge into stories (prepend so trending videos take priority)
        if unique:
            from genlab_core.cache.stable_ids import generate_story_id

            now_iso = datetime.now(UTC).isoformat()
            new_stories = []
            for p in unique:
                sid = generate_story_id(p["url"], now_iso)
                new_stories.append(
                    {
                        "story_id": sid,
                        "title": p["title"],
                        "source": p["source"],
                        "source_url": p["url"],
                        "canonical_url": p["url"],
                        "published_at": now_iso,
                        "fetched_at": now_iso,
                        "summary": _build_promo_summary(p),
                        "video_id": p["video_id"],
                        "video_source": p["source"],
                        "niche_id": niche_id,
                        "_trending_video": True,
                        "source_mention_count": 2,
                    }
                )

            existing = context.get("stories", [])
            # Avoid duplicates with existing stories
            existing_urls = {s.get("source_url") for s in existing}
            new_stories = [s for s in new_stories if s["source_url"] not in existing_urls]
            # P1 phase-2: intent-revealing merge + StoryCandidate validation.
            merge_stories(context, new_stories)

        run_stats = context.setdefault("run_stats", {})
        run_stats["anime_promos_found"] = len(unique)
        logger.info("[AnimePromos] %d unique promos added to pipeline", len(unique))
        return context

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        return self.execute(context)
