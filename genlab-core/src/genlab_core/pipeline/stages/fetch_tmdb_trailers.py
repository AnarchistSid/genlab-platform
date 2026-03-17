"""Pipeline stage: Fetch YouTube trailer IDs from TMDB for movies.

TMDB already provides YouTube video IDs for movie trailers in its API.
This stage collects those IDs so they can be enriched via videos.list
at 1 unit per 50 — instead of routing through search.list at 100 units.

TMDB rate limit: 40 req/10 sec. No YouTube quota consumed here.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

import requests

logger = logging.getLogger(__name__)


def _fetch_trending_movie_ids(api_key: str, max_movies: int = 20) -> list[dict]:
    """Fetch trending movie IDs from TMDB weekly trending endpoint."""
    try:
        r = requests.get(
            "https://api.themoviedb.org/3/trending/movie/week",
            params={"api_key": api_key},
            timeout=10,
        )
        r.raise_for_status()
        return [
            {"id": m["id"], "title": m.get("title", ""), "release_date": m.get("release_date", "")}
            for m in r.json().get("results", [])[:max_movies]
        ]
    except Exception as e:
        logger.warning("[TMDBTrailers] Trending fetch failed: %s", e)
        return []


def _fetch_movie_trailer_ids(api_key: str, movie_ids: list[dict]) -> list[dict]:
    """Get YouTube trailer IDs for movies from TMDB /movie/{id}/videos.

    Returns dicts with title, yt_video_id, and movie metadata.
    """
    results = []
    for movie in movie_ids:
        movie_id = movie["id"]
        try:
            r = requests.get(
                f"https://api.themoviedb.org/3/movie/{movie_id}/videos",
                params={"api_key": api_key, "language": "en-US"},
                timeout=10,
            )
            if r.status_code != 200:
                continue
            for video in r.json().get("results", []):
                if (video.get("site") == "YouTube" and
                        video.get("type") in ("Trailer", "Teaser") and
                        video.get("official", False)):
                    yt_id = video["key"]
                    results.append({
                        "title": f"{movie['title']} - {video.get('name', 'Trailer')}",
                        "video_id": yt_id,
                        "url": f"https://www.youtube.com/watch?v={yt_id}",
                        "source_url": f"https://www.youtube.com/watch?v={yt_id}",
                        "tmdb_id": movie_id,
                        "release_date": movie.get("release_date", ""),
                        "source": "tmdb_trailer",
                        "_trending_video": True,
                    })
                    break  # Take first official trailer per movie
            time.sleep(0.1)  # 10 req/sec comfortable rate
        except Exception as e:
            logger.warning("[TMDBTrailers] Videos fetch failed for %d: %s", movie_id, e)
    return results


class FetchTMDBTrailers:
    """Pipeline stage: fetch YouTube trailer IDs from TMDB.

    Adds trailer video IDs to context for downstream download.
    Zero YouTube API quota — TMDB provides the YouTube IDs directly.

    Requires TMDB_API_KEY in environment.
    """

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        niche_id = context.get("niche_id", "")
        if niche_id != "movies":
            return context

        api_key = os.environ.get("TMDB_API_KEY")
        if not api_key:
            logger.warning("[TMDBTrailers] TMDB_API_KEY not set, skipping")
            return context

        sources_config = context.get("sources_config", {})
        tmdb_cfg = sources_config.get("tmdb_trailers", {})
        if tmdb_cfg.get("enabled") is False:
            return context

        # Fetch trending movies and their trailer IDs
        movies = _fetch_trending_movie_ids(api_key)
        if not movies:
            logger.info("[TMDBTrailers] No trending movies found")
            return context

        trailers = _fetch_movie_trailer_ids(api_key, movies)
        logger.info("[TMDBTrailers] %d trailers from %d movies (0 YT quota)", len(trailers), len(movies))

        if trailers:
            from genlab_core.cache.stable_ids import generate_story_id

            now_iso = datetime.now(UTC).isoformat()
            new_stories = []
            for t in trailers:
                sid = generate_story_id(t["url"], now_iso)
                new_stories.append({
                    "story_id": sid,
                    "title": t["title"],
                    "source": t["source"],
                    "source_url": t["url"],
                    "canonical_url": t["url"],
                    "published_at": t.get("release_date") or now_iso,
                    "fetched_at": now_iso,
                    "summary": "",
                    "video_id": t["video_id"],
                    "video_source": "tmdb_trailer",
                    "niche_id": niche_id,
                    "_trending_video": True,
                    "source_mention_count": 2,
                    "tmdb_id": t.get("tmdb_id"),
                })

            existing = context.get("stories", [])
            existing_urls = {s.get("source_url") for s in existing}
            new_stories = [s for s in new_stories if s["source_url"] not in existing_urls]
            context["stories"] = existing + new_stories

        run_stats = context.setdefault("run_stats", {})
        run_stats["tmdb_trailers_found"] = len(trailers)
        return context

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        return self.execute(context)
