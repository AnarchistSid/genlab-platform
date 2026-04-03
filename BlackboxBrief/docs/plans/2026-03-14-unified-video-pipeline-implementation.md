# Unified Video Pipeline — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** All 5 channels (BB, CriticalRush, FrameDrift, SpliceReel, ClutchWire) source real videos after ranking and publish video reels, with cross-channel credential isolation enforced.

**Architecture:** A shared `VideoSourcer` engine in genlab-core provides a 4-level fallback chain (direct URL → YouTube search → Reddit search → TMDB trailers) to find real source videos for top-N ranked stories. A shared `DownloadTopVideos` pipeline stage wraps this engine for both BB's bash pipeline and GenericPipelineRunner. CriticalRush's publisher is refactored to use the niche credential guard.

**Tech Stack:** Python 3.11, yt-dlp (video download), YouTube Data API v3 (search), scikit-learn (TF-IDF relevance scoring), FFmpeg (validation/re-encode), existing genlab-core infrastructure (DiskCache, QuotaManager, VideoDownloader)

**Design doc:** `docs/plans/2026-03-14-unified-video-pipeline-design.md`

---

## Task 1: Cross-Channel Credential Guard (CriticalRush Fix)

**Priority: CRITICAL — prevents gaming content publishing to BB's accounts.**

**Files:**
- Modify: `genlab-core/src/genlab_core/publishing/niche_credentials.py`
- Modify: `CriticalRush/niches/gaming/stages/publish_gaming_content.py`
- Create: `genlab-core/tests/publishing/test_cross_channel_guard.py`

**Step 1: Write failing test for CrossChannelPublishError**

```python
# genlab-core/tests/publishing/test_cross_channel_guard.py
import pytest
from genlab_core.publishing.niche_credentials import (
    validate_niche_match,
    CrossChannelPublishError,
)


def test_matching_niche_passes():
    """Same niche should not raise."""
    validate_niche_match(blueprint_niche="gaming", credential_niche="gaming")


def test_mismatched_niche_raises():
    """Cross-channel mismatch must raise CrossChannelPublishError."""
    with pytest.raises(CrossChannelPublishError, match="gaming.*ai_creators"):
        validate_niche_match(blueprint_niche="gaming", credential_niche="ai_creators")


def test_none_credential_niche_raises():
    """Missing credential niche must raise."""
    with pytest.raises(CrossChannelPublishError):
        validate_niche_match(blueprint_niche="gaming", credential_niche="")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/publishing/test_cross_channel_guard.py -v`
Expected: FAIL — `CrossChannelPublishError` and `validate_niche_match` don't exist yet

**Step 3: Implement CrossChannelPublishError and validate_niche_match**

Add to `genlab-core/src/genlab_core/publishing/niche_credentials.py`:

```python
class CrossChannelPublishError(RuntimeError):
    """Raised when a blueprint's niche doesn't match the credential niche."""


def validate_niche_match(blueprint_niche: str, credential_niche: str) -> None:
    """Assert that blueprint niche matches credential niche.

    Raises CrossChannelPublishError if there is a mismatch.
    """
    if not credential_niche:
        raise CrossChannelPublishError(
            f"No credential niche provided for blueprint niche '{blueprint_niche}'"
        )
    if blueprint_niche != credential_niche:
        raise CrossChannelPublishError(
            f"Cross-channel publish blocked: blueprint niche '{blueprint_niche}' "
            f"!= credential niche '{credential_niche}'"
        )
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/publishing/test_cross_channel_guard.py -v`
Expected: PASS (3 tests)

**Step 5: Refactor publish_gaming_content.py credentials**

In `CriticalRush/niches/gaming/stages/publish_gaming_content.py`, replace the `PLATFORM_CREDENTIALS` dict + `settings` attribute pattern with `niche_credentials` imports.

Replace the `validate_credentials()` method body (lines ~207-235) to use:

```python
from genlab_core.publishing.niche_credentials import (
    resolve_meta_credentials,
    resolve_youtube_credentials,
    resolve_twitter_credentials,
    resolve_threads_credentials,
    resolve_fb_credentials,
)

def _resolve_all_credentials(self) -> dict:
    """Resolve credentials for gaming niche via niche guard."""
    niche_id = "gaming"
    meta = resolve_meta_credentials(niche_id)
    yt = resolve_youtube_credentials(niche_id)
    tw = resolve_twitter_credentials(niche_id)
    fb_token, fb_page = resolve_fb_credentials(niche_id)
    threads_token, threads_uid = resolve_threads_credentials(niche_id)
    return {
        "instagram": {
            "access_token": meta.get("ig_access_token", ""),
            "user_id": meta.get("ig_user_id", ""),
        },
        "youtube": yt,
        "x_twitter": tw,
        "facebook": {
            "access_token": fb_token,
            "page_id": fb_page,
        },
        "threads": {
            "access_token": threads_token,
            "user_id": threads_uid,
        },
    }
```

Then update each `_publish_to_*` method to call `self._resolve_all_credentials()` instead of `getattr(settings, ...)`.

**Step 6: Run existing CR tests**

Run: `cd /Users/anarchistsid/GenLab && uv run --package criticalrush pytest CriticalRush/tests/ -v -k publish`
Expected: PASS (no regression)

**Step 7: Commit**

```bash
git add genlab-core/src/genlab_core/publishing/niche_credentials.py \
       genlab-core/tests/publishing/test_cross_channel_guard.py \
       CriticalRush/niches/gaming/stages/publish_gaming_content.py
git commit -m "feat(publishing): add cross-channel credential guard + refactor CR publisher

- Add CrossChannelPublishError and validate_niche_match to niche_credentials
- Refactor publish_gaming_content.py to use niche_credentials resolvers
- Prevents gaming content from publishing to BB's accounts"
```

---

## Task 2: BB Tag Inference Fix (compose_blueprints.py)

**Priority: CRITICAL — unblocks BB blueprint generation immediately.**

**Files:**
- Modify: `Content Scraper/execution/compose_blueprints.py` (lines ~1502-1558)
- Create: `Content Scraper/tests/test_tag_inference_ordering.py`

**Step 1: Write failing test**

```python
# Content Scraper/tests/test_tag_inference_ordering.py
"""Verify tag inference runs BEFORE video filter in compose pipeline."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch

from execution.compose_blueprints import infer_story_tags, match_templates


def test_infer_story_tags_never_empty():
    """Every story must get at least one tag (fallback: 'creative')."""
    story = {"title": "Luma AI releases new video model", "summary": ""}
    tags = infer_story_tags(story)
    assert len(tags) > 0, "infer_story_tags must never return empty list"


def test_infer_story_tags_matches_template():
    """A story with AI keywords should match at least one template."""
    story = {"title": "OpenAI launches GPT-5 with new features", "summary": "Product launch"}
    tags = infer_story_tags(story)
    templates = [
        {"template_id": "TPL_REE_NEWS", "best_for": ["news", "products"]},
        {"template_id": "TPL_REE_CREATOR", "best_for": ["creative", "pop_culture"]},
    ]
    matches = match_templates(tags, templates)
    assert len(matches) > 0, f"Tags {tags} should match at least one template"


def test_empty_title_gets_fallback_tag():
    """Stories with empty/generic titles get fallback 'creative' tag."""
    story = {"title": "", "summary": ""}
    tags = infer_story_tags(story)
    assert "creative" in tags
```

**Step 2: Run test to verify tests pass (these test existing functions)**

Run: `cd "/Users/anarchistsid/GenLab/Content Scraper" && uv run --package content-scraper pytest tests/test_tag_inference_ordering.py -v`
Expected: PASS — these test the functions in isolation, which already work

**Step 3: Fix tag inference ordering in compose_blueprints.py main()**

In `Content Scraper/execution/compose_blueprints.py`, move tag inference BEFORE the video filter. Currently at lines ~1502-1534, the order is:

```python
# CURRENT (broken) — lines 1502-1534:
stories = trend_pack.get("stories", [])
# ... risk filter ...
# ... video filter (requires tags but tags not set yet) ...
# ... top-N cutoff ...
# ... compose_blueprints() calls infer_story_tags() internally
```

Change to add tag inference right after risk filter, BEFORE video filter:

```python
# After risk filter (line ~1509), BEFORE video filter (line ~1517):
# Pre-infer tags so template matching works during video filter decisions
for story in stories:
    if not story.get("tags"):
        story["tags"] = infer_story_tags(story)
```

This ensures every story has tags before any filtering step. The `infer_story_tags()` call inside `compose_blueprints()` will be a no-op for stories that already have tags.

**Step 4: Run full BB test suite**

Run: `cd "/Users/anarchistsid/GenLab/Content Scraper" && uv run --package content-scraper pytest tests/ -v --timeout=60`
Expected: PASS (no regression)

**Step 5: Commit**

```bash
git add "Content Scraper/execution/compose_blueprints.py" \
       "Content Scraper/tests/test_tag_inference_ordering.py"
git commit -m "fix(compose): run tag inference before video filter

Stories arrived with empty tags, causing template matching to fail
after the video filter. Now infer_story_tags() runs before any
filtering, ensuring every story has tags for template matching."
```

---

## Task 3: VideoSourcer Engine (genlab-core)

**Files:**
- Create: `genlab-core/src/genlab_core/media/video_sourcer.py`
- Create: `genlab-core/tests/media/test_video_sourcer.py`

**Step 1: Write failing tests for VideoSourcer**

```python
# genlab-core/tests/media/test_video_sourcer.py
"""Tests for VideoSourcer fallback chain and scoring."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from genlab_core.media.video_sourcer import (
    VideoSourcer,
    VideoSearchResult,
    score_video_result,
    is_direct_video_url,
)


def test_is_direct_video_url_youtube():
    assert is_direct_video_url("https://www.youtube.com/watch?v=abc123")
    assert is_direct_video_url("https://youtu.be/abc123")


def test_is_direct_video_url_reddit():
    assert is_direct_video_url("https://v.redd.it/abc123")
    assert is_direct_video_url("https://www.reddit.com/r/anime/comments/abc/my_post/")


def test_is_direct_video_url_not_video():
    assert not is_direct_video_url("https://www.animenewsnetwork.com/article/123")
    assert not is_direct_video_url("https://espn.com/nfl/story/_/id/123")


def test_score_video_result_relevance():
    """Higher title similarity = higher score."""
    result_good = VideoSearchResult(
        url="https://youtube.com/watch?v=1",
        title="OpenAI launches GPT-5",
        duration_seconds=60,
        view_count=10000,
        published_at="2026-03-14T00:00:00Z",
    )
    result_bad = VideoSearchResult(
        url="https://youtube.com/watch?v=2",
        title="Cooking with AI robots in the kitchen",
        duration_seconds=60,
        view_count=10000,
        published_at="2026-03-14T00:00:00Z",
    )
    story_title = "OpenAI launches GPT-5 with new capabilities"
    score_good = score_video_result(result_good, story_title)
    score_bad = score_video_result(result_bad, story_title)
    assert score_good > score_bad


def test_score_video_result_duration_fit():
    """Videos near 60s score higher than very long videos."""
    short = VideoSearchResult(
        url="u1", title="test", duration_seconds=60,
        view_count=1000, published_at="2026-03-14T00:00:00Z",
    )
    long = VideoSearchResult(
        url="u2", title="test", duration_seconds=3600,
        view_count=1000, published_at="2026-03-14T00:00:00Z",
    )
    assert score_video_result(short, "test") > score_video_result(long, "test")


def test_video_sourcer_init():
    """VideoSourcer initializes with niche config."""
    sourcer = VideoSourcer(niche_id="anime", niche_keywords=["anime", "manga"])
    assert sourcer.niche_id == "anime"
    assert "anime" in sourcer.niche_keywords
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/media/test_video_sourcer.py -v`
Expected: FAIL — `video_sourcer` module doesn't exist

**Step 3: Implement VideoSourcer**

Create `genlab-core/src/genlab_core/media/video_sourcer.py`:

```python
"""Video sourcing engine with pluggable search backends and fallback chain.

Finds real source videos for stories using a ranked fallback:
  1. Direct URL (YouTube/Reddit/Vimeo links in story URL or extracted video_url)
  2. YouTube Data API search (by story title + niche keywords)
  3. Reddit search (niche subreddits for video posts)
  4. TMDB trailers (movies niche only)

Each search result is scored on relevance, freshness, quality, and duration fit.
"""
from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)

# ── URL detection patterns ──────────────────────────────────────
_YOUTUBE_RE = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)"
)
_REDDIT_VIDEO_RE = re.compile(
    r"(?:v\.redd\.it/|reddit\.com/r/\w+/comments/)"
)
_VIMEO_RE = re.compile(r"vimeo\.com/\d+")
_TIKTOK_RE = re.compile(r"tiktok\.com/@[\w.]+/video/")
_TWITTER_RE = re.compile(r"(?:twitter\.com|x\.com)/\w+/status/\d+")

_DIRECT_VIDEO_PATTERNS = [
    _YOUTUBE_RE, _REDDIT_VIDEO_RE, _VIMEO_RE, _TIKTOK_RE, _TWITTER_RE,
]

# ── Scoring weights ─────────────────────────────────────────────
WEIGHT_RELEVANCE = 0.40
WEIGHT_FRESHNESS = 0.25
WEIGHT_QUALITY = 0.20
WEIGHT_DURATION = 0.15

FRESHNESS_HALF_LIFE_HOURS = 48.0
DURATION_CENTER_SECONDS = 60.0
DURATION_SIGMA_SECONDS = 45.0
MIN_RELEVANCE_THRESHOLD = 0.3


@dataclass
class VideoSearchResult:
    """A single video search result from any backend."""
    url: str
    title: str
    duration_seconds: float = 0.0
    view_count: int = 0
    like_count: int = 0
    published_at: str = ""
    channel_name: str = ""
    channel_subscribers: int = 0
    thumbnail_url: str = ""
    backend: str = "unknown"
    metadata: Dict[str, Any] = field(default_factory=dict)


def is_direct_video_url(url: str) -> bool:
    """Check if URL points directly to a video platform."""
    if not url:
        return False
    return any(p.search(url) for p in _DIRECT_VIDEO_PATTERNS)


def score_video_result(
    result: VideoSearchResult,
    story_title: str,
    story_published_at: Optional[str] = None,
) -> float:
    """Score a video search result on 4 dimensions (0.0 - 1.0)."""
    # Relevance: TF-IDF cosine similarity between story title and video title
    relevance = 0.0
    if result.title and story_title:
        try:
            vectorizer = TfidfVectorizer(stop_words="english")
            tfidf = vectorizer.fit_transform([story_title, result.title])
            relevance = float(cosine_similarity(tfidf[0:1], tfidf[1:2])[0][0])
        except ValueError:
            relevance = 0.0

    # Freshness: exponential decay from story publish date
    freshness = 1.0
    if story_published_at and result.published_at:
        try:
            story_dt = datetime.fromisoformat(story_published_at.replace("Z", "+00:00"))
            video_dt = datetime.fromisoformat(result.published_at.replace("Z", "+00:00"))
            hours_diff = abs((story_dt - video_dt).total_seconds()) / 3600.0
            freshness = math.exp(-0.693 * hours_diff / FRESHNESS_HALF_LIFE_HOURS)
        except (ValueError, TypeError):
            freshness = 0.5

    # Quality: log-scaled view count (0-1)
    quality = 0.0
    if result.view_count > 0:
        quality = min(1.0, math.log10(result.view_count + 1) / 7.0)  # 10M views = 1.0

    # Duration fit: gaussian centered on 60s
    duration_fit = 1.0
    if result.duration_seconds > 0:
        z = (result.duration_seconds - DURATION_CENTER_SECONDS) / DURATION_SIGMA_SECONDS
        duration_fit = math.exp(-0.5 * z * z)

    total = (
        WEIGHT_RELEVANCE * relevance
        + WEIGHT_FRESHNESS * freshness
        + WEIGHT_QUALITY * quality
        + WEIGHT_DURATION * duration_fit
    )
    return round(total, 4)


class VideoSourcer:
    """Multi-backend video search engine with fallback chain."""

    def __init__(
        self,
        niche_id: str,
        niche_keywords: Optional[List[str]] = None,
        youtube_api_key: Optional[str] = None,
        max_results_per_backend: int = 5,
        min_score: float = MIN_RELEVANCE_THRESHOLD,
    ):
        self.niche_id = niche_id
        self.niche_keywords = niche_keywords or []
        self.youtube_api_key = youtube_api_key or os.getenv("YOUTUBE_API_KEY", "")
        self.max_results = max_results_per_backend
        self.min_score = min_score
        self._stats = {
            "direct_url": 0,
            "youtube_search": 0,
            "reddit_search": 0,
            "tmdb_trailer": 0,
            "no_video": 0,
        }

    @property
    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    def find_video_for_story(
        self,
        story: Dict[str, Any],
    ) -> Optional[VideoSearchResult]:
        """Find the best video for a story using the fallback chain.

        Returns the highest-scoring VideoSearchResult, or None if all
        backends fail or no result meets the minimum score threshold.
        """
        story_url = story.get("url", "")
        story_title = story.get("title", "")
        video_url = story.get("video_url", "")
        published_at = story.get("published_at", "")

        # Level 1: Direct source URL
        direct_url = video_url or story_url
        if is_direct_video_url(direct_url):
            self._stats["direct_url"] += 1
            return VideoSearchResult(
                url=direct_url,
                title=story_title,
                backend="direct_url",
            )

        # Level 2: YouTube search
        if self.youtube_api_key:
            results = self._youtube_search(story_title, published_at)
            if results:
                best = self._pick_best(results, story_title, published_at)
                if best:
                    self._stats["youtube_search"] += 1
                    return best

        # Level 3: Reddit search
        results = self._reddit_search(story_title)
        if results:
            best = self._pick_best(results, story_title, published_at)
            if best:
                self._stats["reddit_search"] += 1
                return best

        # Level 4: TMDB trailers (movies only)
        if self.niche_id == "movies":
            result = self._tmdb_trailer_search(story)
            if result:
                self._stats["tmdb_trailer"] += 1
                return result

        # Level 5: No video found
        self._stats["no_video"] += 1
        logger.info("No video found for story: %.80s", story_title)
        return None

    def _pick_best(
        self,
        results: List[VideoSearchResult],
        story_title: str,
        published_at: str,
    ) -> Optional[VideoSearchResult]:
        """Score results and return the best one above min threshold."""
        scored = [
            (score_video_result(r, story_title, published_at), r)
            for r in results
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        if scored and scored[0][0] >= self.min_score:
            return scored[0][1]
        return None

    def _youtube_search(
        self,
        query: str,
        published_after: str = "",
    ) -> List[VideoSearchResult]:
        """Search YouTube Data API v3 for videos matching query.

        Uses search.list endpoint. Costs 100 quota units per call.
        """
        try:
            from googleapiclient.discovery import build
        except ImportError:
            logger.warning("google-api-python-client not installed, skipping YouTube search")
            return []

        if not self.youtube_api_key:
            return []

        search_query = query
        if self.niche_keywords:
            search_query = f"{query} {' '.join(self.niche_keywords[:2])}"

        try:
            youtube = build("youtube", "v3", developerKey=self.youtube_api_key)
            params = {
                "q": search_query,
                "part": "snippet",
                "type": "video",
                "maxResults": self.max_results,
                "order": "relevance",
                "videoEmbeddable": "true",
            }
            if published_after:
                try:
                    dt = datetime.fromisoformat(published_after.replace("Z", "+00:00"))
                    params["publishedAfter"] = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                except ValueError:
                    pass

            response = youtube.search().list(**params).execute()
            results = []
            video_ids = [
                item["id"]["videoId"]
                for item in response.get("items", [])
                if item.get("id", {}).get("videoId")
            ]

            if video_ids:
                # Fetch durations and stats
                details = youtube.videos().list(
                    part="contentDetails,statistics",
                    id=",".join(video_ids),
                ).execute()
                detail_map = {
                    d["id"]: d for d in details.get("items", [])
                }

                for item in response.get("items", []):
                    vid = item["id"].get("videoId", "")
                    snippet = item.get("snippet", {})
                    detail = detail_map.get(vid, {})
                    duration = _parse_iso_duration(
                        detail.get("contentDetails", {}).get("duration", "PT0S")
                    )
                    stats = detail.get("statistics", {})
                    results.append(VideoSearchResult(
                        url=f"https://www.youtube.com/watch?v={vid}",
                        title=snippet.get("title", ""),
                        duration_seconds=duration,
                        view_count=int(stats.get("viewCount", 0)),
                        like_count=int(stats.get("likeCount", 0)),
                        published_at=snippet.get("publishedAt", ""),
                        channel_name=snippet.get("channelTitle", ""),
                        thumbnail_url=snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
                        backend="youtube_search",
                    ))
            return results

        except Exception as exc:
            logger.warning("YouTube search failed: %s", exc)
            return []

    def _reddit_search(self, query: str) -> List[VideoSearchResult]:
        """Search Reddit for video posts matching query."""
        import urllib.request
        import json as _json

        subreddits = _NICHE_SUBREDDITS.get(self.niche_id, [])
        if not subreddits:
            return []

        results = []
        for sub in subreddits[:2]:  # Limit to 2 subreddits per search
            url = (
                f"https://www.reddit.com/r/{sub}/search.json"
                f"?q={urllib.parse.quote(query)}&sort=relevance&t=week&limit=5"
            )
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "GenLab/1.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = _json.loads(resp.read())
                for post in data.get("data", {}).get("children", []):
                    pd = post.get("data", {})
                    if not pd.get("is_video") and "v.redd.it" not in pd.get("url", ""):
                        continue
                    results.append(VideoSearchResult(
                        url=f"https://www.reddit.com{pd.get('permalink', '')}",
                        title=pd.get("title", ""),
                        view_count=pd.get("score", 0),
                        published_at=datetime.fromtimestamp(
                            pd.get("created_utc", 0), tz=timezone.utc
                        ).isoformat() if pd.get("created_utc") else "",
                        backend="reddit_search",
                    ))
            except Exception as exc:
                logger.debug("Reddit search failed for r/%s: %s", sub, exc)
        return results

    def _tmdb_trailer_search(self, story: Dict[str, Any]) -> Optional[VideoSearchResult]:
        """Search TMDB for official movie trailer."""
        tmdb_key = os.getenv("TMDB_API_KEY", "")
        if not tmdb_key:
            return None

        import urllib.request
        import json as _json

        title = story.get("title", "")
        try:
            search_url = (
                f"https://api.themoviedb.org/3/search/movie"
                f"?api_key={tmdb_key}&query={urllib.parse.quote(title)}&page=1"
            )
            req = urllib.request.Request(search_url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read())
            results = data.get("results", [])
            if not results:
                return None

            movie_id = results[0]["id"]
            videos_url = (
                f"https://api.themoviedb.org/3/movie/{movie_id}/videos"
                f"?api_key={tmdb_key}"
            )
            req = urllib.request.Request(videos_url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                vdata = _json.loads(resp.read())

            for v in vdata.get("results", []):
                if v.get("site") == "YouTube" and v.get("type") in ("Trailer", "Teaser"):
                    return VideoSearchResult(
                        url=f"https://www.youtube.com/watch?v={v['key']}",
                        title=v.get("name", title),
                        backend="tmdb_trailer",
                    )
        except Exception as exc:
            logger.debug("TMDB trailer search failed: %s", exc)
        return None


# ── Niche subreddit mapping ─────────────────────────────────────
_NICHE_SUBREDDITS = {
    "anime": ["anime", "animeclips", "AnimeAMV"],
    "movies": ["movies", "MovieClips", "trailers"],
    "sports": ["sports", "nba", "soccer", "nfl"],
    "gaming": ["gaming", "gameclips", "Games"],
    "ai_news": ["artificial", "ChatGPT", "StableDiffusion"],
    "ai_creators": ["artificial", "ChatGPT", "StableDiffusion"],
}


def _parse_iso_duration(duration_str: str) -> float:
    """Parse ISO 8601 duration (PT1H2M3S) to seconds."""
    match = re.match(
        r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str
    )
    if not match:
        return 0.0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return float(hours * 3600 + minutes * 60 + seconds)
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/media/test_video_sourcer.py -v`
Expected: PASS (all 7 tests)

**Step 5: Commit**

```bash
git add genlab-core/src/genlab_core/media/video_sourcer.py \
       genlab-core/tests/media/test_video_sourcer.py
git commit -m "feat(media): add VideoSourcer engine with 4-level fallback chain

YouTube search, Reddit search, TMDB trailers, direct URL detection.
Relevance scoring via TF-IDF cosine similarity + freshness + quality + duration fit."
```

---

## Task 4: DownloadTopVideos Stage (genlab-core)

**Files:**
- Create: `genlab-core/src/genlab_core/media/download_top_videos.py`
- Create: `genlab-core/tests/media/test_download_top_videos.py`

**Step 1: Write failing test**

```python
# genlab-core/tests/media/test_download_top_videos.py
"""Tests for DownloadTopVideos pipeline stage."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from genlab_core.media.download_top_videos import (
    DownloadTopVideos,
    download_videos_for_stories,
    build_clip_index,
)


@pytest.fixture
def sample_stories():
    return [
        {
            "story_id": "s1",
            "title": "OpenAI launches GPT-5",
            "url": "https://www.youtube.com/watch?v=abc123",
            "summary": "New model release",
            "published_at": "2026-03-14T00:00:00Z",
            "score": 0.9,
        },
        {
            "story_id": "s2",
            "title": "Anime industry report 2026",
            "url": "https://animenewsnetwork.com/article/123",
            "summary": "Annual report",
            "published_at": "2026-03-14T00:00:00Z",
            "score": 0.7,
        },
    ]


def test_build_clip_index_format():
    """clip_index.json must have required fields per entry."""
    entries = {
        "s1": {
            "story_id": "s1",
            "clip_path": "/tmp/clips/s1.mp4",
            "source_url": "https://youtube.com/watch?v=abc",
            "source_backend": "direct_url",
            "relevance_score": 1.0,
            "duration_seconds": 60,
            "success": True,
        }
    }
    index = build_clip_index("run123", entries)
    assert index["run_id"] == "run123"
    assert "clips" in index
    assert index["clips"]["s1"]["success"] is True
    assert "clip_path" in index["clips"]["s1"]


def test_download_top_videos_respects_max_stories(sample_stories):
    """Only top-N stories should be processed."""
    with patch("genlab_core.media.download_top_videos.VideoSourcer") as MockSourcer:
        mock_instance = MagicMock()
        mock_instance.find_video_for_story.return_value = None
        mock_instance.stats = {"direct_url": 0, "youtube_search": 0, "reddit_search": 0, "tmdb_trailer": 0, "no_video": 2}
        MockSourcer.return_value = mock_instance

        with patch("genlab_core.media.download_top_videos._download_video") as mock_dl:
            results = download_videos_for_stories(
                stories=sample_stories,
                run_dir=Path("/tmp/test_run"),
                niche_id="ai_creators",
                max_stories=1,
            )
            # Should only process 1 story (top-N cutoff)
            assert mock_instance.find_video_for_story.call_count == 1
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/media/test_download_top_videos.py -v`
Expected: FAIL — module doesn't exist

**Step 3: Implement DownloadTopVideos**

Create `genlab-core/src/genlab_core/media/download_top_videos.py`:

```python
"""Download videos for top-N ranked stories using VideoSourcer fallback chain.

Standalone CLI usage (BB pipeline):
    python -m genlab_core.media.download_top_videos --run-id RUN_ID --niche ai_creators

GenericPipelineRunner stage usage:
    Loaded dynamically from niche.yaml pipeline config.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from genlab_core.media.video_sourcer import VideoSourcer, VideoSearchResult

logger = logging.getLogger(__name__)

# ── Niche keyword config ────────────────────────────────────────
_NICHE_KEYWORDS = {
    "ai_creators": ["AI", "artificial intelligence", "machine learning"],
    "ai_news": ["AI", "artificial intelligence", "machine learning"],
    "anime": ["anime", "manga", "otaku"],
    "movies": ["movie", "film", "cinema", "trailer"],
    "sports": ["sports", "highlights", "game", "match"],
    "gaming": ["gaming", "gameplay", "esports"],
}

# ── Constants ───────────────────────────────────────────────────
DEFAULT_MAX_STORIES = 10
DOWNLOAD_TIMEOUT = 120  # seconds per video
MAX_CONCURRENT = 3


def download_videos_for_stories(
    stories: List[Dict[str, Any]],
    run_dir: Path,
    niche_id: str,
    max_stories: int = DEFAULT_MAX_STORIES,
    youtube_api_key: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Download videos for top-N stories using VideoSourcer.

    Returns dict mapping story_id -> clip entry (for clip_index.json).
    """
    clips_dir = run_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    # Top-N cutoff
    if max_stories and len(stories) > max_stories:
        logger.info("Top-N cutoff: %d → %d stories", len(stories), max_stories)
        stories = stories[:max_stories]

    sourcer = VideoSourcer(
        niche_id=niche_id,
        niche_keywords=_NICHE_KEYWORDS.get(niche_id, []),
        youtube_api_key=youtube_api_key or os.getenv("YOUTUBE_API_KEY", ""),
    )

    entries: Dict[str, Dict[str, Any]] = {}
    for story in stories:
        story_id = story.get("story_id", "")
        if not story_id:
            continue

        result = sourcer.find_video_for_story(story)
        if not result:
            entries[story_id] = {
                "story_id": story_id,
                "clip_path": "",
                "source_url": "",
                "source_backend": "none",
                "relevance_score": 0.0,
                "duration_seconds": 0,
                "success": False,
                "error": "no_video_found",
            }
            continue

        # Download the video
        clip_path = clips_dir / f"{story_id}.mp4"
        dl_result = _download_video(result.url, clip_path)

        entries[story_id] = {
            "story_id": story_id,
            "clip_path": str(clip_path) if dl_result["success"] else "",
            "source_url": result.url,
            "source_backend": result.backend,
            "relevance_score": dl_result.get("relevance_score", 0.0),
            "duration_seconds": dl_result.get("duration", 0),
            "success": dl_result["success"],
            "error": dl_result.get("error", ""),
        }

    logger.info(
        "Video sourcing complete: %d/%d successful | backends: %s",
        sum(1 for e in entries.values() if e["success"]),
        len(entries),
        sourcer.stats,
    )
    return entries


def _download_video(url: str, output_path: Path) -> Dict[str, Any]:
    """Download a video using yt-dlp. Returns success/error dict."""
    try:
        cmd = [
            "yt-dlp",
            "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
            "-o", str(output_path),
            "--no-playlist",
            "--socket-timeout", "30",
            "--retries", "2",
            url,
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=DOWNLOAD_TIMEOUT,
        )
        if proc.returncode != 0:
            logger.warning("yt-dlp failed for %s: %s", url, proc.stderr[:200])
            return {"success": False, "error": proc.stderr[:200]}

        # Probe duration
        duration = _probe_duration(output_path)

        # Validate file
        if not output_path.exists() or output_path.stat().st_size < 100_000:
            return {"success": False, "error": "file_too_small_or_missing"}

        return {"success": True, "duration": duration}

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "download_timeout"}
    except Exception as exc:
        return {"success": False, "error": str(exc)[:200]}


def _probe_duration(path: Path) -> float:
    """Get video duration via ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, timeout=15,
        )
        return float(result.stdout.strip() or 0)
    except Exception:
        return 0.0


def build_clip_index(
    run_id: str,
    entries: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Build clip_index.json structure."""
    successful = sum(1 for e in entries.values() if e.get("success"))
    return {
        "run_id": run_id,
        "videos_total": len(entries),
        "videos_downloaded": successful,
        "videos_failed": len(entries) - successful,
        "clips": entries,
    }


class DownloadTopVideos:
    """Pipeline stage for GenericPipelineRunner.

    Loaded dynamically from niche.yaml:
        - class: genlab_core.media.download_top_videos.DownloadTopVideos
    """

    def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute video download stage."""
        stories = context.get("stories", [])
        run_dir = Path(context.get("run_dir", ".tmp/runs/unknown"))
        niche_id = context.get("niche_id", "")
        max_stories = context.get("config", {}).get(
            "pipeline", {}
        ).get("max_items_per_run", DEFAULT_MAX_STORIES)

        entries = download_videos_for_stories(
            stories=stories,
            run_dir=run_dir,
            niche_id=niche_id,
            max_stories=max_stories,
        )

        # Write clip_index.json
        clip_index = build_clip_index(run_dir.name, entries)
        clip_index_path = run_dir / "clip_index.json"
        with open(clip_index_path, "w") as f:
            json.dump(clip_index, f, indent=2)

        context["clip_index"] = clip_index
        context["clip_index_path"] = str(clip_index_path)
        return context


# ── CLI entry point (for BB daily_intel.sh) ─────────────────────
def main():
    parser = argparse.ArgumentParser(description="Download videos for top-N ranked stories")
    parser.add_argument("--run-id", required=True, help="Run directory name")
    parser.add_argument("--niche", default="ai_creators", help="Niche ID")
    parser.add_argument("--max-stories", type=int, default=DEFAULT_MAX_STORIES)
    parser.add_argument("--project-dir", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    project_dir = Path(args.project_dir or os.getenv("GENLAB_PROJECT_DIR", "."))
    run_dir = project_dir / ".tmp" / "runs" / args.run_id

    # Load trend_pack stories
    trend_pack_path = run_dir / "trend_pack.json"
    if not trend_pack_path.exists():
        logger.error("trend_pack.json not found at %s", trend_pack_path)
        sys.exit(1)

    with open(trend_pack_path) as f:
        trend_pack = json.load(f)
    stories = trend_pack.get("stories", [])

    if not stories:
        logger.warning("No stories in trend_pack — nothing to download")
        sys.exit(2)

    entries = download_videos_for_stories(
        stories=stories,
        run_dir=run_dir,
        niche_id=args.niche,
        max_stories=args.max_stories,
    )

    clip_index = build_clip_index(args.run_id, entries)
    clip_index_path = run_dir / "clip_index.json"
    with open(clip_index_path, "w") as f:
        json.dump(clip_index, f, indent=2)

    logger.info(
        "Wrote clip_index.json: %d/%d videos downloaded",
        clip_index["videos_downloaded"],
        clip_index["videos_total"],
    )


if __name__ == "__main__":
    main()
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/media/test_download_top_videos.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add genlab-core/src/genlab_core/media/download_top_videos.py \
       genlab-core/tests/media/test_download_top_videos.py
git commit -m "feat(media): add DownloadTopVideos stage with CLI and pipeline interface

Downloads videos for top-N ranked stories using VideoSourcer fallback chain.
CLI: python -m genlab_core.media.download_top_videos --run-id RUN_ID --niche ai_creators
Pipeline: loaded via niche.yaml as DownloadTopVideos stage class."
```

---

## Task 5: BB Pipeline Reorder (daily_intel.sh)

**Files:**
- Modify: `Content Scraper/runbooks/daily_intel.sh` (lines 360-367)

**Step 1: Insert download_top_videos step after build_trend_pack**

In `Content Scraper/runbooks/daily_intel.sh`, after step 5 (build_trend_pack, line 366) and before step 6 (compose_blueprints, line 367), insert the new download step.

Change step 6 to be download_top_videos, and renumber compose_blueprints to step 7. Update the step counter in `run_step()` header from `/22` to `/23`.

```bash
# After line 366 (build_trend_pack):
run_step 6  "Downloading videos for top stories"            false "$VENV_PYTHON" -m genlab_core.media.download_top_videos --run-id "$RUN_ID" --niche "${NICHE_ID:-ai_creators}" --max-stories 10 --project-dir "$PROJECT_DIR"

# Renumber existing step 6 (compose_blueprints) to step 7:
run_step 7  "Composing blueprints (platform hints + virality scoring)" true "$VENV_PYTHON" execution/compose_blueprints.py --run-id "$RUN_ID"
```

Also renumber all subsequent steps (7→8, 8→9, ... 22→23) and update the step counter format from `/22` to `/23` in the `run_step()` function and summary.

Also add the download step to the express lane (after E5 compose_blueprints or before it):

```bash
# In express lane, before E5 (compose_blueprints):
run_step "E4b" "Express: downloading videos" false "$VENV_PYTHON" -m genlab_core.media.download_top_videos --run-id "$RUN_ID" --niche "${NICHE_ID:-ai_creators}" --max-stories 5 --project-dir "$PROJECT_DIR"
```

**Step 2: Verify script syntax**

Run: `bash -n "/Users/anarchistsid/GenLab/Content Scraper/runbooks/daily_intel.sh"`
Expected: No syntax errors

**Step 3: Commit**

```bash
git add "Content Scraper/runbooks/daily_intel.sh"
git commit -m "feat(pipeline): insert download_top_videos step after ranking

New step 6 downloads videos for top-N stories using VideoSourcer.
Compose blueprints moved to step 7. Express lane also downloads
before composing."
```

---

## Task 6: YouTube Sources for Non-Gaming Niches

**Files:**
- Modify: `FrameDrift/config/sources.yaml`
- Modify: `SpliceReel/config/sources.yaml`
- Modify: `ClutchWire/config/sources.yaml`

**Step 1: Add YouTube channel RSS feeds to FrameDrift (anime)**

Add to `FrameDrift/config/sources.yaml` under `tier_1` or as a new `youtube` section:

```yaml
  youtube_channels:
    - name: "Crunchyroll"
      url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCDGbTiKs1Dqe-kvURsHlIAw"
      type: "rss"
      weight: 0.9
      category: "anime_trailers"
    - name: "Anime News Network"
      url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCIKzjSHL-jYhGTwNs1x-3SA"
      type: "rss"
      weight: 0.8
      category: "anime_news"
    - name: "Gigguk"
      url: "https://www.youtube.com/feeds/videos.xml?channel_id=UC7dF9qfBMXrSlaaFFDvV_Yg"
      type: "rss"
      weight: 0.7
      category: "anime_reviews"
    - name: "Mother's Basement"
      url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCBs2Y3i14e1NWQxOGliatmg"
      type: "rss"
      weight: 0.7
      category: "anime_analysis"
```

**Step 2: Add YouTube channel RSS feeds to SpliceReel (movies)**

Add to `SpliceReel/config/sources.yaml`:

```yaml
  youtube_channels:
    - name: "FilmSelect Trailer"
      url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCY2qt3dw2TQJxvBrDiYGHdQ"
      type: "rss"
      weight: 0.9
      category: "movie_trailers"
    - name: "ONE Media"
      url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCiEwjMpBiMheJqBWFmkUNpg"
      type: "rss"
      weight: 0.8
      category: "movie_trailers"
    - name: "Chris Stuckmann"
      url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCCqEeDAUf4Mg0GgENmMbGPg"
      type: "rss"
      weight: 0.7
      category: "movie_reviews"
    - name: "Screen Junkies"
      url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCOpcACMWblDls9Z6GERVi1A"
      type: "rss"
      weight: 0.7
      category: "movie_commentary"
```

**Step 3: Add YouTube channel RSS feeds to ClutchWire (sports)**

Add to `ClutchWire/config/sources.yaml`:

```yaml
  youtube_channels:
    - name: "NBA"
      url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCWJ2lWNubArHWmf3FIHbfcQ"
      type: "rss"
      weight: 0.9
      category: "sports_highlights"
    - name: "NFL"
      url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCDVYQ4Zhbm3S2dlz7P1GBDg"
      type: "rss"
      weight: 0.9
      category: "sports_highlights"
    - name: "Premier League"
      url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCG5qGWdu8nIRZqJ_GgDwQ-w"
      type: "rss"
      weight: 0.9
      category: "sports_highlights"
    - name: "House of Highlights"
      url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCqQo7ewe87aYAe7ub5cYxDg"
      type: "rss"
      weight: 0.8
      category: "sports_highlights"
    - name: "ESPN"
      url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCiWLfSweyRNmLpgEHekhoAg"
      type: "rss"
      weight: 0.8
      category: "sports_news"
```

**Step 4: Verify YAML syntax**

Run: `cd /Users/anarchistsid/GenLab && python3 -c "import yaml; [yaml.safe_load(open(f)) for f in ['FrameDrift/config/sources.yaml', 'SpliceReel/config/sources.yaml', 'ClutchWire/config/sources.yaml']]" && echo "OK"`
Expected: OK (no parse errors)

**Step 5: Commit**

```bash
git add FrameDrift/config/sources.yaml \
       SpliceReel/config/sources.yaml \
       ClutchWire/config/sources.yaml
git commit -m "feat(sources): add YouTube channel RSS feeds for anime, movies, sports

Anime: Crunchyroll, ANN, Gigguk, Mother's Basement
Movies: FilmSelect, ONE Media, Chris Stuckmann, Screen Junkies
Sports: NBA, NFL, Premier League, House of Highlights, ESPN"
```

---

## Task 7: Non-Gaming Pipeline Stage Addition (niche.yaml)

**Files:**
- Modify: `FrameDrift/config/niche.yaml`
- Modify: `SpliceReel/config/niche.yaml`
- Modify: `ClutchWire/config/niche.yaml`

**Step 1: Add DownloadTopVideos stage to FrameDrift**

In `FrameDrift/config/niche.yaml`, insert after `AnimeScoringStrategy` and before `AnimeWritingStrategy`:

```yaml
    - class: genlab_core.media.download_top_videos.DownloadTopVideos
      retries: 1
      retry_delay_seconds: 30
```

**Step 2: Add DownloadTopVideos stage to SpliceReel**

Same insertion in `SpliceReel/config/niche.yaml` after scoring, before writing.

**Step 3: Add DownloadTopVideos stage to ClutchWire**

Same insertion in `ClutchWire/config/niche.yaml` after scoring, before writing.

**Step 4: Verify YAML syntax**

Run: `cd /Users/anarchistsid/GenLab && python3 -c "import yaml; [yaml.safe_load(open(f)) for f in ['FrameDrift/config/niche.yaml', 'SpliceReel/config/niche.yaml', 'ClutchWire/config/niche.yaml']]" && echo "OK"`
Expected: OK

**Step 5: Commit**

```bash
git add FrameDrift/config/niche.yaml \
       SpliceReel/config/niche.yaml \
       ClutchWire/config/niche.yaml
git commit -m "feat(pipeline): add DownloadTopVideos stage to anime, movies, sports pipelines

Inserted after scoring stage, before writing. Downloads real source
videos for top-N ranked stories using VideoSourcer fallback chain."
```

---

## Task 8: VisualRenderStrategy Upgrade (Non-Gaming)

**Files:**
- Modify: `FrameDrift/fd_strategies/visual_render.py`
- Modify: `SpliceReel/sr_strategies/visual_render.py`
- Modify: `ClutchWire/cw_strategies/visual_render.py`

**Step 1: Read current VisualRenderStrategy implementations**

Read each file to understand the current Pexels query generation logic.

**Step 2: Upgrade to check for downloaded video**

Replace the Pexels query generation with video presence check + VideoCompositor rendering:

```python
def run(self, context):
    stories = context.get("stories", [])
    clip_index = context.get("clip_index", {})
    clips = clip_index.get("clips", {})
    run_dir = Path(context.get("run_dir", ""))

    for story in stories:
        sid = story.get("story_id", "")
        clip_entry = clips.get(sid, {})

        if clip_entry.get("success") and clip_entry.get("clip_path"):
            clip_path = Path(clip_entry["clip_path"])
            if clip_path.exists():
                # Video exists — set rendered path
                story.setdefault("media", {})["rendered_path"] = str(clip_path)
                story["media"]["render_status"] = "video_ready"
                continue

        # No video — mark as no_video
        story.setdefault("media", {})["render_status"] = "no_video"

    return context
```

**Step 3: Run niche tests**

Run: `cd /Users/anarchistsid/GenLab && uv run --package framedrift pytest FrameDrift/tests/ -v --timeout=60`
Run: `cd /Users/anarchistsid/GenLab && uv run --package splicereel pytest SpliceReel/tests/ -v --timeout=60`
Run: `cd /Users/anarchistsid/GenLab && uv run --package clutchwire pytest ClutchWire/tests/ -v --timeout=60`
Expected: PASS (no regression)

**Step 4: Commit**

```bash
git add FrameDrift/fd_strategies/visual_render.py \
       SpliceReel/sr_strategies/visual_render.py \
       ClutchWire/cw_strategies/visual_render.py
git commit -m "feat(render): upgrade VisualRenderStrategy to use downloaded videos

Replaces Pexels query generation with video presence check.
Sets rendered_path when video exists, marks no_video otherwise."
```

---

## Task 9: PushToBacklog Upgrade (Non-Gaming)

**Files:**
- Modify: `FrameDrift/fd_strategies/push_to_backlog.py`
- Modify: `SpliceReel/sr_strategies/push_to_backlog.py`
- Modify: `ClutchWire/cw_strategies/push_to_backlog.py`

**Step 1: Read current PushToBacklog implementations**

Understand how `rendered_path` check determines DRAFTED vs VISUAL_READY status.

**Step 2: Upgrade status logic**

Ensure that when `rendered_path` is set (from VisualRenderStrategy upgrade), the blueprint status is set to `VISUAL_READY` instead of `DRAFTED`:

```python
# In the status determination logic:
if story.get("media", {}).get("rendered_path"):
    status = "VISUAL_READY"
else:
    status = "DRAFTED"
```

**Step 3: Run niche tests**

Same test commands as Task 8.
Expected: PASS

**Step 4: Commit**

```bash
git add FrameDrift/fd_strategies/push_to_backlog.py \
       SpliceReel/sr_strategies/push_to_backlog.py \
       ClutchWire/cw_strategies/push_to_backlog.py
git commit -m "feat(backlog): set VISUAL_READY when video exists in non-gaming pipelines

PushToBacklog now checks rendered_path from VisualRenderStrategy.
Video-backed stories get VISUAL_READY; no-video stories stay DRAFTED."
```

---

## Task 10: Run Full Test Suites

**Step 1: Run genlab-core tests**

Run: `cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/ -v --timeout=120 -x`
Expected: PASS (695+ tests)

**Step 2: Run BB tests**

Run: `cd "/Users/anarchistsid/GenLab/Content Scraper" && uv run --package content-scraper pytest tests/ -v --timeout=120`
Expected: PASS (1380+ tests, 1 known flaky)

**Step 3: Run CriticalRush tests**

Run: `cd /Users/anarchistsid/GenLab && uv run --package criticalrush pytest CriticalRush/tests/ -v --timeout=60`
Expected: PASS

**Step 4: Run non-gaming niche tests**

Run: `cd /Users/anarchistsid/GenLab && uv run --package framedrift pytest FrameDrift/tests/ -v --timeout=60`
Run: `cd /Users/anarchistsid/GenLab && uv run --package splicereel pytest SpliceReel/tests/ -v --timeout=60`
Run: `cd /Users/anarchistsid/GenLab && uv run --package clutchwire pytest ClutchWire/tests/ -v --timeout=60`
Expected: PASS (105, 96, 88 tests respectively)

**Step 5: Commit (if any test fixes needed)**

Fix any failing tests and commit fixes before proceeding.

---

## Task 11: End-to-End Smoke Test

**Step 1: Run BB pipeline dry run**

Run: `cd "/Users/anarchistsid/GenLab/Content Scraper" && uv run python -m genlab_core.media.download_top_videos --run-id test_smoke --niche ai_creators --max-stories 2 --project-dir .`
Expected: Downloads 1-2 videos (or gracefully fails with no trend_pack)

**Step 2: Verify clip_index.json format**

Check that `.tmp/runs/test_smoke/clip_index.json` has the expected structure with `run_id`, `clips`, `videos_total`, `videos_downloaded` fields.

**Step 3: Run compose_blueprints with the new clip_index**

Verify that `compose_blueprints.py` correctly reads the new clip_index format and produces blueprints for stories with videos.

**Step 4: Clean up test artifacts**

```bash
rm -rf "/Users/anarchistsid/GenLab/Content Scraper/.tmp/runs/test_smoke"
```

---

## Execution Order & Dependencies

```
Task 1 (CR credential fix)     ──── independent, CRITICAL
Task 2 (BB tag inference fix)   ──── independent, CRITICAL
Task 3 (VideoSourcer engine)    ──── independent, foundation
Task 4 (DownloadTopVideos)      ──── depends on Task 3
Task 5 (BB pipeline reorder)    ──── depends on Task 4
Task 6 (YouTube sources)        ──── independent
Task 7 (niche.yaml stages)      ──── depends on Task 4
Task 8 (VisualRender upgrade)   ──── depends on Task 4, 7
Task 9 (PushToBacklog upgrade)  ──── depends on Task 8
Task 10 (Full test suites)      ──── depends on all above
Task 11 (E2E smoke test)        ──── depends on Task 10
```

**Parallel-safe groups:**
- Group A (immediate fixes): Tasks 1, 2 (can run in parallel)
- Group B (foundation): Task 3 → Task 4
- Group C (BB integration): Task 5 (after Group B)
- Group D (niche configs): Tasks 6, 7 (Task 7 after Group B; Task 6 independent)
- Group E (niche code): Tasks 8, 9 (after Group D)
- Group F (validation): Tasks 10, 11 (after all)
