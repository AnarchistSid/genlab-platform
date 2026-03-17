"""Shared video sourcing engine with 4-level fallback chain.

Searches for real source videos for stories across YouTube, Reddit,
TMDB trailers, and direct URL detection.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Niche → subreddit mapping for Reddit search
# ---------------------------------------------------------------------------
NICHE_SUBREDDITS: dict[str, list[str]] = {
    "ai_creators": ["artificial", "MachineLearning", "LocalLLaMA", "singularity"],
    "gaming": ["gaming", "pcgaming", "Games", "GameDeals"],
    "sports": ["sports", "nba", "soccer", "nfl"],
    "movies": ["movies", "MovieClips", "trailers", "boxoffice"],
    "anime": ["anime", "animeclips", "AnimeReccomendations", "Crunchyroll"],
}

# ---------------------------------------------------------------------------
# Direct-URL regex patterns
# ---------------------------------------------------------------------------
_DIRECT_VIDEO_PATTERNS: list[re.Pattern[str]] = [
    # YouTube
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/watch\?v=[\w-]+"),
    re.compile(r"(?:https?://)?youtu\.be/[\w-]+"),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/shorts/[\w-]+"),
    # Reddit
    re.compile(r"(?:https?://)?v\.redd\.it/[\w-]+"),
    re.compile(r"(?:https?://)?(?:www\.)?reddit\.com/r/\w+/comments/\w+"),
    # Vimeo
    re.compile(r"(?:https?://)?(?:www\.)?vimeo\.com/\d+"),
    # TikTok
    re.compile(r"(?:https?://)?(?:www\.)?tiktok\.com/@[\w.]+/video/\d+"),
    re.compile(r"(?:https?://)?vm\.tiktok\.com/[\w-]+"),
    # Twitter / X
    re.compile(r"(?:https?://)?(?:www\.)?(?:twitter|x)\.com/\w+/status/\d+"),
    # Twitch clips (page URL — yt-dlp handles download natively)
    re.compile(r"(?:https?://)?clips\.twitch\.tv/[\w-]+"),
    re.compile(r"(?:https?://)?(?:www\.)?twitch\.tv/\w+/clip/[\w-]+"),
    re.compile(r"(?:https?://)?clips-media-assets\w*\.twitch\.tv/.+\.mp4"),
    # Direct MP4/WEBM URLs (Twitch CDN, Steam CDN, etc.)
    re.compile(r"https?://[^\s\"']+\.(?:mp4|webm)(?:\?[^\s\"']*)?$"),
    # Streamable
    re.compile(r"(?:https?://)?(?:www\.)?streamable\.com/[\w-]+"),
]


def is_direct_video_url(url: str) -> bool:
    """Return True if *url* points to a known video platform."""
    if not url:
        return False
    return any(p.search(url) for p in _DIRECT_VIDEO_PATTERNS)


# ---------------------------------------------------------------------------
# ISO 8601 duration parser (YouTube style: PT1H2M3S)
# ---------------------------------------------------------------------------
_ISO_DUR_RE = re.compile(
    r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", re.IGNORECASE
)


def parse_iso_duration(raw: str) -> float:
    """Parse ISO 8601 duration like ``PT1H2M3S`` into total seconds."""
    m = _ISO_DUR_RE.match(raw.strip())
    if not m:
        return 0.0
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    seconds = int(m.group(3) or 0)
    return float(hours * 3600 + minutes * 60 + seconds)


# ---------------------------------------------------------------------------
# VideoSearchResult
# ---------------------------------------------------------------------------
@dataclass
class VideoSearchResult:
    """A single video search result from any backend."""

    url: str
    title: str = ""
    duration_seconds: float = 0.0
    view_count: int = 0
    like_count: int = 0
    published_at: datetime | None = None
    channel_name: str = ""
    thumbnail_url: str = ""
    backend: str = ""


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
_WEIGHT_RELEVANCE = 0.40
_WEIGHT_FRESHNESS = 0.25
_WEIGHT_QUALITY = 0.20
_WEIGHT_DURATION = 0.15

_FRESHNESS_HALF_LIFE_H = 48.0
_DURATION_MU = 60.0  # ideal duration in seconds
_DURATION_SIGMA = 45.0


def _relevance_score(story_title: str, video_title: str) -> float:
    """TF-IDF cosine similarity between *story_title* and *video_title*."""
    if not story_title or not video_title:
        return 0.0
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError:
        logger.debug("sklearn not available; returning 0 relevance")
        return 0.0

    vec = TfidfVectorizer(stop_words="english")
    try:
        tfidf = vec.fit_transform([story_title, video_title])
    except ValueError:
        return 0.0
    sim = cosine_similarity(tfidf[0:1], tfidf[1:2])[0, 0]
    return float(max(0.0, min(1.0, sim)))


def _freshness_score(
    video_published: datetime | None, story_published: datetime | None
) -> float:
    """Exponential decay with 48-hour half-life relative to story publish."""
    if video_published is None:
        return 0.5  # unknown → neutral
    ref = story_published or datetime.now(tz=timezone.utc)
    if video_published.tzinfo is None:
        video_published = video_published.replace(tzinfo=timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    delta_h = abs((ref - video_published).total_seconds()) / 3600.0
    return math.exp(-math.log(2) * delta_h / _FRESHNESS_HALF_LIFE_H)


def _quality_score(view_count: int) -> float:
    """Log-scaled view count, saturating at ~10M views."""
    if view_count <= 0:
        return 0.0
    # log10(10M) ≈ 7; normalise to [0, 1]
    return min(1.0, math.log10(view_count + 1) / 7.0)


def _duration_fit(duration_seconds: float) -> float:
    """Gaussian centred on 60 s, sigma 45 s."""
    if duration_seconds <= 0:
        return 0.5  # unknown → neutral
    return math.exp(
        -0.5 * ((duration_seconds - _DURATION_MU) / _DURATION_SIGMA) ** 2
    )


def score_video_result(
    result: VideoSearchResult,
    story_title: str,
    story_published_at: datetime | None = None,
) -> float:
    """Return a composite score in [0, 1] for *result*."""
    rel = _relevance_score(story_title, result.title)
    fresh = _freshness_score(result.published_at, story_published_at)
    qual = _quality_score(result.view_count)
    dur = _duration_fit(result.duration_seconds)
    return (
        _WEIGHT_RELEVANCE * rel
        + _WEIGHT_FRESHNESS * fresh
        + _WEIGHT_QUALITY * qual
        + _WEIGHT_DURATION * dur
    )


# ---------------------------------------------------------------------------
# VideoSourcer
# ---------------------------------------------------------------------------
@dataclass
class _Stats:
    """Per-backend hit counts."""

    direct_url: int = 0
    youtube: int = 0
    reddit: int = 0
    tmdb: int = 0
    none: int = 0


class VideoSourcer:
    """Multi-backend video search with fallback chain.

    Fallback levels:
        1. Direct video URL in story
        2. YouTube Data API v3 search
        3. Reddit JSON search (niche subreddits)
        4. TMDB trailer lookup (movies niche only)
        5. Return None
    """

    def __init__(
        self,
        niche_id: str,
        niche_keywords: list[str] | None = None,
        youtube_api_key: str | None = None,
        tmdb_api_key: str | None = None,
        max_results_per_backend: int = 5,
        min_score: float = 0.3,
    ) -> None:
        self.niche_id = niche_id
        self.niche_keywords = niche_keywords or []
        self.youtube_api_key = youtube_api_key or os.environ.get(
            "YOUTUBE_API_KEY", ""
        )
        self.tmdb_api_key = tmdb_api_key or os.environ.get("TMDB_API_KEY", "")
        self.max_results = max_results_per_backend
        self.min_score = min_score
        self.stats = _Stats()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def find_video_for_story(
        self, story: dict[str, Any]
    ) -> VideoSearchResult | None:
        """Run the fallback chain and return the best-scoring result."""
        story_title: str = story.get("title", "")
        story_published_at = story.get("published_at")
        if isinstance(story_published_at, str):
            try:
                story_published_at = datetime.fromisoformat(story_published_at)
            except (ValueError, TypeError):
                story_published_at = None

        # Level 1 — direct URL
        result = self._try_direct_url(story)
        if result is not None:
            self.stats.direct_url += 1
            return result

        # Level 2 — YouTube search
        results = self._search_youtube(story_title)
        best = self._pick_best(results, story_title, story_published_at)
        if best is not None:
            self.stats.youtube += 1
            return best

        # Level 3 — Reddit search
        results = self._search_reddit(story_title)
        best = self._pick_best(results, story_title, story_published_at)
        if best is not None:
            self.stats.reddit += 1
            return best

        # Level 4 — TMDB trailers (movies niche only)
        if self.niche_id == "movies":
            results = self._search_tmdb(story_title)
            best = self._pick_best(results, story_title, story_published_at)
            if best is not None:
                self.stats.tmdb += 1
                return best

        # Level 5 — nothing found
        self.stats.none += 1
        return None

    def get_stats(self) -> dict[str, int]:
        """Return per-backend usage counts."""
        return {
            "direct_url": self.stats.direct_url,
            "youtube": self.stats.youtube,
            "reddit": self.stats.reddit,
            "tmdb": self.stats.tmdb,
            "none": self.stats.none,
        }

    # ------------------------------------------------------------------
    # Level 1 — Direct URL
    # ------------------------------------------------------------------
    def _try_direct_url(self, story: dict[str, Any]) -> VideoSearchResult | None:
        """Check story fields for a direct video platform URL."""
        for key in ("_clip_url", "video_url", "canonical_url", "url", "source_url", "link"):
            url = story.get(key, "")
            if url and is_direct_video_url(url):
                return VideoSearchResult(
                    url=url,
                    title=story.get("title", ""),
                    backend="direct_url",
                )
        return None

    # ------------------------------------------------------------------
    # Level 2 — YouTube Data API v3
    # ------------------------------------------------------------------
    def _search_youtube(self, query: str) -> list[VideoSearchResult]:
        if not self.youtube_api_key or not query:
            return []
        try:
            from googleapiclient.discovery import build as yt_build
        except ImportError:
            logger.debug("googleapiclient not installed; skipping YouTube search")
            return []

        try:
            youtube = yt_build(
                "youtube", "v3", developerKey=self.youtube_api_key
            )

            # Step 1: search.list
            search_resp = (
                youtube.search()
                .list(
                    q=query,
                    part="snippet",
                    type="video",
                    maxResults=self.max_results,
                    order="relevance",
                    videoEmbeddable="true",
                )
                .execute()
            )

            video_ids = [
                item["id"]["videoId"]
                for item in search_resp.get("items", [])
                if "videoId" in item.get("id", {})
            ]
            if not video_ids:
                return []

            # Step 2: videos.list for details
            details_resp = (
                youtube.videos()
                .list(
                    id=",".join(video_ids),
                    part="snippet,contentDetails,statistics",
                )
                .execute()
            )

            results: list[VideoSearchResult] = []
            for item in details_resp.get("items", []):
                snippet = item.get("snippet", {})
                stats = item.get("statistics", {})
                content = item.get("contentDetails", {})
                pub = None
                if snippet.get("publishedAt"):
                    try:
                        pub = datetime.fromisoformat(
                            snippet["publishedAt"].replace("Z", "+00:00")
                        )
                    except (ValueError, TypeError):
                        pass
                results.append(
                    VideoSearchResult(
                        url=f"https://www.youtube.com/watch?v={item['id']}",
                        title=snippet.get("title", ""),
                        duration_seconds=parse_iso_duration(
                            content.get("duration", "")
                        ),
                        view_count=int(stats.get("viewCount", 0)),
                        like_count=int(stats.get("likeCount", 0)),
                        published_at=pub,
                        channel_name=snippet.get("channelTitle", ""),
                        thumbnail_url=snippet.get("thumbnails", {})
                        .get("high", {})
                        .get("url", ""),
                        backend="youtube",
                    )
                )
            return results
        except Exception:
            logger.warning("YouTube search failed", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Level 3 — Reddit JSON search
    # ------------------------------------------------------------------
    def _search_reddit(self, query: str) -> list[VideoSearchResult]:
        if not query:
            return []
        subreddits = NICHE_SUBREDDITS.get(self.niche_id, ["videos"])
        results: list[VideoSearchResult] = []

        for sub in subreddits:
            if len(results) >= self.max_results:
                break
            try:
                encoded_q = quote(query)
                url = (
                    f"https://www.reddit.com/r/{sub}/search.json?"
                    f"q={encoded_q}&sort=relevance&t=week&limit={self.max_results}"
                    f"&restrict_sr=on"
                )
                req = Request(url, headers={"User-Agent": "GenLab/1.0"})
                with urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode())

                for child in data.get("data", {}).get("children", []):
                    post = child.get("data", {})
                    if not post.get("is_video", False):
                        continue
                    pub = None
                    if post.get("created_utc"):
                        try:
                            pub = datetime.fromtimestamp(
                                post["created_utc"], tz=timezone.utc
                            )
                        except (ValueError, TypeError, OSError):
                            pass
                    permalink = post.get("permalink", "")
                    post_url = (
                        f"https://www.reddit.com{permalink}"
                        if permalink
                        else post.get("url", "")
                    )
                    results.append(
                        VideoSearchResult(
                            url=post_url,
                            title=post.get("title", ""),
                            view_count=int(post.get("score", 0)),
                            like_count=int(post.get("ups", 0)),
                            published_at=pub,
                            channel_name=f"r/{sub}",
                            thumbnail_url=post.get("thumbnail", ""),
                            backend="reddit",
                        )
                    )
                    if len(results) >= self.max_results:
                        break
            except Exception:
                logger.warning("Reddit search failed for r/%s", sub, exc_info=True)
                continue

        return results

    # ------------------------------------------------------------------
    # Level 4 — TMDB trailers (movies niche only)
    # ------------------------------------------------------------------
    def _search_tmdb(self, query: str) -> list[VideoSearchResult]:
        if not self.tmdb_api_key or not query:
            return []
        try:
            # Step 1: Search for the movie
            search_url = (
                "https://api.themoviedb.org/3/search/movie?"
                + urlencode(
                    {"api_key": self.tmdb_api_key, "query": query, "page": "1"}
                )
            )
            req = Request(search_url, headers={"User-Agent": "GenLab/1.0"})
            with urlopen(req, timeout=10) as resp:
                search_data = json.loads(resp.read().decode())

            movie_results = search_data.get("results", [])
            if not movie_results:
                return []

            results: list[VideoSearchResult] = []
            # Check top 3 movie matches
            for movie in movie_results[: min(3, len(movie_results))]:
                movie_id = movie.get("id")
                if not movie_id:
                    continue

                # Step 2: Get videos for the movie
                videos_url = (
                    f"https://api.themoviedb.org/3/movie/{movie_id}/videos?"
                    + urlencode({"api_key": self.tmdb_api_key})
                )
                vreq = Request(videos_url, headers={"User-Agent": "GenLab/1.0"})
                with urlopen(vreq, timeout=10) as resp:
                    videos_data = json.loads(resp.read().decode())

                for vid in videos_data.get("results", []):
                    if vid.get("site", "").lower() != "youtube":
                        continue
                    if vid.get("type", "").lower() not in (
                        "trailer",
                        "teaser",
                        "clip",
                    ):
                        continue
                    pub = None
                    if movie.get("release_date"):
                        try:
                            pub = datetime.fromisoformat(
                                movie["release_date"]
                            ).replace(tzinfo=timezone.utc)
                        except (ValueError, TypeError):
                            pass
                    results.append(
                        VideoSearchResult(
                            url=f"https://www.youtube.com/watch?v={vid['key']}",
                            title=f"{movie.get('title', '')} - {vid.get('name', '')}",
                            view_count=int(movie.get("popularity", 0) * 1000),
                            published_at=pub,
                            channel_name="TMDB",
                            thumbnail_url=(
                                f"https://img.youtube.com/vi/{vid['key']}/hqdefault.jpg"
                            ),
                            backend="tmdb",
                        )
                    )
                    if len(results) >= self.max_results:
                        return results

            return results
        except Exception:
            logger.warning("TMDB search failed", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _pick_best(
        self,
        results: list[VideoSearchResult],
        story_title: str,
        story_published_at: datetime | None,
    ) -> VideoSearchResult | None:
        """Score all results and return the best one above ``min_score``."""
        if not results:
            return None
        scored = [
            (r, score_video_result(r, story_title, story_published_at))
            for r in results
        ]
        scored.sort(key=lambda t: t[1], reverse=True)
        best_result, best_score = scored[0]
        if best_score < self.min_score:
            logger.debug(
                "Best score %.3f below threshold %.3f; rejecting",
                best_score,
                self.min_score,
            )
            return None
        return best_result
