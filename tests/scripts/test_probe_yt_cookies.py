"""Pin `scripts/probe_yt_cookies.py`.

Startup health probe runs from `ensure_yt_dlp_environment.sh`
`ExecStartPre` before every gaming pipeline fire. Must:

  * Exit 0 always (fail-open — never block the pipeline)
  * Detect the bot-check markers correctly
  * Not crash when DATABASE_URL / YT_DLP_COOKIES_FILE are unset
  * Detect a missing cookies file separately from a stale one
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import the script by path (not on sys.path as a package)
_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "probe_yt_cookies.py"


@pytest.fixture
def probe_module():
    spec = importlib.util.spec_from_file_location("probe_yt_cookies", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["probe_yt_cookies"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("probe_yt_cookies", None)


class TestBotCheckDetection:
    def test_matches_sign_in(self, probe_module):
        assert probe_module._is_bot_check(
            "ERROR: [youtube] abc: Sign in to confirm you're not a bot."
        )

    def test_matches_429(self, probe_module):
        assert probe_module._is_bot_check("HTTP Error 429: Too Many Requests")

    def test_rejects_generic(self, probe_module):
        assert not probe_module._is_bot_check("Video unavailable: private")

    def test_empty(self, probe_module):
        assert not probe_module._is_bot_check("")


class TestMainExitCode:
    """`main()` must always return 0 regardless of what fails."""

    def test_no_cookies_env_var_emits_warning_and_exits_zero(
        self, probe_module, monkeypatch
    ):
        monkeypatch.delenv("YT_DLP_COOKIES_FILE", raising=False)
        with patch.object(probe_module, "_emit_alert") as em:
            assert probe_module.main() == 0
        em.assert_called_once()
        # First positional arg is check_name
        assert em.call_args.args[0] == "yt_cookies_not_configured"

    def test_missing_cookies_file_emits_critical(
        self, probe_module, monkeypatch, tmp_path
    ):
        monkeypatch.setenv(
            "YT_DLP_COOKIES_FILE", str(tmp_path / "missing.txt"),
        )
        with patch.object(probe_module, "_emit_alert") as em:
            assert probe_module.main() == 0
        em.assert_called_once()
        assert em.call_args.args[0] == "yt_cookies_file_missing"
        assert em.call_args.args[1] == "critical"

    def test_bot_check_stderr_emits_stale_critical(
        self, probe_module, monkeypatch, tmp_path
    ):
        cookies = tmp_path / "cookies.txt"
        cookies.write_text("# fake")
        monkeypatch.setenv("YT_DLP_COOKIES_FILE", str(cookies))
        fake_result = MagicMock()
        fake_result.returncode = 1
        fake_result.stdout = ""
        fake_result.stderr = "Sign in to confirm you're not a bot."
        with patch.object(probe_module.subprocess, "run", return_value=fake_result), \
             patch.object(probe_module, "_emit_alert") as em:
            assert probe_module.main() == 0
        em.assert_called_once()
        assert em.call_args.args[0] == "yt_cookies_stale"
        assert em.call_args.args[1] == "critical"

    def test_success_emits_no_alert(self, probe_module, monkeypatch, tmp_path):
        cookies = tmp_path / "cookies.txt"
        cookies.write_text("# fake")
        monkeypatch.setenv("YT_DLP_COOKIES_FILE", str(cookies))
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "Never Gonna Give You Up"
        fake_result.stderr = ""
        with patch.object(probe_module.subprocess, "run", return_value=fake_result), \
             patch.object(probe_module, "_emit_alert") as em:
            assert probe_module.main() == 0
        em.assert_not_called()

    def test_timeout_returns_zero_without_alert(
        self, probe_module, monkeypatch, tmp_path
    ):
        cookies = tmp_path / "cookies.txt"
        cookies.write_text("# fake")
        monkeypatch.setenv("YT_DLP_COOKIES_FILE", str(cookies))
        with patch.object(
            probe_module.subprocess, "run",
            side_effect=probe_module.subprocess.TimeoutExpired("yt-dlp", 20),
        ), patch.object(probe_module, "_emit_alert") as em:
            assert probe_module.main() == 0
        em.assert_not_called()

    def test_ytdlp_not_installed_returns_zero(
        self, probe_module, monkeypatch, tmp_path
    ):
        cookies = tmp_path / "cookies.txt"
        cookies.write_text("# fake")
        monkeypatch.setenv("YT_DLP_COOKIES_FILE", str(cookies))
        with patch.object(
            probe_module.subprocess, "run", side_effect=FileNotFoundError("yt-dlp"),
        ), patch.object(probe_module, "_emit_alert") as em:
            assert probe_module.main() == 0
        em.assert_not_called()


class TestEmitAlertFailOpen:
    def test_no_dsn_no_crash(self, probe_module, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        # Must not raise
        probe_module._emit_alert("test", "warning", "msg", {})

    def test_bogus_dsn_no_crash(self, probe_module, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://nowhere:1/none")
        probe_module._emit_alert("test", "warning", "msg", {})
