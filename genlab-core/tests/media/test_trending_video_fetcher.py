"""Tests for TrendingVideoFetcher and FetchTrendingVideos pipeline stage."""
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from genlab_core.media.trending_video_fetcher import (
    MIN_VIEW_VELOCITY,
    NICHE_SEARCH_KEYWORDS,
    YOUTUBE_CATEGORIES,
    FetchTrendingVideos,
    TrendingVideo,
    TrendingVideoFetcher,
)


def _make_fetcher():
    return TrendingVideoFetcher(api_key="test_key")


def _make_api_video_item(
    video_id="abc123", age_hours=2, view_count=10000, duration="PT2M30S",
):
    published = (
        datetime.now(UTC) - timedelta(hours=age_hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "id": video_id,
        "snippet": {
            "title": f"Test Video {video_id}",
            "channelTitle": "Test Channel",
            "channelId": "UC123",
            "publishedAt": published,
            "description": "A test video description",
            "tags": ["gaming", "clip"],
            "thumbnails": {"high": {"url": "https://thumb.url/img.jpg"}},
        },
        "statistics": {
            "viewCount": str(view_count),
            "likeCount": "500",
        },
        "contentDetails": {
            "duration": duration,
            "licensedContent": True,
        },
        "status": {"license": "youtube"},
    }


class TestParseDuration:
    def test_minutes_and_seconds(self):
        assert _make_fetcher()._parse_duration("PT4M33S") == 273

    def test_hours_only(self):
        assert _make_fetcher()._parse_duration("PT1H") == 3600

    def test_seconds_only(self):
        assert _make_fetcher()._parse_duration("PT30S") == 30

    def test_empty_string(self):
        assert _make_fetcher()._parse_duration("") == 0

    def test_full_hms(self):
        assert _make_fetcher()._parse_duration("PT1H2M3S") == 3723


class TestParseVideo:
    def test_computes_view_velocity(self):
        f = _make_fetcher()
        item = _make_api_video_item(age_hours=2, view_count=10000)
        video = f._parse_video(item, "test")
        assert video is not None
        # 10000 views / ~2 hours ≈ 5000 views/hour
        assert 4000 < video.view_velocity < 6000

    def test_parses_duration(self):
        f = _make_fetcher()
        item = _make_api_video_item(duration="PT3M45S")
        video = f._parse_video(item, "test")
        assert video is not None
        assert video.duration_seconds == 225

    def test_download_url_format(self):
        f = _make_fetcher()
        item = _make_api_video_item("xyz789")
        video = f._parse_video(item, "test")
        assert video.download_url == "https://www.youtube.com/watch?v=xyz789"

    def test_official_channel_detection(self):
        f = _make_fetcher()
        item = _make_api_video_item()
        item["snippet"]["channelTitle"] = "ESPN Official"
        video = f._parse_video(item, "test")
        assert video.is_official_channel is True

    def test_regular_channel_not_official(self):
        f = _make_fetcher()
        item = _make_api_video_item()
        item["snippet"]["channelTitle"] = "Random Gamer"
        video = f._parse_video(item, "test")
        assert video.is_official_channel is False


class TestFetchTrending:
    def test_filters_by_duration(self):
        f = _make_fetcher()
        short = f._parse_video(_make_api_video_item("short", duration="PT10S"), "test")
        long_v = f._parse_video(_make_api_video_item("long", duration="PT10M"), "test")
        good = f._parse_video(_make_api_video_item("good", duration="PT2M30S"), "test")

        with patch.object(f, "_fetch_most_popular", return_value=[short, long_v, good]), \
             patch.object(f, "_search_recent", return_value=[]), \
             patch.object(f, "_fetch_video_details", return_value=[short, long_v, good]):
            results = f.fetch_trending("gaming", limit=10, min_velocity=0)

        ids = [v.video_id for v in results]
        assert "good" in ids
        assert "short" not in ids
        assert "long" not in ids

    def test_filters_by_velocity(self):
        f = _make_fetcher()
        # Low velocity: 100 views / 10 hours = 10 views/hr
        slow = f._parse_video(
            _make_api_video_item("slow", age_hours=10, view_count=100, duration="PT2M"),
            "test",
        )
        # High velocity: 50000 views / 1 hour = 50000 views/hr
        fast = f._parse_video(
            _make_api_video_item("fast", age_hours=1, view_count=50000, duration="PT2M"),
            "test",
        )

        with patch.object(f, "_fetch_most_popular", return_value=[slow, fast]), \
             patch.object(f, "_search_recent", return_value=[]), \
             patch.object(f, "_fetch_video_details", return_value=[slow, fast]):
            results = f.fetch_trending("gaming", limit=10, min_velocity=500)

        ids = [v.video_id for v in results]
        assert "fast" in ids
        assert "slow" not in ids

    def test_deduplicates_by_video_id(self):
        f = _make_fetcher()
        v1 = f._parse_video(
            _make_api_video_item("dup", view_count=50000, duration="PT2M"), "test",
        )
        v2 = f._parse_video(
            _make_api_video_item("dup", view_count=50000, duration="PT2M"), "test",
        )

        with patch.object(f, "_fetch_most_popular", return_value=[v1]), \
             patch.object(f, "_search_recent", return_value=[v2]), \
             patch.object(f, "_fetch_video_details", return_value=[v1]):
            results = f.fetch_trending("gaming", limit=10, min_velocity=0)

        assert len([v for v in results if v.video_id == "dup"]) == 1


class TestNicheConfig:
    def test_all_niches_have_keywords(self):
        for niche in ["gaming", "sports", "movies", "anime", "ai_creators"]:
            assert niche in NICHE_SEARCH_KEYWORDS
            assert len(NICHE_SEARCH_KEYWORDS[niche]) >= 3

    def test_niche_categories_defined(self):
        for niche in ["gaming", "sports", "movies", "ai_creators"]:
            assert niche in YOUTUBE_CATEGORIES

    def test_velocity_thresholds_defined(self):
        for niche in ["gaming", "sports", "movies", "anime", "ai_creators"]:
            assert niche in MIN_VIEW_VELOCITY
            assert MIN_VIEW_VELOCITY[niche] > 0


class TestTrendingVideoToStory:
    def test_to_story_produces_valid_story_dict(self):
        video = TrendingVideo(
            video_id="abc123",
            title="Amazing Gaming Clip",
            channel_name="ProGamer",
            channel_id="UC123",
            published_at=datetime.now(UTC),
            view_count=50000,
            like_count=2000,
            duration_seconds=120,
            thumbnail_url="https://thumb.url",
            niche_id="gaming",
            search_query="gaming highlights",
            view_velocity=5000.0,
            download_url="https://www.youtube.com/watch?v=abc123",
            is_official_channel=False,
            license="youtube",
        )
        story = video.to_story()

        assert story["story_id"]  # non-empty
        assert story["title"] == "Amazing Gaming Clip"
        assert story["source"] == "youtube_trending"
        assert story["video_source"] == "trending"
        assert story["_trending_video"] is True
        assert story["view_velocity"] == 5000.0

    def test_to_dict_roundtrip(self):
        video = TrendingVideo(
            video_id="xyz",
            title="Test",
            channel_name="Ch",
            channel_id="UC",
            published_at=datetime(2026, 3, 14, tzinfo=UTC),
            view_count=1000,
            like_count=50,
            duration_seconds=60,
            thumbnail_url="",
            niche_id="gaming",
            search_query="test",
            view_velocity=100.0,
            download_url="https://youtube.com/watch?v=xyz",
            is_official_channel=False,
            license="youtube",
        )
        d = video.to_dict()
        assert d["video_id"] == "xyz"
        assert d["view_velocity"] == 100.0


class TestFetchTrendingVideosStage:
    def test_no_api_key_returns_context_unchanged(self):
        stage = FetchTrendingVideos()
        context = {"stories": [], "niche_id": "gaming", "niche_config": {}}

        with patch.dict("os.environ", {}, clear=True):
            result = stage.execute(context)

        assert result["stories"] == []
        assert result.get("run_stats", {}).get("trending_videos_found") == 0

    def test_prepends_video_stories(self):
        stage = FetchTrendingVideos()
        existing_story = {"story_id": "existing", "title": "RSS Story"}
        context = {
            "stories": [existing_story],
            "niche_id": "gaming",
            "niche_config": {"video_sourcing": {"top_n_per_run": 2}},
        }

        mock_video = TrendingVideo(
            video_id="trend1",
            title="Epic Gaming Gameplay Highlights",
            channel_name="Ch",
            channel_id="UC",
            published_at=datetime.now(UTC),
            view_count=50000,
            like_count=2000,
            duration_seconds=120,
            thumbnail_url="",
            niche_id="gaming",
            search_query="test",
            view_velocity=5000.0,
            download_url="https://youtube.com/watch?v=trend1",
            is_official_channel=False,
            license="youtube",
        )

        with patch.dict("os.environ", {"YOUTUBE_API_KEY": "fake"}), \
             patch.object(
                 TrendingVideoFetcher, "fetch_trending", return_value=[mock_video],
             ):
            result = stage.execute(context)

        # Trending video story should be first
        assert len(result["stories"]) == 2
        assert result["stories"][0]["video_source"] == "trending"
        assert result["stories"][1]["story_id"] == "existing"
        assert result["run_stats"]["trending_videos_found"] == 1
