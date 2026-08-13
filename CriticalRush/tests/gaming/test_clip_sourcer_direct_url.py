"""Pin ClipSourcer Tier 0 direct-URL short-circuit.

Live-fire 2026-08-13 root cause: stories from FetchTrendingVideos
arrived with `download_url` = exact YouTube video URL. But
ClipSourcer ignored it and did a fresh `ytsearch3:{title}` which
returned 0 hits on 5/7 daily candidates (marketing-laden titles).

Fix: add Tier 0 that downloads a known URL directly via yt-dlp,
skipping all search logic when the story already has the answer.

## Coverage

  * source_clip(download_url=X) attempts direct download first
  * On success, result.source_tier == "direct_url" and
    result.source_url == the URL
  * On failure, falls through to Steam/YT/Twitch tiers as before
  * download_url=None (not provided) → skip Tier 0 entirely
  * Wire from ExtractGamingMedia passes story['download_url']
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from niches.gaming.tools.clip_sourcer import GamingClipSourcer, ClipResult, ClipSourcerConfig


class _StubResult:
    def __init__(self):
        self.source_tier = ""
        self.source_url = ""


def _mkclip():
    """Build a minimally-configured GamingClipSourcer with all tier fetchers mocked."""
    config = ClipSourcerConfig()
    sourcer = GamingClipSourcer(config)
    sourcer._steam = MagicMock()
    sourcer._youtube = MagicMock()
    sourcer._twitch = MagicMock()
    sourcer._ensure_output_dir = MagicMock(return_value=Path("/tmp/fake"))
    sourcer._post_process = MagicMock(
        return_value=ClipResult(
            file_path="/tmp/fake/clip.mp4",
            source_tier="",
            source_url="",
            duration_seconds=30.0,
            width=1920, height=1080, fps=30.0, aspect_ratio="16:9",
        )
    )
    return sourcer


class TestDirectUrlTier0Preferred:
    def test_download_url_success_short_circuits_other_tiers(self):
        sourcer = _mkclip()
        with patch.object(
            sourcer, "_direct_url_fetch",
            return_value="/tmp/fake/direct_abc123.mp4",
        ):
            result = sourcer.source_clip(
                game_title="Any Title",
                download_url="https://www.youtube.com/watch?v=abc",
                steam_app_id="12345",  # would normally trigger Steam tier
                igdb_game_id="99",     # would normally trigger Twitch tier
            )
        assert result is not None
        assert result.source_tier == "direct_url"
        assert result.source_url == "https://www.youtube.com/watch?v=abc"
        # None of the other tier fetchers should have been called
        sourcer._steam.fetch.assert_not_called()
        sourcer._youtube.fetch.assert_not_called()
        sourcer._twitch.fetch.assert_not_called()

    def test_download_url_failure_falls_through_to_steam(self):
        sourcer = _mkclip()
        sourcer._steam.fetch.return_value = "/tmp/steam.mp4"
        with patch.object(sourcer, "_direct_url_fetch", return_value=None):
            result = sourcer.source_clip(
                game_title="Some Game",
                download_url="https://youtu.be/xyz",
                steam_app_id="12345",
            )
        assert result is not None
        assert result.source_tier == "steam"
        sourcer._steam.fetch.assert_called_once()

    def test_download_url_and_steam_fail_falls_through_to_youtube(self):
        sourcer = _mkclip()
        sourcer._steam.fetch.return_value = None
        sourcer._youtube.fetch.return_value = "/tmp/yt.mp4"
        with patch.object(sourcer, "_direct_url_fetch", return_value=None):
            result = sourcer.source_clip(
                game_title="Some Game",
                download_url="https://youtu.be/xyz",
                steam_app_id="12345",
            )
        assert result is not None
        assert result.source_tier == "youtube"


class TestNoDownloadUrl:
    def test_download_url_none_skips_tier_0(self):
        sourcer = _mkclip()
        sourcer._youtube.fetch.return_value = "/tmp/yt.mp4"
        with patch.object(sourcer, "_direct_url_fetch") as mock_direct:
            result = sourcer.source_clip(
                game_title="Some Game",
                download_url=None,
            )
        mock_direct.assert_not_called()
        assert result.source_tier == "youtube"

    def test_download_url_omitted_defaults_to_none(self):
        """Backward compat: existing callers not passing download_url
        get pre-fix behavior."""
        sourcer = _mkclip()
        sourcer._youtube.fetch.return_value = "/tmp/yt.mp4"
        with patch.object(sourcer, "_direct_url_fetch") as mock_direct:
            result = sourcer.source_clip(
                game_title="Some Game",
                # no download_url kwarg
            )
        mock_direct.assert_not_called()


class TestDirectUrlFetchImpl:
    def test_empty_url_returns_none(self):
        sourcer = _mkclip()
        # url="" would not even reach _direct_url_fetch because tier
        # 0 guards with `if download_url:`. But if called directly,
        # yt-dlp on empty string would fail.
        with patch("subprocess.run", return_value=MagicMock(returncode=1, stderr="")):
            result = sourcer._direct_url_fetch("", Path("/tmp"))
        assert result is None

    def test_ytdlp_exit_nonzero_returns_none(self, tmp_path):
        sourcer = _mkclip()
        with patch("subprocess.run", return_value=MagicMock(returncode=1, stderr="")):
            result = sourcer._direct_url_fetch(
                "https://youtu.be/xyz", tmp_path,
            )
        assert result is None

    def test_ytdlp_output_missing_returns_none(self, tmp_path):
        sourcer = _mkclip()
        # yt-dlp exit 0 but file doesn't materialize (rare — network
        # race, permissions). Still return None.
        with patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stderr=""),
        ):
            result = sourcer._direct_url_fetch(
                "https://youtu.be/xyz", tmp_path,
            )
        assert result is None

    def test_ytdlp_output_tiny_returns_none(self, tmp_path):
        """< 1024 bytes = failed download. Truncated files can trick
        subsequent stages; reject early."""
        sourcer = _mkclip()
        # Simulate yt-dlp writing a tiny file
        def _fake_run(cmd, *_, **__):
            import re
            output = cmd[cmd.index("--output") + 1]
            Path(output).write_bytes(b"tiny")
            return MagicMock(returncode=0, stderr="")

        with patch("subprocess.run", side_effect=_fake_run):
            result = sourcer._direct_url_fetch(
                "https://youtu.be/xyz", tmp_path,
            )
        assert result is None

    def test_timeout_returns_none(self, tmp_path):
        import subprocess
        sourcer = _mkclip()
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="yt-dlp", timeout=120),
        ):
            result = sourcer._direct_url_fetch(
                "https://youtu.be/xyz", tmp_path,
            )
        assert result is None


class TestBotCheckDetector:
    """`_is_bot_check_stderr` must recognize the datacenter-IP bot
    detection signatures. Wire from ClipSourcer routes these into a
    WARNING log + pipeline_alerts row instead of the silent INFO path.
    Class-of-bug: [[class-of-bug-datacenter-ip-bot-detection]]."""

    def test_matches_sign_in_marker(self):
        from niches.gaming.tools.clip_sourcer import _is_bot_check_stderr
        assert _is_bot_check_stderr(
            "ERROR: [youtube] abc123: Sign in to confirm you're not a bot."
        )

    def test_matches_cookies_from_browser_hint(self):
        from niches.gaming.tools.clip_sourcer import _is_bot_check_stderr
        assert _is_bot_check_stderr(
            "Use --cookies-from-browser or --cookies for the authentication."
        )

    def test_matches_http_429(self):
        from niches.gaming.tools.clip_sourcer import _is_bot_check_stderr
        assert _is_bot_check_stderr("ERROR: HTTP Error 429: Too Many Requests")

    def test_case_insensitive(self):
        from niches.gaming.tools.clip_sourcer import _is_bot_check_stderr
        assert _is_bot_check_stderr("SIGN IN TO CONFIRM YOU'RE NOT A BOT")

    def test_rejects_generic_failure(self):
        from niches.gaming.tools.clip_sourcer import _is_bot_check_stderr
        assert not _is_bot_check_stderr(
            "ERROR: [youtube] Video unavailable: This video is private."
        )

    def test_rejects_empty(self):
        from niches.gaming.tools.clip_sourcer import _is_bot_check_stderr
        assert not _is_bot_check_stderr("")
        assert not _is_bot_check_stderr(None or "")


class TestCookiesStaleAlertFailOpen:
    """`_emit_cookies_stale_alert` must never raise into the caller. If
    DATABASE_URL is unset, pg_connect times out, or the table is missing,
    the write is silently dropped. Same fail-open shape as
    hook_classifier._emit_training_failure_alert."""

    def test_no_dsn_returns_silently(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        from niches.gaming.tools.clip_sourcer import _emit_cookies_stale_alert
        # Must not raise
        _emit_cookies_stale_alert("gaming", "https://youtube.com/w?v=x", "err")

    def test_pg_connect_error_swallowed(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://nowhere:1/none")
        from niches.gaming.tools.clip_sourcer import _emit_cookies_stale_alert
        # pg_connect will fail (bogus DSN) — helper swallows
        _emit_cookies_stale_alert("gaming", "https://youtube.com/w?v=x", "err")


class TestYtDlpCookiesArgs:
    """Cookies-file helper: env var pointing at a real file activates
    --cookies. Missing / non-file → empty args (yt-dlp runs unauth'd
    and fails on YT bot check as before)."""

    def test_no_env_returns_empty(self, monkeypatch):
        monkeypatch.delenv("YT_DLP_COOKIES_FILE", raising=False)
        from niches.gaming.tools.clip_sourcer import _yt_dlp_cookies_args
        assert _yt_dlp_cookies_args() == []

    def test_empty_env_returns_empty(self, monkeypatch):
        monkeypatch.setenv("YT_DLP_COOKIES_FILE", "")
        from niches.gaming.tools.clip_sourcer import _yt_dlp_cookies_args
        assert _yt_dlp_cookies_args() == []

    def test_nonexistent_path_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setenv(
            "YT_DLP_COOKIES_FILE", str(tmp_path / "missing.txt"),
        )
        from niches.gaming.tools.clip_sourcer import _yt_dlp_cookies_args
        assert _yt_dlp_cookies_args() == []

    def test_real_file_returns_args(self, monkeypatch, tmp_path):
        cookies = tmp_path / "cookies.txt"
        cookies.write_text("# fake cookies")
        monkeypatch.setenv("YT_DLP_COOKIES_FILE", str(cookies))
        from niches.gaming.tools.clip_sourcer import _yt_dlp_cookies_args
        args = _yt_dlp_cookies_args()
        assert args == ["--cookies", str(cookies)]


class TestExtractGamingMediaWireDownloadUrl:
    """The wire from stage → sourcer must pass download_url. Without
    this the Tier 0 short-circuit is inert."""

    def test_stage_passes_download_url_when_present(self):
        from niches.gaming.stages.extract_gaming_media import ExtractGamingMedia

        stage = ExtractGamingMedia()
        sourcer = MagicMock()
        result_obj = MagicMock()
        result_obj.model_dump.return_value = {"source_tier": "direct_url"}
        sourcer.source_clip.return_value = result_obj

        with patch.object(stage, "_get_sourcer", return_value=sourcer):
            story = {
                "title": "ACE COMBAT 8 The Art of Aircraft Trailer",
                "download_url": "https://www.youtube.com/watch?v=vPqLcA9LQMo",
                "steam_app_id": None,
                "igdb_game_id": None,
            }
            stage._source_clip_for_story(story, Path("/tmp"))

        kwargs = sourcer.source_clip.call_args.kwargs
        assert kwargs["download_url"] == "https://www.youtube.com/watch?v=vPqLcA9LQMo"

    def test_stage_falls_back_to_source_url_when_download_url_missing(self):
        from niches.gaming.stages.extract_gaming_media import ExtractGamingMedia

        stage = ExtractGamingMedia()
        sourcer = MagicMock()
        sourcer.source_clip.return_value = None
        with patch.object(stage, "_get_sourcer", return_value=sourcer):
            story = {
                "title": "Game X",
                "source_url": "https://twitch.tv/foo",
                "steam_app_id": None,
                "igdb_game_id": None,
            }
            stage._source_clip_for_story(story, Path("/tmp"))
        assert sourcer.source_clip.call_args.kwargs["download_url"] == "https://twitch.tv/foo"

    def test_stage_passes_none_when_neither_present(self):
        from niches.gaming.stages.extract_gaming_media import ExtractGamingMedia

        stage = ExtractGamingMedia()
        sourcer = MagicMock()
        sourcer.source_clip.return_value = None
        with patch.object(stage, "_get_sourcer", return_value=sourcer):
            story = {"title": "Game X", "steam_app_id": None, "igdb_game_id": None}
            stage._source_clip_for_story(story, Path("/tmp"))
        assert sourcer.source_clip.call_args.kwargs["download_url"] is None

    def test_stage_passes_none_when_not_http(self):
        """Guards against passing garbage strings like empty string,
        file:// paths, or malformed data to the direct downloader."""
        from niches.gaming.stages.extract_gaming_media import ExtractGamingMedia

        stage = ExtractGamingMedia()
        sourcer = MagicMock()
        sourcer.source_clip.return_value = None
        with patch.object(stage, "_get_sourcer", return_value=sourcer):
            story = {
                "title": "Game X",
                "download_url": "not-a-url",
                "steam_app_id": None,
                "igdb_game_id": None,
            }
            stage._source_clip_for_story(story, Path("/tmp"))
        assert sourcer.source_clip.call_args.kwargs["download_url"] is None
