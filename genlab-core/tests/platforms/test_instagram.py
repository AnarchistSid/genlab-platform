"""Tests for InstagramClient — mocks all HTTP."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.platforms.models import PublishPayload, InstagramSpecific


@pytest.fixture
def ig_client():
    from genlab_core.platforms.instagram import InstagramClient
    return InstagramClient(
        access_token="EAA_TEST_TOKEN",
        ig_user_id="17841448019867838",
        api_version="v21.0",
    )


class TestPublish:
    def test_publish_video_reel(self, ig_client):
        """Reel publish: create container → poll → publish."""
        payload = PublishPayload(
            caption="Test reel",
            media_paths=[Path("/tmp/video.mp4")],
            media_type="video",
            hashtags=["#test"],
            hook="Watch this",
            niche_id="ai_creators",
            platform_specific=InstagramSpecific(share_to_feed=True),
        )

        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            # Mock container creation
            mock_req.post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"id": "container_123"},
            )
            # Mock status check (FINISHED)
            mock_req.get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"status_code": "FINISHED"},
            )
            result = ig_client.publish(payload)

        assert result.platform == "instagram"
        assert hasattr(result, "success")
        assert hasattr(result, "post_id")

    def test_publish_requires_media(self, ig_client):
        """Publishing with no media paths should fail."""
        payload = PublishPayload(
            caption="No media",
            media_paths=[],
            media_type="text",
            hashtags=[],
            hook="",
            niche_id="ai_creators",
        )
        result = ig_client.publish(payload)
        assert result.success is False
        assert "media" in result.error.lower() or "video" in result.error.lower()

    def test_publish_reel_success_returns_post_id(self, ig_client):
        """Successful reel publish returns post_id from media_publish response."""
        payload = PublishPayload(
            caption="Test reel #ai",
            media_paths=[Path("/tmp/clip.mp4")],
            media_type="video",
            hashtags=["#ai"],
            hook="Big news!",
            niche_id="ai_creators",
            platform_specific=InstagramSpecific(share_to_feed=True),
        )

        post_call_count = {"n": 0}

        def mock_post(*args, **kwargs):
            post_call_count["n"] += 1
            if post_call_count["n"] == 1:
                # Container creation
                return MagicMock(status_code=200, json=lambda: {"id": "ctr_abc"})
            else:
                # media_publish call
                return MagicMock(status_code=200, json=lambda: {"id": "post_xyz"})

        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            mock_req.post.side_effect = mock_post
            mock_req.get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"status_code": "FINISHED"},
            )
            result = ig_client.publish(payload)

        assert result.success is True
        assert result.post_id == "post_xyz"
        assert result.platform == "instagram"

    def test_publish_reel_already_published_skips_second_post(self, ig_client):
        """If poll returns PUBLISHED, skip the media_publish call."""
        payload = PublishPayload(
            caption="Already live",
            media_paths=[Path("/tmp/v.mp4")],
            media_type="video",
            hashtags=[],
            hook="",
            niche_id="gaming",
            platform_specific=InstagramSpecific(),
        )

        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            mock_req.post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"id": "ctr_already"},
            )
            mock_req.get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"status_code": "PUBLISHED"},
            )
            result = ig_client.publish(payload)

        # Should succeed with the container id as fallback
        assert result.success is True
        # media_publish POST should NOT be called (only container creation)
        assert mock_req.post.call_count == 1

    def test_publish_container_creation_failure(self, ig_client):
        """Container creation API error → publish failure."""
        payload = PublishPayload(
            caption="Fail",
            media_paths=[Path("/tmp/v.mp4")],
            media_type="video",
            hashtags=[],
            hook="",
            niche_id="ai_creators",
        )

        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            mock_req.post.return_value = MagicMock(
                status_code=400,
                json=lambda: {"error": {"message": "Invalid video_url"}},
            )
            result = ig_client.publish(payload)

        assert result.success is False
        assert result.error != ""

    def test_publish_poll_error_status(self, ig_client):
        """Poll returns ERROR status → publish failure."""
        payload = PublishPayload(
            caption="Error reel",
            media_paths=[Path("/tmp/v.mp4")],
            media_type="video",
            hashtags=[],
            hook="",
            niche_id="ai_creators",
        )

        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            mock_req.post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"id": "ctr_err"},
            )
            mock_req.get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"status_code": "ERROR"},
            )
            result = ig_client.publish(payload)

        assert result.success is False

    def test_publish_uses_graph_facebook_url(self, ig_client):
        """All requests go to graph.facebook.com, never graph.instagram.com."""
        payload = PublishPayload(
            caption="URL check",
            media_paths=[Path("/tmp/v.mp4")],
            media_type="video",
            hashtags=[],
            hook="",
            niche_id="ai_creators",
        )

        captured_urls = []

        def capture_post(url, *args, **kwargs):
            captured_urls.append(url)
            if len(captured_urls) == 1:
                return MagicMock(status_code=200, json=lambda: {"id": "ctr_url"})
            return MagicMock(status_code=200, json=lambda: {"id": "post_url"})

        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            mock_req.post.side_effect = capture_post
            mock_req.get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"status_code": "FINISHED"},
            )
            ig_client.publish(payload)

        for url in captured_urls:
            assert "graph.facebook.com" in url
            assert "graph.instagram.com" not in url

    def test_publish_with_cover_url(self, ig_client):
        """Reel with cover_url passes cover_url in container params."""
        payload = PublishPayload(
            caption="Cover test",
            media_paths=[Path("/tmp/v.mp4")],
            media_type="video",
            hashtags=[],
            hook="",
            niche_id="ai_creators",
            platform_specific=InstagramSpecific(
                cover_url="https://example.com/cover.jpg",
                share_to_feed=True,
            ),
        )

        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            mock_req.post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"id": "ctr_cover"},
            )
            mock_req.get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"status_code": "FINISHED"},
            )
            ig_client.publish(payload)

        first_call_kwargs = mock_req.post.call_args_list[0]
        data_arg = first_call_kwargs[1].get("data") or first_call_kwargs[0][1]
        assert "cover_url" in data_arg


class TestEngagement:
    def test_post_reply(self, ig_client):
        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            mock_req.post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"id": "reply_456"},
            )
            ok = ig_client.post_reply(
                parent_id="comment_789",
                text="Thanks!",
                context_id="media_123",
            )
        assert ok is True

    def test_post_reply_failure(self, ig_client):
        """HTTP error from reply endpoint → returns False."""
        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            mock_req.post.return_value = MagicMock(
                status_code=400,
                json=lambda: {"error": {"message": "Invalid comment"}},
            )
            ok = ig_client.post_reply(
                parent_id="comment_bad",
                text="Hello",
                context_id="media_123",
            )
        assert ok is False

    def test_like(self, ig_client):
        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            mock_req.post.return_value = MagicMock(status_code=200, json=lambda: {"success": True})
            ok = ig_client.like(target_id="comment_789")
        assert ok is True

    def test_post_reply_uses_facebook_url(self, ig_client):
        """Reply endpoint must use graph.facebook.com."""
        captured = []
        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            def cap(url, *a, **kw):
                captured.append(url)
                return MagicMock(status_code=200, json=lambda: {"id": "r1"})
            mock_req.post.side_effect = cap
            ig_client.post_reply("comment_x", "Hi!", context_id="media_y")

        assert any("graph.facebook.com" in u for u in captured)
        assert not any("graph.instagram.com" in u for u in captured)


class TestHealthCheck:
    def test_valid_token(self, ig_client):
        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            mock_req.get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"id": "17841448019867838", "name": "Test"},
            )
            status = ig_client.check_token_health()
        assert status.valid is True
        assert status.platform == "instagram"

    def test_invalid_token(self, ig_client):
        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            mock_req.get.return_value = MagicMock(
                status_code=400,
                json=lambda: {"error": {"message": "Invalid token"}},
            )
            status = ig_client.check_token_health()
        assert status.valid is False

    def test_health_check_message_contains_info(self, ig_client):
        """TokenStatus.message should be non-empty on success."""
        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            mock_req.get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"id": "17841448019867838", "name": "BB Page"},
            )
            status = ig_client.check_token_health()
        assert status.message != ""

    def test_health_check_platform_id(self, ig_client):
        """platform attribute on InstagramClient is 'instagram'."""
        from genlab_core.platforms.instagram import InstagramClient
        assert InstagramClient.platform_id == "instagram"


class TestInit:
    def test_env_var_defaults(self, monkeypatch):
        """Constructor falls back to env vars when args are omitted."""
        monkeypatch.setenv("META_ACCESS_TOKEN", "EAA_from_env")
        monkeypatch.setenv("META_IG_USER_ID", "11111111111111111")

        from genlab_core.platforms.instagram import InstagramClient
        client = InstagramClient()
        assert client._access_token == "EAA_from_env"
        assert client._ig_user_id == "11111111111111111"

    def test_explicit_args_override_env(self, monkeypatch):
        """Explicit constructor args override env vars."""
        monkeypatch.setenv("META_ACCESS_TOKEN", "EAA_from_env")
        from genlab_core.platforms.instagram import InstagramClient
        client = InstagramClient(access_token="EAA_explicit", ig_user_id="99999")
        assert client._access_token == "EAA_explicit"
        assert client._ig_user_id == "99999"

    def test_base_url_uses_api_version(self):
        """Base URL includes the configured api_version."""
        from genlab_core.platforms.instagram import InstagramClient
        client = InstagramClient(
            access_token="t",
            ig_user_id="u",
            api_version="v22.0",
        )
        assert "v22.0" in client._base_url

    def test_max_poll_seconds_default(self):
        """Default poll timeout is 120 seconds."""
        from genlab_core.platforms.instagram import InstagramClient
        client = InstagramClient(access_token="t", ig_user_id="u")
        assert client._max_poll_seconds == 120

    def test_max_poll_seconds_custom(self):
        """Custom poll timeout is stored on the instance."""
        from genlab_core.platforms.instagram import InstagramClient
        client = InstagramClient(
            access_token="t", ig_user_id="u", max_poll_seconds=600
        )
        assert client._max_poll_seconds == 600


class TestVerifyChannel:
    def test_verify_channel_success(self, ig_client):
        """verify_channel returns True when the IG account is accessible."""
        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            mock_req.get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"id": "17841448019867838", "username": "blackboxbrief"},
            )
            ok = ig_client.verify_channel()
        assert ok is True

    def test_verify_channel_failure_invalid_account(self, ig_client):
        """verify_channel returns False when the API returns an error."""
        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            mock_req.get.return_value = MagicMock(
                status_code=400,
                json=lambda: {"error": {"message": "Object does not exist"}},
            )
            ok = ig_client.verify_channel()
        assert ok is False

    def test_verify_channel_exception(self, ig_client):
        """verify_channel returns False on network exception."""
        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            mock_req.get.side_effect = Exception("Connection timeout")
            ok = ig_client.verify_channel()
        assert ok is False

    def test_verify_channel_uses_graph_facebook_url(self, ig_client):
        """verify_channel must use graph.facebook.com."""
        captured = []
        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            def cap(url, *a, **kw):
                captured.append(url)
                return MagicMock(status_code=200, json=lambda: {"id": "123"})
            mock_req.get.side_effect = cap
            ig_client.verify_channel()

        assert any("graph.facebook.com" in u for u in captured)
        assert not any("graph.instagram.com" in u for u in captured)

    def test_verify_channel_requests_id_and_username(self, ig_client):
        """verify_channel requests fields=id,username."""
        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            mock_req.get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"id": "17841448019867838", "username": "bb"},
            )
            ig_client.verify_channel()

        call_kwargs = mock_req.get.call_args
        params = call_kwargs[1].get("params") or call_kwargs[0][1]
        assert "id" in params.get("fields", "")
        assert "username" in params.get("fields", "")
