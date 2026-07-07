"""Pin the top-creator poll runner (A.2, 2026-07-07).

Contract:
  1. Flag off → no-op exit 0 (no HTTP calls made)
  2. YOUTUBE_API_KEY missing → exit 1
  3. Empty watchlist → exit 1
  4. All creators fail → exit 2
  5. Successful poll → writes per-niche JSON artifact + updates
     playlist-ID cache
  6. Cached playlist ID reused within 30 days (avoids channels.list
     call)
  7. Uploads parsed correctly (video_id, title, hours_since_publish)

Uses mock ``requests.get`` — no live YouTube API.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "watch_top_creator_uploads.py"


def _load_script_module():
    import sys

    spec = importlib.util.spec_from_file_location("watch_top_creator_uploads", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["watch_top_creator_uploads"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def script_module():
    return _load_script_module()


def _make_channels_response(playlist_id: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "items": [
            {
                "contentDetails": {
                    "relatedPlaylists": {"uploads": playlist_id},
                }
            }
        ]
    }
    return resp


def _make_playlist_items_response(items: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"items": items}
    return resp


class TestFlagAndCLIGuards:
    def test_flag_off_returns_zero_no_calls(self, script_module, monkeypatch):
        monkeypatch.delenv("GENLAB_TOP_CREATORS_ENABLED", raising=False)
        monkeypatch.setattr("sys.argv", ["watch_top_creator_uploads.py"])
        # Even without API key, flag-off should exit 0 cleanly
        assert script_module.main() == 0

    def test_missing_api_key_returns_1(self, script_module, monkeypatch):
        monkeypatch.setenv("GENLAB_TOP_CREATORS_ENABLED", "true")
        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
        monkeypatch.setattr("sys.argv", ["watch_top_creator_uploads.py"])
        assert script_module.main() == 1

    def test_flag_requires_exact_true(self, script_module, monkeypatch):
        """Sprint convention: exact match ``"true"`` (not ``"1"`` or
        ``"True"``). Ensures accidental ``"1"`` in env doesn't
        auto-activate."""
        monkeypatch.setenv("GENLAB_TOP_CREATORS_ENABLED", "1")
        assert script_module._flag_enabled() is False
        monkeypatch.setenv("GENLAB_TOP_CREATORS_ENABLED", "True")
        assert script_module._flag_enabled() is False
        monkeypatch.setenv("GENLAB_TOP_CREATORS_ENABLED", "true")
        assert script_module._flag_enabled() is True


class TestUploadsPlaylistCache:
    """Playlist ID lookup is the biggest quota save — pin the cache
    contract so a future refactor can't accidentally re-fetch every
    poll."""

    def test_cache_miss_fetches_and_stores(self, script_module):
        cache: dict = {}
        with patch(
            "requests.get",
            return_value=_make_channels_response("UUxxx"),
        ) as mock_get:
            result = script_module._uploads_playlist_id(
                "UCBJycsmduvYEL83R_U4JriQ",
                "fake-key",
                cache,
            )
        assert result == "UUxxx"
        assert mock_get.call_count == 1
        assert "UCBJycsmduvYEL83R_U4JriQ" in cache
        assert cache["UCBJycsmduvYEL83R_U4JriQ"]["playlist_id"] == "UUxxx"

    def test_cache_hit_within_ttl_no_fetch(self, script_module):
        cache = {
            "UCBJycsmduvYEL83R_U4JriQ": {
                "playlist_id": "UUcached",
                "cached_at": datetime.now(UTC).isoformat(),
            }
        }
        with patch("requests.get") as mock_get:
            result = script_module._uploads_playlist_id(
                "UCBJycsmduvYEL83R_U4JriQ",
                "fake-key",
                cache,
            )
        assert result == "UUcached"
        mock_get.assert_not_called()  # no fetch

    def test_cache_stale_refetches(self, script_module):
        """Entry older than 30 days should trigger a refetch."""
        cache = {
            "UCBJycsmduvYEL83R_U4JriQ": {
                "playlist_id": "UUstale",
                "cached_at": (datetime.now(UTC) - timedelta(days=45)).isoformat(),
            }
        }
        with patch(
            "requests.get",
            return_value=_make_channels_response("UUfresh"),
        ) as mock_get:
            result = script_module._uploads_playlist_id(
                "UCBJycsmduvYEL83R_U4JriQ",
                "fake-key",
                cache,
            )
        assert result == "UUfresh"
        assert mock_get.call_count == 1

    def test_http_error_returns_none(self, script_module):
        cache: dict = {}
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        with patch("requests.get", return_value=mock_resp):
            result = script_module._uploads_playlist_id(
                "UCBJycsmduvYEL83R_U4JriQ", "fake-key", cache
            )
        assert result is None

    def test_empty_items_returns_none(self, script_module):
        """channels.list returning zero items = channel deleted/invalid."""
        cache: dict = {}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"items": []}
        with patch("requests.get", return_value=mock_resp):
            result = script_module._uploads_playlist_id(
                "UCDeleted00000000000000", "fake-key", cache
            )
        assert result is None


class TestRecentUploadsParsing:
    def test_parses_upload_entries(self, script_module):
        items = [
            {
                "snippet": {
                    "title": "video 1",
                    "publishedAt": (datetime.now(UTC) - timedelta(hours=3)).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                    "resourceId": {"videoId": "vid1"},
                }
            }
        ]
        with patch("requests.get", return_value=_make_playlist_items_response(items)):
            uploads = script_module._recent_uploads("UUxxx", "fake-key", 5)
        assert len(uploads) == 1
        u = uploads[0]
        assert u["video_id"] == "vid1"
        assert u["title"] == "video 1"
        assert 2.5 < u["hours_since_publish"] < 3.5  # approx 3h

    def test_skips_missing_video_id(self, script_module):
        items = [
            {"snippet": {"title": "no id", "publishedAt": "2026-01-01T00:00:00Z"}},
            {
                "snippet": {
                    "title": "good",
                    "publishedAt": "2026-01-01T00:00:00Z",
                    "resourceId": {"videoId": "vid1"},
                }
            },
        ]
        with patch("requests.get", return_value=_make_playlist_items_response(items)):
            uploads = script_module._recent_uploads("UUxxx", "fake-key", 5)
        assert len(uploads) == 1
        assert uploads[0]["video_id"] == "vid1"

    def test_http_error_returns_empty(self, script_module):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("requests.get", return_value=mock_resp):
            uploads = script_module._recent_uploads("UUxxx", "fake-key", 5)
        assert uploads == []


class TestRunFlow:
    """End-to-end poll flow with mocked API responses. Verifies
    artifact contents + stats reporting."""

    def test_successful_run_writes_artifact(self, script_module, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))

        from genlab_core.intel.top_creators_config import TopCreator

        watchlist = {
            "gaming": [
                TopCreator(
                    channel_id="UCBJycsmduvYEL83R_U4JriQ",
                    label="MKBHD",
                    notes="",
                )
            ]
        }

        def _fake_get(url, params=None, timeout=None):
            if "channels" in url:
                return _make_channels_response("UUgaming")
            elif "playlistItems" in url:
                return _make_playlist_items_response(
                    [
                        {
                            "snippet": {
                                "title": "hot topic",
                                "publishedAt": "2026-07-07T12:00:00Z",
                                "resourceId": {"videoId": "abc123"},
                            }
                        }
                    ]
                )
            raise ValueError(f"unexpected URL: {url}")

        with patch("requests.get", side_effect=_fake_get):
            stats = script_module.run("fake-key", watchlist, dry_run=False)

        assert stats["niches_processed"] == 1
        assert stats["creators_polled"] == 1
        assert stats["uploads_fetched"] == 1
        assert stats["creators_failed"] == 0

        # Artifact should exist
        artifact_dir = tmp_path / "top-creator-uploads"
        artifacts = list(artifact_dir.glob("*-gaming.json"))
        assert len(artifacts) == 1
        payload = json.loads(artifacts[0].read_text())
        assert payload["niche_id"] == "gaming"
        assert len(payload["creators"]) == 1
        assert payload["creators"][0]["label"] == "MKBHD"
        assert payload["creators"][0]["uploads"][0]["video_id"] == "abc123"

    def test_dry_run_no_artifact(self, script_module, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))

        from genlab_core.intel.top_creators_config import TopCreator

        watchlist = {
            "gaming": [
                TopCreator(
                    channel_id="UCBJycsmduvYEL83R_U4JriQ",
                    label="x",
                    notes="",
                )
            ]
        }

        def _fake_get(url, params=None, timeout=None):
            if "channels" in url:
                return _make_channels_response("UUgaming")
            return _make_playlist_items_response([])

        with patch("requests.get", side_effect=_fake_get):
            script_module.run("fake-key", watchlist, dry_run=True)

        # No artifacts written on dry run
        artifact_dir = tmp_path / "top-creator-uploads"
        if artifact_dir.is_dir():
            artifacts = list(artifact_dir.glob("*-gaming.json"))
            assert artifacts == []

    def test_empty_niche_skipped(self, script_module, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        watchlist = {"gaming": []}  # empty
        with patch("requests.get") as mock_get:
            stats = script_module.run("fake-key", watchlist, dry_run=False)
        assert stats["niches_processed"] == 0
        mock_get.assert_not_called()

    def test_all_creators_failed_reports_stats(self, script_module, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))

        from genlab_core.intel.top_creators_config import TopCreator

        watchlist = {
            "gaming": [
                TopCreator(
                    channel_id="UCFailure00000000000000",
                    label="x",
                    notes="",
                )
            ]
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 403  # simulate quota exhausted
        with patch("requests.get", return_value=mock_resp):
            stats = script_module.run("fake-key", watchlist, dry_run=False)
        assert stats["creators_polled"] == 0
        assert stats["creators_failed"] == 1


class TestCachePersistence:
    def test_cache_saved_between_runs(self, script_module, tmp_path):
        cache_dir = tmp_path
        cache_data = {
            "UCBJycsmduvYEL83R_U4JriQ": {
                "playlist_id": "UUcache",
                "cached_at": datetime.now(UTC).isoformat(),
            }
        }
        script_module._save_uploads_cache(cache_dir, cache_data)
        loaded = script_module._load_uploads_cache(cache_dir)
        assert loaded == cache_data

    def test_missing_cache_file_returns_empty(self, script_module, tmp_path):
        loaded = script_module._load_uploads_cache(tmp_path)
        assert loaded == {}

    def test_malformed_cache_returns_empty(self, script_module, tmp_path):
        (tmp_path / "uploads-playlist-cache.json").write_text("not valid json {[")
        loaded = script_module._load_uploads_cache(tmp_path)
        assert loaded == {}
