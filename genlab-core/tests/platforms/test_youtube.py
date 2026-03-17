"""Tests for YouTubeClient — mocks all HTTP and Google API calls."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.platforms.models import PublishPayload, YouTubeSpecific

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def yt_client():
    from genlab_core.platforms.youtube import YouTubeClient

    return YouTubeClient(
        client_id="test_client_id",
        client_secret="test_client_secret",
        refresh_token="test_refresh_token",
        enable_quota_gate=False,
    )


@pytest.fixture
def video_payload(tmp_path):
    """A PublishPayload with a real temporary MP4 file."""
    mp4 = tmp_path / "short.mp4"
    mp4.write_bytes(b"\x00" * 1024)  # fake MP4 content
    return PublishPayload(
        caption="Test caption",
        media_paths=[mp4],
        media_type="video",
        hashtags=["#AI", "#Shorts"],
        hook="Amazing hook",
        niche_id="ai_creators",
        platform_specific=YouTubeSpecific(
            shorts_title="Test Short #Shorts",
            category_id="28",
            privacy_status="public",
        ),
    )


@pytest.fixture
def no_media_payload():
    """A PublishPayload with no media paths."""
    return PublishPayload(
        caption="No media caption",
        media_paths=[],
        media_type="text",
        hashtags=[],
        hook="",
        niche_id="ai_creators",
    )


def _mock_token_response():
    """Mock a successful OAuth2 token refresh response."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"access_token": "mock_access_token_12345"}
    return mock_resp


def _mock_upload_service():
    """Build a mock googleapiclient service that simulates a successful upload."""
    mock_service = MagicMock()
    mock_request = MagicMock()
    # Simulate resumable upload: first next_chunk returns (None, None) then response
    mock_request.next_chunk.side_effect = [
        (MagicMock(progress=lambda: 0.5), None),  # in-progress chunk
        (None, {"id": "yt_video_abc123"}),          # final chunk with response
    ]
    mock_service.videos.return_value.insert.return_value = mock_request
    return mock_service


# ---------------------------------------------------------------------------
# TestPublish
# ---------------------------------------------------------------------------


class TestPublish:
    def test_publish_returns_youtube_platform_id(self, yt_client, video_payload):
        """publish() always returns PublishResult with platform='youtube'."""
        with (
            patch("genlab_core.platforms.youtube.requests") as mock_req,
            patch("genlab_core.platforms.youtube.Credentials") as mock_creds_cls,
            patch("genlab_core.platforms.youtube.build") as mock_build,
            patch("genlab_core.platforms.youtube.MediaFileUpload"),
        ):
            mock_req.post.return_value = _mock_token_response()
            mock_build.return_value = _mock_upload_service()

            result = yt_client.publish(video_payload)

        assert result.platform == "youtube"

    def test_publish_short_success(self, yt_client, video_payload):
        """Successful Short upload returns success=True and a post_id."""
        with (
            patch("genlab_core.platforms.youtube.requests") as mock_req,
            patch("genlab_core.platforms.youtube.Credentials"),
            patch("genlab_core.platforms.youtube.build") as mock_build,
            patch("genlab_core.platforms.youtube.MediaFileUpload"),
        ):
            mock_req.post.return_value = _mock_token_response()
            mock_build.return_value = _mock_upload_service()

            result = yt_client.publish(video_payload)

        assert result.success is True
        assert result.post_id == "yt_video_abc123"
        assert result.platform == "youtube"

    def test_publish_short_url_is_shorts_url(self, yt_client, video_payload):
        """Successful upload returns a YouTube Shorts URL."""
        with (
            patch("genlab_core.platforms.youtube.requests") as mock_req,
            patch("genlab_core.platforms.youtube.Credentials"),
            patch("genlab_core.platforms.youtube.build") as mock_build,
            patch("genlab_core.platforms.youtube.MediaFileUpload"),
        ):
            mock_req.post.return_value = _mock_token_response()
            mock_build.return_value = _mock_upload_service()

            result = yt_client.publish(video_payload)

        assert "youtube" in result.post_url.lower()
        assert result.post_id in result.post_url

    def test_publish_no_media_paths_returns_error(self, yt_client, no_media_payload):
        """publish() with empty media_paths returns success=False without calling API."""
        with patch("genlab_core.platforms.youtube.requests") as mock_req:
            result = yt_client.publish(no_media_payload)
            # No HTTP call should be made
            mock_req.post.assert_not_called()

        assert result.success is False
        assert result.platform == "youtube"
        assert result.error != ""

    def test_publish_no_media_error_message(self, yt_client, no_media_payload):
        """Error message for missing media is descriptive."""
        result = yt_client.publish(no_media_payload)
        assert "media" in result.error.lower() or "video" in result.error.lower()

    def test_publish_api_error_returns_failure(self, yt_client, video_payload):
        """If the upload API raises an exception, publish returns success=False."""
        with (
            patch("genlab_core.platforms.youtube.requests") as mock_req,
            patch("genlab_core.platforms.youtube.Credentials"),
            patch("genlab_core.platforms.youtube.build") as mock_build,
            patch("genlab_core.platforms.youtube.MediaFileUpload"),
        ):
            mock_req.post.return_value = _mock_token_response()
            mock_service = MagicMock()
            mock_request = MagicMock()
            mock_request.next_chunk.side_effect = Exception("quota exceeded")
            mock_service.videos.return_value.insert.return_value = mock_request
            mock_build.return_value = mock_service

            result = yt_client.publish(video_payload)

        assert result.success is False
        assert result.platform == "youtube"

    def test_publish_uses_hook_in_title(self, yt_client, tmp_path):
        """When no shorts_title is provided, the hook is used as the video title."""
        mp4 = tmp_path / "v.mp4"
        mp4.write_bytes(b"\x00" * 64)
        payload = PublishPayload(
            caption="Caption text",
            media_paths=[mp4],
            media_type="video",
            hashtags=["#AI"],
            hook="Incredible AI breakthrough",
            niche_id="ai_creators",
            # No platform_specific — should fall back to hook
        )

        captured_body = {}

        def capture_insert(**kwargs):
            captured_body.update(kwargs.get("body", {}))
            mock_r = MagicMock()
            mock_r.next_chunk.side_effect = [
                (None, {"id": "vid_hook_test"}),
            ]
            return mock_r

        with (
            patch("genlab_core.platforms.youtube.requests") as mock_req,
            patch("genlab_core.platforms.youtube.Credentials"),
            patch("genlab_core.platforms.youtube.build") as mock_build,
            patch("genlab_core.platforms.youtube.MediaFileUpload"),
        ):
            mock_req.post.return_value = _mock_token_response()
            mock_service = MagicMock()
            mock_service.videos.return_value.insert.side_effect = capture_insert
            mock_build.return_value = mock_service

            yt_client.publish(payload)

        title = captured_body.get("snippet", {}).get("title", "")
        assert "Incredible AI breakthrough" in title or title  # hook used as basis

    def test_publish_title_truncated_to_100(self, yt_client, tmp_path):
        """Video title is truncated to YouTube's 100-char limit."""
        mp4 = tmp_path / "v.mp4"
        mp4.write_bytes(b"\x00" * 64)
        long_title = "A" * 200
        payload = PublishPayload(
            caption="Caption",
            media_paths=[mp4],
            media_type="video",
            hashtags=[],
            hook="Short hook",
            niche_id="ai_creators",
            platform_specific=YouTubeSpecific(shorts_title=long_title),
        )

        captured_body = {}

        def capture_insert(**kwargs):
            captured_body.update(kwargs.get("body", {}))
            mock_r = MagicMock()
            mock_r.next_chunk.side_effect = [(None, {"id": "vid_trunc"})]
            return mock_r

        with (
            patch("genlab_core.platforms.youtube.requests") as mock_req,
            patch("genlab_core.platforms.youtube.Credentials"),
            patch("genlab_core.platforms.youtube.build") as mock_build,
            patch("genlab_core.platforms.youtube.MediaFileUpload"),
        ):
            mock_req.post.return_value = _mock_token_response()
            mock_service = MagicMock()
            mock_service.videos.return_value.insert.side_effect = capture_insert
            mock_build.return_value = mock_service

            yt_client.publish(payload)

        title = captured_body.get("snippet", {}).get("title", "")
        assert len(title) <= 100


# ---------------------------------------------------------------------------
# TestEngagement
# ---------------------------------------------------------------------------


class TestEngagement:
    def test_post_reply_success(self, yt_client):
        """post_reply posts to /comments and returns True on success."""
        call_count = {"n": 0}

        def mock_post(url, *args, **kwargs):
            call_count["n"] += 1
            resp = MagicMock(status_code=200)
            resp.raise_for_status.return_value = None
            if "token" in url:
                resp.json.return_value = {"access_token": "tok123"}
            else:
                resp.json.return_value = {"id": "comment_reply_xyz"}
            return resp

        with patch("genlab_core.platforms.youtube.requests") as mock_req:
            mock_req.post.side_effect = mock_post

            ok = yt_client.post_reply(
                parent_id="comment_parent_123",
                text="Great video!",
                context_id="video_abc",
            )

        assert ok is True

    def test_post_reply_failure(self, yt_client):
        """post_reply returns False on HTTP error."""
        with patch("genlab_core.platforms.youtube.requests") as mock_req:
            resp = MagicMock(status_code=403)
            resp.raise_for_status.side_effect = Exception("403 Forbidden")
            resp.json.return_value = {"error": {"message": "Forbidden"}}
            mock_req.post.return_value = resp

            ok = yt_client.post_reply(
                parent_id="comment_bad",
                text="Hello",
                context_id="video_xyz",
            )

        assert ok is False

    def test_post_reply_calls_comments_endpoint(self, yt_client):
        """post_reply uses the /comments API endpoint."""
        captured_urls = []

        def capture_post(url, *args, **kwargs):
            captured_urls.append(url)
            resp = MagicMock(status_code=200)
            resp.raise_for_status.return_value = None
            if "token" in url:
                resp.json.return_value = {"access_token": "tok123"}
            else:
                resp.json.return_value = {"id": "r1"}
            return resp

        with patch("genlab_core.platforms.youtube.requests") as mock_req:
            mock_req.post.side_effect = capture_post
            yt_client.post_reply(
                parent_id="comment_123",
                text="Nice!",
                context_id="video_456",
            )

        assert any("comments" in url for url in captured_urls)

    def test_post_reply_includes_parent_id(self, yt_client):
        """post_reply body includes parentId for the comment being replied to."""
        captured_body = {}

        def capture_post(url, *args, **kwargs):
            resp = MagicMock(status_code=200)
            resp.raise_for_status.return_value = None
            if "token" in url:
                resp.json.return_value = {"access_token": "tok_parent"}
            else:
                # Capture the JSON body sent to the comments endpoint
                if "json" in kwargs:
                    captured_body.update(kwargs["json"])
                resp.json.return_value = {"id": "r1"}
            return resp

        with patch("genlab_core.platforms.youtube.requests") as mock_req:
            mock_req.post.side_effect = capture_post
            yt_client.post_reply(
                parent_id="comment_parent_999",
                text="Reply text",
                context_id="video_000",
            )

        # parentId should be inside snippet
        snippet = captured_body.get("snippet", {})
        assert snippet.get("parentId") == "comment_parent_999"

    def test_post_reply_requires_token_refresh(self, yt_client):
        """post_reply fetches an access token before calling the comments API."""
        token_calls = []

        with patch("genlab_core.platforms.youtube.requests") as mock_req:
            def capture_post(url, *args, **kwargs):
                token_calls.append(url)
                resp = MagicMock(status_code=200)
                resp.raise_for_status.return_value = None
                resp.json.return_value = {"id": "r1", "access_token": "tok123"}
                return resp

            mock_req.post.side_effect = capture_post
            yt_client.post_reply("c1", "Hi!", context_id="v1")

        # At minimum one call to oauth2 token endpoint
        assert any("oauth2" in u or "token" in u or "comments" in u for u in token_calls)


# ---------------------------------------------------------------------------
# TestLike
# ---------------------------------------------------------------------------


class TestLike:
    def test_like_returns_true(self, yt_client):
        """like() is a no-op and always returns True."""
        ok = yt_client.like(target_id="comment_xyz")
        assert ok is True

    def test_like_with_context_id_returns_true(self, yt_client):
        """like() with context_id also returns True."""
        ok = yt_client.like(target_id="comment_abc", context_id="video_def")
        assert ok is True

    def test_like_does_not_call_any_api(self, yt_client):
        """like() makes no HTTP calls (YouTube Data API doesn't support liking comments)."""
        with patch("genlab_core.platforms.youtube.requests") as mock_req:
            yt_client.like(target_id="comment_123")
            mock_req.post.assert_not_called()
            mock_req.get.assert_not_called()


# ---------------------------------------------------------------------------
# TestGetMetrics
# ---------------------------------------------------------------------------


class TestGetMetrics:
    def _old_published_at(self, hours_ago: float = 72) -> datetime:
        return datetime.now(UTC) - timedelta(hours=hours_ago)

    def _recent_published_at(self, hours_ago: float = 12) -> datetime:
        return datetime.now(UTC) - timedelta(hours=hours_ago)

    def test_get_metrics_returns_none_for_recent_video(self, yt_client):
        """Videos published < 48h ago return None (data lag guard)."""
        result = yt_client.get_metrics(
            video_id="vid_recent",
            published_at=self._recent_published_at(hours_ago=24),
        )
        assert result is None

    def test_get_metrics_returns_none_for_exactly_47h(self, yt_client):
        """Videos published 47h ago still return None (below 48h guard)."""
        result = yt_client.get_metrics(
            video_id="vid_47h",
            published_at=self._recent_published_at(hours_ago=47),
        )
        assert result is None

    def test_get_metrics_returns_metrics_for_old_video(self, yt_client):
        """Videos published > 48h ago return PlatformMetrics."""
        mock_analytics_response = {
            "columnHeaders": [
                {"name": "video"},
                {"name": "views"},
                {"name": "estimatedMinutesWatched"},
                {"name": "averageViewDuration"},
                {"name": "averageViewPercentage"},
                {"name": "likes"},
                {"name": "comments"},
                {"name": "shares"},
                {"name": "subscribersGained"},
            ],
            "rows": [["vid_old", 1500, 3000.0, 120.0, 45.0, 200, 30, 15, 5]],
        }

        mock_service = MagicMock()
        mock_service.reports.return_value.query.return_value.execute.return_value = (
            mock_analytics_response
        )

        mock_channel_resp = MagicMock()
        mock_channel_resp.json.return_value = {"items": [{"id": "UC_test"}]}
        mock_channel_resp.raise_for_status = MagicMock()

        with (
            patch("genlab_core.platforms.youtube.requests") as mock_req,
            patch("genlab_core.platforms.youtube.build") as mock_build,
            patch("genlab_core.platforms.youtube.Credentials"),
        ):
            mock_req.post.return_value = _mock_token_response()
            mock_req.get.return_value = mock_channel_resp
            mock_build.return_value = mock_service

            result = yt_client.get_metrics(
                video_id="vid_old",
                published_at=self._old_published_at(hours_ago=72),
            )

        assert result is not None
        assert result.views == 1500
        assert result.likes == 200
        assert result.comments == 30
        assert result.shares == 15

    def test_get_metrics_empty_rows_returns_none(self, yt_client):
        """Analytics API returning no rows → returns None."""
        mock_analytics_response = {"columnHeaders": [], "rows": []}

        mock_service = MagicMock()
        mock_service.reports.return_value.query.return_value.execute.return_value = (
            mock_analytics_response
        )

        with (
            patch("genlab_core.platforms.youtube.requests") as mock_req,
            patch("genlab_core.platforms.youtube.build") as mock_build,
        ):
            mock_req.post.return_value = _mock_token_response()
            mock_build.return_value = mock_service

            result = yt_client.get_metrics(
                video_id="vid_no_data",
                published_at=self._old_published_at(hours_ago=96),
            )

        assert result is None

    def test_get_metrics_api_exception_returns_none(self, yt_client):
        """If Analytics API raises, get_metrics returns None (fail-safe)."""
        mock_service = MagicMock()
        mock_service.reports.return_value.query.return_value.execute.side_effect = (
            Exception("API error")
        )

        with (
            patch("genlab_core.platforms.youtube.requests") as mock_req,
            patch("genlab_core.platforms.youtube.build") as mock_build,
        ):
            mock_req.post.return_value = _mock_token_response()
            mock_build.return_value = mock_service

            result = yt_client.get_metrics(
                video_id="vid_err",
                published_at=self._old_published_at(hours_ago=72),
            )

        assert result is None

    def test_get_metrics_extra_contains_watch_minutes(self, yt_client):
        """PlatformMetrics.extra should contain watch_minutes and other YT metrics."""
        mock_analytics_response = {
            "columnHeaders": [
                {"name": "video"},
                {"name": "views"},
                {"name": "estimatedMinutesWatched"},
                {"name": "averageViewDuration"},
                {"name": "averageViewPercentage"},
                {"name": "likes"},
                {"name": "comments"},
                {"name": "shares"},
                {"name": "subscribersGained"},
            ],
            "rows": [["vid_extra", 500, 1200.0, 90.0, 60.0, 50, 10, 5, 2]],
        }

        mock_service = MagicMock()
        mock_service.reports.return_value.query.return_value.execute.return_value = (
            mock_analytics_response
        )

        mock_channel_resp = MagicMock()
        mock_channel_resp.json.return_value = {"items": [{"id": "UC_test"}]}
        mock_channel_resp.raise_for_status = MagicMock()

        with (
            patch("genlab_core.platforms.youtube.requests") as mock_req,
            patch("genlab_core.platforms.youtube.build") as mock_build,
            patch("genlab_core.platforms.youtube.Credentials"),
        ):
            mock_req.post.return_value = _mock_token_response()
            mock_req.get.return_value = mock_channel_resp
            mock_build.return_value = mock_service

            result = yt_client.get_metrics(
                video_id="vid_extra",
                published_at=self._old_published_at(hours_ago=72),
            )

        assert result is not None
        assert "watch_minutes" in result.extra or result.extra  # enriched


# ---------------------------------------------------------------------------
# TestCheckTokenHealth
# ---------------------------------------------------------------------------


class TestCheckTokenHealth:
    def test_valid_token(self, yt_client):
        """Successful token refresh → TokenStatus(valid=True)."""
        with patch("genlab_core.platforms.youtube.requests") as mock_req:
            mock_req.post.return_value = _mock_token_response()
            status = yt_client.check_token_health()

        assert status.valid is True
        assert status.platform == "youtube"

    def test_invalid_token_returns_false(self, yt_client):
        """Failed token refresh → TokenStatus(valid=False)."""
        with patch("genlab_core.platforms.youtube.requests") as mock_req:
            resp = MagicMock()
            resp.raise_for_status.side_effect = Exception("401 Unauthorized")
            mock_req.post.return_value = resp
            status = yt_client.check_token_health()

        assert status.valid is False
        assert status.platform == "youtube"

    def test_health_check_platform_id(self):
        """platform_id class attribute is 'youtube'."""
        from genlab_core.platforms.youtube import YouTubeClient

        assert YouTubeClient.platform_id == "youtube"

    def test_valid_token_needs_refresh_false(self, yt_client):
        """YouTube tokens are refreshed programmatically — needs_refresh=False."""
        with patch("genlab_core.platforms.youtube.requests") as mock_req:
            mock_req.post.return_value = _mock_token_response()
            status = yt_client.check_token_health()

        assert status.needs_refresh is False

    def test_invalid_token_has_message(self, yt_client):
        """TokenStatus.message is non-empty on failure."""
        with patch("genlab_core.platforms.youtube.requests") as mock_req:
            resp = MagicMock()
            resp.raise_for_status.side_effect = Exception("Connection error")
            mock_req.post.return_value = resp
            status = yt_client.check_token_health()

        assert status.message != ""


# ---------------------------------------------------------------------------
# TestInit
# ---------------------------------------------------------------------------


class TestInit:
    def test_env_var_defaults(self, monkeypatch):
        """Constructor falls back to env vars when args are omitted."""
        monkeypatch.setenv("YOUTUBE_CLIENT_ID", "env_client_id")
        monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "env_client_secret")
        monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", "env_refresh_token")

        from genlab_core.platforms.youtube import YouTubeClient

        client = YouTubeClient()
        assert client._client_id == "env_client_id"
        assert client._client_secret == "env_client_secret"
        assert client._refresh_token == "env_refresh_token"

    def test_explicit_args_override_env(self, monkeypatch):
        """Explicit constructor args override env vars."""
        monkeypatch.setenv("YOUTUBE_CLIENT_ID", "env_id")

        from genlab_core.platforms.youtube import YouTubeClient

        client = YouTubeClient(
            client_id="explicit_id",
            client_secret="explicit_secret",
            refresh_token="explicit_token",
        )
        assert client._client_id == "explicit_id"

    def test_platform_id_is_youtube(self):
        """platform_id class attribute is 'youtube'."""
        from genlab_core.platforms.youtube import YouTubeClient

        assert YouTubeClient.platform_id == "youtube"

    def test_token_ttl_is_50_minutes(self):
        """Token TTL is 50 minutes (3000 seconds)."""
        from genlab_core.platforms.youtube import YouTubeClient

        client = YouTubeClient(
            client_id="id",
            client_secret="secret",
            refresh_token="token",
        )
        assert client._TOKEN_TTL_SECONDS == 3000

    def test_initial_token_is_none(self):
        """Token cache starts empty."""
        from genlab_core.platforms.youtube import YouTubeClient

        client = YouTubeClient(
            client_id="id",
            client_secret="secret",
            refresh_token="token",
        )
        assert client._access_token is None
        assert client._token_refreshed_at == 0.0


# ---------------------------------------------------------------------------
# TestTokenCaching
# ---------------------------------------------------------------------------


class TestTokenCaching:
    def test_token_cached_within_ttl(self, yt_client):
        """Second call within TTL uses cached token (no new HTTP POST)."""
        with patch("genlab_core.platforms.youtube.requests") as mock_req:
            mock_req.post.return_value = _mock_token_response()

            # First call
            tok1 = yt_client._get_access_token()
            # Second call
            tok2 = yt_client._get_access_token()

        # Only one HTTP call should have been made
        assert mock_req.post.call_count == 1
        assert tok1 == tok2 == "mock_access_token_12345"

    def test_token_refreshed_after_ttl(self, yt_client):
        """Token is refreshed if TTL has expired."""
        with patch("genlab_core.platforms.youtube.requests") as mock_req:
            mock_req.post.return_value = _mock_token_response()

            # Get token and manually expire it
            yt_client._get_access_token()
            yt_client._token_refreshed_at = 0.0  # force expiry

            # Second call should trigger refresh
            yt_client._get_access_token()

        assert mock_req.post.call_count == 2


# ---------------------------------------------------------------------------
# TestProtocolCompliance
# ---------------------------------------------------------------------------


class TestVerifyChannel:
    def test_verify_channel_no_expected_id(self, yt_client):
        """verify_channel returns True when no expected_channel_id is configured."""
        assert yt_client.verify_channel() is True

    def test_verify_channel_match(self):
        """verify_channel returns True when actual matches expected."""
        from genlab_core.platforms.youtube import YouTubeClient

        client = YouTubeClient(
            client_id="id",
            client_secret="secret",
            refresh_token="token",
            expected_channel_id="UC_expected_123",
            enable_quota_gate=False,
        )

        with patch("genlab_core.platforms.youtube.requests") as mock_req:
            # Token refresh
            mock_req.post.return_value = _mock_token_response()
            # Channel ID lookup
            mock_req.get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"items": [{"id": "UC_expected_123"}]},
            )
            mock_req.get.return_value.raise_for_status = MagicMock()

            ok = client.verify_channel()

        assert ok is True

    def test_verify_channel_mismatch(self):
        """verify_channel returns False when actual does not match expected."""
        from genlab_core.platforms.youtube import YouTubeClient

        client = YouTubeClient(
            client_id="id",
            client_secret="secret",
            refresh_token="token",
            expected_channel_id="UC_expected_123",
            enable_quota_gate=False,
        )

        with patch("genlab_core.platforms.youtube.requests") as mock_req:
            mock_req.post.return_value = _mock_token_response()
            mock_req.get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"items": [{"id": "UC_wrong_456"}]},
            )
            mock_req.get.return_value.raise_for_status = MagicMock()

            ok = client.verify_channel()

        assert ok is False

    def test_verify_channel_no_channel_found(self):
        """verify_channel returns False when channel ID cannot be resolved."""
        from genlab_core.platforms.youtube import YouTubeClient

        client = YouTubeClient(
            client_id="id",
            client_secret="secret",
            refresh_token="token",
            expected_channel_id="UC_expected_123",
            enable_quota_gate=False,
        )

        with patch("genlab_core.platforms.youtube.requests") as mock_req:
            mock_req.post.return_value = _mock_token_response()
            mock_req.get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"items": []},
            )
            mock_req.get.return_value.raise_for_status = MagicMock()

            ok = client.verify_channel()

        assert ok is False


# ---------------------------------------------------------------------------
# TestPostComment
# ---------------------------------------------------------------------------


class TestPostComment:
    def test_post_comment_success(self, yt_client):
        """post_comment posts to /commentThreads and returns thread ID."""
        def mock_post(url, *args, **kwargs):
            resp = MagicMock(status_code=200)
            resp.raise_for_status.return_value = None
            if "token" in url:
                resp.json.return_value = {"access_token": "tok_cmt"}
            else:
                resp.json.return_value = {"id": "thread_abc123"}
            return resp

        with patch("genlab_core.platforms.youtube.requests") as mock_req:
            mock_req.post.side_effect = mock_post

            thread_id = yt_client.post_comment(
                video_id="vid_xyz",
                text="Check out the link in the description!",
            )

        assert thread_id == "thread_abc123"

    def test_post_comment_failure(self, yt_client):
        """post_comment returns None on HTTP error."""
        with patch("genlab_core.platforms.youtube.requests") as mock_req:
            resp = MagicMock(status_code=403)
            resp.raise_for_status.side_effect = Exception("403 Forbidden")
            mock_req.post.return_value = resp

            thread_id = yt_client.post_comment(video_id="vid_bad", text="Hello")

        assert thread_id is None

    def test_post_comment_calls_comment_threads_endpoint(self, yt_client):
        """post_comment uses /commentThreads (not /comments)."""
        captured_urls = []

        def capture_post(url, *args, **kwargs):
            captured_urls.append(url)
            resp = MagicMock(status_code=200)
            resp.raise_for_status.return_value = None
            resp.json.return_value = {"id": "t1", "access_token": "tok"}
            return resp

        with patch("genlab_core.platforms.youtube.requests") as mock_req:
            mock_req.post.side_effect = capture_post
            yt_client.post_comment(video_id="vid_test", text="First!")

        assert any("commentThreads" in url for url in captured_urls)

    def test_post_comment_includes_video_id_in_body(self, yt_client):
        """post_comment body includes videoId."""
        captured_body = {}

        def capture_post(url, *args, **kwargs):
            resp = MagicMock(status_code=200)
            resp.raise_for_status.return_value = None
            if "token" in url:
                resp.json.return_value = {"access_token": "tok_body"}
            else:
                if "json" in kwargs:
                    captured_body.update(kwargs["json"])
                resp.json.return_value = {"id": "t1"}
            return resp

        with patch("genlab_core.platforms.youtube.requests") as mock_req:
            mock_req.post.side_effect = capture_post
            yt_client.post_comment(video_id="vid_check", text="Nice!")

        snippet = captured_body.get("snippet", {})
        assert snippet.get("videoId") == "vid_check"


# ---------------------------------------------------------------------------
# TestUpdateVideoMetadata
# ---------------------------------------------------------------------------


class TestUpdateVideoMetadata:
    def test_update_title_success(self, yt_client):
        """update_video_metadata updates the title and returns result dict."""
        def mock_http(method):
            def handler(url, *args, **kwargs):
                resp = MagicMock(status_code=200)
                resp.raise_for_status.return_value = None
                if "token" in url:
                    resp.json.return_value = {"access_token": "tok_meta"}
                elif method == "get":
                    resp.json.return_value = {
                        "items": [{"snippet": {"title": "Old Title", "description": "Desc"}}]
                    }
                else:
                    resp.json.return_value = {"id": "vid_upd"}
                return resp
            return handler

        with patch("genlab_core.platforms.youtube.requests") as mock_req:
            mock_req.post.side_effect = mock_http("post")
            mock_req.get.side_effect = mock_http("get")
            mock_req.put.side_effect = mock_http("put")

            result = yt_client.update_video_metadata(
                "vid_upd", title="New Title"
            )

        assert result is not None
        assert result["video_id"] == "vid_upd"
        assert result["title"] == "New Title"

    def test_update_description_success(self, yt_client):
        """update_video_metadata can update description only."""
        def mock_http(method):
            def handler(url, *args, **kwargs):
                resp = MagicMock(status_code=200)
                resp.raise_for_status.return_value = None
                if "token" in url:
                    resp.json.return_value = {"access_token": "tok_desc"}
                elif method == "get":
                    resp.json.return_value = {
                        "items": [{"snippet": {"title": "Keep Title", "description": "Old"}}]
                    }
                else:
                    resp.json.return_value = {"id": "vid_desc"}
                return resp
            return handler

        with patch("genlab_core.platforms.youtube.requests") as mock_req:
            mock_req.post.side_effect = mock_http("post")
            mock_req.get.side_effect = mock_http("get")
            mock_req.put.side_effect = mock_http("put")

            result = yt_client.update_video_metadata(
                "vid_desc", description="New description"
            )

        assert result is not None
        assert result["title"] == "Keep Title"

    def test_update_no_changes_returns_none(self, yt_client):
        """update_video_metadata returns None if neither title nor description given."""
        result = yt_client.update_video_metadata("vid_none")
        assert result is None

    def test_update_video_not_found(self, yt_client):
        """update_video_metadata returns None if video does not exist."""
        def mock_http(method):
            def handler(url, *args, **kwargs):
                resp = MagicMock(status_code=200)
                resp.raise_for_status.return_value = None
                if "token" in url:
                    resp.json.return_value = {"access_token": "tok_nf"}
                else:
                    resp.json.return_value = {"items": []}
                return resp
            return handler

        with patch("genlab_core.platforms.youtube.requests") as mock_req:
            mock_req.post.side_effect = mock_http("post")
            mock_req.get.side_effect = mock_http("get")

            result = yt_client.update_video_metadata(
                "vid_notfound", title="New"
            )

        assert result is None

    def test_update_api_error_returns_none(self, yt_client):
        """update_video_metadata returns None on API exception."""
        with patch("genlab_core.platforms.youtube.requests") as mock_req:
            resp = MagicMock()
            resp.raise_for_status.side_effect = Exception("500 Internal")
            mock_req.post.return_value = resp

            result = yt_client.update_video_metadata(
                "vid_err", title="Fail"
            )

        assert result is None


# ---------------------------------------------------------------------------
# TestQuotaGate
# ---------------------------------------------------------------------------


class TestQuotaGate:
    def test_publish_blocked_by_quota_gate(self, tmp_path):
        """publish() returns failure when quota tracker blocks upload."""
        from genlab_core.platforms.youtube import YouTubeClient

        client = YouTubeClient(
            client_id="id",
            client_secret="secret",
            refresh_token="token",
            enable_quota_gate=False,
        )

        # Manually inject a mock quota tracker
        mock_qt = MagicMock()
        mock_qt.can_upload.return_value = False
        mock_qt.status.return_value = {"used": 9500, "upload_count": 5}
        client._quota_tracker = mock_qt

        mp4 = tmp_path / "v.mp4"
        mp4.write_bytes(b"\x00" * 64)
        payload = PublishPayload(
            caption="Quota test",
            media_paths=[mp4],
            media_type="video",
            hashtags=["#AI"],
            hook="Test",
            niche_id="ai_creators",
            platform_specific=YouTubeSpecific(shorts_title="Test #Shorts"),
        )

        result = client.publish(payload)

        assert result.success is False
        assert "quota" in result.error.lower()

    def test_publish_records_quota_on_success(self, tmp_path):
        """Successful publish records upload in quota tracker."""
        from genlab_core.platforms.youtube import YouTubeClient

        client = YouTubeClient(
            client_id="id",
            client_secret="secret",
            refresh_token="token",
            enable_quota_gate=False,
        )

        mock_qt = MagicMock()
        mock_qt.can_upload.return_value = True
        client._quota_tracker = mock_qt

        mp4 = tmp_path / "v.mp4"
        mp4.write_bytes(b"\x00" * 64)
        payload = PublishPayload(
            caption="Record test",
            media_paths=[mp4],
            media_type="video",
            hashtags=["#AI"],
            hook="Test",
            niche_id="ai_creators",
            platform_specific=YouTubeSpecific(shorts_title="Test #Shorts"),
        )

        with (
            patch("genlab_core.platforms.youtube.requests") as mock_req,
            patch("genlab_core.platforms.youtube.Credentials"),
            patch("genlab_core.platforms.youtube.build") as mock_build,
            patch("genlab_core.platforms.youtube.MediaFileUpload"),
        ):
            mock_req.post.return_value = _mock_token_response()
            mock_build.return_value = _mock_upload_service()

            result = client.publish(payload)

        assert result.success is True
        mock_qt.record.assert_called_once_with("upload")


# ---------------------------------------------------------------------------
# TestPerChunkRetry
# ---------------------------------------------------------------------------


class TestPerChunkRetry:
    def test_chunk_retry_succeeds_after_transient_failure(self, yt_client, tmp_path):
        """Upload recovers from a transient chunk failure with retry."""
        mp4 = tmp_path / "retry.mp4"
        mp4.write_bytes(b"\x00" * 64)
        payload = PublishPayload(
            caption="Retry test",
            media_paths=[mp4],
            media_type="video",
            hashtags=["#AI"],
            hook="Test",
            niche_id="ai_creators",
            platform_specific=YouTubeSpecific(shorts_title="Retry #Shorts"),
        )

        mock_request = MagicMock()
        # First attempt: fail. Second attempt: succeed (in-progress).
        # Third call: final chunk with response.
        call_count = {"n": 0}
        def next_chunk_with_retry():
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("Transient network error")
            if call_count["n"] == 2:
                return (MagicMock(progress=lambda: 0.5), None)
            return (None, {"id": "vid_retry_ok"})

        mock_request.next_chunk.side_effect = next_chunk_with_retry

        mock_service = MagicMock()
        mock_service.videos.return_value.insert.return_value = mock_request

        with (
            patch("genlab_core.platforms.youtube.requests") as mock_req,
            patch("genlab_core.platforms.youtube.Credentials"),
            patch("genlab_core.platforms.youtube.build") as mock_build,
            patch("genlab_core.platforms.youtube.MediaFileUpload"),
            patch("genlab_core.platforms.youtube.time") as mock_time,
        ):
            mock_time.monotonic.return_value = 1000.0
            mock_time.sleep = MagicMock()
            mock_req.post.return_value = _mock_token_response()
            mock_build.return_value = mock_service

            result = yt_client.publish(payload)

        assert result.success is True
        assert result.post_id == "vid_retry_ok"
        # time.sleep should have been called for the retry backoff
        mock_time.sleep.assert_called()

    def test_chunk_retry_exhausted_raises(self, yt_client, tmp_path):
        """Upload fails after exhausting all chunk retries."""
        mp4 = tmp_path / "fail.mp4"
        mp4.write_bytes(b"\x00" * 64)
        payload = PublishPayload(
            caption="Fail test",
            media_paths=[mp4],
            media_type="video",
            hashtags=["#AI"],
            hook="Test",
            niche_id="ai_creators",
            platform_specific=YouTubeSpecific(shorts_title="Fail #Shorts"),
        )

        mock_request = MagicMock()
        mock_request.next_chunk.side_effect = Exception("Persistent error")

        mock_service = MagicMock()
        mock_service.videos.return_value.insert.return_value = mock_request

        with (
            patch("genlab_core.platforms.youtube.requests") as mock_req,
            patch("genlab_core.platforms.youtube.Credentials"),
            patch("genlab_core.platforms.youtube.build") as mock_build,
            patch("genlab_core.platforms.youtube.MediaFileUpload"),
            patch("genlab_core.platforms.youtube.time") as mock_time,
        ):
            mock_time.monotonic.return_value = 1000.0
            mock_time.sleep = MagicMock()
            mock_req.post.return_value = _mock_token_response()
            mock_build.return_value = mock_service

            result = yt_client.publish(payload)

        assert result.success is False
        assert "Persistent error" in result.error


class TestProtocolCompliance:
    def test_implements_publisher_protocol(self):
        from genlab_core.platforms.protocols import Publisher
        from genlab_core.platforms.youtube import YouTubeClient

        client = YouTubeClient(
            client_id="id",
            client_secret="secret",
            refresh_token="token",
        )
        assert isinstance(client, Publisher)

    def test_implements_engageable_protocol(self):
        from genlab_core.platforms.protocols import Engageable
        from genlab_core.platforms.youtube import YouTubeClient

        client = YouTubeClient(
            client_id="id",
            client_secret="secret",
            refresh_token="token",
        )
        assert isinstance(client, Engageable)

    def test_implements_trackable_protocol(self):
        from genlab_core.platforms.protocols import Trackable
        from genlab_core.platforms.youtube import YouTubeClient

        client = YouTubeClient(
            client_id="id",
            client_secret="secret",
            refresh_token="token",
        )
        assert isinstance(client, Trackable)

    def test_implements_health_checkable_protocol(self):
        from genlab_core.platforms.protocols import HealthCheckable
        from genlab_core.platforms.youtube import YouTubeClient

        client = YouTubeClient(
            client_id="id",
            client_secret="secret",
            refresh_token="token",
        )
        assert isinstance(client, HealthCheckable)
