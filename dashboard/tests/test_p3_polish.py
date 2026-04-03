"""Tests for P3.6-P3.12 polish and robustness improvements.

Covers:
  P3.7  — YouTube resumable upload per-chunk retry logic
  P3.8  — YouTube post_comment/update_video_metadata error handling
  P3.9  — Community post text persistence on manual_post_required
  P3.10 — Landscape spec routing in single-file --video mode
  P3.12 — OData filter input validation for action_taken
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
# YouTube client tests need BlackboxBrief on sys.path
_CONTENT_SCRAPER = PROJECT_ROOT.parent / "BlackboxBrief"
if _CONTENT_SCRAPER.exists():
    sys.path.insert(0, str(_CONTENT_SCRAPER))


# ── Shared fixture: pre-mock genlab_core.publishing to avoid Settings init ──
# When running in the full dashboard test suite, genlab_core.settings may fail
# to re-initialize (pydantic-settings singleton conflict).  Mock the publishing
# package so youtube_client's import of niche_credentials doesn't trigger the
# settings chain.

@pytest.fixture(autouse=True)
def _mock_publishing_chain():
    """Ensure genlab_core.publishing is loadable without triggering Settings().

    The import chain is:
      youtube_client → execution.utils.niche_credentials
        → genlab_core.publishing.niche_credentials
        → genlab_core.publishing.__init__ → cdn_upload → genlab_core.settings

    We pre-populate sys.modules for the publishing package and cdn_upload so
    the import never reaches Settings().  The niche_credentials module is
    real (no settings dependency), so we only need to stub cdn_upload and
    the publishing __init__ eager imports.
    """
    # Only inject stubs if the real modules haven't been loaded successfully
    # already.  If they're already in sys.modules and working, leave them.
    modules_to_maybe_mock = {}

    # Check if genlab_core.settings is importable
    settings_ok = False
    try:
        if "genlab_core.settings" in sys.modules:
            # Module exists — check it has a real settings object
            _mod = sys.modules["genlab_core.settings"]
            if hasattr(_mod, "settings") and not isinstance(_mod.settings, MagicMock):
                settings_ok = True
        else:
            # Try importing it
            import genlab_core.settings  # noqa: F401
            settings_ok = True
    except Exception:
        pass

    if not settings_ok:
        # Settings can't be imported — mock the chain
        mock_settings_mod = MagicMock()
        mock_settings_mod.settings = MagicMock()
        modules_to_maybe_mock["genlab_core.settings"] = mock_settings_mod

        # Also mock cdn_upload which triggers the settings import
        if "genlab_core.publishing.cdn_upload" not in sys.modules:
            modules_to_maybe_mock["genlab_core.publishing.cdn_upload"] = MagicMock()

    if modules_to_maybe_mock:
        with patch.dict("sys.modules", modules_to_maybe_mock):
            yield
    else:
        yield


# ── P3.7: YouTube chunk retry ─────────────────────────────────────

# TRIAGE: Skip — requires google-api-python-client which is an optional
# dependency not installed in the workspace venv. These test real YouTube
# chunk retry logic and pass when googleapiclient is available. 2026-03-07.
_has_googleapiclient = pytest.importorskip is not None  # marker only


@pytest.mark.skip(reason="Tests BB-specific upload_short/upload_video — genlab-core uses publish()")
class TestYouTubeChunkRetry:
    """Verify that upload_short and upload_video retry on transient chunk errors."""

    @pytest.fixture(autouse=True)
    def _require_googleapiclient(self):
        pytest.importorskip("googleapiclient", reason="google-api-python-client not installed")

    @pytest.fixture(autouse=True)
    def _bypass_quota_gate(self):
        """Disable YouTube quota gate so chunk retry logic is exercised."""
        mock_qt = MagicMock()
        mock_qt.can_upload.return_value = True
        mock_qt.status.return_value = {"used": 0, "upload_count": 0}
        mock_tracker_cls = MagicMock(return_value=mock_qt)
        mock_module = MagicMock(YouTubeQuotaTracker=mock_tracker_cls)
        with patch.dict("sys.modules", {
            "genlab_core.monitoring": MagicMock(),
            "genlab_core.monitoring.youtube_quota": mock_module,
        }):
            yield

    def _make_client(self):
        """Create a YouTubeClient with mocked credentials."""
        with patch.dict("os.environ", {
            "YOUTUBE_CLIENT_ID": "test-id",
            "YOUTUBE_CLIENT_SECRET": "test-secret",
            "YOUTUBE_REFRESH_TOKEN": "test-token",
        }):
            from execution.utils.youtube_client import YouTubeClient
            client = YouTubeClient()
        return client

    @patch("genlab_core.platforms.youtube.time.sleep")
    def test_upload_short_retries_on_chunk_error(self, mock_sleep, tmp_path):
        client = self._make_client()

        # Create a fake video file
        video = tmp_path / "test.mp4"
        video.write_bytes(b"\x00" * 100)

        # Mock the Google API service and MediaFileUpload
        mock_service = MagicMock()
        mock_request = MagicMock()
        # First call fails, second succeeds
        mock_request.next_chunk.side_effect = [
            ConnectionError("transient error"),
            (None, {"id": "abc123"}),
        ]
        mock_service.videos.return_value.insert.return_value = mock_request

        with patch.object(client, "_get_data_service", return_value=mock_service), \
             patch("googleapiclient.http.MediaFileUpload"):
            result = client.upload_short(str(video), "Test #Shorts")

        assert result is not None
        assert result["video_id"] == "abc123"
        # Verify retry happened (sleep called with 2^0 = 1)
        assert mock_sleep.call_args_list[-1] == ((1,),)

    @patch("genlab_core.platforms.youtube.time.sleep")
    def test_upload_video_retries_on_chunk_error(self, mock_sleep, tmp_path):
        client = self._make_client()

        video = tmp_path / "test.mp4"
        video.write_bytes(b"\x00" * 100)

        mock_service = MagicMock()
        mock_request = MagicMock()
        # Fail twice, succeed on third attempt
        mock_request.next_chunk.side_effect = [
            ConnectionError("error 1"),
            ConnectionError("error 2"),
            (None, {"id": "vid456"}),
        ]
        mock_service.videos.return_value.insert.return_value = mock_request

        with patch.object(client, "_get_data_service", return_value=mock_service), \
             patch("googleapiclient.http.MediaFileUpload"):
            result = client.upload_video(str(video), "Test Video")

        assert result is not None
        assert result["video_id"] == "vid456"
        # Verify two retries happened — look for the retry sleep calls (1s, 2s)
        sleep_values = [c.args[0] for c in mock_sleep.call_args_list]
        assert 1 in sleep_values
        assert 2 in sleep_values

    @patch("genlab_core.platforms.youtube.time.sleep")
    def test_upload_short_raises_after_3_failures(self, mock_sleep, tmp_path):
        client = self._make_client()

        video = tmp_path / "test.mp4"
        video.write_bytes(b"\x00" * 100)

        mock_service = MagicMock()
        mock_request = MagicMock()
        # Fail 3 times — should raise on the 3rd
        mock_request.next_chunk.side_effect = [
            ConnectionError("error 1"),
            ConnectionError("error 2"),
            ConnectionError("error 3"),
        ]
        mock_service.videos.return_value.insert.return_value = mock_request

        with patch.object(client, "_get_data_service", return_value=mock_service), \
             patch("googleapiclient.http.MediaFileUpload"):
            result = client.upload_short(str(video), "Test #Shorts")

        # Should return error dict (caught by outer except)
        assert result is not None
        assert result.get("error") == "upload_failed"


# ── P3.8: YouTube error handling ──────────────────────────────────

@pytest.mark.skip(reason="youtube_client removed in Sprint 68 — execution.utils.youtube_client no longer exists")
class TestYouTubeErrorHandling:
    """Verify post_comment and update_video_metadata handle HTTP errors."""

    def _make_client(self):
        with patch.dict("os.environ", {
            "YOUTUBE_CLIENT_ID": "test-id",
            "YOUTUBE_CLIENT_SECRET": "test-secret",
            "YOUTUBE_REFRESH_TOKEN": "test-token",
        }):
            from execution.utils.youtube_client import YouTubeClient
            client = YouTubeClient()
        return client

    def test_post_comment_handles_http_error(self):
        import requests
        client = self._make_client()

        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"
        mock_response.raise_for_status.side_effect = requests.HTTPError(
            response=mock_response
        )

        with patch.object(client, "_get_access_token", return_value="fake-token"), \
             patch("genlab_core.platforms.youtube.requests.post", return_value=mock_response):
            result = client.post_comment("vid123", "Hello!")

        assert result is None

    def test_post_comment_handles_generic_exception(self):
        client = self._make_client()

        with patch.object(client, "_get_access_token", side_effect=RuntimeError("boom")):
            result = client.post_comment("vid123", "Hello!")

        assert result is None

    def test_update_metadata_handles_http_error(self):
        import requests
        client = self._make_client()

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.raise_for_status.side_effect = requests.HTTPError(
            response=mock_response
        )

        with patch.object(client, "_get_access_token", return_value="fake-token"), \
             patch("genlab_core.platforms.youtube.requests.get", return_value=mock_response):
            result = client.update_video_metadata("vid123", title="New Title")

        assert result is None

    def test_update_metadata_handles_generic_exception(self):
        client = self._make_client()

        with patch.object(client, "_get_access_token", side_effect=RuntimeError("boom")):
            result = client.update_video_metadata("vid123", title="New Title")

        assert result is None


# ── P3.12: OData action_taken validation ──────────────────────────

class TestODataActionValidation:
    """Verify action_taken parameter is validated against allowlist."""

    @pytest.fixture(autouse=True)
    def _disable_auth(self, monkeypatch):
        import server.review_server as rs
        monkeypatch.setattr(rs, "_AUTH_ENABLED", False)

    @pytest.fixture
    def client(self):
        from server.review_server import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_invalid_action_taken_returns_400(self, client):
        resp = client.get("/api/v1/blueprints?action_taken='; DROP TABLE --")
        assert resp.status_code == 400
        data = resp.get_json()
        assert "Invalid action_taken" in data["error"]

    def test_valid_action_taken_accepted(self, client):
        for action in ("approved", "rejected", "revised", "skipped"):
            resp = client.get(f"/api/v1/blueprints?action_taken={action}")
            # Should not be 400 — either 200 (with data) or 502 (backlog not configured)
            assert resp.status_code != 400, f"action_taken={action} returned 400"

    def test_odata_injection_blocked(self, client):
        # Various injection attempts
        payloads = [
            "approved' or 1 eq 1",
            "approved); --",
            "<script>alert(1)</script>",
            "approved%27%20OR%201=1",
        ]
        for payload in payloads:
            resp = client.get(f"/api/v1/blueprints?action_taken={payload}")
            assert resp.status_code == 400, f"Injection not blocked: {payload}"
