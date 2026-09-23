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
from genlab_core.writing.constants import SUMMARY_MAX_CHARS

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
      id
      siteUrl
      title { romaji english }
      trailer { id site thumbnail }
      coverImage { extraLarge large color }
      bannerImage
      episodes
      status
      nextAiringEpisode { episode airingAt timeUntilAiring }
      season
      seasonYear
      startDate { year month day }
      popularity
      trending
      description(asHtml: false)
      genres
      studios(isMain: true) { nodes { name } }
      characters(sort: FAVOURITES_DESC, perPage: 4) {
        nodes { name { full } image { large } }
      }
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
        return desc[:SUMMARY_MAX_CHARS]
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
    return (synthesized or desc)[:SUMMARY_MAX_CHARS]


#: Fields a story carries so the STILL path can use the SHOW'S OWN material.
#: `still/sourcing.carried_art` reads the story record and nothing else, on
#: purpose -- fetching art from the catalogue at render time would be new
#: material under someone else's licence dressed as something we already had.
#: The rule was right and the record was thin: AniList hands us the cover, the
#: banner, the PV id and the character portraits in the SAME response we
#: already make, and dropping them was what forced the generated-anime-picture
#: -of-nothing reels. Carrying them costs one larger query and no new request.
ANILIST_CARRIED_FIELDS = (
    "status",
    "cover_image_url",
    "banner_image_url",
    "trailer_id",
    "trailer_site",
    "characters",
    "art_attribution",
    "art_provenance",
)


def _anilist_characters(media: dict) -> list[dict]:
    """Top characters by favourites, each with a portrait we may use."""
    out: list[dict] = []
    for node in (media.get("characters") or {}).get("nodes") or []:
        name = ((node.get("name") or {}).get("full") or "").strip()
        image = ((node.get("image") or {}).get("large") or "").strip()
        if name and image.startswith("http"):
            out.append({"name": name, "image_url": image})
    return out


def _anilist_art(media: dict) -> dict[str, Any]:
    """Cover, banner and the show's own dominant colour.

    bannerImage is null for most seasonal entries -- 2 of the top 3 FALL 2026
    shows had none when this was written -- so every consumer must degrade
    rather than assume it. extraLarge is preferred over large because the
    cover is used as a full-frame hero shot at 1080x1920.
    """
    cover = media.get("coverImage") or {}
    return {
        "cover_image_url": (cover.get("extraLarge") or cover.get("large") or "").strip(),
        "cover_color": (cover.get("color") or "").strip(),
        "banner_image_url": (media.get("bannerImage") or "").strip(),
    }


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
                    "trailer_id": yt_id,
                    "trailer_site": "youtube",
                    # MAL's own entry image. Same rule as AniList: carried with
                    # a statement of origin, or not carried.
                    "cover_image_url": (
                        ((entry.get("images") or {}).get("jpg") or {}).get("image_url") or ""
                    ).strip(),
                    "art_attribution": "Key art: MyAnimeList",
                    "art_provenance": (entry.get("url") or "jikan").strip(),
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
            sd = media.get("startDate") or {}
            entry = {
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
                # ── the show's own material, carried with provenance ──
                "trailer_id": yt_id,
                "trailer_site": "youtube",
                "trailer_thumbnail_url": (trailer.get("thumbnail") or "").strip(),
                "characters": _anilist_characters(media),
                "episodes": media.get("episodes"),
                # ANIME-16 §6. A date is not the news. "11 JULY 2025" was
                # slammed over a show that had been airing for months; the
                # reveal has to know whether the show is upcoming, airing or
                # finished before it can say anything true about it.
                "status": media.get("status") or "",
                "next_airing": media.get("nextAiringEpisode") or None,
                "season": media.get("season") or "",
                "season_year": media.get("seasonYear"),
                "start_date": {
                    "year": sd.get("year"),
                    "month": sd.get("month"),
                    "day": sd.get("day"),
                },
                "anilist_id": media.get("id"),
                "anilist_url": (media.get("siteUrl") or "").strip(),
                # Provenance is the AniList entry itself. An art URL with no
                # statement of where it came from is the shape that lets a
                # later reader assume it was ours.
                "art_attribution": (f"Key art: {studios[0]}" if studios else "Key art: AniList"),
                "art_provenance": (media.get("siteUrl") or "anilist").strip(),
            }
            entry.update(_anilist_art(media))
            results.append(entry)
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
                # Carry the show's own material onto the STORY, not just into
                # the fetcher's scratch dict. A field held only in the promo
                # dict dies at this boundary -- the four-gate propagator shape
                # (fetcher -> story -> blueprint -> column). This is gate one.
                for field in (
                    *ANILIST_CARRIED_FIELDS,
                    "cover_color",
                    "trailer_thumbnail_url",
                    "episodes",
                    "status",
                    "next_airing",
                    "season",
                    "season_year",
                    "start_date",
                    "anilist_id",
                    "anilist_url",
                    "genres",
                    "studios",
                    "popularity",
                    "description",
                ):
                    if field in p and p[field] not in (None, "", [], {}):
                        new_stories[-1][field] = p[field]

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
