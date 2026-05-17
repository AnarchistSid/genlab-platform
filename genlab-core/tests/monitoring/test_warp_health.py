"""Tests for the WARP health check in health_monitor.

WARP SOCKS proxy is load-bearing for yt-dlp downloads on Hetzner.  When
it drops, every pipeline run for the next 24h fails downloads with a
misleading auto-fix message.  This check catches the network-layer
failure within minutes instead of days.

The test cases mirror the failure modes observed in production:
  * Daemon stopped (history: 2026-05-11 silent stop)
  * Daemon enabled=disabled (won't restart on reboot)
  * Port closed (whole-OS tunnel mode instead of SOCKS)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.monitoring.health_monitor import check_warp_health


def _show_result(load_state="loaded", active_state="active", sub_state="running"):
    """Build a fake `systemctl show warp-svc` result."""
    r = MagicMock()
    r.stdout = (
        f"LoadState={load_state}\n"
        f"ActiveState={active_state}\n"
        f"SubState={sub_state}\n"
    )
    return r


def _ss_result(listening: bool):
    """Build a fake `ss -tln` result with or without WARP's SOCKS port."""
    r = MagicMock()
    if listening:
        r.stdout = (
            "State Recv-Q Send-Q Local Address:Port Peer Address:Port\n"
            "LISTEN 0 1024 127.0.0.1:40000  0.0.0.0:*\n"
            "LISTEN 0 128  0.0.0.0:22       0.0.0.0:*\n"
        )
    else:
        r.stdout = (
            "State Recv-Q Send-Q Local Address:Port Peer Address:Port\n"
            "LISTEN 0 128  0.0.0.0:22       0.0.0.0:*\n"
        )
    return r


def _run_router(show, ss):
    """subprocess.run side_effect that routes by command."""
    def _side_effect(cmd, *_a, **_k):
        if cmd[0] == "systemctl":
            return show
        if cmd[0] == "ss":
            return ss
        return MagicMock()
    return _side_effect


class TestWarpHealthCheck:
    def test_happy_path_no_alerts(self):
        """Daemon active, port listening → silent."""
        with patch(
            "subprocess.run",
            side_effect=_run_router(
                _show_result(active_state="active"),
                _ss_result(listening=True),
            ),
        ):
            alerts = check_warp_health()
        assert alerts == []

    def test_daemon_not_active_fires_critical(self):
        """Daemon stopped (mirrors 2026-05-11 incident) → critical."""
        with patch(
            "subprocess.run",
            side_effect=_run_router(
                _show_result(active_state="inactive", sub_state="dead"),
                _ss_result(listening=False),
            ),
        ):
            alerts = check_warp_health()
        assert len(alerts) == 1
        assert alerts[0].severity == "critical"
        assert alerts[0].check == "warp_down"
        assert "not active" in alerts[0].message
        assert "ActiveState=inactive" in alerts[0].message
        # Should include restart guidance
        assert "systemctl restart warp-svc" in alerts[0].message

    def test_daemon_failed_state_fires_critical(self):
        """`failed` is a distinct ActiveState that also halts downloads."""
        with patch(
            "subprocess.run",
            side_effect=_run_router(
                _show_result(active_state="failed", sub_state="failed"),
                _ss_result(listening=False),
            ),
        ):
            alerts = check_warp_health()
        assert len(alerts) == 1
        assert alerts[0].severity == "critical"

    def test_port_not_listening_fires_distinct_alert(self):
        """Daemon up but port closed (whole-OS tunnel mode bug) → distinct
        alert with the right remediation steps.
        """
        with patch(
            "subprocess.run",
            side_effect=_run_router(
                _show_result(active_state="active"),
                _ss_result(listening=False),
            ),
        ):
            alerts = check_warp_health()
        assert len(alerts) == 1
        assert alerts[0].check == "warp_port_closed"
        assert alerts[0].severity == "critical"
        # Should surface the warp-cli commands to fix it
        assert "warp-cli mode proxy" in alerts[0].message
        assert "warp-cli proxy port 40000" in alerts[0].message

    def test_warp_not_installed_silent(self):
        """Dev environments without warp-svc → no alerts (no false positives)."""
        with patch(
            "subprocess.run",
            side_effect=_run_router(
                _show_result(load_state="not-found", active_state="inactive"),
                _ss_result(listening=False),
            ),
        ):
            alerts = check_warp_health()
        assert alerts == []

    def test_warp_masked_silent(self):
        """Intentionally masked unit → no alerts."""
        with patch(
            "subprocess.run",
            side_effect=_run_router(
                _show_result(load_state="masked", active_state="inactive"),
                _ss_result(listening=False),
            ),
        ):
            alerts = check_warp_health()
        assert alerts == []

    def test_daemon_down_skips_port_check(self):
        """When daemon is down, don't waste a subprocess on `ss -tln` —
        the daemon-down alert is sufficient and the port can't be open.
        """
        ss_mock = _ss_result(listening=False)
        with patch(
            "subprocess.run",
            side_effect=_run_router(
                _show_result(active_state="inactive"),
                ss_mock,
            ),
        ) as mock_run:
            alerts = check_warp_health()

        # Only the daemon alert fires (not the port one)
        assert len(alerts) == 1
        assert alerts[0].check == "warp_down"
        # Verify `ss` was never invoked
        ss_calls = [
            c for c in mock_run.call_args_list
            if c.args and c.args[0][0] == "ss"
        ]
        assert ss_calls == []

    def test_subprocess_exception_silent(self):
        """If systemctl itself errors (CI sandbox, weird host) → no spam."""
        with patch("subprocess.run", side_effect=Exception("no systemctl")):
            alerts = check_warp_health()
        assert alerts == []

    def test_alert_details_payload(self):
        """Alert details should carry enough state for dashboard triage."""
        with patch(
            "subprocess.run",
            side_effect=_run_router(
                _show_result(active_state="failed", sub_state="dead"),
                _ss_result(listening=False),
            ),
        ):
            alerts = check_warp_health()
        assert len(alerts) == 1
        d = alerts[0].details
        assert d["active_state"] == "failed"
        assert d["sub_state"] == "dead"
        # Auto-fix is noted as "not attempted" — restoring WARP needs
        # warp-cli mode + port + connect which we don't want to script.
        assert "not attempted" in alerts[0].auto_fix
