"""Trending video fetcher — finds trending YouTube clips per niche.

This is the heart of the video-first pipeline for non-BB channels.
The video IS the content. This module finds what's trending on YouTube
RIGHT NOW for each niche category, so channels can create reels
around actual video content that audiences are already watching.

Architecture:
    CriticalRush:   YouTube trending Gaming category (ID: 20) + keyword search
    ClutchWire:     YouTube trending Sports category (ID: 17) + search
    SpliceReel:     YouTube trending Film/Entertainment (ID: 1, 24) + trailers
    FrameDrift:     YouTube keyword search (anime has no native category)
    BB (ai_news):   YouTube search for creator/explainer clips about AI topics

Video selection criteria:
    - Published within the last 48 hours (recency)
    - View velocity: views/hours_since_published > threshold (viral potential)
    - Duration: 20s–4min (clips, not full episodes)
    - Channel verified or official (quality signal)

Usage:
    fetcher = TrendingVideoFetcher(api_key=os.environ["YOUTUBE_API_KEY"])
    videos = fetcher.fetch_trending("gaming", limit=10)
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
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# YouTube video category IDs
YOUTUBE_CATEGORIES: Dict[str, str] = {
    "gaming": "20",
    "sports": "17",
    "movies": "1",           # Film & Animation
    "entertainment": "24",   # Entertainment (backup for movies)
    "ai_creators": "28",     # Science & Technology (canonical)
    "ai_news": "28",         # backward compat alias
}

# Keyword sets for YouTube search per niche
NICHE_SEARCH_KEYWORDS: Dict[str, List[str]] = {
    "gaming": [
        "gaming highlights 2026",
        "viral gaming moment",
        "best gaming clip today",
        "esports highlight",
        "game clip trending",
    ],
    "sports": [
        "sports highlights today",
        "best sports moment 2026",
        "nba highlights today",
        "nfl play of the day",
        "soccer goal today",
        "viral sports clip",
    ],
    "movies": [
        "movie trailer 2026",
        "official trailer new",
        "film clip viral",
        "movie scene trending",
        "box office 2026",
    ],
    "anime": [
        "anime fight scene 2026",
        "anime clip viral",
        "anime moment trending",
        "anime episode reaction",
        "anime opening 2026",
    ],
    "ai_creators": [
        "AI demo 2026",
        "artificial intelligence explained",
        "AI tool tutorial",
        "LLM explained",
        "AI model released",
    ],
    "ai_news": [  # backward compat alias
        "AI demo 2026",
        "artificial intelligence explained",
        "AI tool tutorial",
        "LLM explained",
        "AI model released",
    ],
}

# View velocity thresholds — minimum views/hour to be considered "trending"
MIN_VIEW_VELOCITY: Dict[str, float] = {
    "gaming": 500,
    "sports": 800,
    "movies": 300,
    "anime": 400,
    "ai_creators": 150,
    "ai_news": 150,  # backward compat alias
}

MAX_DURATION_SECONDS = 240  # 4 minutes
MIN_DURATION_SECONDS = 20   # 20 seconds


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
        now = datetime.now(timezone.utc)
        pub = self.published_at
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
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

    def to_story(self) -> Dict[str, Any]:
        """Convert to a story dict compatible with the pipeline context.

        This is the key bridge: a TrendingVideo becomes a "story" that
        downstream stages (writing, hooks, render) can consume.
        """
        from genlab_core.cache.stable_ids import generate_story_id

        sid = generate_story_id(self.download_url, self.published_at.isoformat())
        now_iso = datetime.now(timezone.utc).isoformat()
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
            # Trending videos already have proven engagement
            "source_mention_count": 3,
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
        min_velocity: Optional[float] = None,
        extra_keywords: Optional[List[str]] = None,
    ) -> list[TrendingVideo]:
        """Fetch trending videos for a niche.

        Strategy:
          1. Fetch mostPopular chart for the niche's YouTube category
          2. Fetch keyword search results (last 48h, sorted by view count)
          3. Deduplicate by video_id
          4. Fetch full stats (views, duration) for all candidates
          5. Filter by duration, age, velocity
          6. Return top N sorted by view_velocity

        Args:
            niche_id: One of gaming, sports, movies, anime, ai_news
            limit: Maximum number of videos to return
            max_age_hours: Only include videos published within N hours
            min_velocity: Minimum views/hour (defaults to niche threshold)
            extra_keywords: Additional search keywords (e.g. from Google Trends)
        """
        if min_velocity is None:
            min_velocity = MIN_VIEW_VELOCITY.get(niche_id, 200)

        candidates: dict[str, TrendingVideo] = {}
        published_after = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

        # Strategy 1: Category trending chart
        category_id = YOUTUBE_CATEGORIES.get(niche_id)
        if category_id:
            chart_videos = self._fetch_most_popular(category_id, published_after)
            for v in chart_videos:
                v.niche_id = niche_id
                candidates[v.video_id] = v
            logger.info("[%s] Category chart: %d videos", niche_id, len(chart_videos))

        # Strategy 2: Keyword search (last 48h)
        keywords = list(NICHE_SEARCH_KEYWORDS.get(niche_id, []))
        if extra_keywords:
            keywords = list(extra_keywords[:3]) + keywords
        for keyword in keywords[:3]:  # Top 3 to manage quota (100 units each)
            search_videos = self._search_recent(
                query=keyword,
                niche_id=niche_id,
                published_after=published_after,
            )
            for v in search_videos:
                if v.video_id not in candidates:
                    candidates[v.video_id] = v
            logger.info("[%s] Keyword '%s': %d videos", niche_id, keyword, len(search_videos))
            time.sleep(0.2)  # Rate limit spacing

        if not candidates:
            logger.warning("[%s] No video candidates found", niche_id)
            return []

        # Fetch full stats for all candidates
        video_ids = list(candidates.keys())
        detailed = self._fetch_video_details(video_ids, niche_id)
        for v in detailed:
            candidates[v.video_id] = v

        # Filter and score
        results = []
        for video in candidates.values():
            if video.duration_seconds < MIN_DURATION_SECONDS:
                continue
            if video.duration_seconds > MAX_DURATION_SECONDS:
                continue
            if video.view_velocity < min_velocity:
                continue
            results.append(video)

        results.sort(key=lambda v: v.view_velocity, reverse=True)
        logger.info(
            "[%s] %d/%d passed filters (velocity≥%.0f, %d–%ds)",
            niche_id, len(results), len(candidates),
            min_velocity, MIN_DURATION_SECONDS, MAX_DURATION_SECONDS,
        )
        return results[:limit]

    def _fetch_most_popular(
        self, category_id: str, published_after: datetime,
    ) -> list[TrendingVideo]:
        """Fetch YouTube's most popular chart for a category."""
        try:
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
            items = resp.json().get("items", [])
            results = []
            for item in items:
                v = self._parse_video(item, "mostPopular")
                if v is not None and self._is_recent(item, published_after):
                    results.append(v)
            return results
        except Exception as e:
            logger.error("mostPopular fetch failed for category %s: %s", category_id, e)
            return []

    def _search_recent(
        self, query: str, niche_id: str, published_after: datetime,
    ) -> list[TrendingVideo]:
        """Search YouTube for recent videos matching a keyword."""
        try:
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
                    if pub_str else datetime.now(timezone.utc)
                )
                thumb = snippet.get("thumbnails", {})
                thumb_url = (
                    thumb.get("high", thumb.get("default", {})).get("url", "")
                )
                results.append(TrendingVideo(
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
                ))
            return results
        except Exception as e:
            logger.error("YouTube search failed for '%s': %s", query, e)
            return []

    def _fetch_video_details(
        self, video_ids: list[str], niche_id: str = "",
    ) -> list[TrendingVideo]:
        """Fetch full stats for a list of video IDs (1 unit per call)."""
        results = []
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i + 50]
            try:
                resp = self._session.get(
                    f"{self.BASE_URL}/videos",
                    params={
                        "key": self.api_key,
                        "part": "snippet,statistics,contentDetails,status",
                        "id": ",".join(batch),
                        "maxResults": 50,
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                for item in resp.json().get("items", []):
                    video = self._parse_video(item, "detail")
                    if video:
                        video.niche_id = niche_id
                        results.append(video)
            except Exception as e:
                logger.error("Video details fetch failed: %s", e)
        return results

    def _parse_video(self, item: dict, source: str) -> Optional[TrendingVideo]:
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
                if pub_str else datetime.now(timezone.utc)
            )

            view_count = int(stats.get("viewCount", 0))
            age_hours = max(0.1, (datetime.now(timezone.utc) - published_at).total_seconds() / 3600)
            view_velocity = view_count / age_hours

            channel_title = snippet.get("channelTitle", "")
            is_official = any(
                kw in channel_title.lower()
                for kw in [
                    "official", "nba", "nfl", "mlb", "nhl", "espn", "bleacher",
                    "ign", "gamespot", "twitch", "crunchyroll", "funimation",
                ]
            )

            thumb = snippet.get("thumbnails", {})
            thumb_url = (
                thumb.get("maxres", thumb.get("high", {})).get("url", "")
            )

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

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        niche_id = context.get("niche_id", "")
        config = context.get("niche_config", {})
        vs_config = config.get("video_sourcing", {})

        api_key = os.environ.get("YOUTUBE_API_KEY") or os.environ.get("YOUTUBE_DATA_API_KEY")
        if not api_key:
            logger.error("[FetchTrendingVideos] YOUTUBE_API_KEY not set")
            context.setdefault("run_stats", {})["trending_videos_found"] = 0
            return context

        top_n = vs_config.get("top_n_per_run", 5)
        max_age = vs_config.get("max_age_hours", 48)
        min_vel = vs_config.get("min_view_velocity")

        # Optionally enrich keywords with Google Trends
        extra_keywords: List[str] = []
        if vs_config.get("use_google_trends", False):
            try:
                from genlab_core.intel.google_trends import GoogleTrendsIntel
                intel = GoogleTrendsIntel()
                extra_keywords = intel.get_trending_topics(niche_id, top_n=5)
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
        )

        logger.info(
            "[FetchTrendingVideos] Found %d trending videos for %s",
            len(videos), niche_id,
        )

        # ── Composite quality gate ──────────────────────────────────
        # Score each video and filter out those below the niche threshold.
        # Only videos that pass the gate become stories for downstream stages.
        scoring_cfg = vs_config.get("composite_quality_gate", {})
        from genlab_core.scoring.composite_scorer import CompositeScorer
        scorer = CompositeScorer(
            niche_id,
            velocity_threshold=scoring_cfg.get("velocity_threshold"),
            min_composite=scoring_cfg.get("min_composite_score"),
        )

        # Build per-video trend multipliers from Google Trends (if available)
        trend_multipliers: Dict[str, float] = {}
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
            len(videos), len(video_dicts), niche_id,
        )
        # ── End quality gate ────────────────────────────────────────

        # Convert to story dicts and prepend to context stories
        video_stories = []
        for v in videos:
            story = v.to_story()
            story["composite_score"] = round(composite_map.get(v.video_id, 0.0), 4)
            video_stories.append(story)
        existing_stories = context.get("stories", [])
        context["stories"] = video_stories + existing_stories
        context["trending_videos"] = [v.to_dict() for v in videos]
        run_stats = context.setdefault("run_stats", {})
        run_stats["trending_videos_found"] = len(videos)
        run_stats["trending_videos_fetched"] = len(video_dicts)
        run_stats["trending_videos_filtered"] = len(video_dicts) - len(videos)
        if not videos and video_dicts:
            logger.warning(
                "[FetchTrendingVideos] All %d videos filtered by quality gate for %s — "
                "no content will be published this run",
                len(video_dicts), niche_id,
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

    def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Alias for execute()."""
        return self.execute(context)
