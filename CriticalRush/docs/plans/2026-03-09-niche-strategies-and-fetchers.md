# Niche Strategies & Source Fetchers Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Flesh out all stub strategies (scoring, hooks, writing, visual_render) across sports/movies/anime niches, wire up source fetchers with RSS + Reddit + API support, update pipeline_runner for per-niche stage dispatch, and validate with a dry-run.

**Architecture:** Each niche gets a shared `NicheFetcher` base that handles RSS (feedparser) and Reddit (JSON API) fetching with 3-pass dedup. Writing strategies call Anthropic Haiku with niche-specific prompt templates. Hook strategies use template formulas + placeholder substitution. Scoring follows the proven sports 4-dimension pattern. Pipeline runner dispatches niche-specific stage lists.

**Tech Stack:** Python 3.13, feedparser, httpx, anthropic SDK, yaml, genlab_core (DedupEngine, HookValidator, enforce_platform_rules, retry)

---

## Task 1: Shared Niche Fetcher Base

Creates a reusable fetcher that handles RSS and Reddit for all niches, so each niche only provides its config.

**Files:**
- Create: `CriticalRush/core/niche_fetcher.py`
- Test: `CriticalRush/tests/core/test_niche_fetcher.py`

**Step 1: Write the failing test**

```python
# tests/core/test_niche_fetcher.py
"""Tests for the shared NicheFetcher base."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


class TestRSSFetching:
    """Test RSS feed parsing and scoring."""

    def test_rss_entries_scored_by_recency_and_weight(self):
        from core.niche_fetcher import NicheFetcher

        feeds = [{"name": "TestFeed", "url": "http://example.com/rss", "weight": 0.9}]
        fetcher = NicheFetcher(niche_id="test", rss_feeds=feeds, subreddits=[], cutoff_hours=48)

        # Mock feedparser
        mock_entry = MagicMock()
        mock_entry.title = "Big Story"
        mock_entry.link = "http://example.com/story"
        mock_entry.summary = "Summary of big story"
        mock_entry.get.return_value = None
        now = datetime.now(timezone.utc)
        mock_entry.published_parsed = now.timetuple()

        mock_feed = MagicMock()
        mock_feed.entries = [mock_entry]
        mock_feed.bozo = False

        with patch("core.niche_fetcher.feedparser.parse", return_value=mock_feed):
            stories = fetcher._fetch_rss()

        assert len(stories) == 1
        assert stories[0]["title"] == "Big Story"
        assert stories[0]["source"] == "rss"
        assert 0 < stories[0]["score"] <= 1.0

    def test_rss_entries_older_than_cutoff_dropped(self):
        from core.niche_fetcher import NicheFetcher

        feeds = [{"name": "TestFeed", "url": "http://example.com/rss", "weight": 0.8}]
        fetcher = NicheFetcher(niche_id="test", rss_feeds=feeds, subreddits=[], cutoff_hours=48)

        # Entry from 72 hours ago
        old_time = datetime(2020, 1, 1, tzinfo=timezone.utc)
        mock_entry = MagicMock()
        mock_entry.title = "Old Story"
        mock_entry.link = "http://example.com/old"
        mock_entry.summary = "Old"
        mock_entry.get.return_value = None
        mock_entry.published_parsed = old_time.timetuple()

        mock_feed = MagicMock()
        mock_feed.entries = [mock_entry]
        mock_feed.bozo = False

        with patch("core.niche_fetcher.feedparser.parse", return_value=mock_feed):
            stories = fetcher._fetch_rss()

        assert len(stories) == 0


class TestRedditFetching:
    """Test Reddit JSON API fetching."""

    def test_reddit_posts_parsed_with_scores(self):
        from core.niche_fetcher import NicheFetcher

        subs = [{"name": "sports", "weight": 0.9}]
        fetcher = NicheFetcher(niche_id="test", rss_feeds=[], subreddits=subs, cutoff_hours=48)

        reddit_response = {
            "data": {
                "children": [
                    {
                        "data": {
                            "title": "Amazing play",
                            "url": "https://reddit.com/r/sports/abc",
                            "permalink": "/r/sports/comments/abc/amazing_play",
                            "score": 5000,
                            "num_comments": 300,
                            "created_utc": datetime.now(timezone.utc).timestamp(),
                            "selftext": "This was incredible",
                            "over_18": False,
                        }
                    }
                ]
            }
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = reddit_response
        mock_resp.raise_for_status = MagicMock()

        with patch("core.niche_fetcher.requests.get", return_value=mock_resp):
            stories = fetcher._fetch_reddit()

        assert len(stories) == 1
        assert stories[0]["title"] == "Amazing play"
        assert stories[0]["source"] == "reddit"
        assert stories[0]["upvotes"] == 5000

    def test_reddit_nsfw_posts_filtered(self):
        from core.niche_fetcher import NicheFetcher

        subs = [{"name": "test", "weight": 1.0}]
        fetcher = NicheFetcher(niche_id="test", rss_feeds=[], subreddits=subs, cutoff_hours=48)

        reddit_response = {
            "data": {
                "children": [
                    {
                        "data": {
                            "title": "NSFW post",
                            "url": "https://reddit.com/nsfw",
                            "permalink": "/r/test/comments/nsfw",
                            "score": 100,
                            "num_comments": 10,
                            "created_utc": datetime.now(timezone.utc).timestamp(),
                            "selftext": "",
                            "over_18": True,
                        }
                    }
                ]
            }
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = reddit_response
        mock_resp.raise_for_status = MagicMock()

        with patch("core.niche_fetcher.requests.get", return_value=mock_resp):
            stories = fetcher._fetch_reddit()

        assert len(stories) == 0


class TestMergeAndDedup:
    """Test story merging and deduplication."""

    def test_duplicate_urls_deduplicated(self):
        from core.niche_fetcher import NicheFetcher

        fetcher = NicheFetcher(niche_id="test", rss_feeds=[], subreddits=[], cutoff_hours=48)

        stories = [
            {"title": "Story A", "source_url": "http://example.com/a", "score": 0.9},
            {"title": "Story A duplicate", "source_url": "http://example.com/a", "score": 0.7},
            {"title": "Story B", "source_url": "http://example.com/b", "score": 0.8},
        ]

        deduped = fetcher._dedup_stories(stories)
        assert len(deduped) == 2

    def test_fetch_returns_sorted_capped_stories(self):
        from core.niche_fetcher import NicheFetcher

        fetcher = NicheFetcher(
            niche_id="test", rss_feeds=[], subreddits=[],
            cutoff_hours=48, max_stories=2,
        )

        stories = [
            {"title": f"Story {i}", "source_url": f"http://example.com/{i}", "score": i * 0.1}
            for i in range(5)
        ]

        with patch.object(fetcher, "_fetch_rss", return_value=stories[:3]), \
             patch.object(fetcher, "_fetch_reddit", return_value=stories[3:]):
            result = fetcher.fetch()

        assert len(result) <= 2
        # Highest score first
        assert result[0]["score"] >= result[1]["score"]
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/anarchistsid/GenLab && uv run --package CriticalRush pytest CriticalRush/tests/core/test_niche_fetcher.py -v`
Expected: FAIL (module not found)

**Step 3: Write minimal implementation**

```python
# core/niche_fetcher.py
"""Shared niche fetcher — RSS + Reddit for all niches.

Each niche configures its sources in sources.yaml. This module handles
the actual HTTP fetching, parsing, scoring, and dedup.
"""

from __future__ import annotations

import calendar
import logging
import socket
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import feedparser
import requests
import yaml

logger = logging.getLogger(__name__)

_USER_AGENT = "GenLab-NicheFetcher/1.0"


class NicheFetcher:
    """Fetch content from RSS feeds and Reddit, merge, dedup, cap."""

    def __init__(
        self,
        niche_id: str,
        rss_feeds: List[Dict[str, Any]],
        subreddits: List[Dict[str, Any]],
        cutoff_hours: int = 48,
        max_stories: int = 20,
    ) -> None:
        self._niche_id = niche_id
        self._rss_feeds = rss_feeds
        self._subreddits = subreddits
        self._cutoff_hours = cutoff_hours
        self._max_stories = max_stories

    # -- RSS --

    def _fetch_rss(self) -> List[Dict[str, Any]]:
        """Fetch and score entries from all configured RSS feeds."""
        stories: List[Dict[str, Any]] = []
        now = datetime.now(timezone.utc)

        for feed_cfg in self._rss_feeds:
            url = feed_cfg["url"]
            weight = feed_cfg.get("weight", 1.0)
            feed_name = feed_cfg.get("name", url)

            try:
                old_timeout = socket.getdefaulttimeout()
                try:
                    socket.setdefaulttimeout(30)
                    parsed = feedparser.parse(url, request_headers={"User-Agent": _USER_AGENT})
                finally:
                    socket.setdefaulttimeout(old_timeout)

                for entry in parsed.entries[:20]:
                    title = getattr(entry, "title", "") or ""
                    link = getattr(entry, "link", "") or ""
                    summary = getattr(entry, "summary", "") or ""

                    pub_parsed = getattr(entry, "published_parsed", None)
                    if pub_parsed:
                        pub_ts = calendar.timegm(pub_parsed)
                        published_at = datetime.fromtimestamp(pub_ts, tz=timezone.utc)
                    else:
                        published_at = now

                    hours_old = (now - published_at).total_seconds() / 3600
                    if hours_old > self._cutoff_hours:
                        continue

                    # Recency multiplier
                    if hours_old <= 6:
                        recency = 1.0
                    elif hours_old <= 24:
                        recency = 0.8
                    else:
                        recency = 0.5

                    score = min(1.0, weight * recency)

                    stories.append({
                        "title": title,
                        "source_url": link,
                        "source": "rss",
                        "source_name": feed_name,
                        "summary": summary,
                        "published_at": published_at.isoformat(),
                        "fetched_at": now.isoformat(),
                        "score": round(score, 4),
                    })

            except Exception as e:
                logger.warning("[%s] RSS fetch failed for %s: %s", self._niche_id, feed_name, e)

        return stories

    # -- Reddit --

    def _fetch_reddit(self) -> List[Dict[str, Any]]:
        """Fetch hot posts from all configured subreddits via JSON API."""
        stories: List[Dict[str, Any]] = []
        now = datetime.now(timezone.utc)

        for sub_cfg in self._subreddits:
            subreddit = sub_cfg["name"]
            weight = sub_cfg.get("weight", 1.0)

            url = f"https://www.reddit.com/r/{subreddit}/hot.json"
            try:
                resp = requests.get(
                    url,
                    params={"limit": "25", "raw_json": "1"},
                    headers={"User-Agent": _USER_AGENT},
                    timeout=15,
                )
                if resp.status_code == 429:
                    logger.warning("[%s] Reddit rate limit for r/%s", self._niche_id, subreddit)
                    time.sleep(3)
                    continue
                resp.raise_for_status()
                data = resp.json()

                for child in data.get("data", {}).get("children", []):
                    post = child.get("data", {})
                    if post.get("over_18"):
                        continue
                    if post.get("stickied"):
                        continue

                    title = post.get("title", "")
                    permalink = post.get("permalink", "")
                    upvotes = post.get("score", 0)
                    comments = post.get("num_comments", 0)
                    created_utc = post.get("created_utc", 0)
                    selftext = post.get("selftext", "")

                    created_at = datetime.fromtimestamp(created_utc, tz=timezone.utc)
                    hours_old = (now - created_at).total_seconds() / 3600
                    if hours_old > self._cutoff_hours:
                        continue

                    engagement = min(1.0, (upvotes + comments * 2) / 1000)
                    score = min(1.0, weight * engagement)

                    stories.append({
                        "title": title,
                        "source_url": f"https://www.reddit.com{permalink}",
                        "source": "reddit",
                        "source_name": f"r/{subreddit}",
                        "summary": selftext[:500] if selftext else title,
                        "published_at": created_at.isoformat(),
                        "fetched_at": now.isoformat(),
                        "score": round(score, 4),
                        "upvotes": upvotes,
                        "comment_count": comments,
                    })

                time.sleep(2)  # Rate limit between subreddits

            except Exception as e:
                logger.warning("[%s] Reddit fetch failed for r/%s: %s", self._niche_id, subreddit, e)

        return stories

    # -- Merge & Dedup --

    def _dedup_stories(self, stories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """URL-based dedup (keeps highest score for duplicate URLs)."""
        seen: Dict[str, Dict[str, Any]] = {}
        for story in stories:
            url = story.get("source_url", "")
            if url in seen:
                if story["score"] > seen[url]["score"]:
                    seen[url] = story
            else:
                seen[url] = story
        return list(seen.values())

    def fetch(self) -> List[Dict[str, Any]]:
        """Fetch from all sources, merge, dedup, sort, cap."""
        rss_stories = self._fetch_rss()
        reddit_stories = self._fetch_reddit()

        all_stories = rss_stories + reddit_stories
        deduped = self._dedup_stories(all_stories)
        deduped.sort(key=lambda s: s["score"], reverse=True)

        capped = deduped[:self._max_stories]
        logger.info(
            "[%s] Fetched %d RSS + %d Reddit = %d total, %d after dedup, %d capped",
            self._niche_id,
            len(rss_stories), len(reddit_stories), len(all_stories),
            len(deduped), len(capped),
        )
        return capped
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/anarchistsid/GenLab && uv run --package CriticalRush pytest CriticalRush/tests/core/test_niche_fetcher.py -v`
Expected: PASS (all 5 tests)

**Step 5: Commit**

```bash
git add core/niche_fetcher.py tests/core/test_niche_fetcher.py
git commit -m "feat(core): add shared NicheFetcher for RSS + Reddit fetching"
```

---

## Task 2: Niche Fetch Stages (sports, movies, anime)

Each niche gets a `FetchStories` stage that reads `sources.yaml` and delegates to `NicheFetcher`.

**Files:**
- Create: `CriticalRush/niches/sports/stages/__init__.py`
- Create: `CriticalRush/niches/sports/stages/fetch_sports_stories.py`
- Create: `CriticalRush/niches/movies/stages/__init__.py`
- Create: `CriticalRush/niches/movies/stages/fetch_movie_stories.py`
- Create: `CriticalRush/niches/anime/stages/__init__.py`
- Create: `CriticalRush/niches/anime/stages/fetch_anime_stories.py`
- Test: `CriticalRush/niches/sports/tests/test_fetch_stories.py`
- Test: `CriticalRush/niches/movies/tests/test_fetch_stories.py`
- Test: `CriticalRush/niches/anime/tests/test_fetch_stories.py`

**Step 1: Write the failing tests**

All 3 niche fetch tests follow the same pattern. Example for sports:

```python
# niches/sports/tests/test_fetch_stories.py
"""Tests for ClutchWire story fetching stage."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


class TestFetchSportsStories:
    def test_execute_populates_context_stories(self):
        from niches.sports.stages.fetch_sports_stories import FetchSportsStories

        mock_stories = [
            {"title": "NBA Finals Game 7", "source": "rss", "score": 0.9},
            {"title": "Premier League Result", "source": "reddit", "score": 0.7},
        ]

        stage = FetchSportsStories()
        with patch.object(stage, "_fetcher") as mock_fetcher:
            mock_fetcher.fetch.return_value = mock_stories
            context = {"stories": [], "run_stats": {}}
            result = stage.execute(context)

        assert len(result["stories"]) == 2
        assert result["run_stats"]["fetch"]["total_count"] == 2

    def test_execute_loads_sources_from_yaml(self):
        from niches.sports.stages.fetch_sports_stories import FetchSportsStories

        stage = FetchSportsStories()
        # Verify it can load config without error
        assert stage._rss_feeds is not None
        assert stage._subreddits is not None

    def test_execute_handles_empty_fetch(self):
        from niches.sports.stages.fetch_sports_stories import FetchSportsStories

        stage = FetchSportsStories()
        with patch.object(stage, "_fetcher") as mock_fetcher:
            mock_fetcher.fetch.return_value = []
            context = {"stories": [], "run_stats": {}}
            result = stage.execute(context)

        assert result["stories"] == []
        assert result["run_stats"]["fetch"]["total_count"] == 0
```

Movies and anime tests are identical except for class names (FetchMovieStories, FetchAnimeStories) and import paths.

**Step 2: Run tests to verify they fail**

Run: `cd /Users/anarchistsid/GenLab && uv run --package CriticalRush pytest CriticalRush/niches/sports/tests/test_fetch_stories.py -v`
Expected: FAIL (module not found)

**Step 3: Write implementations**

All 3 follow the same pattern. Example for sports:

```python
# niches/sports/stages/fetch_sports_stories.py
"""ClutchWire story fetching stage.

Reads sources.yaml, delegates to NicheFetcher for RSS + Reddit,
populates context["stories"].
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

import yaml

from core.niche_fetcher import NicheFetcher

logger = logging.getLogger(__name__)

NICHE_ROOT = Path(__file__).resolve().parent.parent


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


class FetchSportsStories:
    """Fetch sports stories from RSS feeds and Reddit."""

    def __init__(self) -> None:
        config = _load_yaml(NICHE_ROOT / "config" / "sources.yaml")
        self._rss_feeds = self._collect_rss(config)
        self._subreddits = config.get("reddit", {}).get("subreddits", [])
        self._fetcher = NicheFetcher(
            niche_id="sports",
            rss_feeds=self._rss_feeds,
            subreddits=self._subreddits,
            cutoff_hours=48,
            max_stories=20,
        )

    def _collect_rss(self, config: dict) -> List[Dict[str, Any]]:
        """Collect all RSS-type sources from tier_1, tier_2, tier_3."""
        feeds = []
        for tier_key in ("tier_1", "tier_2", "tier_3"):
            tier = config.get(tier_key, {})
            for source in tier.get("sources", []):
                if source.get("type") == "rss":
                    feeds.append({
                        "name": source["name"],
                        "url": source["url"],
                        "weight": source.get("weight", 1.0),
                    })
        return feeds

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        stories = self._fetcher.fetch()
        context["stories"] = stories
        context.setdefault("run_stats", {})["fetch"] = {
            "total_count": len(stories),
            "rss_count": sum(1 for s in stories if s.get("source") == "rss"),
            "reddit_count": sum(1 for s in stories if s.get("source") == "reddit"),
        }
        logger.info("[sports] Fetched %d stories", len(stories))
        return context
```

Movies (`fetch_movie_stories.py`) and anime (`fetch_anime_stories.py`) are identical except:
- Class names: `FetchMovieStories`, `FetchAnimeStories`
- `niche_id`: `"movies"`, `"anime"`
- Logger prefix: `[movies]`, `[anime]`

**Step 4: Run tests to verify they pass**

Run: `cd /Users/anarchistsid/GenLab && uv run --package CriticalRush pytest CriticalRush/niches/sports/tests/test_fetch_stories.py CriticalRush/niches/movies/tests/test_fetch_stories.py CriticalRush/niches/anime/tests/test_fetch_stories.py -v`
Expected: PASS (all 9 tests)

**Step 5: Commit**

```bash
git add niches/sports/stages/ niches/movies/stages/ niches/anime/stages/ \
        niches/sports/tests/test_fetch_stories.py \
        niches/movies/tests/test_fetch_stories.py \
        niches/anime/tests/test_fetch_stories.py
git commit -m "feat(niches): add fetch stages for sports, movies, anime"
```

---

## Task 3: Movie & Anime Scoring Strategies

Sports scoring is already implemented. Clone the pattern for movies (72h half-life) and anime (24h half-life + sakuga boost).

**Files:**
- Modify: `CriticalRush/niches/movies/strategies/scoring.py`
- Modify: `CriticalRush/niches/anime/strategies/scoring.py`
- Test: `CriticalRush/niches/movies/tests/test_scoring.py`
- Test: `CriticalRush/niches/anime/tests/test_scoring.py`

**Step 1: Write the failing tests**

```python
# niches/movies/tests/test_scoring.py
"""Tests for SpliceReel scoring strategy."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


class TestMovieScoringStrategy:
    def test_franchise_blockbuster_scores_higher_than_streaming(self):
        from niches.movies.strategies.scoring import MovieScoringStrategy

        scorer = MovieScoringStrategy()
        franchise = scorer.score_item({
            "title": "Avengers", "fetched_at": datetime.now(timezone.utc).isoformat(),
            "upvotes": 100, "film_type": "franchise_blockbuster",
        })
        streaming = scorer.score_item({
            "title": "Random Film", "fetched_at": datetime.now(timezone.utc).isoformat(),
            "upvotes": 100, "film_type": "straight_to_streaming",
        })
        assert franchise["final_score"] > streaming["final_score"]

    def test_recent_story_scores_higher_than_old(self):
        from niches.movies.strategies.scoring import MovieScoringStrategy

        scorer = MovieScoringStrategy()
        recent = scorer.score_item({
            "title": "New Film", "fetched_at": datetime.now(timezone.utc).isoformat(),
            "upvotes": 50,
        })
        old = scorer.score_item({
            "title": "Old Film", "fetched_at": "2020-01-01T00:00:00+00:00",
            "upvotes": 50,
        })
        assert recent["final_score"] > old["final_score"]

    def test_execute_filters_below_threshold(self):
        from niches.movies.strategies.scoring import MovieScoringStrategy

        scorer = MovieScoringStrategy()
        context = {
            "stories": [
                {"title": "Good", "fetched_at": datetime.now(timezone.utc).isoformat(), "upvotes": 500},
                {"title": "Bad", "fetched_at": "2020-01-01T00:00:00+00:00", "upvotes": 0},
            ],
            "run_stats": {},
        }
        result = scorer.execute(context)
        scored = result["stories"]
        assert all(s["final_score"] >= 0 for s in scored)

    def test_execute_empty_stories(self):
        from niches.movies.strategies.scoring import MovieScoringStrategy

        scorer = MovieScoringStrategy()
        result = scorer.execute({"stories": [], "run_stats": {}})
        assert result["stories"] == []
        assert result["run_stats"]["scoring"]["input_count"] == 0

    def test_72h_half_life_loaded_from_config(self):
        from niches.movies.strategies.scoring import MovieScoringStrategy

        scorer = MovieScoringStrategy()
        scorer._ensure_config()
        half_life = scorer._config.get("recency", {}).get("half_life_hours")
        assert half_life == 72
```

```python
# niches/anime/tests/test_scoring.py
"""Tests for FrameDrift scoring strategy."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


class TestAnimeScoringStrategy:
    def test_top_seasonal_scores_higher_than_older_series(self):
        from niches.anime.strategies.scoring import AnimeScoringStrategy

        scorer = AnimeScoringStrategy()
        seasonal = scorer.score_item({
            "title": "Jujutsu Kaisen", "fetched_at": datetime.now(timezone.utc).isoformat(),
            "upvotes": 100, "anime_type": "top_seasonal_title",
        })
        older = scorer.score_item({
            "title": "Old Anime", "fetched_at": datetime.now(timezone.utc).isoformat(),
            "upvotes": 100, "anime_type": "older_series",
        })
        assert seasonal["final_score"] > older["final_score"]

    def test_sakuga_content_gets_novelty_boost(self):
        from niches.anime.strategies.scoring import AnimeScoringStrategy

        scorer = AnimeScoringStrategy()
        sakuga = scorer.score_item({
            "title": "Amazing sakuga cut", "fetched_at": datetime.now(timezone.utc).isoformat(),
            "upvotes": 100,
        })
        # Sakuga content should get a novelty boost of 0.8 instead of 0.5
        assert sakuga["scores"]["novelty"] == 0.8

    def test_24h_half_life_loaded_from_config(self):
        from niches.anime.strategies.scoring import AnimeScoringStrategy

        scorer = AnimeScoringStrategy()
        scorer._ensure_config()
        half_life = scorer._config.get("recency", {}).get("half_life_hours")
        assert half_life == 24

    def test_execute_ranks_and_caps(self):
        from niches.anime.strategies.scoring import AnimeScoringStrategy

        scorer = AnimeScoringStrategy()
        stories = [
            {"title": f"Anime {i}", "fetched_at": datetime.now(timezone.utc).isoformat(),
             "upvotes": i * 100}
            for i in range(5)
        ]
        result = scorer.execute({"stories": stories, "run_stats": {}})
        scored = result["stories"]
        if len(scored) > 1:
            assert scored[0]["final_score"] >= scored[1]["final_score"]
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/anarchistsid/GenLab && uv run --package CriticalRush pytest CriticalRush/niches/movies/tests/test_scoring.py CriticalRush/niches/anime/tests/test_scoring.py -v`
Expected: FAIL (stub returns context without scoring)

**Step 3: Write implementations**

Movie scoring — replace the stub in `niches/movies/strategies/scoring.py` with a full implementation following the sports pattern but using `film_type` instead of `game_type`, 72h half-life, and movie magnitude multipliers.

Anime scoring — replace the stub in `niches/anime/strategies/scoring.py` with the same pattern but using `anime_type`, 24h half-life, and a **sakuga novelty boost**: if the title or summary contains any sakuga keyword from `scoring_weights.yaml`, set novelty score to 0.8 instead of default 0.5.

Both follow the exact structure of `niches/sports/strategies/scoring.py` (already proven):
- `_ensure_config()` loads from `scoring_weights.yaml`
- `_score_recency()` with configurable half-life
- `_score_community_signal()` from upvotes/comments
- `_score_magnitude()` from type-specific multipliers
- `_score_novelty()` with niche-specific boost (anime: sakuga keywords → 0.8)
- `score_item()` combines all dimensions
- `execute()` scores, sorts, filters, caps

**Step 4: Run tests to verify they pass**

Run: `cd /Users/anarchistsid/GenLab && uv run --package CriticalRush pytest CriticalRush/niches/movies/tests/test_scoring.py CriticalRush/niches/anime/tests/test_scoring.py -v`
Expected: PASS (all 9 tests)

**Step 5: Commit**

```bash
git add niches/movies/strategies/scoring.py niches/anime/strategies/scoring.py \
        niches/movies/tests/test_scoring.py niches/anime/tests/test_scoring.py
git commit -m "feat(scoring): implement movie and anime scoring strategies"
```

---

## Task 4: Sports & Movies Hook Strategies

Anime hooks already implemented (sakuga routing). Sports and movies need template-based hook generation with placeholder substitution.

**Files:**
- Modify: `CriticalRush/niches/sports/strategies/hooks.py`
- Modify: `CriticalRush/niches/movies/strategies/hooks.py`
- Existing tests: `CriticalRush/niches/sports/tests/test_hooks.py` (already has 5 tests)
- Existing tests: `CriticalRush/niches/movies/tests/test_hooks.py` (already has tests)

**Step 1: Verify existing tests fail against stubs**

Run: `cd /Users/anarchistsid/GenLab && uv run --package CriticalRush pytest CriticalRush/niches/sports/tests/test_hooks.py CriticalRush/niches/movies/tests/test_hooks.py -v`
Expected: Some tests pass (stub runs without error), but hook generation returns empty/no hooks

**Step 2: Write implementations**

Sports hooks — follow the anime pattern but simpler (no sakuga routing):
- Load formulas from `templates.yaml` `hooks.formulas`
- `generate_hook(item)`: Pick random formula, substitute `{team}`, `{player}`, `{sport}`, `{play_type}` from item dict
- `execute(context)`: Generate hooks for all stories, store in `story["content"]["hook"]`

```python
# niches/sports/strategies/hooks.py (replace stub)
"""ClutchWire hook generation strategy."""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Any, Dict, List

import yaml

from genlab_core.strategies import HookStrategy

logger = logging.getLogger(__name__)

NICHE_ROOT = Path(__file__).resolve().parent.parent


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


class SportHookStrategy(HookStrategy):
    """Generate hooks using template formulas with placeholder substitution."""

    def __init__(self) -> None:
        logger.info("[sports] SportHookStrategy initialized")
        self._formulas: List[str] | None = None
        self._forbidden: List[str] | None = None

    def _ensure_config(self) -> None:
        if self._formulas is not None:
            return
        templates = _load_yaml(NICHE_ROOT / "config" / "templates.yaml")
        hooks = templates.get("hooks", {})
        self._formulas = hooks.get("formulas", [])
        self._forbidden = hooks.get("forbidden_styles", [])

    def generate_hook(self, item: Dict[str, Any]) -> str:
        """Pick a formula and substitute placeholders."""
        self._ensure_config()

        if not self._formulas:
            return item.get("title", "")

        hook = random.choice(self._formulas)
        hook = hook.replace("{team}", item.get("team", "this team"))
        hook = hook.replace("{player}", item.get("player", "this player"))
        hook = hook.replace("{sport}", item.get("sport", "sports"))
        hook = hook.replace("{play_type}", item.get("play_type", "play"))
        return hook

    def execute(self, context: Any) -> Any:
        """Generate hooks for all stories."""
        self._ensure_config()
        stories = context.get("stories", [])

        for story in stories:
            hook = self.generate_hook(story)
            story.setdefault("content", {})["hook"] = hook

        context.setdefault("run_stats", {})["hooks"] = {
            "total": len(stories),
        }
        logger.info("[sports] HookStrategy: generated %d hooks", len(stories))
        return context
```

Movies hooks — same pattern with `{film}`, `{director}`, `{scene}` placeholders:

```python
# niches/movies/strategies/hooks.py (replace stub)
"""SpliceReel hook generation strategy."""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Any, Dict, List

import yaml

from genlab_core.strategies import HookStrategy

logger = logging.getLogger(__name__)

NICHE_ROOT = Path(__file__).resolve().parent.parent


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


class MovieHookStrategy(HookStrategy):
    """Generate hooks using template formulas with placeholder substitution."""

    def __init__(self) -> None:
        logger.info("[movies] MovieHookStrategy initialized")
        self._formulas: List[str] | None = None

    def _ensure_config(self) -> None:
        if self._formulas is not None:
            return
        templates = _load_yaml(NICHE_ROOT / "config" / "templates.yaml")
        hooks = templates.get("hooks", {})
        self._formulas = hooks.get("formulas", [])

    def generate_hook(self, item: Dict[str, Any]) -> str:
        self._ensure_config()
        if not self._formulas:
            return item.get("title", "")

        hook = random.choice(self._formulas)
        hook = hook.replace("{film}", item.get("title", "this film"))
        hook = hook.replace("{director}", item.get("director", "the director"))
        hook = hook.replace("{scene}", item.get("scene", "this scene"))
        return hook

    def execute(self, context: Any) -> Any:
        self._ensure_config()
        stories = context.get("stories", [])

        for story in stories:
            hook = self.generate_hook(story)
            story.setdefault("content", {})["hook"] = hook

        context.setdefault("run_stats", {})["hooks"] = {"total": len(stories)}
        logger.info("[movies] HookStrategy: generated %d hooks", len(stories))
        return context
```

**Step 3: Run existing tests**

Run: `cd /Users/anarchistsid/GenLab && uv run --package CriticalRush pytest CriticalRush/niches/sports/tests/test_hooks.py CriticalRush/niches/movies/tests/test_hooks.py -v`
Expected: PASS (all existing tests)

**Step 4: Commit**

```bash
git add niches/sports/strategies/hooks.py niches/movies/strategies/hooks.py
git commit -m "feat(hooks): implement sports and movies hook strategies"
```

---

## Task 5: Niche-Specific Hook Validators

Each niche needs a hook validator wrapping genlab_core's universal HookValidator, following the gaming pattern.

**Files:**
- Create: `CriticalRush/niches/sports/hooks/hook_validator.py`
- Create: `CriticalRush/niches/movies/hooks/hook_validator.py`
- Create: `CriticalRush/niches/anime/hooks/hook_validator.py`

**Step 1: Write implementations**

All 3 follow the gaming `GamingHookValidator` pattern (universal rules + niche forbidden styles from templates.yaml). Key differences:
- Sports: Forbidden styles include sports-specific news language
- Movies: Forbidden styles include "spoiler alert", "must-see"
- Anime: Forbidden styles include "weeb", "cringe", "mid"

Each validator loads forbidden_styles from its niche's `templates.yaml` and checks against them dynamically, plus the universal rules.

```python
# niches/sports/hooks/hook_validator.py
"""ClutchWire hook validator — universal + sports-specific rules."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

from genlab_core.intelligence.hook_validator import HookFailure, HookValidator

MAX_HOOK_LENGTH = 150
MIN_HOOK_LENGTH = 20

_WEAK_OPENERS = re.compile(
    r"^(?:So |Well |Okay so |Um |Uh |Like |Basically |Honestly |"
    r"I mean |You know |Look |Listen )",
    re.IGNORECASE,
)
_NEWS_PREFIXES = re.compile(
    r"^(?:BREAKING:|JUST IN:|ALERT:|URGENT:|EXCLUSIVE:|REPORT:)\s*",
    re.IGNORECASE,
)
_MARKDOWN_STRIP = re.compile(r"\*\*|__|[*_]|^#{1,6}\s+", re.MULTILINE)


@dataclass
class NicheHookResult:
    hook: str
    passed: bool = True
    universal_failures: list[HookFailure] = field(default_factory=list)
    niche_failures: list[str] = field(default_factory=list)

    @property
    def all_issues(self) -> List[str]:
        return [f.value for f in self.universal_failures] + self.niche_failures


class SportHookValidator:
    def __init__(self) -> None:
        self._universal = HookValidator()

    def validate(self, hook: str, platform: str = "instagram") -> NicheHookResult:
        result = NicheHookResult(hook=hook)
        universal = self._universal.validate(hook, platform)
        if not universal.passed:
            result.universal_failures = universal.failures
        if _WEAK_OPENERS.match(hook):
            result.niche_failures.append("weak_opener")
        if _NEWS_PREFIXES.match(hook):
            result.niche_failures.append("news_prefix")
        if len(hook) > MAX_HOOK_LENGTH:
            result.niche_failures.append("too_long")
        elif len(hook) < MIN_HOOK_LENGTH:
            result.niche_failures.append("too_short")
        result.passed = not result.universal_failures and not result.niche_failures
        return result

    def clean(self, hook: str) -> str:
        cleaned = _MARKDOWN_STRIP.sub("", hook)
        cleaned = _NEWS_PREFIXES.sub("", cleaned)
        cleaned = _WEAK_OPENERS.sub("", cleaned)
        return re.sub(r"\s{2,}", " ", cleaned).strip()
```

Movies and anime validators follow the same pattern with niche-appropriate forbidden words loaded from `niche.yaml`.

**Step 2: Commit**

```bash
git add niches/sports/hooks/hook_validator.py niches/movies/hooks/hook_validator.py niches/anime/hooks/hook_validator.py
git commit -m "feat(hooks): add niche-specific hook validators for sports, movies, anime"
```

---

## Task 6: Writing Strategies (All 3 Niches)

LLM-powered content generation using Anthropic Haiku, following the gaming `WriteGamingContent` pattern.

**Files:**
- Modify: `CriticalRush/niches/sports/strategies/writing.py`
- Modify: `CriticalRush/niches/movies/strategies/writing.py`
- Modify: `CriticalRush/niches/anime/strategies/writing.py`
- Test: `CriticalRush/niches/sports/tests/test_writing.py`
- Test: `CriticalRush/niches/movies/tests/test_writing.py`
- Test: `CriticalRush/niches/anime/tests/test_writing.py`

**Step 1: Write the failing tests**

```python
# niches/sports/tests/test_writing.py
"""Tests for ClutchWire writing strategy."""

from __future__ import annotations

from unittest.mock import patch, MagicMock
import json

import pytest


class TestSportWritingStrategy:
    def test_execute_generates_content_for_stories(self):
        from niches.sports.strategies.writing import SportWritingStrategy

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps({
            "hook": "Nobody saw this coming",
            "instagram": {"caption": "Game changer moment", "hashtags": ["#sports"]},
            "youtube": {"title": "Best Play of the Year?", "description": "Incredible play."},
            "x_twitter": {"tweet": "This play changed everything", "hashtags": ["#sports"]},
            "facebook": {"caption": "Did you see this? What a moment!"},
            "tiktok": {"caption": "Nobody saw this coming", "hashtags": ["#sports"]},
            "threads": {"caption": "This changed the game."},
        }))]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("niches.sports.strategies.writing.anthropic.Anthropic", return_value=mock_client), \
             patch("niches.sports.strategies.writing.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"
            strategy = SportWritingStrategy()
            context = {
                "stories": [{"title": "NBA Finals", "summary": "Amazing game"}],
                "run_stats": {},
            }
            result = strategy.execute(context)

        assert result["stories"][0]["content"]["hook"] == "Nobody saw this coming"
        assert "instagram" in result["stories"][0]["content"]

    def test_execute_skips_when_no_api_key(self):
        from niches.sports.strategies.writing import SportWritingStrategy

        with patch("niches.sports.strategies.writing.settings") as mock_settings:
            mock_settings.anthropic_api_key = ""
            strategy = SportWritingStrategy()
            context = {"stories": [{"title": "Test"}], "run_stats": {}}
            result = strategy.execute(context)

        assert result["run_stats"]["content_writing"]["status"] == "skipped_no_api_key"

    def test_fallback_on_llm_failure(self):
        from niches.sports.strategies.writing import SportWritingStrategy

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("LLM error")

        with patch("niches.sports.strategies.writing.anthropic.Anthropic", return_value=mock_client), \
             patch("niches.sports.strategies.writing.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"
            strategy = SportWritingStrategy()
            context = {
                "stories": [{"title": "NBA Finals", "summary": "Amazing game"}],
                "run_stats": {},
            }
            result = strategy.execute(context)

        # Should have fallback content
        assert result["stories"][0].get("content") is not None
```

Movies and anime test files follow the same pattern with niche-appropriate class names and mock data.

**Step 2: Write implementations**

Each writing strategy follows the gaming `WriteGamingContent` pattern:
1. Check for ANTHROPIC_API_KEY
2. Load `templates.yaml` for hook formulas, CTAs, hashtag pool
3. For each story: build niche-specific prompt → call Haiku → parse JSON → validate hook → store in `story["content"]`
4. On failure: use template-based fallback

Key per-niche differences in the prompt:
- **Sports**: "You are writing for ClutchWire, a sports social media brand with an urgent, electric, insider voice."
- **Movies**: "You are writing for SpliceReel, a cinema social media brand with a warm, curious, slightly reverent voice."
- **Anime**: "You are writing for FrameDrift, an anime social media brand with a passionate, community-insider, craft-focused voice." + note about sakuga-aware content

Each prompt uses the same JSON output structure as gaming (6 platforms).

**Step 3: Run tests**

Run: `cd /Users/anarchistsid/GenLab && uv run --package CriticalRush pytest CriticalRush/niches/sports/tests/test_writing.py CriticalRush/niches/movies/tests/test_writing.py CriticalRush/niches/anime/tests/test_writing.py -v`
Expected: PASS

**Step 4: Commit**

```bash
git add niches/sports/strategies/writing.py niches/movies/strategies/writing.py niches/anime/strategies/writing.py \
        niches/sports/tests/test_writing.py niches/movies/tests/test_writing.py niches/anime/tests/test_writing.py
git commit -m "feat(writing): implement LLM writing strategies for sports, movies, anime"
```

---

## Task 7: Visual Render Passthrough

Convert stubs from no-ops to logging passthroughs that record run_stats but skip actual rendering.

**Files:**
- Modify: `CriticalRush/niches/sports/strategies/visual_render.py`
- Modify: `CriticalRush/niches/movies/strategies/visual_render.py`
- Modify: `CriticalRush/niches/anime/strategies/visual_render.py`

**Step 1: Update implementations**

Each visual_render strategy becomes a passthrough that:
- Logs "[niche] VisualRenderStrategy: skipping (no clip sources configured)"
- Records `run_stats["render"] = {"status": "passthrough", "stories_count": N}`
- Returns context unchanged

This is intentional — visual rendering requires niche-specific clip sourcing (YouTube/Pexels/niche APIs) which is a separate body of work.

**Step 2: Run all niche tests**

Run: `cd /Users/anarchistsid/GenLab && uv run --package CriticalRush pytest CriticalRush/niches/sports/tests/ CriticalRush/niches/movies/tests/ CriticalRush/niches/anime/tests/ -v`
Expected: PASS (all tests)

**Step 3: Commit**

```bash
git add niches/sports/strategies/visual_render.py niches/movies/strategies/visual_render.py niches/anime/strategies/visual_render.py
git commit -m "feat(render): convert visual_render stubs to logging passthroughs"
```

---

## Task 8: Pipeline Runner Niche Dispatch

Wire `_load_stages()` to dispatch per-niche stage lists.

**Files:**
- Modify: `CriticalRush/core/pipeline_runner.py` (lines 133-173)

**Step 1: Write the failing test**

```python
# tests/core/test_pipeline_dispatch.py
"""Tests for per-niche pipeline stage dispatch."""

from __future__ import annotations

import pytest


class TestNicheDispatch:
    def test_gaming_loads_gaming_stages(self):
        from core.pipeline_runner import PipelineRunner

        runner = PipelineRunner()
        stages = runner._load_stages("gaming", {})
        class_names = [s.__class__.__name__ for s in stages]
        assert "FetchGamingStories" in class_names

    def test_sports_loads_sports_stages(self):
        from core.pipeline_runner import PipelineRunner

        runner = PipelineRunner()
        stages = runner._load_stages("sports", {})
        class_names = [s.__class__.__name__ for s in stages]
        assert "FetchSportsStories" in class_names
        assert "SportScoringStrategy" in class_names
        assert "FetchGamingStories" not in class_names

    def test_movies_loads_movies_stages(self):
        from core.pipeline_runner import PipelineRunner

        runner = PipelineRunner()
        stages = runner._load_stages("movies", {})
        class_names = [s.__class__.__name__ for s in stages]
        assert "FetchMovieStories" in class_names

    def test_anime_loads_anime_stages(self):
        from core.pipeline_runner import PipelineRunner

        runner = PipelineRunner()
        stages = runner._load_stages("anime", {})
        class_names = [s.__class__.__name__ for s in stages]
        assert "FetchAnimeStories" in class_names

    def test_unknown_niche_raises(self):
        from core.pipeline_runner import PipelineRunner

        runner = PipelineRunner()
        with pytest.raises(ValueError, match="Unsupported niche"):
            runner._load_stages("unknown", {})
```

**Step 2: Write implementation**

Replace the hardcoded `_load_stages()` with per-niche dispatch:

```python
def _load_stages(self, niche_id: str, config: Dict[str, Any]) -> List[Any]:
    if niche_id == "gaming":
        return self._load_gaming_stages()
    elif niche_id == "sports":
        return self._load_sports_stages()
    elif niche_id == "movies":
        return self._load_movies_stages()
    elif niche_id == "anime":
        return self._load_anime_stages()
    else:
        raise ValueError(f"Unsupported niche: {niche_id}")

def _load_gaming_stages(self) -> List[Any]:
    # Existing 13-stage gaming pipeline (unchanged)
    from niches.gaming.stages.fetch_gaming_stories import FetchGamingStories
    # ... all existing imports ...
    return [FetchGamingStories(), ...]

def _load_sports_stages(self) -> List[Any]:
    from niches.sports.stages.fetch_sports_stories import FetchSportsStories
    from niches.sports.strategies.scoring import SportScoringStrategy
    from niches.sports.strategies.hooks import SportHookStrategy
    from niches.sports.strategies.writing import SportWritingStrategy
    from niches.sports.strategies.platform_adaptation import SportPlatformAdaptationStrategy
    from niches.sports.strategies.visual_render import SportVisualRenderStrategy
    return [
        FetchSportsStories(),
        SportScoringStrategy(),
        SportHookStrategy(),
        SportWritingStrategy(),
        SportPlatformAdaptationStrategy(),
        SportVisualRenderStrategy(),
    ]

# _load_movies_stages and _load_anime_stages follow same pattern
```

**Step 3: Run tests**

Run: `cd /Users/anarchistsid/GenLab && uv run --package CriticalRush pytest CriticalRush/tests/core/test_pipeline_dispatch.py -v`
Expected: PASS

**Step 4: Commit**

```bash
git add core/pipeline_runner.py tests/core/test_pipeline_dispatch.py
git commit -m "feat(pipeline): add per-niche stage dispatch in _load_stages()"
```

---

## Task 9: Dry-Run Validation

Test `pipeline_runner.py --niche sports --dry-run` end-to-end.

**Files:**
- Modify: `CriticalRush/core/pipeline_runner.py` (update `_print_dry_run_summary` for non-gaming niches)

**Step 1: Update dry-run summary**

The current `_print_dry_run_summary` references `steam_count`, `twitch_count` etc. which are gaming-specific. Update to use generic stats:

```python
@staticmethod
def _print_dry_run_summary(ctx: PipelineContext) -> None:
    print(f"\n{'=' * 60}")
    print(f"  DRY RUN SUMMARY — {ctx.run_id}")
    print(f"{'=' * 60}")

    stats = ctx.run_stats
    fetch = stats.get("fetch", {})
    print(f"\n  Stories fetched: {fetch.get('total_count', 0)}")
    print(f"    RSS: {fetch.get('rss_count', 0)}")
    print(f"    Reddit: {fetch.get('reddit_count', 0)}")

    scoring = stats.get("scoring", {})
    if scoring:
        print(f"  Scored: {scoring.get('scored_count', 0)} "
              f"(dropped {scoring.get('dropped_count', 0)})")

    hooks = stats.get("hooks", {})
    if hooks:
        print(f"  Hooks generated: {hooks.get('total', 0)}")

    writing = stats.get("content_writing", {})
    if writing:
        print(f"  Content written: {writing.get('written_count', 0)} "
              f"(failed: {writing.get('failed_count', 0)})")

    print(f"\n{'=' * 60}\n")
```

**Step 2: Run dry-run manually**

Run: `cd /Users/anarchistsid/GenLab/CriticalRush && uv run --package CriticalRush python -m core.pipeline_runner --niche sports --dry-run`

Expected output: Dry run summary showing fetch stats, scoring stats, hooks generated, content writing stats. No actual publishing.

**Step 3: Run full test suite to check for regressions**

Run: `cd /Users/anarchistsid/GenLab && uv run --package CriticalRush pytest CriticalRush/ -v --tb=short -q`
Expected: All existing + new tests pass

**Step 4: Commit**

```bash
git add core/pipeline_runner.py
git commit -m "feat(pipeline): update dry-run summary for multi-niche support"
```

---

## Task 10: Final Validation & Commit

**Step 1: Run all tests across all packages**

```bash
cd /Users/anarchistsid/GenLab
uv run --package CriticalRush pytest CriticalRush/ -v --tb=short -q
uv run --package genlab-core pytest genlab-core/tests/ -v --tb=short -q
```

**Step 2: Verify all imports clean**

```bash
cd /Users/anarchistsid/GenLab
uv run --package CriticalRush python -c "
from niches.sports.strategies import *
from niches.movies.strategies import *
from niches.anime.strategies import *
from niches.sports.stages.fetch_sports_stories import FetchSportsStories
from niches.movies.stages.fetch_movie_stories import FetchMovieStories
from niches.anime.stages.fetch_anime_stories import FetchAnimeStories
from core.niche_fetcher import NicheFetcher
print('All imports clean')
"
```

**Step 3: Final commit**

```bash
git add -A
git commit -m "feat(niches): complete strategy implementations and source fetchers

- Shared NicheFetcher: RSS (feedparser) + Reddit (JSON API) + dedup
- Scoring: movies (72h half-life), anime (24h + sakuga boost)
- Hooks: sports + movies template substitution
- Writing: all 3 niches via Anthropic Haiku with niche brand voice
- Hook validators: sports, movies, anime (universal + niche rules)
- Pipeline dispatch: per-niche _load_stages()
- Dry-run summary: multi-niche compatible
"
```
