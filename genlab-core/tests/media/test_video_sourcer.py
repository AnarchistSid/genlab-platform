"""Tests for genlab_core.media.video_sourcer."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

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

    def test_direct_mp4_url_on_allowlisted_cdn(self):
        # R-62: the .mp4 catch-all is gated on a host allowlist.
        # cloudfront.net is in _KNOWN_VIDEO_CDN_HOST_SUFFIXES.
        assert is_direct_video_url("https://cdn.cloudfront.net/videos/clip.mp4")

    def test_direct_webm_url_on_allowlisted_cdn(self):
        # akamaized.net is allow-listed.
        assert is_direct_video_url("https://media.akamaized.net/videos/clip.webm")

    def test_streamable(self):
        assert is_direct_video_url("https://streamable.com/abc123")

    # ------------------------------------------------------------------
    # R-62 regression pins — host-allowlist on the .mp4/.webm catch-all
    # ------------------------------------------------------------------

    def test_r62_rejects_mp4_on_untrusted_host(self):
        """R-62 — Reddit ``fallback_url`` was the historical SSRF
        vector. A .mp4 URL on a non-allowlisted host must be
        REJECTED so it never reaches yt-dlp."""
        assert not is_direct_video_url("https://cdn.example.com/videos/clip.mp4")

    def test_r62_rejects_mp4_on_private_ip(self):
        """Internal hostnames / private IPs must never pass — they
        were the dangerous case the catch-all enabled."""
        assert not is_direct_video_url("http://10.0.0.1/x.mp4")
        assert not is_direct_video_url("http://internal-server.local/secret.mp4")

    def test_r62_rejects_userinfo_spoof(self):
        """``http://evil.com@malicious.com/x.mp4`` — the ``evil.com``
        is the userinfo, NOT the host. A naive ``str.endswith`` check
        would miss this; ``urlsplit``'s ``hostname`` reads the real
        host (``malicious.com``), which is not allow-listed → reject."""
        assert not is_direct_video_url("http://evil.com@malicious.com/x.mp4")

    def test_r62_userinfo_with_allowed_host_still_works(self):
        """An auth'd URL whose real host IS allow-listed should
        still pass — pinning the urlsplit-based host extraction."""
        assert is_direct_video_url("https://user:pass@media.cloudfront.net/x.mp4")

    def test_r62_known_per_platform_urls_still_pass(self):
        """The host-allowlist applies ONLY to the catch-all .mp4/.webm
        pattern. The per-platform patterns above it are inherently
        host-bound by their regex and keep working unconstrained."""
        assert is_direct_video_url("https://www.youtube.com/watch?v=abc123")
        assert is_direct_video_url("https://v.redd.it/abc123")
        assert is_direct_video_url("https://clips.twitch.tv/FunnyClipName-abc123")


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


# ── Reddit OAuth app-only auth (2026-06-18) ──────────────────


class TestRedditOAuth:
    """`_reddit_app_token` returns a cached OAuth token when client
    credentials are configured, falls back to None (anon path) when
    they aren't.

    Why this exists: Reddit's anon search.json 403s from data-center
    IPs (verified 2026-06-18 even WITH WARP through Cloudflare). OAuth
    bypasses the anon rate limit entirely.
    """

    def setup_method(self):
        # Reset module cache between tests so token-expiry/refresh
        # behaviour is testable.
        from genlab_core.media import video_sourcer

        video_sourcer._REDDIT_TOKEN_CACHE.clear()
        video_sourcer._REDDIT_TOKEN_CACHE.update({"token": None, "expires_at": 0.0})

    def test_returns_none_when_credentials_missing(self, monkeypatch):
        from genlab_core.media.video_sourcer import _reddit_app_token

        monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
        monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
        assert _reddit_app_token() is None

    def test_returns_none_when_client_id_only(self, monkeypatch):
        from genlab_core.media.video_sourcer import _reddit_app_token

        monkeypatch.setenv("REDDIT_CLIENT_ID", "abc")
        monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
        assert _reddit_app_token() is None

    def test_returns_token_when_credentials_present(self, monkeypatch):
        """OAuth POST returns 200 → token cached + returned."""
        from genlab_core.media import video_sourcer

        monkeypatch.setenv("REDDIT_CLIENT_ID", "abc")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret123")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"access_token": "tk_test_value", "token_type": "bearer"}

        with patch("requests.post", return_value=mock_resp) as mock_post:
            token = video_sourcer._reddit_app_token()
        assert token == "tk_test_value"
        # Verify HTTP basic auth + grant_type passed correctly
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["data"] == {"grant_type": "client_credentials"}
        assert call_kwargs["auth"] == ("abc", "secret123")
        # User-Agent is non-default (matches Reddit policy)
        assert "GenLab" in call_kwargs["headers"]["User-Agent"]

    def test_returns_none_on_http_error(self, monkeypatch):
        """4xx/5xx response → None (caller falls back to anon)."""
        from genlab_core.media import video_sourcer

        monkeypatch.setenv("REDDIT_CLIENT_ID", "abc")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret123")

        mock_resp = MagicMock()
        mock_resp.status_code = 401  # bad credentials
        mock_resp.json.return_value = {"error": "invalid_grant"}

        with patch("requests.post", return_value=mock_resp):
            token = video_sourcer._reddit_app_token()
        assert token is None

    def test_caches_token_within_ttl(self, monkeypatch):
        """Second call within TTL must NOT re-request from Reddit
        (cost + rate-limit avoidance)."""
        from genlab_core.media import video_sourcer

        monkeypatch.setenv("REDDIT_CLIENT_ID", "abc")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret123")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"access_token": "tk_first"}

        with patch("requests.post", return_value=mock_resp) as mock_post:
            t1 = video_sourcer._reddit_app_token()
            t2 = video_sourcer._reddit_app_token()
            t3 = video_sourcer._reddit_app_token()
        assert t1 == t2 == t3 == "tk_first"
        assert mock_post.call_count == 1, "second + third calls must use cache"

    def test_re_requests_after_ttl_expires(self, monkeypatch):
        """When cached token's expires_at is in the past, refresh."""
        import time as _time

        from genlab_core.media import video_sourcer

        monkeypatch.setenv("REDDIT_CLIENT_ID", "abc")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret123")

        # Pre-populate an expired cache entry
        video_sourcer._REDDIT_TOKEN_CACHE["token"] = "tk_stale"
        video_sourcer._REDDIT_TOKEN_CACHE["expires_at"] = _time.time() - 60

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"access_token": "tk_fresh"}

        with patch("requests.post", return_value=mock_resp):
            token = video_sourcer._reddit_app_token()
        assert token == "tk_fresh"

    def test_handles_request_exception_gracefully(self, monkeypatch):
        """Network failure → None (no exception leaks to caller).
        Pinned because the original anon path used the same
        ``except Exception`` swallow contract — OAuth path must keep
        that semantic for backwards compatibility."""
        from genlab_core.media import video_sourcer

        monkeypatch.setenv("REDDIT_CLIENT_ID", "abc")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret123")

        with patch("requests.post", side_effect=ConnectionError("network down")):
            token = video_sourcer._reddit_app_token()
        assert token is None


# ── Reddit session-cookie auth path (2026-06-18 alternative to OAuth) ───


class TestRedditCookieAuth:
    """When OAuth env vars aren't set BUT a session-cookie file is
    present, video_sourcer should load the cookie jar and use it for
    Reddit search calls. This unblocks the OAuth-captcha-blocked path
    discovered 2026-06-18.
    """

    def test_returns_none_when_no_cookie_file(self, monkeypatch, tmp_path):
        from genlab_core.media import video_sourcer

        monkeypatch.setattr(video_sourcer, "_REDDIT_COOKIE_PATH", str(tmp_path / "missing.txt"))
        assert video_sourcer._reddit_cookie_jar() is None

    def test_returns_none_when_cookie_file_empty(self, monkeypatch, tmp_path):
        """File exists but no cookies inside → return None (no auth)."""
        from genlab_core.media import video_sourcer

        empty = tmp_path / "empty_cookies.txt"
        empty.write_text("# Netscape HTTP Cookie File\n\n")
        monkeypatch.setattr(video_sourcer, "_REDDIT_COOKIE_PATH", str(empty))
        assert video_sourcer._reddit_cookie_jar() is None

    def test_returns_jar_when_cookies_present(self, monkeypatch, tmp_path):
        """Properly-formatted Netscape file → returns a jar with entries."""
        from genlab_core.media import video_sourcer

        path = tmp_path / "good_cookies.txt"
        # Real Netscape format — tab-separated fields, expires=0 for session
        path.write_text(
            "# Netscape HTTP Cookie File\n"
            "\n"
            ".reddit.com\tTRUE\t/\tTRUE\t0\treddit_session\tfake-session-value\n"
            ".reddit.com\tTRUE\t/\tTRUE\t0\tloid\tfake-loid-value\n"
        )
        monkeypatch.setattr(video_sourcer, "_REDDIT_COOKIE_PATH", str(path))

        jar = video_sourcer._reddit_cookie_jar()
        assert jar is not None
        cookie_names = {c.name for c in jar}
        assert "reddit_session" in cookie_names
        assert "loid" in cookie_names

    def test_handles_malformed_file_gracefully(self, monkeypatch, tmp_path):
        """Corrupt/invalid file → return None, log warning, don't raise."""
        from genlab_core.media import video_sourcer

        bad = tmp_path / "bad_cookies.txt"
        bad.write_text("this is not a cookies file at all\njust some junk\n")
        monkeypatch.setattr(video_sourcer, "_REDDIT_COOKIE_PATH", str(bad))

        # Must not raise; returns None
        result = video_sourcer._reddit_cookie_jar()
        assert result is None

    def test_env_var_override_works(self, monkeypatch, tmp_path):
        """REDDIT_COOKIES_PATH env var overrides the default path.
        Pin this for the OOTB-test → CI-test → prod-test pipeline."""
        import importlib

        from genlab_core.media import video_sourcer

        custom = tmp_path / "custom_cookies.txt"
        custom.write_text(
            "# Netscape HTTP Cookie File\n.reddit.com\tTRUE\t/\tTRUE\t0\treddit_session\tvalue\n"
        )
        monkeypatch.setenv("REDDIT_COOKIES_PATH", str(custom))
        importlib.reload(video_sourcer)

        # After reload, _REDDIT_COOKIE_PATH should match the env var
        assert video_sourcer._REDDIT_COOKIE_PATH == str(custom)
        assert video_sourcer._reddit_cookie_jar() is not None
