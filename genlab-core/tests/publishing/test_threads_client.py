"""Tests for ThreadsClient — text, image, and video publishing.

All external HTTP calls are mocked. No real API calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from genlab_core.publishing.threads_client import (
    THREADS_MAX_CAPTION,
    THREADS_PROCESSING_WAIT,
    ThreadsClient,
    ThreadsTokenManager,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    """ThreadsClient with injected credentials (no env lookup)."""
    return ThreadsClient(access_token="test-token-123", user_id="99999")


def _mock_response(json_data: dict, ok: bool = True, status_code: int = 200):
    resp = MagicMock(spec=requests.Response)
    resp.ok = ok
    resp.status_code = status_code
    resp.json.return_value = json_data
    if ok:
        resp.raise_for_status.return_value = None
    else:
        resp.raise_for_status.side_effect = requests.HTTPError(response=resp)
    return resp


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

class TestThreadsClientInit:
    def test_requires_access_token(self):
        with pytest.raises(ValueError, match="THREADS_ACCESS_TOKEN"):
            ThreadsClient(access_token=None, user_id="123")

    def test_requires_user_id(self):
        with pytest.raises(ValueError, match="THREADS_USER_ID"):
            ThreadsClient(access_token="tok", user_id=None)

    def test_constructs_base_url(self, client):
        assert client._base_url == "https://graph.threads.net/v1.0"


# ---------------------------------------------------------------------------
# publish_text
# ---------------------------------------------------------------------------

class TestPublishText:
    @patch("genlab_core.publishing.threads_client.requests.get")
    @patch("genlab_core.publishing.threads_client.requests.post")
    def test_publish_text_success(self, mock_post, mock_get, client):
        mock_post.side_effect = [
            _mock_response({"id": "container-1"}),   # create container
            _mock_response({"id": "thread-post-1"}),  # publish
        ]
        mock_get.return_value = _mock_response(
            {"permalink": "https://threads.net/@user/post/abc123"}
        )

        result = client.publish_text("Hello Threads!")

        assert result["threads_id"] == "thread-post-1"
        assert result["permalink"] == "https://threads.net/@user/post/abc123"

        # Verify container creation payload
        create_call = mock_post.call_args_list[0]
        assert create_call[1]["data"]["media_type"] == "TEXT"
        assert create_call[1]["data"]["text"] == "Hello Threads!"
        assert create_call[1]["data"]["reply_control"] == "everyone"

    @patch("genlab_core.publishing.threads_client.requests.get")
    @patch("genlab_core.publishing.threads_client.requests.post")
    def test_publish_text_truncates_long_text(self, mock_post, mock_get, client):
        long_text = "A" * 600
        mock_post.side_effect = [
            _mock_response({"id": "c1"}),
            _mock_response({"id": "t1"}),
        ]
        mock_get.return_value = _mock_response({"permalink": ""})

        client.publish_text(long_text)

        sent_text = mock_post.call_args_list[0][1]["data"]["text"]
        assert len(sent_text) == THREADS_MAX_CAPTION
        assert sent_text.endswith("…")

    @patch("genlab_core.publishing.threads_client.requests.post")
    def test_publish_text_container_error(self, mock_post, client):
        mock_post.return_value = _mock_response({}, ok=False, status_code=400)

        with pytest.raises(requests.HTTPError):
            client.publish_text("Hello")


# ---------------------------------------------------------------------------
# publish_image
# ---------------------------------------------------------------------------

class TestPublishImage:
    @patch("genlab_core.publishing.threads_client.requests.get")
    @patch("genlab_core.publishing.threads_client.requests.post")
    def test_publish_image_success(self, mock_post, mock_get, client):
        mock_post.side_effect = [
            _mock_response({"id": "img-container-1"}),
            _mock_response({"id": "img-post-1"}),
        ]
        mock_get.return_value = _mock_response(
            {"permalink": "https://threads.net/@user/post/img1"}
        )

        result = client.publish_image(
            image_url="https://cdn.example.com/image.jpg",
            caption="Check this out!",
        )

        assert result["threads_id"] == "img-post-1"
        assert "img1" in result["permalink"]

        create_call = mock_post.call_args_list[0]
        assert create_call[1]["data"]["media_type"] == "IMAGE"
        assert create_call[1]["data"]["image_url"] == "https://cdn.example.com/image.jpg"
        assert create_call[1]["data"]["text"] == "Check this out!"

    @patch("genlab_core.publishing.threads_client.requests.get")
    @patch("genlab_core.publishing.threads_client.requests.post")
    def test_publish_image_empty_caption(self, mock_post, mock_get, client):
        mock_post.side_effect = [
            _mock_response({"id": "c1"}),
            _mock_response({"id": "t1"}),
        ]
        mock_get.return_value = _mock_response({"permalink": ""})

        result = client.publish_image("https://cdn.example.com/img.jpg")

        create_call = mock_post.call_args_list[0]
        assert create_call[1]["data"]["text"] == ""

    @patch("genlab_core.publishing.threads_client.requests.get")
    @patch("genlab_core.publishing.threads_client.requests.post")
    def test_publish_image_truncates_caption(self, mock_post, mock_get, client):
        mock_post.side_effect = [
            _mock_response({"id": "c1"}),
            _mock_response({"id": "t1"}),
        ]
        mock_get.return_value = _mock_response({"permalink": ""})

        client.publish_image("https://cdn.example.com/img.jpg", caption="B" * 600)
        sent_text = mock_post.call_args_list[0][1]["data"]["text"]
        assert len(sent_text) == THREADS_MAX_CAPTION


# ---------------------------------------------------------------------------
# publish_video
# ---------------------------------------------------------------------------

class TestPublishVideo:
    @patch("genlab_core.publishing.threads_client.time.sleep")
    @patch("genlab_core.publishing.threads_client.requests.get")
    @patch("genlab_core.publishing.threads_client.requests.post")
    def test_publish_video_success(self, mock_post, mock_get, mock_sleep, client):
        mock_post.side_effect = [
            _mock_response({"id": "vid-container-1"}),
            _mock_response({"id": "vid-post-1"}),
        ]
        mock_get.return_value = _mock_response(
            {"permalink": "https://threads.net/@user/post/vid1"}
        )

        result = client.publish_video(
            video_url="https://cdn.example.com/video.mp4",
            caption="Watch this!",
        )

        assert result["threads_id"] == "vid-post-1"
        mock_sleep.assert_called_once_with(THREADS_PROCESSING_WAIT)

        create_call = mock_post.call_args_list[0]
        assert create_call[1]["data"]["media_type"] == "VIDEO"
        assert create_call[1]["data"]["video_url"] == "https://cdn.example.com/video.mp4"

    @patch("genlab_core.publishing.threads_client.time.sleep")
    @patch("genlab_core.publishing.threads_client.requests.get")
    @patch("genlab_core.publishing.threads_client.requests.post")
    def test_publish_video_truncates_caption(self, mock_post, mock_get, mock_sleep, client):
        mock_post.side_effect = [
            _mock_response({"id": "c1"}),
            _mock_response({"id": "t1"}),
        ]
        mock_get.return_value = _mock_response({"permalink": ""})

        client.publish_video("https://cdn.example.com/v.mp4", caption="C" * 600)
        sent_text = mock_post.call_args_list[0][1]["data"]["text"]
        assert len(sent_text) == THREADS_MAX_CAPTION


# ---------------------------------------------------------------------------
# get_post_insights
# ---------------------------------------------------------------------------

class TestGetPostInsights:
    @patch("genlab_core.publishing.threads_client.requests.get")
    def test_returns_metrics(self, mock_get, client):
        mock_get.return_value = _mock_response({
            "data": [
                {"name": "views", "values": [{"value": 1200}]},
                {"name": "likes", "values": [{"value": 45}]},
                {"name": "replies", "values": [{"value": 8}]},
            ]
        })

        metrics = client.get_post_insights("post-123")

        assert metrics["views"] == 1200
        assert metrics["likes"] == 45
        assert metrics["replies"] == 8

    @patch("genlab_core.publishing.threads_client.requests.get")
    def test_handles_empty_values(self, mock_get, client):
        mock_get.return_value = _mock_response({
            "data": [
                {"name": "views", "values": []},
            ]
        })

        metrics = client.get_post_insights("post-123")
        assert metrics["views"] == 0


# ---------------------------------------------------------------------------
# refresh_token
# ---------------------------------------------------------------------------

class TestRefreshToken:
    @patch("genlab_core.publishing.threads_client.requests.get")
    def test_refresh_updates_token(self, mock_get, client):
        mock_get.return_value = _mock_response({
            "access_token": "new-token-456",
            "expires_in": 5184000,
        })

        new_token = client.refresh_token()

        assert new_token == "new-token-456"
        assert client._access_token == "new-token-456"


# ---------------------------------------------------------------------------
# _get_permalink
# ---------------------------------------------------------------------------

class TestGetPermalink:
    @patch("genlab_core.publishing.threads_client.requests.get")
    def test_returns_permalink(self, mock_get, client):
        mock_get.return_value = _mock_response(
            {"permalink": "https://threads.net/@user/post/xyz"}
        )
        assert client._get_permalink("123") == "https://threads.net/@user/post/xyz"

    @patch("genlab_core.publishing.threads_client.requests.get")
    def test_returns_empty_on_error(self, mock_get, client):
        mock_get.return_value = _mock_response({}, ok=False, status_code=404)
        assert client._get_permalink("123") == ""


# ---------------------------------------------------------------------------
# ThreadsTokenManager
# ---------------------------------------------------------------------------

class TestThreadsTokenManager:
    @patch("genlab_core.publishing.threads_client.settings")
    def test_unknown_when_no_issued_at(self, mock_settings):
        mock_settings.threads_token_issued_at = None
        manager = ThreadsTokenManager()
        result = manager.check_and_refresh()
        assert result["status"] == "unknown"

    @patch("genlab_core.publishing.threads_client.settings")
    def test_ok_when_fresh(self, mock_settings):
        from datetime import datetime, timezone
        mock_settings.threads_token_issued_at = datetime.now(timezone.utc).isoformat()
        manager = ThreadsTokenManager()
        result = manager.check_and_refresh()
        assert result["status"] == "ok"
        assert result["days_remaining"] == 60
