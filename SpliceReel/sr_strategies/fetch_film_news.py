"""SpliceReel film news fetcher.

Tier 1: TMDB trending + upcoming + now_playing (primary, richest metadata)
Tier 2: Entertainment RSS feeds (Deadline, Variety, Hollywood Reporter)

OMDb: enrichment only — NOT called in bulk. Deferred to scoring stage.

All TMDB API field access uses .get() to guard against missing keys.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import feedparser
import httpx
import yaml

from .film_lifecycle import detect_lifecycle_stage

logger = logging.getLogger(__name__)

NICHE_ROOT = Path(__file__).resolve().parent.parent

# Franchise detection — film title keyword -> franchise name
FRANCHISE_KEYWORDS: dict[str, str] = {
    "avengers": "MCU", "marvel": "MCU", "iron man": "MCU", "thor": "MCU",
    "captain america": "MCU", "spider-man": "MCU", "black panther": "MCU",
    "guardians": "MCU", "ant-man": "MCU", "doctor strange": "MCU",
    "star wars": "Star_Wars", "jedi": "Star_Wars", "sith": "Star_Wars",
    "batman": "DC", "superman": "DC", "wonder woman": "DC", "aquaman": "DC",
    "fast": "Fast_Furious", "furious": "Fast_Furious",
    "toy story": "Pixar", "pixar": "Pixar", "inside out": "Pixar",
    "frozen": "Disney_Animation", "disney": "Disney_Animation",
    "halloween": "Horror_Franchise", "a quiet place": "Horror_Franchise",
    "scream": "Horror_Franchise", "conjuring": "Horror_Franchise",
}


def detect_franchise(title: str) -> str:
    """Return franchise name from film title, or empty string."""
    lower = title.lower()
    for keyword, franchise in FRANCHISE_KEYWORDS.items():
        if keyword in lower:
            return franchise
    return ""


@dataclass
class FilmStoryItem:
    """Normalized film story from any source."""

    title: str
    source: str
    source_url: str = ""
    published_at: str = ""
    fetched_at: str = ""
    summary: str = ""
    film_title: str = ""
    film_id: str = ""
    release_date: str = ""
    lifecycle_stage: str = "unknown"
    franchise: str = ""
    tmdb_popularity: float = 0.0
    tmdb_vote_average: float = 0.0
    rt_score: int | None = None
    imdb_score: float | None = None
    is_trailer_drop: bool = False
    is_box_office_news: bool = False
    is_award_news: bool = False
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TMDBFetcher:
    """Fetch from TMDB public API (requires TMDB_API_KEY)."""

    BASE = "https://api.themoviedb.org/3"

    def __init__(self, api_key: str, timeout: int = 10) -> None:
        self._api_key = api_key
        self._timeout = timeout

    def fetch_trending(self) -> list[FilmStoryItem]:
        try:
            return self._fetch_list("/trending/movie/week", "tmdb_trending", limit=15)
        except Exception:
            logger.exception("[tmdb] trending fetch failed")
            return []

    def fetch_now_playing(self) -> list[FilmStoryItem]:
        try:
            return self._fetch_list("/movie/now_playing", "tmdb_now_playing", limit=10)
        except Exception:
            logger.exception("[tmdb] now_playing fetch failed")
            return []

    def fetch_upcoming(self) -> list[FilmStoryItem]:
        try:
            items = self._fetch_list("/movie/upcoming", "tmdb_upcoming", limit=10)
            cutoff = (datetime.now(UTC) + timedelta(days=30)).isoformat()
            return [i for i in items if i.release_date <= cutoff or not i.release_date]
        except Exception:
            logger.exception("[tmdb] upcoming fetch failed")
            return []

    def _fetch_list(self, endpoint: str, source: str, limit: int) -> list[FilmStoryItem]:
        now_iso = datetime.now(UTC).isoformat()

        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(
                f"{self.BASE}{endpoint}",
                params={"api_key": self._api_key, "language": "en-US"},
            )
            resp.raise_for_status()
            data = resp.json()

        items: list[FilmStoryItem] = []
        for movie in data.get("results", [])[:limit]:
            film_id = str(movie.get("id", ""))
            title = movie.get("title", movie.get("name", ""))
            if not title:
                continue

            release_str = movie.get("release_date", "")
            release_dt: datetime | None = None
            if release_str:
                try:
                    release_dt = datetime.fromisoformat(release_str).replace(tzinfo=UTC)
                except ValueError:
                    pass

            lifecycle = detect_lifecycle_stage(release_dt)

            items.append(FilmStoryItem(
                title=title,
                source=source,
                source_url=f"https://www.themoviedb.org/movie/{film_id}",
                published_at=now_iso,
                fetched_at=now_iso,
                summary=movie.get("overview", ""),
                film_title=title,
                film_id=film_id,
                release_date=release_str,
                lifecycle_stage=lifecycle.value,
                franchise=detect_franchise(title),
                tmdb_popularity=float(movie.get("popularity", 0)),
                tmdb_vote_average=float(movie.get("vote_average", 0)),
            ))

        logger.info("[tmdb] %s: %d films", source, len(items))
        return items


class EntertainmentRSSFetcher:
    """Fetch from entertainment RSS feeds."""

    MAX_AGE_HOURS = 96

    def __init__(self, config: dict) -> None:
        self._tiers = {
            "tier_2": config.get("tier_2", {}),
            "tier_3": config.get("tier_3", {}),
        }

    def fetch_all(self) -> list[FilmStoryItem]:
        items: list[FilmStoryItem] = []
        now_iso = datetime.now(UTC).isoformat()

        for tier_name, tier_config in self._tiers.items():
            for source in tier_config.get("sources", []):
                if source.get("type") != "rss":
                    continue
                url = source.get("url", "")
                name = source.get("name", "")
                if not url:
                    continue
                try:
                    items.extend(self._fetch_feed(url, name, tier_name, now_iso))
                except Exception:
                    logger.exception("[rss] Failed to fetch %s", name)

        logger.info("[rss] Fetched %d items from entertainment feeds", len(items))
        return items

    @staticmethod
    def _fetch_feed(
        url: str, name: str, tier: str, now_iso: str,
    ) -> list[FilmStoryItem]:
        feed = feedparser.parse(url)
        items: list[FilmStoryItem] = []

        for entry in feed.get("entries", [])[:10]:
            title = entry.get("title", "")
            if not title:
                continue

            published = ""
            if hasattr(entry, "published"):
                published = entry.published

            title_lower = title.lower()
            is_trailer = any(w in title_lower for w in ["trailer", "teaser", "first look"])
            is_box_office = any(w in title_lower for w in ["box office", "opening", "million"])
            is_awards = any(w in title_lower for w in [
                "oscar", "golden globe", "bafta", "nominated", "nomination", "winner",
            ])

            story_id = "rss_" + hashlib.sha256(
                (name + title).encode()
            ).hexdigest()[:16]

            items.append(FilmStoryItem(
                title=title,
                source=f"rss_{name.lower().replace(' ', '_')}",
                source_url=entry.get("link", ""),
                published_at=published or now_iso,
                fetched_at=now_iso,
                summary=entry.get("summary", ""),
                film_title=title,
                franchise=detect_franchise(title),
                is_trailer_drop=is_trailer,
                is_box_office_news=is_box_office,
                is_award_news=is_awards,
                extra={"tier": tier},
            ))

        return items


def fetch_all_film_news(config: dict | None = None) -> list[dict[str, Any]]:
    """Entry point: fetch from TMDB + RSS, return normalized dicts.

    Sorted by lifecycle priority (opening_weekend first), then by TMDB popularity.
    """
    if config is None:
        with open(NICHE_ROOT / "config" / "sources.yaml") as f:
            config = yaml.safe_load(f) or {}

    api_key = os.environ.get("TMDB_API_KEY", "")
    all_items: list[FilmStoryItem] = []

    if api_key:
        tmdb = TMDBFetcher(api_key=api_key)
        all_items.extend(tmdb.fetch_trending())
        all_items.extend(tmdb.fetch_now_playing())
        all_items.extend(tmdb.fetch_upcoming())
    else:
        logger.warning("[fetch] TMDB_API_KEY not set — skipping TMDB fetch")

    rss = EntertainmentRSSFetcher(config)
    all_items.extend(rss.fetch_all())

    lifecycle_priority = {
        "opening_weekend": 0,
        "pre_release": 1,
        "long_tail": 2,
        "unknown": 3,
    }

    all_items.sort(key=lambda x: (
        lifecycle_priority.get(x.lifecycle_stage, 3),
        -x.tmdb_popularity,
    ))

    logger.info("[fetch] SpliceReel: %d total film stories", len(all_items))
    return [item.to_dict() for item in all_items]
