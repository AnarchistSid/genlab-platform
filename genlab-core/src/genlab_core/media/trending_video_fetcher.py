"""Trending video fetcher — finds trending YouTube clips per niche.

This is the heart of the video-first pipeline for non-BB channels.
The video IS the content. This module finds what's trending on YouTube
RIGHT NOW for each niche category, so channels can create reels
around actual video content that audiences are already watching.

Architecture (Sprint 64 — quota-optimized):
    3-tier strategy to minimize YouTube API quota usage:
      Tier 1: YouTube RSS feeds for subscribed channels (0 quota)
      Tier 2: playlistItems.list fallback when RSS fails (1 unit/call)
      Tier 3: videos.list for stat enrichment (1 unit per 50 videos)

    Category chart (mostPopular) is kept (1 unit) for niches with YT categories.
    search.list (100 units/call) is REMOVED from default path — only available
    behind an explicit ``allow_keyword_search: true`` config flag.

    Expected quota: ~20 units/day (down from ~1,500).

Video selection criteria:
    - Published within the last 48 hours (recency)
    - View velocity: views/hours_since_published > threshold (viral potential)
    - Duration: 20s–4min (clips, not full episodes)
    - Channel verified or official (quality signal)

Usage:
    fetcher = TrendingVideoFetcher(api_key=os.environ["YOUTUBE_API_KEY"])
    videos = fetcher.fetch_trending("gaming", limit=10, subscribed_channels=[...])
    for video in videos:
        print(video.title, video.view_velocity, video.download_url)

Pipeline stage usage (loaded via niche.yaml):
    pipeline:
      stages:
        - class: genlab_core.media.trending_video_fetcher.FetchTrendingVideos
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from genlab_core.config.tuning import get_tuning_config
from genlab_core.http.circuit_breaker import YOUTUBE_CB, CircuitOpenError

logger = logging.getLogger(__name__)


def _sanitize_exc(exc: Exception) -> str:
    """Redact API keys from exception messages (e.g. HTTPError URLs)."""
    msg = str(exc)
    # Strip key=... query parameter from URLs in error messages
    return re.sub(r"[?&]key=[^&\s]*", "?key=***", msg)


# Quota tracking — reset per pipeline run, logs total units consumed.
# Protected by _QUOTA_LOCK so concurrent niche pipelines don't corrupt counts.
QUOTA_TRACKER: dict[str, int] = {"units_used": 0, "rss_fetches": 0}
_QUOTA_LOCK = threading.Lock()


def _increment_quota(units: int, operation: str) -> None:
    """Track YouTube API quota consumption (thread-safe)."""
    with _QUOTA_LOCK:
        QUOTA_TRACKER["units_used"] += units
        total = QUOTA_TRACKER["units_used"]
    logger.debug("YouTube quota: +%d (%s), total=%d", units, operation, total)


def reset_quota_tracker() -> None:
    """Reset quota tracker — call at start of each pipeline run."""
    with _QUOTA_LOCK:
        QUOTA_TRACKER["units_used"] = 0
        QUOTA_TRACKER["rss_fetches"] = 0


# R-28: the per-process QUOTA_TRACKER above is log-only and resets each run, so
# "max N searches/day across the 5 channels" is unenforced — 5 processes each
# think they have the full 10k budget. The persistent, cross-process,
# atomically-locked YouTubeQuotaTracker (shared with the upload path) provides
# the real global daily ceiling. Lazily created and cached; on any failure we
# fall back to None so a quota-file problem never blocks fetching.
_PERSISTENT_QUOTA: Any = None
_PERSISTENT_QUOTA_FAILED = False


def _get_persistent_quota() -> Any:
    """Return the shared cross-process YouTube quota tracker, or None."""
    global _PERSISTENT_QUOTA, _PERSISTENT_QUOTA_FAILED
    if _PERSISTENT_QUOTA is not None or _PERSISTENT_QUOTA_FAILED:
        return _PERSISTENT_QUOTA
    try:
        from genlab_core.monitoring.youtube_quota import YouTubeQuotaTracker

        _PERSISTENT_QUOTA = YouTubeQuotaTracker()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Persistent YouTube quota tracker unavailable: %s", exc)
        _PERSISTENT_QUOTA_FAILED = True
    return _PERSISTENT_QUOTA


# R-37: cross-run disk cache for YouTube fetches (contra optimization.md, the
# fetcher re-queried YouTube every run). The mostPopular chart and keyword
# searches are stable over hours and any repeats are caught by video_id dedup,
# so a short TTL is safe and avoids spending quota/network on re-runs (retries,
# manual reruns within the window).
_FETCH_CACHE_TTL_HOURS = 6
_FETCH_CACHE: Any = None
_FETCH_CACHE_FAILED = False


def _get_fetch_cache() -> Any:
    """Return the shared on-disk fetch cache, or None on any failure."""
    global _FETCH_CACHE, _FETCH_CACHE_FAILED
    if _FETCH_CACHE is not None or _FETCH_CACHE_FAILED:
        return _FETCH_CACHE
    try:
        from genlab_core.cache.disk_cache import Cache

        _FETCH_CACHE = Cache()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Fetch disk cache unavailable: %s", exc)
        _FETCH_CACHE_FAILED = True
    return _FETCH_CACHE


def _video_to_cacheable(v: TrendingVideo) -> dict:
    """Serialize a TrendingVideo to a JSON-safe dict (datetime -> ISO string)."""
    d = asdict(v)
    d["published_at"] = v.published_at.isoformat()
    return d


def _video_from_cacheable(d: dict) -> TrendingVideo:
    """Rebuild a TrendingVideo from its cached dict (ISO string -> datetime)."""
    d = dict(d)
    d["published_at"] = datetime.fromisoformat(d["published_at"])
    return TrendingVideo(**d)


def _cached_videos(cache_key: str) -> list[TrendingVideo] | None:
    """Return cached videos for *cache_key* if fresh, else None."""
    cache = _get_fetch_cache()
    if cache is None:
        return None
    raw = cache.get(cache_key, ttl_hours=_FETCH_CACHE_TTL_HOURS)
    if not raw:
        return None
    try:
        return [_video_from_cacheable(d) for d in raw]
    except Exception as exc:  # corrupt/old-shape entry — ignore and refetch
        logger.debug("Ignoring unreadable fetch-cache entry %s: %s", cache_key, exc)
        return None


def _store_videos(cache_key: str, videos: list[TrendingVideo]) -> None:
    """Persist *videos* under *cache_key* (best-effort)."""
    cache = _get_fetch_cache()
    if cache is None or not videos:
        return
    try:
        cache.set(cache_key, [_video_to_cacheable(v) for v in videos])
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Fetch-cache store failed for %s: %s", cache_key, exc)


# YouTube video category IDs
YOUTUBE_CATEGORIES: dict[str, str] = {
    "gaming": "20",
    "sports": "17",
    "movies": "1",  # Film & Animation
    "entertainment": "24",  # Entertainment (backup for movies)
    "ai_creators": "28",  # Science & Technology
}

# Keyword sets for YouTube search per niche
# Uses "today"/"new"/"trending" instead of hardcoded years to stay evergreen
NICHE_SEARCH_KEYWORDS: dict[str, list[str]] = {
    "gaming": [
        "gaming highlights today",
        "viral gaming moment",
        "best gaming clip today",
        "esports highlight",
        "game clip trending",
    ],
    "sports": [
        "sports highlights today",
        "best sports moment today",
        "nba highlights today",
        "nfl play of the day",
        "soccer goal today",
        "viral sports clip",
    ],
    "movies": [
        "new movie trailer",
        "official trailer new",
        "film clip viral",
        "movie scene trending",
        "box office this week",
    ],
    "anime": [
        "anime trailer official",
        "anime fight scene new",
        "anime pv 2026",
        "anime clip viral",
        "new anime season announced",
        "anime opening full",
        "anime moment trending",
        "anime episode reaction",
    ],
    "ai_creators": [
        "AI demo new",
        "artificial intelligence explained",
        "AI tool tutorial",
        "LLM explained",
        "AI model released",
    ],
}

# View velocity thresholds — minimum views/hour to be considered "trending"
MIN_VIEW_VELOCITY: dict[str, float] = {
    "gaming": 200,
    "sports": 800,
    "movies": 300,
    "anime": 100,  # Lowered: official studio PVs gain traction slowly
    "ai_creators": 150,
}

# Baseline velocity assigned to UN-ENRICHED RSS-fallback videos (quota-exhaustion
# path; view_count=0, no verified traction). Kept low on purpose so a real
# measured-traction candidate always out-ranks them — they're a last resort, not
# a fast lane for official-account marketing. (Was an effective ~1800.)
_RSS_FALLBACK_BASE_VELOCITY: float = (
    get_tuning_config().trending_video_fetch.rss_fallback_base_velocity
)

MAX_DURATION_SECONDS = 600  # 10 minutes — yt-dlp trims to reel length at render
MIN_DURATION_SECONDS = 15  # 15 seconds


@dataclass
class TrendingVideo:
    """A trending YouTube video candidate for use in a reel."""

    video_id: str
    title: str
    channel_name: str
    channel_id: str
    published_at: datetime
    view_count: int
    like_count: int
    duration_seconds: int
    thumbnail_url: str
    niche_id: str
    search_query: str
    view_velocity: float
    download_url: str
    is_official_channel: bool
    license: str
    tags: list[str] = field(default_factory=list)
    description_snippet: str = ""

    @property
    def age_hours(self) -> float:
        now = datetime.now(UTC)
        pub = self.published_at
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=UTC)
        return max(0.1, (now - pub).total_seconds() / 3600)

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "channel_name": self.channel_name,
            "channel_id": self.channel_id,
            "published_at": self.published_at.isoformat(),
            "view_count": self.view_count,
            "like_count": self.like_count,
            "duration_seconds": self.duration_seconds,
            "view_velocity": round(self.view_velocity, 1),
            "download_url": self.download_url,
            "is_official_channel": self.is_official_channel,
            "license": self.license,
            "niche_id": self.niche_id,
            "search_query": self.search_query,
            "thumbnail_url": self.thumbnail_url,
            "tags": self.tags,
            "description_snippet": self.description_snippet,
        }

    def to_story(self) -> dict[str, Any]:
        """Convert to a story dict compatible with the pipeline context.

        This is the key bridge: a TrendingVideo becomes a "story" that
        downstream stages (writing, hooks, render) can consume.
        """
        from genlab_core.cache.stable_ids import generate_story_id

        sid = generate_story_id(self.download_url, self.published_at.isoformat())
        now_iso = datetime.now(UTC).isoformat()
        return {
            "story_id": sid,
            "title": self.title,
            "source": "youtube_trending",
            "source_url": self.download_url,
            "canonical_url": self.download_url,
            "published_date": self.published_at.isoformat(),
            "published_at": self.published_at.isoformat(),
            "fetched_at": now_iso,
            "summary": self.description_snippet,
            "channel_name": self.channel_name,
            "view_count": self.view_count,
            "view_velocity": self.view_velocity,
            "duration_seconds": self.duration_seconds,
            "thumbnail_url": self.thumbnail_url,
            "tags": self.tags,
            "niche_id": self.niche_id,
            "video_source": "trending",
            "video_id": self.video_id,
            "is_official_channel": self.is_official_channel,
            # Trending videos already have proven engagement — scale with velocity
            "source_mention_count": min(5, max(1, int(self.view_velocity / 500))),
            # Pre-filled clip info so DownloadTopVideos can skip re-sourcing
            "_trending_video": True,
        }


class TrendingVideoFetcher:
    """Fetches trending YouTube videos for each Gen Lab niche.

    Uses the YouTube Data API v3 with two strategies:
    1. mostPopular chart (category-based trending)
    2. search.list with keyword + recent date filter (niche-specific)

    Results are scored by view velocity and filtered by duration/quality.
    """

    BASE_URL = "https://www.googleapis.com/youtube/v3"

    def __init__(self, api_key: str, region_code: str = "US"):
        self.api_key = api_key
        self.region_code = region_code
        self._session = requests.Session()

    def fetch_trending(
        self,
        niche_id: str,
        limit: int = 10,
        max_age_hours: int = 48,
        min_velocity: float | None = None,
        extra_keywords: list[str] | None = None,
        subscribed_channels: list[dict[str, Any]] | None = None,
        allow_keyword_search: bool = False,
    ) -> list[TrendingVideo]:
        """Fetch trending videos for a niche (quota-optimized).

        Strategy (Sprint 64):
          1. Fetch mostPopular chart for the niche's YouTube category (1 unit)
          2. Fetch RSS feeds for subscribed channels (0 quota)
          3. Fall back to playlistItems.list if RSS returns < 3 items (1 unit)
          4. Fetch full stats for all candidates via videos.list (1 unit/50)
          5. Filter by duration, age, velocity
          6. Return top N sorted by view_velocity

        search.list (100 units/call) is ONLY used if ``allow_keyword_search``
        is True — it is NOT part of the default path.

        Args:
            niche_id: One of gaming, sports, movies, anime, ai_news
            limit: Maximum number of videos to return
            max_age_hours: Only include videos published within N hours
            min_velocity: Minimum views/hour (defaults to niche threshold)
            extra_keywords: Additional search keywords (e.g. from Google Trends)
            subscribed_channels: List of channel dicts from sources.yaml
                Each dict has at minimum: ``url`` (RSS URL with channel_id param)
                Optional: ``name``, ``weight``, ``category``
            allow_keyword_search: If True, use search.list as last resort (100 units)
        """
        if min_velocity is None:
            min_velocity = MIN_VIEW_VELOCITY.get(niche_id, 200)

        candidates: dict[str, TrendingVideo] = {}
        published_after = datetime.now(UTC) - timedelta(hours=max_age_hours)

        # Strategy 1: Category trending chart (1 unit)
        category_id = YOUTUBE_CATEGORIES.get(niche_id)
        if category_id:
            chart_videos = self._fetch_most_popular(category_id, published_after)
            for v in chart_videos:
                v.niche_id = niche_id
                candidates[v.video_id] = v
            logger.info("[%s] Category chart: %d videos (1 unit)", niche_id, len(chart_videos))

        # Strategy 2: Subscribed channel RSS feeds (0 quota)
        if subscribed_channels:
            channel_videos = self._fetch_from_channels(
                subscribed_channels,
                niche_id,
                published_after,
            )
            for v in channel_videos:
                if v.video_id not in candidates:
                    candidates[v.video_id] = v
            # NB: this used to say "(0 quota from RSS)" but that's only true
            # when every channel's RSS endpoint works.  See the RSS-coverage
            # line emitted by _fetch_from_channels for the real split.
            logger.info(
                "[%s] Subscribed channels: %d unique videos collected",
                niche_id,
                len(channel_videos),
            )

        # Strategy 3: External video IDs (from Jikan, TMDB, Twitch, etc.)
        # These are injected into context by Track B stages as ``external_video_ids``
        # and enriched here via videos.list (1 unit per 50).

        # Strategy 4 (fallback): Keyword search — ONLY if explicitly enabled
        # For niches without a YouTube category (anime), keyword search is
        # essential — RSS alone returns stale content that dedup rejects.
        _has_category = niche_id in YOUTUBE_CATEGORIES
        _needs_keyword_search = allow_keyword_search and (len(candidates) < 3 or not _has_category)
        if _needs_keyword_search:
            keywords = list(NICHE_SEARCH_KEYWORDS.get(niche_id, []))
            if extra_keywords:
                keywords = list(extra_keywords[:3]) + keywords
            # Niches without a category get 3 searches (more diversity needed);
            # niches with a category keep the original 2-search cap.
            _max_searches = 3 if not _has_category else 2
            for keyword in keywords[:_max_searches]:
                search_videos = self._search_recent(
                    query=keyword,
                    niche_id=niche_id,
                    published_after=published_after,
                )
                for v in search_videos:
                    if v.video_id not in candidates:
                        candidates[v.video_id] = v
                logger.info(
                    "[%s] Search '%s': %d videos (100 units)", niche_id, keyword, len(search_videos)
                )
                time.sleep(0.2)

        if not candidates:
            logger.warning("[%s] No video candidates found", niche_id)
            return []

        # Fetch full stats for all candidates (1 unit per batch of 50)
        video_ids = list(candidates.keys())
        detailed = self._fetch_video_details(video_ids, niche_id)
        for v in detailed:
            candidates[v.video_id] = v

        # Filter and score. Track per-reason rejection counts so the summary
        # log tells us WHY candidates were dropped, not just how many.
        results = []
        dropped_short = dropped_long = dropped_slow = 0
        for video in candidates.values():
            if video.duration_seconds < MIN_DURATION_SECONDS:
                dropped_short += 1
                continue
            if video.duration_seconds > MAX_DURATION_SECONDS:
                dropped_long += 1
                continue
            if video.view_velocity < min_velocity:
                dropped_slow += 1
                continue
            results.append(video)

        results.sort(key=lambda v: v.view_velocity, reverse=True)
        with _QUOTA_LOCK:
            quota_snapshot = QUOTA_TRACKER["units_used"]
        logger.info(
            "[%s] %d/%d passed filters (velocity≥%.0f, %d–%ds) "
            "[dropped: %d too-short, %d too-long, %d too-slow] | quota: %d units",
            niche_id,
            len(results),
            len(candidates),
            min_velocity,
            MIN_DURATION_SECONDS,
            MAX_DURATION_SECONDS,
            dropped_short,
            dropped_long,
            dropped_slow,
            quota_snapshot,
        )
        return results[:limit]

    def _fetch_most_popular(
        self,
        category_id: str,
        published_after: datetime,
    ) -> list[TrendingVideo]:
        """Fetch YouTube's most popular chart for a category (1 unit)."""
        # R-37: serve from the 6h disk cache on re-runs (0 quota, 0 network).
        cache_key = f"yt_mostpopular_{self.region_code}_{category_id}"
        cached = _cached_videos(cache_key)
        if cached is not None:
            logger.info(
                "[cache] mostPopular cat=%s hit (%d videos, 0 quota)",
                category_id,
                len(cached),
            )
            return cached
        try:

            def _do_request():
                resp = self._session.get(
                    f"{self.BASE_URL}/videos",
                    params={
                        "key": self.api_key,
                        "part": "snippet,statistics,contentDetails,status",
                        "chart": "mostPopular",
                        "regionCode": self.region_code,
                        "videoCategoryId": category_id,
                        "maxResults": 25,
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                return resp

            resp = YOUTUBE_CB.call(_do_request)
            _increment_quota(1, f"videos.list/mostPopular cat={category_id}")
            items = resp.json().get("items", [])
            results = []
            for item in items:
                v = self._parse_video(item, "mostPopular")
                if v is not None and self._is_recent(item, published_after):
                    results.append(v)
            _store_videos(cache_key, results)
            return results
        except CircuitOpenError:
            logger.warning(
                "YouTube circuit open — skipping mostPopular for category %s", category_id
            )
            return []
        except Exception as e:
            logger.error(
                "mostPopular fetch failed for category %s: %s", category_id, _sanitize_exc(e)
            )
            return []

    # ------------------------------------------------------------------
    # Tier 1: YouTube RSS feeds (0 quota)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_channel_id(rss_url: str) -> str | None:
        """Extract channel_id from a YouTube RSS feed URL."""
        try:
            parsed = urlparse(rss_url)
            params = parse_qs(parsed.query)
            ids = params.get("channel_id", [])
            return ids[0] if ids else None
        except Exception:
            return None

    def _fetch_channel_rss(
        self,
        rss_url: str,
        max_items: int = 15,
    ) -> list[dict]:
        """Parse a YouTube channel RSS feed — zero API quota.

        Returns lightweight dicts with video_id, title, published_at.
        These need enrichment via _fetch_video_details() to get stats.
        """
        ATOM_NS = "http://www.w3.org/2005/Atom"
        YT_NS = "http://www.youtube.com/xml/schemas/2015"
        MEDIA_NS = "http://search.yahoo.com/mrss/"
        try:
            req = urllib.request.Request(
                rss_url,
                headers={"User-Agent": "GenLab/1.0 (+https://genlab.ai)"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
            root = ET.fromstring(data)
            items = []
            for entry in root.findall(f"{{{ATOM_NS}}}entry")[:max_items]:
                vid_el = entry.find(f"{{{YT_NS}}}videoId")
                title_el = entry.find(f"{{{ATOM_NS}}}title")
                pub_el = entry.find(f"{{{ATOM_NS}}}published")
                author_el = entry.find(f"{{{ATOM_NS}}}author/{{{ATOM_NS}}}name")
                # media:group/media:description for snippet
                media_group = entry.find(f"{{{MEDIA_NS}}}group")
                desc_el = (
                    media_group.find(f"{{{MEDIA_NS}}}description")
                    if media_group is not None
                    else None
                )

                if vid_el is None or not vid_el.text:
                    continue
                vid_id = vid_el.text
                items.append(
                    {
                        "video_id": vid_id,
                        "title": title_el.text if title_el is not None else "",
                        "published_at": pub_el.text if pub_el is not None else None,
                        "channel_name": author_el.text if author_el is not None else "",
                        "description_snippet": (desc_el.text or "")[:200]
                        if desc_el is not None
                        else "",
                        "source": "youtube_rss",
                    }
                )
            with _QUOTA_LOCK:
                QUOTA_TRACKER["rss_fetches"] += 1
            return items
        except Exception as e:
            logger.warning("RSS fetch failed for %s: %s", rss_url, e)
            return []

    # ------------------------------------------------------------------
    # Tier 2: playlistItems.list fallback (1 unit per call)
    # ------------------------------------------------------------------

    def _fetch_playlist_items(
        self,
        channel_id: str,
        max_results: int = 10,
    ) -> list[dict]:
        """Fetch recent uploads via playlistItems.list (1 unit).

        Converts channel_id (UC...) to uploads playlist (UU...).
        """
        uploads_playlist = channel_id.replace("UC", "UU", 1)
        try:

            def _do_playlist():
                resp = self._session.get(
                    f"{self.BASE_URL}/playlistItems",
                    params={
                        "key": self.api_key,
                        "part": "snippet",
                        "playlistId": uploads_playlist,
                        "maxResults": max_results,
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                return resp

            resp = YOUTUBE_CB.call(_do_playlist)
            _increment_quota(1, f"playlistItems.list/{channel_id}")
            items = []
            for item in resp.json().get("items", []):
                snippet = item.get("snippet", {})
                vid_id = snippet.get("resourceId", {}).get("videoId")
                if not vid_id:
                    continue
                items.append(
                    {
                        "video_id": vid_id,
                        "title": snippet.get("title", ""),
                        "published_at": snippet.get("publishedAt"),
                        "channel_name": snippet.get("channelTitle", ""),
                        "channel_id": snippet.get("channelId", channel_id),
                        "description_snippet": snippet.get("description", "")[:200],
                        "source": "youtube_playlist",
                    }
                )
            return items
        except CircuitOpenError:
            logger.warning("YouTube circuit open — skipping playlistItems for %s", channel_id)
            return []
        except Exception as e:
            logger.warning("playlistItems fetch failed for %s: %s", channel_id, _sanitize_exc(e))
            return []

    # ------------------------------------------------------------------
    # Orchestrate: RSS → playlist fallback for subscribed channels
    # ------------------------------------------------------------------

    def _fetch_from_channels(
        self,
        channels: list[dict[str, Any]],
        niche_id: str,
        published_after: datetime,
    ) -> list[TrendingVideo]:
        """Fetch videos from subscribed YouTube channels (RSS-first).

        For each channel:
          1. Try RSS feed (0 quota) — returns up to 15 recent uploads
          2. If RSS returns < 3 items, fall back to playlistItems.list (1 unit)
        Then convert all results to TrendingVideo objects.
        """
        all_video_ids: list[str] = []
        video_metadata: dict[str, dict] = {}  # video_id → metadata from RSS/playlist
        # Track fallback usage so the "Subscribed channels: N videos (0 quota
        # from RSS)" summary doesn't mislead.  Many YouTube channels (notably
        # @anthropic-ai, @googledeepmind, @huggingface) selectively return 404
        # on the public RSS endpoint while the playlistItems API works fine.
        # Surfacing this delta tells us at-a-glance how much we're spending on
        # quota to rescue broken RSS.
        rss_success_count = 0
        rss_fail_count = 0
        playlist_fallback_count = 0
        playlist_rescued_count = 0  # channels where fallback recovered items

        for ch in channels:
            rss_url = ch.get("url", "")
            channel_id = self._extract_channel_id(rss_url)
            if not channel_id and not rss_url:
                continue

            # Tier 1: RSS (rate-limited to avoid throttling)
            items = []
            if rss_url:
                items = self._fetch_channel_rss(rss_url)
                if items:
                    rss_success_count += 1
                else:
                    rss_fail_count += 1
                logger.debug(
                    "[%s] RSS %s: %d items (0 quota)",
                    niche_id,
                    ch.get("name", channel_id or "?"),
                    len(items),
                )
                time.sleep(0.1)  # 100ms between RSS fetches to avoid throttling

            # Tier 2: playlistItems fallback
            if len(items) < 3 and channel_id:
                playlist_items = self._fetch_playlist_items(channel_id)
                playlist_fallback_count += 1
                if playlist_items:
                    playlist_rescued_count += 1
                # Merge — avoid duplicates
                existing_ids = {i["video_id"] for i in items}
                for pi in playlist_items:
                    if pi["video_id"] not in existing_ids:
                        items.append(pi)
                logger.debug(
                    "[%s] Playlist fallback %s: %d additional items (1 unit)",
                    niche_id,
                    ch.get("name", channel_id or "?"),
                    len(playlist_items),
                )

            # Filter by recency and collect
            for item in items:
                vid_id = item["video_id"]
                pub_str = item.get("published_at")
                if pub_str:
                    try:
                        pub = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                        if pub < published_after:
                            continue
                    except (ValueError, TypeError):
                        pass
                if vid_id not in video_metadata:
                    item["channel_weight"] = ch.get("weight", 0.5)
                    video_metadata[vid_id] = item
                    all_video_ids.append(vid_id)

        if not all_video_ids:
            return []

        # Enrich with full stats via videos.list (1 unit per 50)
        detailed = self._fetch_video_details(all_video_ids, niche_id)
        enriched_ids = {v.video_id for v in detailed}

        # Merge channel metadata into detailed videos
        for v in detailed:
            meta = video_metadata.get(v.video_id, {})
            if not v.channel_name and meta.get("channel_name"):
                v.channel_name = meta["channel_name"]
            v.search_query = meta.get("source", "channel_subscription")

        # Fallback: create stub TrendingVideos for RSS videos that
        # didn't get enriched (e.g. YouTube API quota exhausted).
        # These get a weight-based estimated velocity so they survive
        # the velocity filter — the actual video exists on YouTube and
        # yt-dlp can download it without any API quota.
        unenriched = [vid for vid in all_video_ids if vid not in enriched_ids]
        if unenriched:
            logger.info(
                "[%s] %d/%d RSS videos unenriched (API quota likely exhausted), "
                "creating stubs with estimated velocity",
                niche_id,
                len(unenriched),
                len(all_video_ids),
            )
            for vid_id in unenriched:
                meta = video_metadata.get(vid_id, {})
                pub_str = meta.get("published_at", "")
                try:
                    pub = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                except (ValueError, TypeError, AttributeError):
                    pub = datetime.now(UTC) - timedelta(hours=12)
                # Estimate velocity from channel weight: high-weight channels
                # (NBA, NFL, Premier League) get higher estimated velocity.
                # These are UN-ENRICHED subscribed-channel videos (view_count=0)
                # surfaced only when YouTube quota is exhausted — they have NO
                # verified traction. The previous 2000× fabricated ~1800 velocity,
                # which sailed past every gate and flooded the queue with official-
                # account marketing. Use a conservative baseline so they remain
                # available as a last resort but never out-rank a real-traction
                # candidate (which carries a measured view_velocity).
                channel_weight = meta.get("channel_weight", 0.5)
                estimated_velocity = _RSS_FALLBACK_BASE_VELOCITY * channel_weight
                detailed.append(
                    TrendingVideo(
                        video_id=vid_id,
                        title=meta.get("title", ""),
                        channel_name=meta.get("channel_name", ""),
                        channel_id="",
                        published_at=pub,
                        view_count=0,
                        like_count=0,
                        duration_seconds=120,  # Assume ~2min (within 20s-4min range)
                        thumbnail_url="",
                        niche_id=niche_id,
                        search_query="rss_fallback",
                        view_velocity=estimated_velocity,
                        download_url=f"https://www.youtube.com/watch?v={vid_id}",
                        is_official_channel=True,  # Subscribed channel = trusted
                        license="youtube",
                        description_snippet=meta.get("description_snippet", ""),
                    )
                )

        # Surface RSS-vs-fallback split so the operator can see when YouTube
        # RSS is 404-flaking and we're spending Data API quota to rescue.
        if rss_fail_count or playlist_fallback_count:
            logger.info(
                "[%s] RSS coverage: %d/%d channels succeeded, %d fell back to "
                "playlistItems (%d rescued, %d still empty) | quota used: %d units",
                niche_id,
                rss_success_count,
                rss_success_count + rss_fail_count,
                playlist_fallback_count,
                playlist_rescued_count,
                playlist_fallback_count - playlist_rescued_count,
                playlist_fallback_count,
            )

        return detailed

    def _search_recent(
        self,
        query: str,
        niche_id: str,
        published_after: datetime,
    ) -> list[TrendingVideo]:
        """Search YouTube for recent videos matching a keyword (100 units)."""
        # R-37: a fresh cache entry returns before we spend the 100 units (the
        # cache check sits ABOVE the quota gate so a hit costs nothing).
        cache_key = f"yt_search_{niche_id}_{query}"
        cached = _cached_videos(cache_key)
        if cached is not None:
            logger.info("[cache] search '%s' hit (%d videos, 0 quota)", query, len(cached))
            return cached
        # R-28: gate the 100-unit search against the shared cross-process daily
        # budget BEFORE spending it. Without this, 5 niche processes can each
        # blow through to 10k independently (e.g. on an RSS outage).
        _quota = _get_persistent_quota()
        if _quota is not None and not _quota.can_afford("search"):
            logger.warning(
                "[%s] YouTube daily quota near ceiling — skipping search.list for '%s' "
                "(global cross-process budget protects the 10k/day cap)",
                niche_id,
                query,
            )
            return []
        try:

            def _do_search():
                resp = self._session.get(
                    f"{self.BASE_URL}/search",
                    params={
                        "key": self.api_key,
                        "part": "snippet",
                        "q": query,
                        "type": "video",
                        "order": "viewCount",
                        "publishedAfter": published_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "videoDuration": "short",
                        "videoEmbeddable": "true",
                        "maxResults": 15,
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                return resp

            resp = YOUTUBE_CB.call(_do_search)
            _increment_quota(100, f"search.list q={query[:30]}")
            if _quota is not None:
                try:
                    _quota.record("search")  # persist against the global daily budget
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("Persistent quota record failed (non-fatal): %s", exc)
            items = resp.json().get("items", [])
            results = []
            for item in items:
                vid_id = item.get("id", {}).get("videoId")
                if not vid_id:
                    continue
                snippet = item.get("snippet", {})
                pub_str = snippet.get("publishedAt", "")
                published_at = (
                    datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                    if pub_str
                    else datetime.now(UTC)
                )
                thumb = snippet.get("thumbnails", {})
                thumb_url = thumb.get("high", thumb.get("default", {})).get("url", "")
                results.append(
                    TrendingVideo(
                        video_id=vid_id,
                        title=snippet.get("title", ""),
                        channel_name=snippet.get("channelTitle", ""),
                        channel_id=snippet.get("channelId", ""),
                        published_at=published_at,
                        view_count=0,
                        like_count=0,
                        duration_seconds=0,
                        thumbnail_url=thumb_url,
                        niche_id=niche_id,
                        search_query=query,
                        view_velocity=0.0,
                        download_url=f"https://www.youtube.com/watch?v={vid_id}",
                        is_official_channel=False,
                        license="youtube",
                        description_snippet=snippet.get("description", "")[:200],
                    )
                )
            _store_videos(cache_key, results)
            return results
        except CircuitOpenError:
            logger.warning("YouTube circuit open — skipping search for '%s'", query)
            return []
        except Exception as e:
            logger.error("YouTube search failed for '%s': %s", query, _sanitize_exc(e))
            return []

    def _fetch_video_details(
        self,
        video_ids: list[str],
        niche_id: str = "",
    ) -> list[TrendingVideo]:
        """Fetch full stats for a list of video IDs (1 unit per batch of 50)."""
        results = []
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i : i + 50]
            try:

                def _do_details(b=batch):
                    resp = self._session.get(
                        f"{self.BASE_URL}/videos",
                        params={
                            "key": self.api_key,
                            "part": "snippet,statistics,contentDetails,status",
                            "id": ",".join(b),
                            "maxResults": 50,
                        },
                        timeout=15,
                    )
                    resp.raise_for_status()
                    return resp

                resp = YOUTUBE_CB.call(_do_details)
                _increment_quota(1, f"videos.list batch={len(batch)}")
                for item in resp.json().get("items", []):
                    video = self._parse_video(item, "detail")
                    if video:
                        video.niche_id = niche_id
                        results.append(video)
            except CircuitOpenError:
                logger.warning("YouTube circuit open — skipping video details batch")
                break  # No point trying remaining batches
            except Exception as e:
                logger.error("Video details fetch failed: %s", _sanitize_exc(e))
        return results

    def _parse_video(self, item: dict, source: str) -> TrendingVideo | None:
        """Parse a YouTube API video item into a TrendingVideo."""
        try:
            vid_id = item["id"] if isinstance(item["id"], str) else item["id"].get("videoId", "")
            if not vid_id:
                return None

            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            content = item.get("contentDetails", {})

            duration_seconds = self._parse_duration(content.get("duration", "PT0S"))

            pub_str = snippet.get("publishedAt", "")
            published_at = (
                datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                if pub_str
                else datetime.now(UTC)
            )

            view_count = int(stats.get("viewCount", 0))
            age_hours = max(0.1, (datetime.now(UTC) - published_at).total_seconds() / 3600)
            view_velocity = view_count / age_hours

            channel_title = snippet.get("channelTitle", "")
            is_official = any(
                kw in channel_title.lower()
                for kw in [
                    "official",
                    "nba",
                    "nfl",
                    "mlb",
                    "nhl",
                    "espn",
                    "bleacher",
                    "ign",
                    "gamespot",
                    "twitch",
                    "crunchyroll",
                    "funimation",
                ]
            )

            thumb = snippet.get("thumbnails", {})
            thumb_url = thumb.get("maxres", thumb.get("high", {})).get("url", "")

            return TrendingVideo(
                video_id=vid_id,
                title=snippet.get("title", ""),
                channel_name=channel_title,
                channel_id=snippet.get("channelId", ""),
                published_at=published_at,
                view_count=view_count,
                like_count=int(stats.get("likeCount", 0)),
                duration_seconds=duration_seconds,
                thumbnail_url=thumb_url,
                niche_id="",
                search_query=source,
                view_velocity=view_velocity,
                download_url=f"https://www.youtube.com/watch?v={vid_id}",
                is_official_channel=is_official,
                license=content.get("license", "youtube"),
                tags=snippet.get("tags", [])[:10],
                description_snippet=snippet.get("description", "")[:200],
            )
        except Exception as e:
            logger.warning("Failed to parse video item: %s", e)
            return None

    def _is_recent(self, item: dict, published_after: datetime) -> bool:
        pub_str = item.get("snippet", {}).get("publishedAt", "")
        if not pub_str:
            return True
        try:
            pub = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
            return pub >= published_after
        except Exception:
            return True

    @staticmethod
    def _parse_duration(iso_duration: str) -> int:
        """Parse ISO 8601 duration to seconds. PT4M33S → 273."""
        match = re.match(
            r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?",
            iso_duration or "PT0S",
        )
        if not match:
            return 0
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        return hours * 3600 + minutes * 60 + seconds


# ---------------------------------------------------------------------------
# Pipeline stage class (loaded via niche.yaml)
# ---------------------------------------------------------------------------


class FetchTrendingVideos:
    """Pipeline stage: fetch trending YouTube videos as primary content source.

    This stage runs FIRST in the pipeline. It finds trending videos on YouTube
    for the current niche and converts them into story objects that downstream
    stages (writing, hooks, render) can consume.

    Loaded via niche.yaml::

        pipeline:
          stages:
            - class: genlab_core.media.trending_video_fetcher.FetchTrendingVideos

    Reads from context:
        - ``niche_id``: niche identifier
        - ``niche_config.video_sourcing``: video sourcing config
        - ``run_dir``: pipeline run directory

    Sets on context:
        - ``stories``: prepends trending video stories to existing stories
        - ``trending_videos``: raw TrendingVideo dicts for downstream use
        - ``run_stats.trending_videos_found``: count of videos found
    """

    @staticmethod
    def _read_from_content_pool(niche_id: str) -> list[dict]:
        """Read pre-routed stories from the shared content pool.

        Queries the ``content_pool`` table for stories routed to this niche,
        marks them as claimed, and returns them in the same dict format as
        existing story objects so downstream stages can consume them unchanged.
        """
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            logger.debug(
                "[FetchTrending:%s] DATABASE_URL not set — skipping content pool", niche_id
            )
            return []

        try:
            import psycopg
            from psycopg.rows import dict_row

            with psycopg.connect(database_url, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT * FROM content_pool
                        WHERE %s = ANY(routed_niches)
                          AND status = 'available'
                          AND video_url IS NOT NULL AND video_url != ''
                        ORDER BY view_velocity DESC NULLS LAST, fetched_at DESC
                        LIMIT 20
                        FOR UPDATE SKIP LOCKED
                        """,
                        (niche_id,),
                    )
                    rows = cur.fetchall()

                    if not rows:
                        return []

                    # Mark rows as claimed by this niche
                    row_ids = [r["id"] for r in rows]
                    cur.execute(
                        """
                        UPDATE content_pool
                        SET status = 'claimed',
                            claimed_by = %s,
                            claimed_at = NOW()
                        WHERE id = ANY(%s)
                        """,
                        (niche_id, row_ids),
                    )
                    conn.commit()

                    # Convert to story dict format
                    stories = []
                    for row in rows:
                        stories.append(
                            {
                                "title": row["title"],
                                "source_url": row["source_url"],
                                "video_url": row["video_url"] or "",
                                "video_id": row["video_id"] or "",
                                "description": row["summary"] or "",
                                "published_at": row["published_at"],
                                "duration_seconds": row["duration_seconds"] or 0,
                                "view_count": row["view_count"] or 0,
                                "view_velocity": row["view_velocity"] or 0,
                                "source": row["source_platform"] or "shared_pool",
                                "source_type": row["source_platform"] or "shared_pool",
                                "thumbnail_url": row["thumbnail_url"] or "",
                                "_pool_id": str(row["id"]),
                            }
                        )

                    logger.info(
                        "[FetchTrending:%s] Claimed %d stories from content pool",
                        niche_id,
                        len(stories),
                    )
                    return stories

        except Exception as e:
            logger.warning(
                "[FetchTrending:%s] Content pool read failed: %s",
                niche_id,
                e,
            )
            return []

    @staticmethod
    def _load_sources_config(niche_id: str) -> dict:
        """Load sources.yaml for the given niche."""
        from genlab_core.settings import _PROJECT_ROOT

        sources_paths: dict[str, Path] = {
            "gaming": _PROJECT_ROOT
            / "CriticalRush"
            / "niches"
            / "gaming"
            / "config"
            / "sources.yaml",
            "sports": _PROJECT_ROOT / "ClutchWire" / "config" / "sources.yaml",
            "movies": _PROJECT_ROOT / "SpliceReel" / "config" / "sources.yaml",
            "anime": _PROJECT_ROOT / "FrameDrift" / "config" / "sources.yaml",
            "ai_creators": _PROJECT_ROOT / "BlackboxBrief" / "config" / "sources.yaml",
        }
        path = sources_paths.get(niche_id)
        if path and path.is_file():
            try:
                import yaml

                with open(path) as f:
                    return yaml.safe_load(f) or {}
            except Exception as e:
                logger.warning("[FetchTrendingVideos] Failed to load %s: %s", path, e)
        return {}

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        niche_id = context.get("niche_id", "")

        # Read pre-routed stories from shared content pool
        pool_stories = self._read_from_content_pool(niche_id)

        config = context.get("niche_config", {})
        vs_config = config.get("video_sourcing", {})

        api_key = os.environ.get("YOUTUBE_API_KEY") or os.environ.get("YOUTUBE_DATA_API_KEY")
        if not api_key:
            logger.error("[FetchTrendingVideos] YOUTUBE_API_KEY not set")
            context.setdefault("run_stats", {})["trending_videos_found"] = 0
            return context

        # Reset quota tracker for this run
        reset_quota_tracker()

        top_n = vs_config.get("top_n_per_run", 5)
        max_age = vs_config.get("max_age_hours", 48)
        min_vel = vs_config.get("min_view_velocity")

        # Load subscribed YouTube channels from sources.yaml
        sources_config = context.get("sources_config") or self._load_sources_config(niche_id)
        subscribed_channels = sources_config.get("youtube_channels", [])
        if subscribed_channels:
            logger.info(
                "[FetchTrendingVideos] %d subscribed channels for %s",
                len(subscribed_channels),
                niche_id,
            )

        # ── Relevance-filter content pool items ──────────────────
        # Pool items were routed by NicheClassifier but may be weak
        # matches. Apply the same per-niche relevance filter used on
        # YouTube videos before letting them into the story pipeline.
        if pool_stories:
            content_filter_config = sources_config.get("content_filter", {})
            if content_filter_config:
                from genlab_core.media.relevance_filter import RelevanceFilter

                rf = RelevanceFilter(niche_id, content_filter_config)
                pre = len(pool_stories)
                pool_stories = [
                    s
                    for s in pool_stories
                    if rf.score(s.get("title", ""), s.get("description", "")) >= rf.threshold
                ]
                rejected = pre - len(pool_stories)
                if rejected:
                    logger.info(
                        "[FetchTrending:%s] Pool relevance filter: rejected=%d kept=%d",
                        niche_id,
                        rejected,
                        len(pool_stories),
                    )

            # Also reject stories with negligible title/description (spam, one-word posts)
            pool_stories = [
                s
                for s in pool_stories
                if len(s.get("title", "")) >= 10 or len(s.get("description", "")) >= 20
            ]

            if pool_stories:
                logger.info(
                    "[FetchTrending:%s] %d pool stories passed relevance filter",
                    niche_id,
                    len(pool_stories),
                )
                stories = context.get("stories", [])
                stories.extend(pool_stories)
                context["stories"] = stories

        # Fix #4 of the autonomy roadmap: gap-fill mode for the direct
        # YouTube fetch. Pre-fix, the direct fetch fired every run in
        # parallel with content_pool consumption — producing duplicates,
        # cross-niche leakage (the ai_creators/CGI/movies incident of
        # 2026-06-12), and 77% of blueprints bypassing the routed pool.
        # Now: if content_pool already filled the top_n quota AND the
        # operator hasn't asked for multi-publish, skip the direct fetch.
        # ``trending_videos_force_direct_fetch=true`` in vs_config preserves
        # the old behaviour as an escape hatch.
        #
        # The dedup happens at story level (by video_id) further down in
        # the merge — see the video_dedup logic that compares video IDs
        # against existing context["stories"] before adding new ones.
        force_direct = vs_config.get("trending_videos_force_direct_fetch", False)
        pool_satisfies_quota = bool(pool_stories) and len(pool_stories) >= top_n
        if pool_satisfies_quota and not force_direct:
            logger.info(
                "[FetchTrending:%s] content_pool returned %d≥%d stories; "
                "skipping direct YouTube fetch (set "
                "video_sourcing.trending_videos_force_direct_fetch=true to override)",
                niche_id,
                len(pool_stories),
                top_n,
            )
            run_stats = context.setdefault("run_stats", {})
            run_stats["trending_videos_found"] = 0
            run_stats["trending_videos_source"] = "content_pool_only"
            return context

        if pool_stories:
            logger.info(
                "[FetchTrending:%s] content_pool returned %d stories (need %d); "
                "running direct YouTube fetch to gap-fill",
                niche_id,
                len(pool_stories),
                top_n,
            )

        # Optionally enrich keywords with Google Trends
        extra_keywords: list[str] = []
        if vs_config.get("use_google_trends", False):
            try:
                from genlab_core.intel.google_trends import GoogleTrendsIntel

                intel = GoogleTrendsIntel()
                extra_keywords = intel.get_trending_topics(niche_id, top_n=5)
                extra_keywords = [kw for kw in extra_keywords if kw and kw.strip()]
                logger.info("[FetchTrendingVideos] Trends keywords: %s", extra_keywords[:3])
            except Exception as e:
                logger.warning("[FetchTrendingVideos] Google Trends failed: %s", e)

        fetcher = TrendingVideoFetcher(api_key=api_key)
        videos = fetcher.fetch_trending(
            niche_id=niche_id,
            limit=top_n,
            max_age_hours=max_age,
            min_velocity=min_vel,
            extra_keywords=extra_keywords or None,
            subscribed_channels=subscribed_channels or None,
            allow_keyword_search=vs_config.get("allow_keyword_search", False),
        )

        logger.info(
            "[FetchTrendingVideos] Found %d trending videos for %s",
            len(videos),
            niche_id,
        )

        # ── Content relevance filter ─────────────────────────────────
        # Reject off-niche content using per-niche keyword lists from
        # sources.yaml ``content_filter``. Runs before composite quality
        # gate so obviously irrelevant videos never consume scoring quota.
        content_filter_config = sources_config.get("content_filter", {})
        if content_filter_config:
            from genlab_core.media.relevance_filter import RelevanceFilter

            rf = RelevanceFilter(niche_id, content_filter_config)
            pre_count = len(videos)
            videos = [v for v in videos if rf.score(v.title, v.description_snippet) >= rf.threshold]
            rejected_count = pre_count - len(videos)
            if rejected_count:
                logger.info(
                    "[FetchTrendingVideos] Relevance filter: rejected=%d kept=%d for %s",
                    rejected_count,
                    len(videos),
                    niche_id,
                )
        # ── End relevance filter ──────────────────────────────────────

        # ── Composite quality gate ──────────────────────────────────
        # Score each video and filter out those below the niche threshold.
        # Only videos that pass the gate become stories for downstream stages.
        scoring_cfg = vs_config.get("composite_quality_gate", {})
        from genlab_core.scoring.composite_scorer import CompositeScorer

        scorer = CompositeScorer(
            niche_id,
            velocity_threshold=scoring_cfg.get("velocity_threshold"),
            min_composite=scoring_cfg.get("min_composite_score"),
            min_view_count=scoring_cfg.get("min_view_count"),
        )

        # Build per-video trend multipliers from Google Trends (if available)
        trend_multipliers: dict[str, float] = {}
        if extra_keywords and vs_config.get("use_google_trends", False):
            try:
                from genlab_core.intel.google_trends import GoogleTrendsIntel

                trends_intel = GoogleTrendsIntel()
                for v in videos:
                    mult = trends_intel.get_trending_score_multiplier(v.title, niche_id)
                    if mult != 1.0:
                        trend_multipliers[v.video_id] = mult
            except Exception as e:
                logger.warning("[FetchTrendingVideos] Trend multiplier lookup failed: %s", e)

        video_dicts = [v.to_dict() for v in videos]
        scored = scorer.score_and_rank(video_dicts, trend_multipliers=trend_multipliers)
        passed_ids = {s.video_id for s in scored}
        videos = [v for v in videos if v.video_id in passed_ids]

        # Attach composite_score to each video for downstream use
        composite_map = {s.video_id: s.composite for s in scored}

        logger.info(
            "[FetchTrendingVideos] Quality gate: %d/%d passed for %s",
            len(videos),
            len(video_dicts),
            niche_id,
        )
        # ── End quality gate ────────────────────────────────────────

        # Convert to story dicts and prepend to context stories
        video_stories = []
        for v in videos:
            story = v.to_story()
            story["composite_score"] = round(composite_map.get(v.video_id, 0.0), 4)
            video_stories.append(story)
        existing_stories = context.get("stories", [])

        # Cross-source video_id dedup — fix #4. Without this, the same
        # YouTube video can land in both pool_stories AND video_stories
        # (pool ingested it from a different source path; direct fetch
        # found it in trending). Pre-fix this multiplied the dedup work
        # downstream and accounted for the 90.9% expired-pool-rate
        # (everything got expired because direct fetch ate it first).
        existing_video_ids = {
            s.get("video_id") or s.get("source_url") or ""
            for s in existing_stories
            if (s.get("video_id") or s.get("source_url"))
        }
        deduped_video_stories = [
            s
            for s in video_stories
            if (s.get("video_id") or s.get("source_url") or "") not in existing_video_ids
        ]
        cross_source_dupes = len(video_stories) - len(deduped_video_stories)
        if cross_source_dupes:
            logger.info(
                "[FetchTrending:%s] %d direct-fetch videos were already in "
                "content_pool stories — dedup'd at source-merge step",
                niche_id,
                cross_source_dupes,
            )
        context["stories"] = deduped_video_stories + existing_stories
        context["trending_videos"] = [v.to_dict() for v in videos]
        run_stats = context.setdefault("run_stats", {})
        run_stats["trending_videos_found"] = len(videos)
        run_stats["trending_videos_fetched"] = len(video_dicts)
        run_stats["trending_videos_filtered"] = len(video_dicts) - len(videos)
        with _QUOTA_LOCK:
            run_stats["youtube_quota_units"] = QUOTA_TRACKER["units_used"]
            run_stats["youtube_rss_fetches"] = QUOTA_TRACKER["rss_fetches"]
        if not videos and video_dicts:
            logger.warning(
                "[FetchTrendingVideos] All %d videos filtered by quality gate for %s — "
                "no content will be published this run",
                len(video_dicts),
                niche_id,
            )

        # Save trending videos manifest to run_dir
        run_dir = context.get("run_dir")
        if run_dir:
            manifest_path = Path(run_dir) / "trending_videos.json"
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(manifest_path, "w") as f:
                json.dump(context["trending_videos"], f, indent=2)
            logger.info("[FetchTrendingVideos] Wrote %s", manifest_path)

        return context

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        """Alias for execute()."""
        return self.execute(context)
