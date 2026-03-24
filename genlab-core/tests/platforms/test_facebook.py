"""Tests for FacebookClient — mocks all HTTP."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.platforms.models import FacebookSpecific, PublishPayload

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fb_client():
    from genlab_core.platforms.facebook import FacebookClient

    return FacebookClient(
        access_token="EAAtest_fb_token",
        page_id="422278584555262",
        api_version="v21.0",
    )


@pytest.fixture
def video_payload(tmp_path):
    # Use a fake local MP4 so Path() works correctly (no URL normalisation)
    mp4 = tmp_path / "video.mp4"
    mp4.write_bytes(b"\x00" * 1024)  # fake MP4 content
    return PublishPayload(
        caption="Test Facebook video post",
        media_paths=[mp4],
        media_type="video",
        hashtags=["#AI", "#Tech"],
        hook="Breaking news!",
        niche_id="ai_creators",
        platform_specific=FacebookSpecific(),
    )


@pytest.fixture
def text_payload():
    return PublishPayload(
        caption="Check out this article",
        media_paths=[],
        media_type="link",
        hashtags=["#AI"],
        hook="",
        niche_id="ai_creators",
        platform_specific=FacebookSpecific(),
    )


# ---------------------------------------------------------------------------
# TestPublish
# ---------------------------------------------------------------------------


class TestPublish:
    def test_publish_video_returns_facebook_platform(self, fb_client, video_payload):
        """publish() with video returns PublishResult with platform='facebook'."""
        with patch("genlab_core.platforms.facebook.requests") as mock_req:
            mock_req.post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"id": "794399560387686"},
            )
            result = fb_client.publish(video_payload)

        assert result.platform == "facebook"

    def test_publish_video_success(self, fb_client, video_payload):
        """publish() with video URL returns success=True with post_id."""
        with patch("genlab_core.platforms.facebook.requests") as mock_req:
            mock_req.post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"id": "794399560387686"},
            )
            result = fb_client.publish(video_payload)

        assert result.success is True
        assert result.post_id == "794399560387686"

    def test_publish_video_calls_videos_endpoint(self, fb_client, video_payload):
        """Video publish uses /{page_id}/videos endpoint."""
        captured_urls = []

        with patch("genlab_core.platforms.facebook.requests") as mock_req:
            def capture_post(url, *args, **kwargs):
                captured_urls.append(url)
                return MagicMock(
                    status_code=200,
                    json=lambda: {"id": "video_post_123"},
                )

            mock_req.post.side_effect = capture_post
            fb_client.publish(video_payload)

        assert any("/videos" in url for url in captured_urls), (
            f"Expected /videos endpoint call, got: {captured_urls}"
        )

    def test_publish_video_api_error(self, fb_client, video_payload):
        """API error response returns success=False."""
        with patch("genlab_core.platforms.facebook.requests") as mock_req:
            mock_req.post.return_value = MagicMock(
                status_code=400,
                json=lambda: {"error": {"message": "Invalid video", "code": 100}},
            )
            result = fb_client.publish(video_payload)

        assert result.success is False
        assert result.error != ""

    def test_publish_no_media_rejected(self, fb_client, text_payload):
        """No media → rejected (video-first mandate, Sprint 67)."""
        with patch("genlab_core.platforms.facebook.requests") as mock_req:
            # Token pre-flight passes
            mock_req.get.return_value = MagicMock(status_code=200, json=lambda: {"id": "123"})
            result = fb_client.publish(text_payload)

        assert result.platform == "facebook"
        assert result.success is False
        assert "Video required" in result.error

    def test_publish_no_media_blocked_by_video_guard(self, fb_client, text_payload):
        """No media → blocked by video-first mandate (Sprint 67)."""
        with patch("genlab_core.platforms.facebook.requests") as mock_req:
            mock_req.get.return_value = MagicMock(status_code=200, json=lambda: {"id": "123"})
            result = fb_client.publish(text_payload)
        assert result.success is False
        assert "Video required" in result.error

    def test_publish_no_media_does_not_call_feed(self, fb_client, text_payload):
        """No media → no API calls made (blocked before network)."""
        with patch("genlab_core.platforms.facebook.requests") as mock_req:
            mock_req.get.return_value = MagicMock(status_code=200, json=lambda: {"id": "123"})
            fb_client.publish(text_payload)
        # Only the pre-flight GET /me should be called, no POST
        assert mock_req.post.call_count == 0

    def test_publish_uses_graph_facebook_url(self, fb_client, video_payload):
        """All publish requests go to graph.facebook.com, not graph.instagram.com."""
        captured_urls = []

        with patch("genlab_core.platforms.facebook.requests") as mock_req:
            def capture_post(url, *args, **kwargs):
                captured_urls.append(url)
                return MagicMock(
                    status_code=200,
                    json=lambda: {"id": "url_check_post"},
                )

            mock_req.post.side_effect = capture_post
            fb_client.publish(video_payload)

        for url in captured_urls:
            assert "graph.facebook.com" in url
            assert "graph.instagram.com" not in url


# ---------------------------------------------------------------------------
# TestEngagement
# ---------------------------------------------------------------------------


class TestEngagement:
    def test_post_reply_to_comment(self, fb_client):
        """post_reply(parent_id=comment_id, text) posts to /{comment_id}/comments."""
        with patch("genlab_core.platforms.facebook.requests") as mock_req:
            mock_req.post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"id": "reply_comment_456"},
            )
            ok = fb_client.post_reply(
                parent_id="comment_789",
                text="Thanks for the feedback!",
            )

        assert ok is True

    def test_post_reply_no_context_id_needed(self, fb_client):
        """post_reply works without context_id (Facebook replies don't need it)."""
        with patch("genlab_core.platforms.facebook.requests") as mock_req:
            mock_req.post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"id": "reply_no_ctx"},
            )
            # Should not raise
            ok = fb_client.post_reply(parent_id="comment_abc", text="Hello!")

        assert ok is True

    def test_post_reply_calls_comments_endpoint(self, fb_client):
        """post_reply posts to /{parent_id}/comments."""
        captured_urls = []

        with patch("genlab_core.platforms.facebook.requests") as mock_req:
            def capture_post(url, *args, **kwargs):
                captured_urls.append(url)
                return MagicMock(
                    status_code=200,
                    json=lambda: {"id": "reply_cap"},
                )

            mock_req.post.side_effect = capture_post
            fb_client.post_reply(parent_id="comment_xyz", text="Nice!")

        assert any("/comments" in url for url in captured_urls), (
            f"Expected /comments endpoint, got: {captured_urls}"
        )
        assert any("comment_xyz" in url for url in captured_urls)

    def test_post_reply_failure(self, fb_client):
        """HTTP error from reply endpoint returns False."""
        with patch("genlab_core.platforms.facebook.requests") as mock_req:
            mock_req.post.return_value = MagicMock(
                status_code=400,
                json=lambda: {"error": {"message": "Invalid comment ID"}},
            )
            ok = fb_client.post_reply(parent_id="bad_comment", text="Hello!")

        assert ok is False

    def test_post_reply_exception_returns_false(self, fb_client):
        """Exception during reply returns False (no propagation)."""
        with patch("genlab_core.platforms.facebook.requests") as mock_req:
            mock_req.post.side_effect = Exception("Network error")
            ok = fb_client.post_reply(parent_id="comment_abc", text="Hi!")

        assert ok is False

    def test_post_reply_uses_facebook_url(self, fb_client):
        """Reply uses graph.facebook.com."""
        captured = []

        with patch("genlab_core.platforms.facebook.requests") as mock_req:
            def cap(url, *a, **kw):
                captured.append(url)
                return MagicMock(status_code=200, json=lambda: {"id": "r1"})

            mock_req.post.side_effect = cap
            fb_client.post_reply("comment_x", "Hi!")

        assert any("graph.facebook.com" in u for u in captured)
        assert not any("graph.instagram.com" in u for u in captured)


# ---------------------------------------------------------------------------
# TestMetrics
# ---------------------------------------------------------------------------


class TestMetrics:
    def _old_published_at(self):
        """Return a published_at timestamp 72h ago (past data lag guard)."""
        return datetime.now(UTC) - timedelta(hours=72)

    def test_get_metrics_returns_platform_metrics(self, fb_client):
        """get_metrics() returns PlatformMetrics with views/likes/comments."""
        from genlab_core.platforms.models import PlatformMetrics

        published_at = self._old_published_at()

        with patch("genlab_core.platforms.facebook.requests") as mock_req:
            mock_req.get.return_value = MagicMock(
                status_code=200,
                json=lambda: {
                    "data": [
                        {"name": "post_impressions", "values": [{"value": 1200}]},
                        {"name": "post_reactions_like_total", "values": [{"value": 45}]},
                        {"name": "post_comments", "values": [{"value": 8}]},
                        {"name": "post_shares", "values": [{"value": 12}]},
                    ]
                },
            )
            metrics = fb_client.get_metrics(
                post_id="794399560387686",
                published_at=published_at,
            )

        assert metrics is not None
        assert isinstance(metrics, PlatformMetrics)
        assert metrics.views >= 0
        assert metrics.likes >= 0
        assert metrics.comments >= 0
        assert metrics.shares >= 0

    def test_get_metrics_calls_insights_endpoint(self, fb_client):
        """get_metrics calls /{post_id}/insights on Graph API."""
        captured_urls = []
        published_at = self._old_published_at()

        with patch("genlab_core.platforms.facebook.requests") as mock_req:
            def capture_get(url, *args, **kwargs):
                captured_urls.append(url)
                return MagicMock(
                    status_code=200,
                    json=lambda: {"data": []},
                )

            mock_req.get.side_effect = capture_get
            fb_client.get_metrics(
                post_id="794399560387686",
                published_at=published_at,
            )

        assert any("/insights" in url for url in captured_urls), (
            f"Expected /insights endpoint, got: {captured_urls}"
        )

    def test_get_metrics_api_failure_returns_none(self, fb_client):
        """API error response returns None (graceful degradation)."""
        published_at = self._old_published_at()

        with patch("genlab_core.platforms.facebook.requests") as mock_req:
            mock_req.get.return_value = MagicMock(
                status_code=400,
                json=lambda: {"error": {"message": "Unsupported request"}},
            )
            metrics = fb_client.get_metrics(
                post_id="bad_post_id",
                published_at=published_at,
            )

        assert metrics is None

    def test_get_metrics_exception_returns_none(self, fb_client):
        """Exception during metrics fetch returns None."""
        published_at = self._old_published_at()

        with patch("genlab_core.platforms.facebook.requests") as mock_req:
            mock_req.get.side_effect = Exception("Connection timeout")
            metrics = fb_client.get_metrics(
                post_id="post_abc",
                published_at=published_at,
            )

        assert metrics is None


# ---------------------------------------------------------------------------
# TestHealthCheck
# ---------------------------------------------------------------------------


class TestHealthCheck:
    def test_valid_token_returns_true(self, fb_client):
        """EAA token validated via /me returns valid=True."""
        with patch("genlab_core.platforms.facebook.requests") as mock_req:
            mock_req.get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"id": "422278584555262", "name": "Blackbox Brief"},
            )
            status = fb_client.check_token_health()

        assert status.valid is True
        assert status.platform == "facebook"

    def test_valid_token_eaa_never_expires(self, fb_client):
        """EAA token: expires_at=None, needs_refresh=False."""
        with patch("genlab_core.platforms.facebook.requests") as mock_req:
            mock_req.get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"id": "422278584555262", "name": "Blackbox Brief"},
            )
            status = fb_client.check_token_health()

        assert status.expires_at is None
        assert status.needs_refresh is False

    def test_invalid_token_returns_false(self, fb_client):
        """HTTP 400 / error body returns valid=False."""
        with patch("genlab_core.platforms.facebook.requests") as mock_req:
            mock_req.get.return_value = MagicMock(
                status_code=400,
                json=lambda: {"error": {"message": "Invalid OAuth access token"}},
            )
            status = fb_client.check_token_health()

        assert status.valid is False

    def test_token_health_message_is_informative(self, fb_client):
        """TokenStatus.message is non-empty on success."""
        with patch("genlab_core.platforms.facebook.requests") as mock_req:
            mock_req.get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"id": "422278584555262", "name": "BB Page"},
            )
            status = fb_client.check_token_health()

        assert status.message != ""

    def test_token_health_uses_facebook_url(self, fb_client):
        """check_token_health calls /me on graph.facebook.com."""
        captured = []

        with patch("genlab_core.platforms.facebook.requests") as mock_req:
            def cap(url, *args, **kwargs):
                captured.append(url)
                return MagicMock(
                    status_code=200,
                    json=lambda: {"id": "id_123", "name": "Test"},
                )

            mock_req.get.side_effect = cap
            fb_client.check_token_health()

        assert any("graph.facebook.com" in u for u in captured)
        assert not any("graph.instagram.com" in u for u in captured)

    def test_token_health_exception_returns_invalid(self, fb_client):
        """Exception during health check returns valid=False."""
        with patch("genlab_core.platforms.facebook.requests") as mock_req:
            mock_req.get.side_effect = Exception("Network error")
            status = fb_client.check_token_health()

        assert status.valid is False

    def test_platform_id_is_facebook(self):
        """FacebookClient.platform_id class attribute is 'facebook'."""
        from genlab_core.platforms.facebook import FacebookClient

        assert FacebookClient.platform_id == "facebook"


# ---------------------------------------------------------------------------
# TestInit
# ---------------------------------------------------------------------------


class TestInit:
    def test_env_var_defaults(self, monkeypatch):
        """Constructor falls back to env vars when args are omitted."""
        monkeypatch.setenv("META_ACCESS_TOKEN", "EAA_from_env")
        monkeypatch.setenv("META_FB_PAGE_ID", "12345678")

        from genlab_core.platforms.facebook import FacebookClient

        client = FacebookClient()
        assert client._access_token == "EAA_from_env"
        assert client._page_id == "12345678"

    def test_explicit_args_override_env(self, monkeypatch):
        """Explicit constructor args override env vars."""
        monkeypatch.setenv("META_ACCESS_TOKEN", "EAA_from_env")
        monkeypatch.setenv("META_FB_PAGE_ID", "99999")

        from genlab_core.platforms.facebook import FacebookClient

        client = FacebookClient(access_token="EAA_explicit", page_id="12345")
        assert client._access_token == "EAA_explicit"
        assert client._page_id == "12345"

    def test_base_url_uses_api_version(self):
        """Base URL includes the configured api_version."""
        from genlab_core.platforms.facebook import FacebookClient

        client = FacebookClient(
            access_token="t",
            page_id="p",
            api_version="v22.0",
        )
        assert "v22.0" in client._base_url
        assert "graph.facebook.com" in client._base_url

    def test_default_api_version(self):
        """Default api_version is v21.0."""
        from genlab_core.platforms.facebook import FacebookClient

        client = FacebookClient(access_token="t", page_id="p")
        assert "v21.0" in client._base_url
