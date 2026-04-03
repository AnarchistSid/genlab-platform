"""Tests for TikTokClient — Content Posting API v2."""

import os
import tempfile
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.publishing.tiktok_client import TikTokClient


@pytest.fixture
def mock_settings():
    """Provide mock settings with TikTok credentials."""
    with patch("genlab_core.publishing.tiktok_client.settings") as s:
        s.tiktok_client_key = "test-client-key"
        s.tiktok_client_secret = "test-client-secret"
        s.tiktok_access_token = "test-access-token"
        s.tiktok_refresh_token = "test-refresh-token"
        s.tiktok_token_issued_at = None
        s.tiktok_audit_approved = "false"
        yield s


@pytest.fixture
def video_file():
    """Create a temporary 25MB fake video file for upload tests."""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        # Write exactly 25MB (25 * 1024 * 1024 = 26214400 bytes)
        f.write(b"\x00" * 26214400)
        path = f.name
    yield path
    os.unlink(path)


class TestPublishVideoSelfOnlyWhenUnaudited:
    @patch("genlab_core.publishing.tiktok_client.requests.post")
    def test_self_only_privacy(self, mock_post, mock_settings, video_file):
        """When TIKTOK_AUDIT_APPROVED=false, privacy_level must be SELF_ONLY."""
        mock_settings.tiktok_audit_approved = "false"

        # Mock creator info query
        creator_resp = MagicMock()
        creator_resp.ok = True
        creator_resp.json.return_value = {"data": {"max_video_post_duration_sec": 600}}

        # Mock init
        init_resp = MagicMock()
        init_resp.ok = True
        init_resp.json.return_value = {
            "data": {"publish_id": "pub_123", "upload_url": "https://upload.tiktok.com/video"}
        }

        # Mock status poll
        status_resp = MagicMock()
        status_resp.ok = True
        status_resp.json.return_value = {"data": {"status": "PUBLISH_COMPLETE"}}

        mock_post.side_effect = [creator_resp, init_resp, status_resp]

        with patch.object(TikTokClient, "_upload_chunks"):
            with patch.object(TikTokClient, "_get_video_duration", return_value=30.0):
                client = TikTokClient()
                client.publish_video(video_file, "Test caption")

        # Check init call body for SELF_ONLY
        init_call = mock_post.call_args_list[1]
        init_body = init_call[1]["json"]
        assert init_body["post_info"]["privacy_level"] == "SELF_ONLY"


class TestPublishVideoPublicWhenAudited:
    @patch("genlab_core.publishing.tiktok_client.requests.post")
    def test_public_privacy(self, mock_post, mock_settings, video_file):
        """When TIKTOK_AUDIT_APPROVED=true, privacy_level must be PUBLIC_TO_EVERYONE."""
        mock_settings.tiktok_audit_approved = "true"

        creator_resp = MagicMock()
        creator_resp.ok = True
        creator_resp.json.return_value = {"data": {"max_video_post_duration_sec": 600}}

        init_resp = MagicMock()
        init_resp.ok = True
        init_resp.json.return_value = {
            "data": {"publish_id": "pub_456", "upload_url": "https://upload.tiktok.com/video"}
        }

        status_resp = MagicMock()
        status_resp.ok = True
        status_resp.json.return_value = {"data": {"status": "PUBLISH_COMPLETE"}}

        mock_post.side_effect = [creator_resp, init_resp, status_resp]

        with patch.object(TikTokClient, "_upload_chunks"):
            with patch.object(TikTokClient, "_get_video_duration", return_value=30.0):
                client = TikTokClient(audit_approved=True)
                client.publish_video(video_file, "Test caption")

        init_call = mock_post.call_args_list[1]
        init_body = init_call[1]["json"]
        assert init_body["post_info"]["privacy_level"] == "PUBLIC_TO_EVERYONE"


class TestChunkUploadCorrectRanges:
    @patch("genlab_core.publishing.tiktok_client.requests.put")
    @patch("genlab_core.publishing.tiktok_client.time.monotonic")
    def test_25mb_produces_3_chunks(self, mock_monotonic, mock_put, mock_settings, video_file):
        """A 25MB file with 10MB chunks should produce 3 PUT requests with correct ranges."""
        mock_monotonic.return_value = 1.0  # constant time for rate calc
        mock_put.return_value = MagicMock(ok=True)

        client = TikTokClient()
        client._upload_chunks("https://upload.tiktok.com/video", video_file)

        assert mock_put.call_count == 3

        file_size = 26214400  # 25MB exactly
        chunk_size = 10 * 1024 * 1024  # 10MB

        # Verify Content-Range headers
        for i, put_call in enumerate(mock_put.call_args_list):
            headers = put_call[1]["headers"]
            start = i * chunk_size
            end = min(start + chunk_size, file_size) - 1
            expected_range = f"bytes {start}-{end}/{file_size}"
            assert headers["Content-Range"] == expected_range, (
                f"Chunk {i}: expected {expected_range}, got {headers['Content-Range']}"
            )


class TestRefreshTokenRotation:
    @patch("genlab_core.publishing.tiktok_client.requests.post")
    def test_new_refresh_token_stored(self, mock_post, mock_settings):
        """After token refresh, the NEW refresh_token must be stored (rotation)."""
        refresh_resp = MagicMock()
        refresh_resp.ok = True
        refresh_resp.json.return_value = {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token-rotated",
        }
        mock_post.return_value = refresh_resp

        client = TikTokClient()
        old_refresh = client._refresh_token

        client._refresh_access_token()

        assert client._access_token == "new-access-token"
        assert client._refresh_token == "new-refresh-token-rotated"
        assert client._refresh_token != old_refresh


class TestDurationGuard:
    @patch("genlab_core.publishing.tiktok_client.requests.post")
    def test_video_exceeding_max_duration_raises(self, mock_post, mock_settings, video_file):
        """Video longer than creator max should raise ValueError before init call."""
        creator_resp = MagicMock()
        creator_resp.ok = True
        creator_resp.json.return_value = {"data": {"max_video_post_duration_sec": 300}}
        mock_post.return_value = creator_resp

        with patch.object(TikTokClient, "_get_video_duration", return_value=400.0):
            client = TikTokClient()
            with pytest.raises(ValueError, match="exceeds TikTok max"):
                client.publish_video(video_file, "Test caption")

        # Only creator_info query should have been called — NOT init
        assert mock_post.call_count == 1
        assert "creator_info" in mock_post.call_args[0][0]


class TestAccessTokenRefreshAt23h:
    @patch("genlab_core.publishing.tiktok_client.requests.post")
    def test_refresh_called_at_23h(self, mock_post, mock_settings):
        """Token issued 23+ hours ago should trigger refresh before posting."""
        issued_at = datetime.now(UTC) - timedelta(hours=24)
        mock_settings.tiktok_token_issued_at = issued_at.isoformat()

        refresh_resp = MagicMock()
        refresh_resp.ok = True
        refresh_resp.json.return_value = {
            "access_token": "refreshed-token",
            "refresh_token": "new-refresh",
        }
        mock_post.return_value = refresh_resp

        client = TikTokClient()
        client._ensure_token_fresh()

        # The refresh endpoint should have been called
        assert mock_post.call_count == 1
        call_data = mock_post.call_args[1]["data"]
        assert call_data["grant_type"] == "refresh_token"
        assert client._access_token == "refreshed-token"

    def test_no_refresh_when_fresh(self, mock_settings):
        """Token issued 1 hour ago should NOT trigger refresh."""
        issued_at = datetime.now(UTC) - timedelta(hours=1)
        mock_settings.tiktok_token_issued_at = issued_at.isoformat()

        client = TikTokClient()
        # Should not raise or call any API
        client._ensure_token_fresh()
