"""Tests for the WARP-aware auto-fix logic in check_download_failures.

Previously the auto-fix tried ``uv pip install --upgrade yt-dlp`` and
declared "success" whenever the pip install returned 0 — misleading
when the actual blocker was the WARP SOCKS proxy being down (a
network-layer issue that a yt-dlp binary update can't address).

After this fix:
  * If WARP is down → skip the yt-dlp update entirely, point at WARP
  * If WARP is up + pip install succeeds → honest message scoping the
    fix to "binary-level only"
  * If pip install fails → report the rc
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.monitoring.health_monitor import (
    Alert,
    check_download_failures,
)


def _failing_reports(count: int = 3) -> list[dict]:
    """Build N consecutive zero-download run reports."""
    return [
        {"_run_dir": f"/fake/run_{i}"}
        for i in range(count)
    ]


def _zero_clip_index() -> dict:
    return {"videos_total": 5, "videos_downloaded": 0}


class TestAutoFixWarpAware:
    def test_warp_down_skips_yt_dlp_update(self):
        """When WARP is down, yt-dlp update is pointless — skip it
        and point at the real root cause.
        """
        with patch(
            "genlab_core.monitoring.health_monitor._load_clip_index",
            return_value=_zero_clip_index(),
        ), patch(
            "genlab_core.monitoring.health_monitor.check_warp_health",
            return_value=[Alert("warp_down", "critical", "warp inactive")],
        ), patch("subprocess.run") as mock_run:
            alerts = check_download_failures(_failing_reports(3), "gaming")

        assert len(alerts) == 1
        assert alerts[0].check == "download_failure"
        # yt-dlp update should NOT have been attempted
        pip_calls = [
            c for c in mock_run.call_args_list
            if c.args and len(c.args[0]) > 1 and "yt-dlp" in c.args[0][-1]
        ]
        assert pip_calls == []
        # Auto-fix message should point at WARP
        assert "WARP" in alerts[0].auto_fix
        assert "skipped" in alerts[0].auto_fix.lower()

    def test_warp_up_runs_yt_dlp_update_with_honest_scope(self):
        """When WARP is up, run yt-dlp update but be honest about
        what it does and doesn't fix.
        """
        pip_result = MagicMock()
        pip_result.returncode = 0
        with patch(
            "genlab_core.monitoring.health_monitor._load_clip_index",
            return_value=_zero_clip_index(),
        ), patch(
            "genlab_core.monitoring.health_monitor.check_warp_health",
            return_value=[],  # WARP healthy
        ), patch("subprocess.run", return_value=pip_result):
            alerts = check_download_failures(_failing_reports(3), "gaming")

        assert len(alerts) == 1
        # Should not claim "success" — scope honestly to "binary-level only"
        assert "success" not in alerts[0].auto_fix.lower()
        assert "binary-level" in alerts[0].auto_fix
        # Should mention what's NOT addressed
        assert (
            "network" in alerts[0].auto_fix
            or "proxy" in alerts[0].auto_fix
        )

    def test_pip_install_failure_reports_rc(self):
        """If pip install itself fails, surface the return code."""
        pip_result = MagicMock()
        pip_result.returncode = 2
        with patch(
            "genlab_core.monitoring.health_monitor._load_clip_index",
            return_value=_zero_clip_index(),
        ), patch(
            "genlab_core.monitoring.health_monitor.check_warp_health",
            return_value=[],
        ), patch("subprocess.run", return_value=pip_result):
            alerts = check_download_failures(_failing_reports(3), "gaming")

        assert "failed" in alerts[0].auto_fix
        assert "rc=2" in alerts[0].auto_fix

    def test_pip_install_exception_caught(self):
        """Exception during pip install doesn't crash the monitor."""
        with patch(
            "genlab_core.monitoring.health_monitor._load_clip_index",
            return_value=_zero_clip_index(),
        ), patch(
            "genlab_core.monitoring.health_monitor.check_warp_health",
            return_value=[],
        ), patch("subprocess.run", side_effect=OSError("uv not found")):
            alerts = check_download_failures(_failing_reports(3), "gaming")

        assert "failed" in alerts[0].auto_fix
        assert "uv not found" in alerts[0].auto_fix

    def test_no_alert_below_threshold(self):
        """1 consecutive failure → no alert (threshold is 2)."""
        with patch(
            "genlab_core.monitoring.health_monitor._load_clip_index",
            return_value=_zero_clip_index(),
        ):
            alerts = check_download_failures(_failing_reports(1), "gaming")
        assert alerts == []

    def test_healthy_reports_no_alert(self):
        """Run with downloads succeeding → no alert."""
        with patch(
            "genlab_core.monitoring.health_monitor._load_clip_index",
            return_value={"videos_total": 5, "videos_downloaded": 5},
        ):
            alerts = check_download_failures(_failing_reports(3), "gaming")
        assert alerts == []
