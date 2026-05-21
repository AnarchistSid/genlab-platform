"""Tests for genlab_core.media.video_sourcer."""

from __future__ import annotations

from datetime import UTC, datetime

from genlab_core.media.video_sourcer import (
    VideoSearchResult,
    VideoSourcer,
    is_direct_video_url,
    parse_iso_duration,
    score_video_result,
)


# ---------------------------------------------------------------
# is_direct_video_url
# ---------------------------------------------------------------
class TestIsDirectVideoUrl:
    def test_youtube_watch(self):
        assert is_direct_video_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_youtube_short_link(self):
        assert is_direct_video_url("https://youtu.be/dQw4w9WgXcQ")

    def test_youtube_shorts(self):
        assert is_direct_video_url("https://www.youtube.com/shorts/abc123")

    def test_reddit_vreddit(self):
        assert is_direct_video_url("https://v.redd.it/abc123def")

    def test_reddit_comments(self):
        assert is_direct_video_url("https://www.reddit.com/r/gaming/comments/abc123/cool_clip")

    def test_vimeo(self):
        assert is_direct_video_url("https://vimeo.com/123456789")

    def test_tiktok(self):
        assert is_direct_video_url("https://www.tiktok.com/@user.name/video/7123456789")

    def test_tiktok_short(self):
        assert is_direct_video_url("https://vm.tiktok.com/ZMeabc123/")

    def test_twitter(self):
        assert is_direct_video_url("https://twitter.com/user/status/123456789")

    def test_x_dot_com(self):
        assert is_direct_video_url("https://x.com/user/status/123456789")

    def test_not_video_news_site(self):
        assert not is_direct_video_url("https://www.bbc.com/news/technology-12345")

    def test_not_video_wikipedia(self):
        assert not is_direct_video_url("https://en.wikipedia.org/wiki/AI")

    def test_not_video_empty(self):
        assert not is_direct_video_url("")

    def test_not_video_none(self):
        assert not is_direct_video_url(None)  # type: ignore[arg-type]

    def test_not_video_plain_text(self):
        assert not is_direct_video_url("just some text")

    def test_twitch_clip_page(self):
        assert is_direct_video_url("https://clips.twitch.tv/FunnyClipName-abc123")

    def test_twitch_cdn_mp4(self):
        assert is_direct_video_url("https://clips-media-assets2.twitch.tv/AT-cm%7C12345.mp4")

    def test_direct_mp4_url(self):
        assert is_direct_video_url("https://cdn.example.com/videos/clip.mp4")

    def test_direct_webm_url(self):
        assert is_direct_video_url("https://cdn.example.com/videos/clip.webm")

    def test_streamable(self):
        assert is_direct_video_url("https://streamable.com/abc123")


# ---------------------------------------------------------------
# parse_iso_duration
# ---------------------------------------------------------------
class TestParseIsoDuration:
    def test_full_hms(self):
        assert parse_iso_duration("PT1H2M3S") == 3723.0

    def test_minutes_seconds(self):
        assert parse_iso_duration("PT5M30S") == 330.0

    def test_seconds_only(self):
        assert parse_iso_duration("PT45S") == 45.0

    def test_hours_only(self):
        assert parse_iso_duration("PT2H") == 7200.0

    def test_empty_string(self):
        assert parse_iso_duration("") == 0.0

    def test_invalid(self):
        assert parse_iso_duration("not a duration") == 0.0

    def test_lowercase(self):
        assert parse_iso_duration("pt10m5s") == 605.0


# ---------------------------------------------------------------
# score_video_result
# ---------------------------------------------------------------
class TestScoreVideoResult:
    def test_relevance_similar_titles_score_higher(self):
        """Two results with different title similarity should score differently."""
        now = datetime.now(tz=UTC)
        similar = VideoSearchResult(
            url="https://youtube.com/watch?v=1",
            title="OpenAI launches GPT-5 model",
            duration_seconds=60,
            view_count=100_000,
            published_at=now,
            backend="youtube",
        )
        dissimilar = VideoSearchResult(
            url="https://youtube.com/watch?v=2",
            title="Cooking pasta carbonara recipe",
            duration_seconds=60,
            view_count=100_000,
            published_at=now,
            backend="youtube",
        )
        story_title = "OpenAI releases GPT-5 with major improvements"
        s1 = score_video_result(similar, story_title, now)
        s2 = score_video_result(dissimilar, story_title, now)
        assert s1 > s2, f"Similar ({s1:.3f}) should outscore dissimilar ({s2:.3f})"

    def test_duration_fit_60s_beats_3600s(self):
        """A 60-second video should score higher on duration than a 1-hour video."""
        now = datetime.now(tz=UTC)
        short = VideoSearchResult(
            url="https://youtube.com/watch?v=1",
            title="Test video",
            duration_seconds=60,
            view_count=1000,
            published_at=now,
            backend="youtube",
        )
        long = VideoSearchResult(
            url="https://youtube.com/watch?v=2",
            title="Test video",
            duration_seconds=3600,
            view_count=1000,
            published_at=now,
            backend="youtube",
        )
        s_short = score_video_result(short, "Test video", now)
        s_long = score_video_result(long, "Test video", now)
        assert s_short > s_long, f"60s ({s_short:.3f}) should outscore 3600s ({s_long:.3f})"

    def test_score_in_zero_one_range(self):
        result = VideoSearchResult(
            url="https://youtube.com/watch?v=1",
            title="Some video",
            duration_seconds=60,
            view_count=500_000,
            published_at=datetime.now(tz=UTC),
            backend="youtube",
        )
        score = score_video_result(result, "Some video")
        assert 0.0 <= score <= 1.0

    def test_high_view_count_boosts_score(self):
        now = datetime.now(tz=UTC)
        popular = VideoSearchResult(
            url="u1",
            title="AI news",
            duration_seconds=60,
            view_count=5_000_000,
            published_at=now,
            backend="youtube",
        )
        unpopular = VideoSearchResult(
            url="u2",
            title="AI news",
            duration_seconds=60,
            view_count=10,
            published_at=now,
            backend="youtube",
        )
        s1 = score_video_result(popular, "AI news", now)
        s2 = score_video_result(unpopular, "AI news", now)
        assert s1 > s2


# ---------------------------------------------------------------
# VideoSourcer init
# ---------------------------------------------------------------
class TestVideoSourcerInit:
    def test_constructor_sets_niche_id(self):
        vs = VideoSourcer(niche_id="gaming", niche_keywords=["fps", "rpg"])
        assert vs.niche_id == "gaming"

    def test_constructor_sets_keywords(self):
        vs = VideoSourcer(niche_id="ai_tech", niche_keywords=["LLM", "GPT"])
        assert vs.niche_keywords == ["LLM", "GPT"]

    def test_default_min_score(self):
        vs = VideoSourcer(niche_id="movies")
        assert vs.min_score == 0.3

    def test_custom_min_score(self):
        vs = VideoSourcer(niche_id="movies", min_score=0.5)
        assert vs.min_score == 0.5

    def test_default_max_results(self):
        vs = VideoSourcer(niche_id="sports")
        assert vs.max_results == 5

    def test_stats_initially_zero(self):
        vs = VideoSourcer(niche_id="anime")
        stats = vs.get_stats()
        assert all(v == 0 for v in stats.values())


# ---------------------------------------------------------------
# find_video_for_story — direct URL
# ---------------------------------------------------------------
class TestFindVideoDirectUrl:
    def test_story_with_youtube_url_returns_direct(self):
        """A story whose url field is a YouTube link should return immediately
        as a direct_url result without needing API keys."""
        vs = VideoSourcer(
            niche_id="ai_tech",
            niche_keywords=["AI"],
            youtube_api_key="",  # no key — proves no search is attempted
        )
        story = {
            "title": "New AI breakthrough",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        }
        result = vs.find_video_for_story(story)
        assert result is not None
        assert result.backend == "direct_url"
        assert "youtube.com" in result.url

    def test_story_with_video_url_field(self):
        vs = VideoSourcer(niche_id="gaming")
        story = {
            "title": "Epic play",
            "video_url": "https://v.redd.it/abc123def",
        }
        result = vs.find_video_for_story(story)
        assert result is not None
        assert result.backend == "direct_url"
        assert "v.redd.it" in result.url

    def test_story_without_video_url_and_no_api_key_returns_none(self, monkeypatch):
        """With no direct URL and no API keys, all backends fail → None."""
        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
        monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
        vs = VideoSourcer(
            niche_id="ai_tech",
            youtube_api_key="",
        )
        story = {
            "title": "Some AI news",
            "url": "https://www.bbc.com/news/tech-12345",
        }
        result = vs.find_video_for_story(story)
        assert result is None

    def test_direct_url_increments_stats(self):
        vs = VideoSourcer(niche_id="ai_tech")
        story = {"title": "X", "url": "https://youtu.be/abc123"}
        vs.find_video_for_story(story)
        assert vs.get_stats()["direct_url"] == 1

    def test_none_result_increments_none_stats(self, monkeypatch):
        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
        monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
        vs = VideoSourcer(niche_id="anime", youtube_api_key="")
        story = {"title": "Anime news", "url": "https://example.com/article"}
        vs.find_video_for_story(story)
        assert vs.get_stats()["none"] == 1

    def test_story_with_clip_url_field(self):
        """Twitch clips set _clip_url with a clip page URL (yt-dlp downloads natively)."""
        vs = VideoSourcer(niche_id="gaming", youtube_api_key="")
        story = {
            "title": "Insane Fortnite clip",
            "_clip_url": "https://www.twitch.tv/streamer/clip/FunnyClipName-abc123",
            "source_url": "https://www.twitch.tv/streamer/clip/FunnyClipName-abc123",
        }
        result = vs.find_video_for_story(story)
        assert result is not None
        assert result.backend == "direct_url"
        assert "twitch.tv" in result.url
