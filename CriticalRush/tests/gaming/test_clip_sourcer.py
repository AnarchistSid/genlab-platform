"""Tests for the 4-tier gaming clip sourcer — all external calls mocked."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Clear relevant env vars."""
    monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("TWITCH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)


class TestFromConfig:
    def test_loads_without_error(self, tmp_path):
        """from_config() loads even if sources.yaml doesn't exist."""
        from niches.gaming.tools.clip_sourcer import GamingClipSourcer

        sourcer = GamingClipSourcer.from_config(tmp_path)
        assert sourcer is not None
        assert sourcer.config.max_duration_seconds == 60

    def test_loads_from_yaml(self, tmp_path):
        """from_config() parses sources.yaml when present."""
        config_dir = tmp_path / "niches" / "gaming" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "sources.yaml").write_text(
            "clip_sourcer:\n  max_duration_seconds: 30\n  twitch_clip_limit: 10\n"
        )
        from niches.gaming.tools.clip_sourcer import GamingClipSourcer

        sourcer = GamingClipSourcer.from_config(tmp_path)
        assert sourcer.config.max_duration_seconds == 30
        assert sourcer.config.twitch_clip_limit == 10


class TestTierSkipping:
    def test_tier1_skipped_when_no_steam_app_id(self):
        """Tier 1 (Steam) is skipped when steam_app_id is None."""
        from niches.gaming.tools.clip_sourcer import SteamTrailerFetcher

        fetcher = SteamTrailerFetcher()
        result = fetcher.fetch(None, Path("/tmp"))
        assert result is None

    def test_tier3_skipped_when_no_igdb_game_id(self, tmp_path):
        """Tier 3 (Twitch) is skipped when igdb_game_id is None — the
        source_clip method won't call Twitch if igdb_game_id is falsy."""
        from niches.gaming.tools.clip_sourcer import ClipSourcerConfig, GamingClipSourcer

        sourcer = GamingClipSourcer(ClipSourcerConfig())
        # Mock all tiers to fail
        sourcer._steam.fetch = MagicMock(return_value=None)
        sourcer._youtube.fetch = MagicMock(return_value=None)
        sourcer._twitch.fetch = MagicMock(return_value=None)
        sourcer._pexels.fetch = MagicMock(return_value=None)

        result = sourcer.source_clip(
            game_title="Test Game",
            steam_app_id=None,
            igdb_game_id=None,
            project_root=tmp_path,
        )

        # Steam should not be called (no app_id)
        sourcer._steam.fetch.assert_not_called()
        # Twitch should not be called (no igdb_game_id)
        sourcer._twitch.fetch.assert_not_called()
        # YouTube should still be tried
        sourcer._youtube.fetch.assert_called_once()
        # Pexels fallback is disabled in production (stock footage hurts credibility)
        sourcer._pexels.fetch.assert_not_called()
        assert result is None


class TestAllTiersFail:
    def test_returns_none_not_exception(self, tmp_path):
        """When all tiers fail, source_clip returns None, not an exception."""
        from niches.gaming.tools.clip_sourcer import ClipSourcerConfig, GamingClipSourcer

        sourcer = GamingClipSourcer(ClipSourcerConfig())
        sourcer._steam.fetch = MagicMock(return_value=None)
        sourcer._youtube.fetch = MagicMock(return_value=None)
        sourcer._twitch.fetch = MagicMock(return_value=None)
        sourcer._pexels.fetch = MagicMock(return_value=None)

        result = sourcer.source_clip(
            game_title="Totally Fake Game",
            steam_app_id="99999999",
            igdb_game_id="99999999",
            project_root=tmp_path,
        )
        assert result is None


class TestPexelsAttribution:
    @patch("niches.gaming.tools.clip_sourcer.requests.get")
    def test_attribution_is_set_on_pexels_result(self, mock_get, tmp_path):
        """Pexels results include a non-None attribution field."""
        # Mock the search response
        search_resp = MagicMock()
        search_resp.raise_for_status = MagicMock()
        search_resp.json.return_value = {
            "videos": [
                {
                    "url": "https://www.pexels.com/video/123/",
                    "user": {"name": "TestPhotographer"},
                    "video_files": [
                        {"link": "https://example.com/video.mp4", "height": 1080},
                    ],
                }
            ]
        }

        # Mock the video download response
        download_resp = MagicMock()
        download_resp.raise_for_status = MagicMock()
        download_resp.iter_content = MagicMock(return_value=[b"fake_video_data"])

        mock_get.side_effect = [search_resp, download_resp]

        # PexelsFallbackFetcher.__init__ does a local import of settings,
        # so we patch at the source (genlab_core.settings.settings).
        with patch("genlab_core.settings.settings") as mock_settings:
            mock_settings.pexels_api_key = "test_key"
            from niches.gaming.tools.clip_sourcer import PexelsFallbackFetcher

            fetcher = PexelsFallbackFetcher(query_template="{game_title} gameplay")

        result = fetcher.fetch("Test Game", tmp_path)

        assert result is not None
        assert result["attribution"] is not None
        assert "TestPhotographer" in result["attribution"]
        assert "Pexels" in result["attribution"]


class TestProbeVideo:
    @patch("niches.gaming.tools.clip_sourcer.subprocess.run")
    def test_probe_parses_ffprobe_output(self, mock_run):
        """probe_video correctly parses ffprobe JSON output."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "streams": [
                        {"codec_type": "video", "width": 1920, "height": 1080},
                    ],
                    "format": {"duration": "45.5"},
                }
            ),
        )

        from niches.gaming.tools.clip_sourcer import probe_video

        result = probe_video("/fake/path.mp4")
        assert result is not None
        assert result["width"] == 1920
        assert result["height"] == 1080
        assert result["duration"] == 45.5
        assert result["aspect_ratio"] == "1920:1080"

    @patch("niches.gaming.tools.clip_sourcer.subprocess.run")
    def test_probe_returns_none_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        from niches.gaming.tools.clip_sourcer import probe_video

        result = probe_video("/fake/path.mp4")
        assert result is None
