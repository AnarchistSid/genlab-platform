"""Tests for ThreadsClient — mocks all HTTP."""

from __future__ import annotations

import os
from datetime import UTC
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.platforms.models import PublishPayload

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def threads_client():
    from genlab_core.platforms.threads import ThreadsClient

    return ThreadsClient(
        access_token="THREADS_TEST_TOKEN",
        user_id="123456789",
    )


def _make_payload(
    caption: str = "Test post",
    media_paths: list | None = None,
    media_type: str = "text",
    hashtags: list | None = None,
) -> PublishPayload:
    return PublishPayload(
        caption=caption,
        media_paths=media_paths or [],
        media_type=media_type,
        hashtags=hashtags or [],
        hook="",
        niche_id="ai_creators",
    )


# ---------------------------------------------------------------------------
# TestPublish
# ---------------------------------------------------------------------------


class TestPublish:
    def test_publish_video_post(self, threads_client):
        """Video publish: create container → wait → publish → return PublishResult."""
        payload = _make_payload(
            caption="Watch this",
            media_paths=[Path("https://cdn.example.com/video.mp4")],
            media_type="video",
        )

        post_call_count = {"n": 0}

        def mock_post(url, *args, **kwargs):
            post_call_count["n"] += 1
            if post_call_count["n"] == 1:
                # Container creation
                return MagicMock(
                    status_code=200,
                    ok=True,
                    json=lambda: {"id": "container_vid_123"},
                )
            else:
                # threads_publish call
                return MagicMock(
                    status_code=200,
                    ok=True,
                    json=lambda: {"id": "post_vid_456"},
                )

        get_call_count = {"n": 0}

        def mock_get(url, *args, **kwargs):
            get_call_count["n"] += 1
            params = kwargs.get("params", {})
            if isinstance(params, dict) and "status" in (params.get("fields", "") or ""):
                # Poll container status — return FINISHED
                return MagicMock(ok=True, json=lambda: {"status": "FINISHED"})
            else:
                # Permalink fetch
                return MagicMock(
                    ok=True, json=lambda: {"permalink": "https://www.threads.net/@user/post/456"}
                )

        with patch("genlab_core.platforms.threads._META_SESSION") as mock_req:
            with patch("genlab_core.platforms.threads.time"):
                mock_req.post.side_effect = mock_post
                mock_req.get.side_effect = mock_get
                result = threads_client.publish(payload)

        assert result.platform == "threads"
        assert result.success is True
        assert result.post_id == "post_vid_456"

    def test_publish_video_result_has_post_url(self, threads_client):
        """Successful video publish stores post_url in result."""
        payload = _make_payload(
            caption="Check this out",
            media_paths=[Path("https://cdn.example.com/video.mp4")],
            media_type="video",
        )

        post_call_count = {"n": 0}

        def mock_post(url, *args, **kwargs):
            post_call_count["n"] += 1
            if post_call_count["n"] == 1:
                return MagicMock(ok=True, json=lambda: {"id": "ctr_99"})
            return MagicMock(ok=True, json=lambda: {"id": "post_99"})

        def mock_get(url, *args, **kwargs):
            params = kwargs.get("params", {})
            if isinstance(params, dict) and "status" in (params.get("fields", "") or ""):
                return MagicMock(ok=True, json=lambda: {"status": "FINISHED"})
            return MagicMock(
                ok=True, json=lambda: {"permalink": "https://www.threads.net/@user/post/99"}
            )

        with patch("genlab_core.platforms.threads._META_SESSION") as mock_req:
            with patch("genlab_core.platforms.threads.time"):
                mock_req.post.side_effect = mock_post
                mock_req.get.side_effect = mock_get
                result = threads_client.publish(payload)

        assert result.success is True
        assert "threads.net" in result.post_url or result.post_id == "post_99"

    def test_publish_text_only_post(self, threads_client):
        """Text-only publish: create text container → publish → return PublishResult."""
        payload = _make_payload(
            caption="Hello Threads!",
            media_paths=[],
            media_type="text",
        )

        post_call_count = {"n": 0}

        def mock_post(url, *args, **kwargs):
            post_call_count["n"] += 1
            if post_call_count["n"] == 1:
                return MagicMock(ok=True, json=lambda: {"id": "ctr_text_1"})
            return MagicMock(ok=True, json=lambda: {"id": "post_text_1"})

        with patch("genlab_core.platforms.threads._META_SESSION") as mock_req:
            mock_req.post.side_effect = mock_post
            mock_req.get.return_value = MagicMock(
                ok=True,
                json=lambda: {"permalink": "https://www.threads.net/@user/post/t1"},
            )
            result = threads_client.publish(payload)

        assert result.platform == "threads"
        assert result.success is True
        assert result.post_id == "post_text_1"

    def test_publish_text_only_does_not_sleep(self, threads_client):
        """Text-only publish should NOT wait for video processing."""
        payload = _make_payload(caption="Just text", media_type="text")

        post_call_count = {"n": 0}

        def mock_post(url, *args, **kwargs):
            post_call_count["n"] += 1
            if post_call_count["n"] == 1:
                return MagicMock(ok=True, json=lambda: {"id": "ctr_t"})
            return MagicMock(ok=True, json=lambda: {"id": "post_t"})

        with patch("genlab_core.platforms.threads._META_SESSION") as mock_req:
            with patch("genlab_core.platforms.threads.time") as mock_time:
                mock_req.post.side_effect = mock_post
                mock_req.get.return_value = MagicMock(ok=True, json=lambda: {"permalink": ""})
                threads_client.publish(payload)

        mock_time.sleep.assert_not_called()

    def test_publish_no_media_no_caption_returns_error(self, threads_client):
        """No media and no caption → return failure PublishResult."""
        payload = _make_payload(caption="", media_paths=[], media_type="text")

        result = threads_client.publish(payload)

        assert result.platform == "threads"
        assert result.success is False
        assert result.error != ""

    def test_publish_image_post(self, threads_client):
        """Image publish: create image container → publish (no sleep)."""
        payload = _make_payload(
            caption="Nice image",
            media_paths=[Path("https://cdn.example.com/image.jpg")],
            media_type="image",
        )

        post_call_count = {"n": 0}

        def mock_post(url, *args, **kwargs):
            post_call_count["n"] += 1
            if post_call_count["n"] == 1:
                return MagicMock(ok=True, json=lambda: {"id": "ctr_img_1"})
            return MagicMock(ok=True, json=lambda: {"id": "post_img_1"})

        with patch("genlab_core.platforms.threads._META_SESSION") as mock_req:
            with patch("genlab_core.platforms.threads.time") as mock_time:
                mock_req.post.side_effect = mock_post
                mock_req.get.return_value = MagicMock(ok=True, json=lambda: {"permalink": ""})
                result = threads_client.publish(payload)

        assert result.success is True
        assert result.post_id == "post_img_1"
        # Image does not need processing wait
        mock_time.sleep.assert_not_called()

    def test_publish_video_media_type_sent_as_VIDEO(self, threads_client):
        """Container creation for video must set media_type=VIDEO."""
        payload = _make_payload(
            caption="vid",
            media_paths=[Path("https://cdn.example.com/video.mp4")],
            media_type="video",
        )

        captured_data = []

        def capture_post(url, *args, **kwargs):
            data = kwargs.get("data", {})
            captured_data.append(data)
            return MagicMock(ok=True, json=lambda: {"id": "ctr_chk"})

        with patch("genlab_core.platforms.threads._META_SESSION") as mock_req:
            with patch("genlab_core.platforms.threads.time"):
                mock_req.post.side_effect = capture_post
                mock_req.get.return_value = MagicMock(ok=True, json=lambda: {"permalink": ""})
                threads_client.publish(payload)

        # First call is container creation
        container_data = captured_data[0]
        assert container_data.get("media_type") == "VIDEO"
        assert container_data.get("video_url") is not None

    def test_publish_container_creation_failure(self, threads_client):
        """API error on container creation → failure PublishResult."""
        payload = _make_payload(
            caption="Test",
            media_paths=[Path("https://cdn.example.com/video.mp4")],
            media_type="video",
        )

        with patch("genlab_core.platforms.threads._META_SESSION") as mock_req:
            with patch("genlab_core.platforms.threads.time"):
                mock_req.post.return_value = MagicMock(
                    ok=False,
                    status_code=400,
                    json=lambda: {"error": {"message": "Invalid video_url"}},
                )
                result = threads_client.publish(payload)

        assert result.success is False
        assert result.error != ""

    def test_publish_uses_threads_net_base_url(self, threads_client):
        """All requests must go to graph.threads.net."""
        payload = _make_payload(
            caption="URL check",
            media_paths=[],
            media_type="text",
        )

        captured_urls = []

        post_call_count = {"n": 0}

        def capture_post(url, *args, **kwargs):
            captured_urls.append(url)
            post_call_count["n"] += 1
            if post_call_count["n"] == 1:
                return MagicMock(ok=True, json=lambda: {"id": "ctr_url"})
            return MagicMock(ok=True, json=lambda: {"id": "post_url"})

        with patch("genlab_core.platforms.threads._META_SESSION") as mock_req:
            mock_req.post.side_effect = capture_post
            mock_req.get.return_value = MagicMock(ok=True, json=lambda: {"permalink": ""})
            threads_client.publish(payload)

        assert any("graph.threads.net" in u for u in captured_urls)


# ---------------------------------------------------------------------------
# TestReply
# ---------------------------------------------------------------------------


class TestReply:
    def test_post_reply(self, threads_client):
        """post_reply creates a reply container then publishes it."""
        post_call_count = {"n": 0}

        def mock_post(url, *args, **kwargs):
            post_call_count["n"] += 1
            if post_call_count["n"] == 1:
                # Reply container creation
                return MagicMock(ok=True, json=lambda: {"id": "reply_ctr_1"})
            else:
                # Publish the reply container
                return MagicMock(ok=True, json=lambda: {"id": "reply_post_1"})

        with patch("genlab_core.platforms.threads._META_SESSION") as mock_req:
            mock_req.post.side_effect = mock_post
            ok = threads_client.post_reply(
                parent_id="thread_123",
                text="Great post!",
            )

        assert ok is True

    def test_post_reply_no_context_id_needed(self, threads_client):
        """context_id is optional and defaults to empty string."""
        post_call_count = {"n": 0}

        def mock_post(url, *args, **kwargs):
            post_call_count["n"] += 1
            if post_call_count["n"] == 1:
                return MagicMock(ok=True, json=lambda: {"id": "reply_ctr_2"})
            return MagicMock(ok=True, json=lambda: {"id": "reply_post_2"})

        with patch("genlab_core.platforms.threads._META_SESSION") as mock_req:
            mock_req.post.side_effect = mock_post
            # No context_id passed
            ok = threads_client.post_reply(
                parent_id="thread_456",
                text="Nice!",
            )

        assert ok is True

    def test_post_reply_sends_reply_to_id(self, threads_client):
        """reply container must include reply_to_id in request data."""
        captured_data = []

        post_call_count = {"n": 0}

        def capture_post(url, *args, **kwargs):
            data = kwargs.get("data", {})
            captured_data.append(data)
            post_call_count["n"] += 1
            if post_call_count["n"] == 1:
                return MagicMock(ok=True, json=lambda: {"id": "reply_ctr_3"})
            return MagicMock(ok=True, json=lambda: {"id": "reply_post_3"})

        with patch("genlab_core.platforms.threads._META_SESSION") as mock_req:
            mock_req.post.side_effect = capture_post
            threads_client.post_reply(parent_id="thread_parent_789", text="Hello!")

        # First POST is the container creation
        container_data = captured_data[0]
        assert container_data.get("reply_to_id") == "thread_parent_789"

    def test_post_reply_failure_returns_false(self, threads_client):
        """API error on reply → returns False."""
        with patch("genlab_core.platforms.threads._META_SESSION") as mock_req:
            mock_req.post.return_value = MagicMock(
                ok=False,
                status_code=400,
                json=lambda: {"error": {"message": "Comment not allowed"}},
            )
            ok = threads_client.post_reply(parent_id="thread_bad", text="Reply")

        assert ok is False

    def test_post_reply_exception_returns_false(self, threads_client):
        """Network exception on reply → returns False without raising."""
        with patch("genlab_core.platforms.threads._META_SESSION") as mock_req:
            mock_req.post.side_effect = ConnectionError("Network unreachable")
            ok = threads_client.post_reply(parent_id="thread_err", text="Hi")

        assert ok is False


# ---------------------------------------------------------------------------
# TestTokenHealth
# ---------------------------------------------------------------------------


class TestTokenHealth:
    def test_valid_token(self, threads_client):
        """GET /me returns 200 → TokenStatus valid=True."""
        with patch("genlab_core.platforms.threads._META_SESSION") as mock_req:
            mock_req.get.return_value = MagicMock(
                status_code=200,
                ok=True,
                json=lambda: {"id": "123456789", "name": "TestUser"},
            )
            status = threads_client.check_token_health()

        assert status.valid is True
        assert status.platform == "threads"

    def test_invalid_token(self, threads_client):
        """GET /me returns 401 → TokenStatus valid=False."""
        with patch("genlab_core.platforms.threads._META_SESSION") as mock_req:
            mock_req.get.return_value = MagicMock(
                status_code=401,
                ok=False,
                json=lambda: {"error": {"message": "Invalid token"}},
            )
            status = threads_client.check_token_health()

        assert status.valid is False
        assert status.platform == "threads"

    def test_token_health_message_non_empty_on_success(self, threads_client):
        """TokenStatus.message is non-empty when token is valid."""
        with patch("genlab_core.platforms.threads._META_SESSION") as mock_req:
            mock_req.get.return_value = MagicMock(
                status_code=200,
                ok=True,
                json=lambda: {"id": "123456789", "name": "MyAccount"},
            )
            status = threads_client.check_token_health()

        assert status.message != ""

    def test_token_health_needs_refresh_false_when_no_issued_at(self, threads_client):
        """Without THREADS_TOKEN_ISSUED_AT, needs_refresh stays False (can't know age)."""
        with patch("genlab_core.platforms.threads._META_SESSION") as mock_req:
            with patch.dict(os.environ, {}, clear=False):
                # Ensure env var is absent
                os.environ.pop("THREADS_TOKEN_ISSUED_AT", None)
                mock_req.get.return_value = MagicMock(
                    status_code=200,
                    ok=True,
                    json=lambda: {"id": "123456789", "name": "MyAccount"},
                )
                status = threads_client.check_token_health()

        # Without issued_at we can't know age, needs_refresh must be False
        assert status.needs_refresh is False

    def test_token_health_needs_refresh_true_when_old_token(self, threads_client):
        """THREADS_TOKEN_ISSUED_AT > 50 days ago → needs_refresh=True."""
        from datetime import datetime, timedelta

        old_date = (datetime.now(UTC) - timedelta(days=51)).isoformat()

        with patch("genlab_core.platforms.threads._META_SESSION") as mock_req:
            with patch.dict(os.environ, {"THREADS_TOKEN_ISSUED_AT": old_date}):
                mock_req.get.return_value = MagicMock(
                    status_code=200,
                    ok=True,
                    json=lambda: {"id": "123456789", "name": "MyAccount"},
                )
                status = threads_client.check_token_health()

        assert status.needs_refresh is True

    def test_token_health_needs_refresh_false_when_fresh_token(self, threads_client):
        """THREADS_TOKEN_ISSUED_AT < 50 days ago → needs_refresh=False."""
        from datetime import datetime, timedelta

        fresh_date = (datetime.now(UTC) - timedelta(days=5)).isoformat()

        with patch("genlab_core.platforms.threads._META_SESSION") as mock_req:
            with patch.dict(os.environ, {"THREADS_TOKEN_ISSUED_AT": fresh_date}):
                mock_req.get.return_value = MagicMock(
                    status_code=200,
                    ok=True,
                    json=lambda: {"id": "123456789", "name": "MyAccount"},
                )
                status = threads_client.check_token_health()

        assert status.needs_refresh is False

    def test_token_health_exception_returns_invalid(self, threads_client):
        """Network exception → valid=False, no raise."""
        with patch("genlab_core.platforms.threads._META_SESSION") as mock_req:
            mock_req.get.side_effect = ConnectionError("Timeout")
            status = threads_client.check_token_health()

        assert status.valid is False

    def test_platform_id_is_threads(self):
        """platform_id class attribute must be 'threads'."""
        from genlab_core.platforms.threads import ThreadsClient

        assert ThreadsClient.platform_id == "threads"


# ---------------------------------------------------------------------------
# TestInit
# ---------------------------------------------------------------------------


class TestInit:
    def test_env_var_defaults(self, monkeypatch):
        """Constructor falls back to env vars when args are omitted."""
        monkeypatch.setenv("THREADS_ACCESS_TOKEN", "token_from_env")
        monkeypatch.setenv("THREADS_USER_ID", "user_from_env")

        from genlab_core.platforms.threads import ThreadsClient

        client = ThreadsClient()
        assert client._access_token == "token_from_env"
        assert client._user_id == "user_from_env"

    def test_explicit_args_override_env(self, monkeypatch):
        """Explicit constructor args take precedence over env vars."""
        monkeypatch.setenv("THREADS_ACCESS_TOKEN", "token_from_env")
        monkeypatch.setenv("THREADS_USER_ID", "user_from_env")

        from genlab_core.platforms.threads import ThreadsClient

        client = ThreadsClient(access_token="explicit_token", user_id="explicit_user")
        assert client._access_token == "explicit_token"
        assert client._user_id == "explicit_user"

    def test_base_url_is_graph_threads_net(self):
        """Base URL must be graph.threads.net/v1.0."""
        from genlab_core.platforms.threads import ThreadsClient

        client = ThreadsClient(access_token="t", user_id="u")
        assert "graph.threads.net" in client._base_url
        assert "v1.0" in client._base_url
