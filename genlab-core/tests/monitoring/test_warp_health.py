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
    r.stdout = f"LoadState={load_state}\nActiveState={active_state}\nSubState={sub_state}\n"
    return r


def _ss_result(listening: bool):
    """Build a fake `ss -tln` result with or without WARP's SOCKS port.

    Sets returncode=0 explicitly — without it MagicMock's default
    auto-attribute returns a MagicMock instance which compares !=0
    in the post-restart port-listening probe, falsely tripping the
    escalation path.
    """
    r = MagicMock()
    r.returncode = 0
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


def _restart_result(returncode: int = 1, stderr: str = "sudo: a password is required\n"):
    """Build a fake ``sudo -n systemctl restart warp-svc`` result.

    Defaults to the "sudoers not configured" shape because that's the
    state on a freshly-deployed prod box — the operator-action
    prerequisite for the auto-fix to actually work. Override for the
    specific auto-fix-path tests.
    """
    r = MagicMock()
    r.returncode = returncode
    r.stderr = stderr
    return r


def _is_active_result(active: bool = False):
    """Build a fake ``systemctl is-active warp-svc`` result.

    Used by ``_attempt_warp_restart`` to verify post-restart state.
    """
    r = MagicMock()
    r.returncode = 0 if active else 3  # systemd uses rc=3 for inactive
    r.stdout = "active\n" if active else "inactive\n"
    return r


def _run_router(show, ss, *, restart=None, is_active=None, ss_post_restart=None):
    """subprocess.run side_effect that routes by command.

    Args:
        show: result of ``systemctl show warp-svc`` (initial state probe)
        ss: result of the INITIAL ``ss -ltn`` (pre-restart port check)
        restart: result of ``sudo -n systemctl restart warp-svc``
            (each call returns the same mock — escalation reuses)
        is_active: result of post-restart ``systemctl is-active``
        ss_post_restart: result of the POST-restart ``ss -ltn`` —
            used to verify the new layered escalation logic
            (2026-06-16). When None, falls back to the same ss
            result as before — that's the pre-escalation behaviour.

    All ``restart`` and ``ss_post_restart`` invocations after the
    first one return the SAME mock — escalation re-checks happen
    against the same fake state.
    """
    restart_mock = restart if restart is not None else _restart_result()
    is_active_mock = is_active if is_active is not None else _is_active_result(active=False)

    # Whether we've seen at least one ``sudo -n systemctl restart`` —
    # once we have, subsequent ss calls represent the POST-restart
    # port-bind check inside ``_attempt_warp_restart``'s escalation.
    state = {"restart_seen": False}

    def _side_effect(cmd, *_a, **_k):
        # Order matters — ``sudo -n systemctl restart`` would otherwise
        # match the ``cmd[0] == "systemctl"`` branch on the WRONG arg
        # since cmd[0] is "sudo" but downstream parsing inspects more
        # of the argv. Keep ``sudo`` branch first to be explicit.
        if cmd[0] == "sudo":
            state["restart_seen"] = True
            return restart_mock
        if cmd[0] == "systemctl":
            # is-active vs show — both go through systemctl.
            if len(cmd) >= 2 and cmd[1] == "is-active":
                return is_active_mock
            return show
        if cmd[0] == "ss":
            # Post-restart probes return ss_post_restart so the
            # escalation logic sees the wedge clear (or not).
            if state["restart_seen"] and ss_post_restart is not None:
                return ss_post_restart
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
        ss_calls = [c for c in mock_run.call_args_list if c.args and c.args[0][0] == "ss"]
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
        # Auto-fix now reports the attempted-restart outcome. The
        # default _run_router shape ("sudoers not configured") is the
        # day-one prod state, so we pin that this PR documents the
        # exact sudoers entry needed to unblock the auto-fix.
        assert "sudoers not configured" in alerts[0].auto_fix
        assert "NOPASSWD" in alerts[0].auto_fix
        assert "systemctl restart warp-svc" in alerts[0].auto_fix


class TestWarpAutoFix:
    """Pins for ``_attempt_warp_restart`` — the daemon-restart auto-fix
    introduced to close the autonomy-doc Week 1 item ("warp_down 12
    events, 0 auto-fix successes")."""

    def test_happy_path_reports_success(self):
        """Restart returns 0, post-restart is-active confirms 'active'
        AND ss confirms port 40000 LISTENING → auto_fix is the
        canonical happy-path string."""
        with (
            patch("subprocess.run") as mock_run,
            patch("time.sleep"),
        ):
            mock_run.side_effect = _run_router(
                _show_result(active_state="inactive", sub_state="dead"),
                _ss_result(listening=False),
                restart=_restart_result(returncode=0, stderr=""),
                is_active=_is_active_result(active=True),
                ss_post_restart=_ss_result(listening=True),
            )
            alerts = check_warp_health()
        assert len(alerts) == 1
        assert alerts[0].auto_fix == "restarted warp-svc, port 40000 LISTENING"

    def test_restart_succeeds_but_daemon_still_inactive(self):
        """The pessimistic case: sudo worked, restart returned 0, but
        warp-svc didn't come back up (startup config error). Auto-fix
        must say so explicitly — claiming success here would mislead
        the operator into ignoring the alert."""
        with patch("subprocess.run") as mock_run, patch("time.sleep"):
            mock_run.side_effect = _run_router(
                _show_result(active_state="inactive", sub_state="dead"),
                _ss_result(listening=False),
                restart=_restart_result(returncode=0, stderr=""),
                is_active=_is_active_result(active=False),
            )
            alerts = check_warp_health()
        assert len(alerts) == 1
        assert "restart applied but warp-svc still inactive" in alerts[0].auto_fix

    def test_sudoers_blocked_message_includes_exact_entry(self):
        """The day-one prod state. The auto_fix MUST include the
        exact sudoers line so the operator can copy-paste the fix in
        seconds. Pinning both the prefix marker and the canonical
        entry shape so a refactor that drops either trips this test."""
        with patch("subprocess.run") as mock_run, patch("time.sleep"):
            mock_run.side_effect = _run_router(
                _show_result(active_state="inactive", sub_state="dead"),
                _ss_result(listening=False),
                restart=_restart_result(
                    returncode=1,
                    stderr="sudo: a password is required\n",
                ),
            )
            alerts = check_warp_health()
        assert len(alerts) == 1
        fix = alerts[0].auto_fix
        assert "sudoers not configured" in fix
        assert "genlab ALL=(root) NOPASSWD: /bin/systemctl restart warp-svc" in fix

    def test_other_failure_includes_stderr_snippet(self):
        """A non-sudoers non-zero exit (e.g. unit-not-found, masked).
        Must surface the truncated stderr so the operator has a
        starting point without journalctl spelunking."""
        with patch("subprocess.run") as mock_run, patch("time.sleep"):
            mock_run.side_effect = _run_router(
                _show_result(active_state="inactive", sub_state="dead"),
                _ss_result(listening=False),
                restart=_restart_result(
                    returncode=5,
                    stderr="Failed to restart warp-svc.service: Unit warp-svc.service not loaded.\n",
                ),
            )
            alerts = check_warp_health()
        assert len(alerts) == 1
        fix = alerts[0].auto_fix
        assert "restart failed (rc=5)" in fix
        assert "Unit warp-svc.service not loaded" in fix

    def test_subprocess_exception_does_not_crash_check(self):
        """subprocess.run itself raising must not break the health check
        — we still want the warp_down alert to be emitted, just with
        an honest 'raised' auto_fix message."""

        # First call (the systemctl show) returns the inactive show
        # mock; second call (the sudo restart) raises.
        call_count = {"n": 0}
        show_mock = _show_result(active_state="inactive", sub_state="dead")

        def _flaky_subprocess(cmd, *_a, **_k):
            call_count["n"] += 1
            if cmd[0] == "systemctl" and call_count["n"] == 1:
                return show_mock
            raise OSError("PATH borked: sudo binary missing")

        with patch("subprocess.run", side_effect=_flaky_subprocess), patch("time.sleep"):
            alerts = check_warp_health()
        assert len(alerts) == 1
        assert "restart raised: OSError" in alerts[0].auto_fix
        assert "PATH borked" in alerts[0].auto_fix


class TestWarpAutoFixEscalation:
    """Pins for the 2026-06-16 escalation logic (PR 'feat/warp-autofix-escalation').

    The 2026-06-12 autonomy-doc finding: 12 warp_down events / 0 auto-fix
    successes. Root cause: ``systemctl restart`` returns rc=0 + unit
    flips Active, but port 40000 stays unbound (the wedge needs reboot).

    Escalation:
      attempt 1 → wait 3s → check is-active AND ss → if either fails,
      attempt 2 → wait 8s → re-check → if still wedged, return
      "REBOOT REQUIRED" string.

    Reboot is NOT auto-attempted — operator must SSH and reboot.
    """

    def test_daemon_active_but_port_unbound_triggers_second_restart_then_reboot_string(self):
        """The wedge shape from 2026-06-12: systemctl reports active,
        port is dead. Should attempt 2 restarts, then escalate to
        REBOOT REQUIRED."""
        with patch("subprocess.run") as mock_run, patch("time.sleep"):
            mock_run.side_effect = _run_router(
                _show_result(active_state="inactive", sub_state="dead"),
                _ss_result(listening=False),
                restart=_restart_result(returncode=0, stderr=""),
                is_active=_is_active_result(active=True),
                ss_post_restart=_ss_result(listening=False),  # wedged
            )
            alerts = check_warp_health()
        assert len(alerts) == 1
        assert "REBOOT REQUIRED" in alerts[0].auto_fix
        assert "2 restart attempts" in alerts[0].auto_fix
        assert "SSH and run: reboot" in alerts[0].auto_fix

    def test_first_restart_unbinds_port_no_second_attempt(self):
        """Happy escalation: first restart works AND port comes up.
        Don't bother with a second restart."""
        with patch("subprocess.run") as mock_run, patch("time.sleep"):
            mock_run.side_effect = _run_router(
                _show_result(active_state="inactive", sub_state="dead"),
                _ss_result(listening=False),
                restart=_restart_result(returncode=0, stderr=""),
                is_active=_is_active_result(active=True),
                ss_post_restart=_ss_result(listening=True),
            )
            alerts = check_warp_health()
        assert "LISTENING" in alerts[0].auto_fix
        # NOT the "2 attempts" wording
        assert "2 restart attempts" not in alerts[0].auto_fix

    def test_reboot_string_does_not_invoke_reboot_command(self):
        """Operator must reboot manually. The auto-fix MUST NOT run
        ``reboot`` itself — too dangerous to losing the box mid-publish."""
        reboot_called = {"value": False}

        def _spy(cmd, *_a, **_k):
            if cmd == ["reboot"] or (isinstance(cmd, str) and "reboot" in cmd):
                reboot_called["value"] = True
            # Route normally
            return _run_router(
                _show_result(active_state="inactive", sub_state="dead"),
                _ss_result(listening=False),
                restart=_restart_result(returncode=0, stderr=""),
                is_active=_is_active_result(active=True),
                ss_post_restart=_ss_result(listening=False),
            )(cmd, *_a, **_k)

        with patch("subprocess.run", side_effect=_spy), patch("time.sleep"):
            check_warp_health()
        assert reboot_called["value"] is False, (
            "auto-fix MUST NOT call reboot. Reboot is operator-only."
        )
