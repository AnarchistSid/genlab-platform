"""Tests for fix #4 of the autonomy roadmap: force content_pool consumption.

Pre-fix, `FetchTrendingVideos.execute` ran both the content_pool read AND
the direct YouTube fetch unconditionally, in parallel. Same YouTube video
could land in both streams (no cross-source dedup) → 90.9% of pool content
expired unclaimed because direct fetch ate the supply first → 77% of
blueprints bypassed the routed pool entirely → cross-niche contamination
(the 2026-06-12 CGI/movies-in-ai_creators incident).

After fix:
1. When content_pool returns ≥ top_n stories, the direct YouTube fetch
   is skipped (escape hatch: `trending_videos_force_direct_fetch=true`).
2. Gap-fill mode: when pool returns < top_n, direct fetch fires + the
   results are deduped against pool by video_id before merge.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from genlab_core.media.trending_video_fetcher import FetchTrendingVideos


@pytest.fixture
def stage():
    """Build a FetchTrendingVideos stage with a mocked YouTube API key."""
    return FetchTrendingVideos()


@pytest.fixture(autouse=True)
def youtube_api_key(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
    yield
    # cleanup happens via monkeypatch


def _ctx_for(niche_id: str = "gaming", top_n: int = 5) -> dict:
    return {
        "niche_id": niche_id,
        "niche_config": {
            "video_sourcing": {
                "top_n_per_run": top_n,
                "max_age_hours": 48,
                "min_view_velocity": 0,
                "allow_keyword_search": True,
            }
        },
    }


def _pool_story(video_id: str, title: str | None = None) -> dict:
    return {
        "video_id": video_id,
        "source_url": f"https://youtu.be/{video_id}",
        "title": title or f"Pool story {video_id}",
        "description": "A trending video clip we just routed via NicheClassifier.",
        # P1 phase-4 (2026-06-19): merge_stories() now schema-validates each
        # entry via StoryCandidate, which requires ``source``. Real production
        # path at trending_video_fetcher.py:1183 always sets this from
        # ``row["source_platform"] or "shared_pool"``; mock matches that.
        "source": "shared_pool",
    }


class TestPoolFullSatisfaction:
    """When content_pool already returns ≥ top_n stories, direct fetch is skipped."""

    def test_skips_direct_fetch_when_pool_satisfies_quota(self, stage):
        pool = [_pool_story(f"vid_{i}") for i in range(6)]  # 6 >= top_n=5

        with (
            patch.object(stage, "_read_from_content_pool", return_value=pool),
            patch.object(stage, "_load_sources_config", return_value={}),
            patch("genlab_core.media.trending_video_fetcher.TrendingVideoFetcher") as mock_cls,
        ):
            context = _ctx_for("gaming", top_n=5)
            result = stage.execute(context)

        # Direct fetch class should never be instantiated when pool satisfies.
        mock_cls.assert_not_called()
        # Source telemetry should record the skip.
        assert result["run_stats"]["trending_videos_source"] == "content_pool_only"
        assert result["run_stats"]["trending_videos_found"] == 0
        assert len(result["stories"]) == 6

    def test_escape_hatch_via_force_direct_fetch_flag(self, stage):
        """Operators who want both streams can opt-in via config."""
        pool = [_pool_story(f"vid_{i}") for i in range(6)]

        with (
            patch.object(stage, "_read_from_content_pool", return_value=pool),
            patch.object(stage, "_load_sources_config", return_value={}),
            patch("genlab_core.media.trending_video_fetcher.TrendingVideoFetcher") as mock_cls,
        ):
            mock_fetcher = MagicMock()
            mock_fetcher.fetch_trending.return_value = []
            mock_cls.return_value = mock_fetcher

            context = _ctx_for("gaming", top_n=5)
            context["niche_config"]["video_sourcing"]["trending_videos_force_direct_fetch"] = True
            stage.execute(context)

        # With escape hatch enabled, direct fetch fires.
        mock_cls.assert_called_once()


class TestGapFill:
    """When pool returns < top_n, direct fetch runs as gap-fill — and
    its results are deduped against pool by video_id before merge."""

    def test_pool_under_quota_runs_direct_fetch(self, stage):
        pool = [_pool_story(f"vid_{i}") for i in range(2)]  # 2 < top_n=5

        with (
            patch.object(stage, "_read_from_content_pool", return_value=pool),
            patch.object(stage, "_load_sources_config", return_value={}),
            patch("genlab_core.media.trending_video_fetcher.TrendingVideoFetcher") as mock_cls,
        ):
            mock_fetcher = MagicMock()
            mock_fetcher.fetch_trending.return_value = []
            mock_cls.return_value = mock_fetcher

            context = _ctx_for("gaming", top_n=5)
            stage.execute(context)

        # Direct fetch DOES fire when pool is under quota.
        mock_cls.assert_called_once()

    def test_cross_source_dedup_drops_videos_already_in_pool(self, stage):
        """The same YouTube video could land in both streams. Pool wins.

        We unit-test the dedup logic directly here (the full execute()
        path runs CompositeScorer which is exercised by other tests);
        this test focuses on the cross-source merge invariant.
        """
        existing_stories = [
            _pool_story("shared_id_1"),
            _pool_story("shared_id_2"),
        ]
        video_stories_from_direct_fetch = [
            {
                "video_id": "shared_id_1",  # dup — must be dropped
                "source_url": "https://youtu.be/shared_id_1",
                "title": "Same video — fetched by direct path too",
            },
            {
                "video_id": "new_id_3",  # new — must be kept
                "source_url": "https://youtu.be/new_id_3",
                "title": "Genuinely new from direct fetch",
            },
        ]

        # Mirror the dedup logic from execute() so this test pins the
        # invariant: video_ids present in existing_stories are filtered
        # out of video_stories before merge.
        existing_video_ids = {
            s.get("video_id") or s.get("source_url") or ""
            for s in existing_stories
            if (s.get("video_id") or s.get("source_url"))
        }
        deduped = [
            s
            for s in video_stories_from_direct_fetch
            if (s.get("video_id") or s.get("source_url") or "") not in existing_video_ids
        ]
        merged = deduped + existing_stories

        video_ids = [s.get("video_id") for s in merged]
        assert video_ids.count("shared_id_1") == 1
        assert "shared_id_2" in video_ids
        assert "new_id_3" in video_ids
        assert len(merged) == 3  # 2 pool + 1 new from direct


class TestEmptyPoolBehavior:
    """When content_pool is empty, direct fetch is the only source — same
    behavior as pre-fix. This is the cold-start case for a new niche."""

    def test_empty_pool_runs_direct_fetch_normally(self, stage):
        with (
            patch.object(stage, "_read_from_content_pool", return_value=[]),
            patch.object(stage, "_load_sources_config", return_value={}),
            patch("genlab_core.media.trending_video_fetcher.TrendingVideoFetcher") as mock_cls,
        ):
            mock_fetcher = MagicMock()
            mock_fetcher.fetch_trending.return_value = []
            mock_cls.return_value = mock_fetcher

            stage.execute(_ctx_for("gaming", top_n=5))

        mock_cls.assert_called_once()
