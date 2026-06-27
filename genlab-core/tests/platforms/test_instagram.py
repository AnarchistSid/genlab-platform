"""Tests for InstagramClient — mocks all HTTP."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.platforms.models import InstagramSpecific, PublishPayload


@pytest.fixture
def ig_client():
    from genlab_core.platforms.instagram import InstagramClient

    return InstagramClient(
        access_token="EAA_TEST_TOKEN",
        ig_user_id="17841400000000001",
        api_version="v21.0",
    )


@pytest.fixture(autouse=True)
def _mock_cdn_upload():
    """All Instagram publish tests need CDN upload mocked (local paths don't exist).

    Patches both the structured ``upload_to_cdn_full`` (used by the publish
    path's per-provider retry loop) and the legacy ``upload_to_cdn`` shim
    so any test that exercises either entry point keeps working.
    """
    from genlab_core.platforms.cdn_upload import PROVIDER_LITTERBOX, CdnUploadResult

    with (
        patch(
            "genlab_core.platforms.cdn_upload.upload_to_cdn_full",
            return_value=CdnUploadResult(
                url="https://cdn.test/clip.mp4",
                provider=PROVIDER_LITTERBOX,
                size_mb=1.0,
            ),
        ),
        patch(
            "genlab_core.platforms.cdn_upload.upload_to_cdn",
            return_value="https://cdn.test/clip.mp4",
        ),
    ):
        yield


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
                json=lambda: {"id": "17841400000000001", "name": "Test"},
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
                json=lambda: {"id": "17841400000000001", "name": "BB Page"},
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

    def test_default_api_version_follows_centralized_constant(self):
        """Default ``api_version`` is sourced from
        ``genlab_core.platforms.meta_api.META_GRAPH_API_VERSION`` —
        the single source of truth that R-44 introduced. Bumping
        the constant should flow through here without any test edit."""
        from genlab_core.platforms.instagram import InstagramClient
        from genlab_core.platforms.meta_api import META_GRAPH_API_VERSION

        client = InstagramClient(access_token="t", ig_user_id="u")
        assert META_GRAPH_API_VERSION in client._base_url

    def test_max_poll_seconds_default(self):
        """Default poll timeout matches _DEFAULT_MAX_POLL_SECONDS module constant.

        IG container processing for video can legitimately take 5-8 min for
        complex videos. The default was raised from 120 to 480 to reduce
        spurious timeout failures on motion-heavy reels.
        """
        from genlab_core.platforms.instagram import (
            _DEFAULT_MAX_POLL_SECONDS,
            InstagramClient,
        )

        client = InstagramClient(access_token="t", ig_user_id="u")
        assert client._max_poll_seconds == _DEFAULT_MAX_POLL_SECONDS

    def test_max_poll_seconds_custom(self):
        """Custom poll timeout is stored on the instance."""
        from genlab_core.platforms.instagram import InstagramClient

        client = InstagramClient(access_token="t", ig_user_id="u", max_poll_seconds=600)
        assert client._max_poll_seconds == 600


class TestNicheIdResolution:
    """P2 phase 1 (2026-06-19): per-niche token resolution via the new
    ``niche_id`` kwarg. 3-tier lookup: explicit kwarg → niche-resolved →
    global env fallback. Existing callers that don't pass ``niche_id``
    continue to use the env fallback (backward compatible)."""

    def test_niche_id_kwarg_resolves_via_niche_credentials(self, monkeypatch):
        """When niche_id is set and explicit token is None, the client
        resolves via ``resolve_meta_credentials(niche_id)`` — not the
        global META_ACCESS_TOKEN."""
        monkeypatch.setenv("META_ACCESS_TOKEN", "GLOBAL_token")  # should NOT be used
        from genlab_core.platforms.instagram import InstagramClient

        with patch(
            "genlab_core.publishing.niche_credentials.resolve_meta_credentials",
            return_value={"ig_access_token": "CW_per_niche_token"},
        ) as mock_resolve:
            client = InstagramClient(niche_id="sports")
        assert client._access_token == "CW_per_niche_token"
        assert client.niche_id == "sports"
        mock_resolve.assert_called_once_with("sports")

    def test_explicit_token_overrides_niche_id(self, monkeypatch):
        """Test fixtures pass access_token explicitly — this must win
        over niche_id-resolved tokens AND env fallback. Otherwise existing
        tests would unexpectedly route through the niche resolver."""
        monkeypatch.setenv("META_ACCESS_TOKEN", "ENV_token")
        from genlab_core.platforms.instagram import InstagramClient

        with patch(
            "genlab_core.publishing.niche_credentials.resolve_meta_credentials",
            return_value={"ig_access_token": "NICHE_token"},
        ) as mock_resolve:
            client = InstagramClient(access_token="EXPLICIT_token", niche_id="sports")
        assert client._access_token == "EXPLICIT_token"
        # When explicit is provided, niche resolver is NOT called (perf + correctness)
        mock_resolve.assert_not_called()

    def test_empty_niche_id_falls_back_to_env(self, monkeypatch):
        """Backward compat: callers that don't pass niche_id keep using
        the global env fallback (this is the legacy path that ~3 callers
        in publishing/ still use)."""
        monkeypatch.setenv("META_ACCESS_TOKEN", "ENV_token")
        monkeypatch.setenv("META_IG_USER_ID", "ENV_user_id")
        from genlab_core.platforms.instagram import InstagramClient

        client = InstagramClient()  # no niche_id, no explicit token
        assert client._access_token == "ENV_token"
        assert client._ig_user_id == "ENV_user_id"

    def test_niche_id_empty_resolution_falls_back_to_env(self, monkeypatch):
        """If niche_id is set but the niche resolver returns empty (e.g.
        unknown niche or missing credentials), fall back to env. The env
        fallback is the LAST resort — niche-resolved overrides it when
        present."""
        monkeypatch.setenv("META_ACCESS_TOKEN", "ENV_fallback")
        from genlab_core.platforms.instagram import InstagramClient

        with patch(
            "genlab_core.publishing.niche_credentials.resolve_meta_credentials",
            return_value={"ig_access_token": ""},  # missing per-niche
        ):
            client = InstagramClient(niche_id="some_niche")
        assert client._access_token == "ENV_fallback"

    def test_niche_id_resolves_ig_user_id_too(self, monkeypatch):
        """ig_user_id has its own per-niche resolver path via
        ``resolve_niche_env`` (META_IG_USER_ID isn't in the meta_credentials
        bundle — it's a separate per-niche env var)."""
        monkeypatch.setenv("META_IG_USER_ID", "GLOBAL_user")
        from genlab_core.platforms.instagram import InstagramClient

        with (
            patch(
                "genlab_core.publishing.niche_credentials.resolve_meta_credentials",
                return_value={"ig_access_token": "tok"},
            ),
            patch(
                "genlab_core.publishing.niche_credentials.resolve_niche_env",
                return_value="CR_per_niche_user_id",
            ),
        ):
            client = InstagramClient(niche_id="gaming")
        assert client._ig_user_id == "CR_per_niche_user_id"


class TestNicheLoggerAdapter:
    """P2 phase 2 (2026-06-19): per-niche structured-logger adapter.
    When niche_id is set, self._log injects extra={'niche_id': ..., 'platform':
    'instagram'} so JSON log records carry niche identity for operator
    filtering. When niche_id is empty (legacy callers), self._log is the
    plain module logger — backward compatible."""

    def test_niche_id_set_creates_logger_adapter(self):
        """When niche_id is non-empty, self._log is a LoggerAdapter
        carrying niche_id + platform in its extra context."""
        import logging

        from genlab_core.platforms.instagram import InstagramClient

        client = InstagramClient(access_token="t", ig_user_id="u", niche_id="gaming")
        assert isinstance(client._log, logging.LoggerAdapter)
        assert client._log.extra == {"niche_id": "gaming", "platform": "instagram"}

    def test_empty_niche_id_uses_module_logger_directly(self):
        """Backward compat: legacy callers (no niche_id) get the plain
        module logger — no adapter overhead, no per-niche context (since
        there's none to inject)."""
        import logging

        from genlab_core.platforms.instagram import InstagramClient, logger

        client = InstagramClient(access_token="t", ig_user_id="u")
        # _log IS the module logger when niche_id is empty
        assert client._log is logger
        assert not isinstance(client._log, logging.LoggerAdapter)

    def test_adapter_injects_niche_id_into_log_records(self, caplog):
        """End-to-end: a log call via self._log surfaces niche_id in the
        record's extra field. Verifies the structlog/JSON path can promote
        it to a top-level field for Loki filtering."""
        import logging

        from genlab_core.platforms.instagram import InstagramClient

        client = InstagramClient(access_token="t", ig_user_id="u", niche_id="sports")
        with caplog.at_level(logging.INFO, logger="genlab_core.platforms.instagram"):
            client._log.info("test log message")
        assert any(r.message == "test log message" for r in caplog.records)
        rec = next(r for r in caplog.records if r.message == "test log message")
        # The LoggerAdapter merges extra into the record's __dict__
        assert getattr(rec, "niche_id", None) == "sports"
        assert getattr(rec, "platform", None) == "instagram"

    def test_different_niches_get_different_adapters(self):
        """Two IG client instances for different niches get distinct
        adapters with distinct extra context — niche identity doesn't
        bleed across instances."""
        from genlab_core.platforms.instagram import InstagramClient

        a = InstagramClient(access_token="t", ig_user_id="u", niche_id="gaming")
        b = InstagramClient(access_token="t", ig_user_id="u", niche_id="movies")
        assert a._log.extra["niche_id"] == "gaming"
        assert b._log.extra["niche_id"] == "movies"
        assert a._log is not b._log


class TestVerifyChannel:
    def test_verify_channel_success(self, ig_client):
        """verify_channel returns True when the IG account is accessible."""
        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            mock_req.get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"id": "17841400000000001", "username": "blackboxbrief"},
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
                json=lambda: {"id": "17841400000000001", "username": "bb"},
            )
            ig_client.verify_channel()

        call_kwargs = mock_req.get.call_args
        params = call_kwargs[1].get("params") or call_kwargs[0][1]
        assert "id" in params.get("fields", "")
        assert "username" in params.get("fields", "")
