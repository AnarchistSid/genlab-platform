"""Tests for genlab_core.platform.postiz_client.

All tests use httpx mock transport — no real Postiz server needed.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from genlab_core.platform.postiz_client import (
    MultiPublishResult,
    PostizClient,
    PostizPlatform,
    PublishResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_transport(handler):
    """Create an httpx.MockTransport from a handler function."""
    return httpx.MockTransport(handler)


def _make_client(handler, **kwargs) -> PostizClient:
    """Build a PostizClient with a mocked transport."""
    client = PostizClient(
        base_url="http://postiz.test",
        api_key="test-key-123",
        **kwargs,
    )
    # Replace the lazy-init client with one using our mock transport
    client._client = httpx.Client(
        base_url="http://postiz.test",
        headers={
            "Authorization": "Bearer test-key-123",
            "Content-Type": "application/json",
        },
        transport=_mock_transport(handler),
        timeout=30.0,
    )
    return client


# ---------------------------------------------------------------------------
# 1. Successful single-platform publish
# ---------------------------------------------------------------------------


class TestPublishSuccess:
    def test_returns_post_id_and_url(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/posts"
            body = json.loads(request.content)
            assert body["integrationId"] == "ig_123"
            assert body["content"] == "Test caption"
            return httpx.Response(
                200,
                json={"id": "post_abc", "url": "https://instagram.com/p/abc"},
            )

        client = _make_client(handler)
        result = client.publish(
            integration_id="ig_123",
            text="Test caption",
            platform="instagram",
        )
        assert result.success is True
        assert result.post_id == "post_abc"
        assert result.post_url == "https://instagram.com/p/abc"
        assert result.platform == "instagram"
        client.close()


# ---------------------------------------------------------------------------
# 2. Publish with media_url attached
# ---------------------------------------------------------------------------


class TestPublishWithMedia:
    def test_media_url_in_payload(self):
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["media"] == [{"url": "https://cdn.example.com/video.mp4"}]
            return httpx.Response(200, json={"id": "p1"})

        client = _make_client(handler)
        result = client.publish(
            integration_id="yt_456",
            text="Video post",
            media_url="https://cdn.example.com/video.mp4",
            platform="youtube",
        )
        assert result.success is True
        client.close()


# ---------------------------------------------------------------------------
# 3. HTTP 401 returns PublishResult with success=False
# ---------------------------------------------------------------------------


class TestPublish401:
    def test_unauthorized_returns_failure(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="Invalid API key")

        client = _make_client(handler)
        result = client.publish(
            integration_id="ig_123",
            text="Test",
            platform="instagram",
        )
        assert result.success is False
        assert "401" in result.error
        assert result.post_id == ""
        client.close()


# ---------------------------------------------------------------------------
# 4. HTTP 500 returns PublishResult with success=False
# ---------------------------------------------------------------------------


class TestPublish500:
    def test_server_error_returns_failure(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        client = _make_client(handler)
        result = client.publish(
            integration_id="fb_789",
            text="Test",
            platform="facebook",
        )
        assert result.success is False
        assert "500" in result.error
        client.close()


# ---------------------------------------------------------------------------
# 5. Multi-platform publish aggregates results
# ---------------------------------------------------------------------------


class TestMultiPublish:
    def test_mixed_success_and_failure(self):
        call_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            body = json.loads(request.content)
            if body["integrationId"] == "ig_ok":
                return httpx.Response(200, json={"id": "p1", "url": "https://ig/p1"})
            else:
                return httpx.Response(403, text="Forbidden")

        client = _make_client(handler)
        result = client.publish_multi(
            integration_ids={
                "instagram": "ig_ok",
                "youtube": "yt_fail",
            },
            text="Multi test",
        )
        assert len(result.results) == 2
        assert result.any_succeeded is True
        assert result.all_succeeded is False
        assert len(result.succeeded) == 1
        assert len(result.failed) == 1
        assert result.succeeded[0].platform == "instagram"
        assert result.failed[0].platform == "youtube"
        client.close()


# ---------------------------------------------------------------------------
# 6. list_integrations returns parsed JSON
# ---------------------------------------------------------------------------


class TestListIntegrations:
    def test_returns_integration_list(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/integrations"
            return httpx.Response(
                200,
                json=[
                    {"id": "ig_1", "name": "CriticalRush IG", "providerIdentifier": "instagram"},
                    {"id": "yt_1", "name": "CriticalRush YT", "providerIdentifier": "youtube"},
                ],
            )

        client = _make_client(handler)
        integrations = client.list_integrations()
        assert len(integrations) == 2
        assert integrations[0]["providerIdentifier"] == "instagram"
        client.close()


# ---------------------------------------------------------------------------
# 7. get_integration_by_provider finds correct integration
# ---------------------------------------------------------------------------


class TestGetIntegrationByProvider:
    def test_finds_matching_provider(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {"id": "ig_1", "providerIdentifier": "instagram"},
                    {"id": "yt_1", "providerIdentifier": "youtube"},
                ],
            )

        client = _make_client(handler)
        ig = client.get_integration_by_provider("instagram")
        assert ig is not None
        assert ig["id"] == "ig_1"

        missing = client.get_integration_by_provider("tiktok")
        assert missing is None
        client.close()


# ---------------------------------------------------------------------------
# 8. Missing API key raises ValueError
# ---------------------------------------------------------------------------


class TestMissingApiKey:
    def test_no_key_raises(self):
        with patch(
            "genlab_core.platform.postiz_client.settings"
        ) as mock_settings:
            mock_settings.postiz_url = "http://localhost:5000"
            mock_settings.postiz_api_key = None
            with pytest.raises(ValueError, match="API key required"):
                PostizClient()


# ---------------------------------------------------------------------------
# 9. health_check returns True on 200, False on error
# ---------------------------------------------------------------------------


class TestHealthCheck:
    def test_healthy(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/user":
                return httpx.Response(200, json={"id": "user1"})
            return httpx.Response(404)

        client = _make_client(handler)
        assert client.health_check() is True
        client.close()

    def test_unhealthy(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="Service Unavailable")

        client = _make_client(handler)
        assert client.health_check() is False
        client.close()


# ---------------------------------------------------------------------------
# 10. PublishResult and MultiPublishResult dataclass behavior
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_publish_result_defaults(self):
        r = PublishResult(platform="instagram", success=True)
        assert r.post_id == ""
        assert r.post_url == ""
        assert r.error == ""
        assert r.raw_response == {}

    def test_multi_publish_result_empty(self):
        m = MultiPublishResult()
        assert m.all_succeeded is False  # empty → False
        assert m.any_succeeded is False
        assert m.failed == []
        assert m.succeeded == []

    def test_multi_publish_result_all_success(self):
        m = MultiPublishResult(
            results=[
                PublishResult(platform="ig", success=True),
                PublishResult(platform="yt", success=True),
            ]
        )
        assert m.all_succeeded is True
        assert m.any_succeeded is True
        assert len(m.succeeded) == 2
        assert len(m.failed) == 0

    def test_postiz_platform_enum(self):
        assert PostizPlatform.INSTAGRAM.value == "instagram"
        assert PostizPlatform.THREADS.value == "threads"


