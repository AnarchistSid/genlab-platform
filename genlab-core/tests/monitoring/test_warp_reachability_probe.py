"""Pin the WARP reachability probe added 2026-07-22.

Prior check_warp_health returned zero alerts when warp-svc was active
and port 40000 was LISTEN, even if actual SOCKS5 requests were
returning "Errno 4 Host unreachable". Today's morning-fire outage
was invisible under that contract — all 5 niche pipelines silently
failed downloads with the health check reporting green.

The added probe fires curl through the SOCKS5 proxy after the
port-LISTEN check passes. Any non-HTTP response triggers a
warp_unreachable critical alert.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.monitoring.checks.infrastructure import check_warp_health


def _mock_active_and_listening() -> list[MagicMock]:
    """Return subprocess.run mocks for: systemctl show (active) + ss (listening).
    Callers append additional mocks for the curl probe."""
    systemctl_active = MagicMock(
        stdout="LoadState=loaded\nActiveState=active\nSubState=running\n",
        stderr="",
        returncode=0,
    )
    ss_listening = MagicMock(
        stdout="tcp   LISTEN 0      1024       127.0.0.1:40000      0.0.0.0:*\n",
        stderr="",
        returncode=0,
    )
    return [systemctl_active, ss_listening]


class TestWarpReachabilityProbe:
    def test_probe_fires_when_daemon_active_and_port_listens(self, monkeypatch) -> None:
        """Real regression from today: warp-svc active + port 40000
        LISTEN + SOCKS5 requests fail with Host unreachable. Must alert."""
        calls = _mock_active_and_listening()
        # curl returns nothing on stdout (proxy connect refused / hung)
        curl_fail = MagicMock(stdout="", stderr="curl: (7) SOCKS5 host unreachable", returncode=7)
        calls.append(curl_fail)

        with patch("subprocess.run", side_effect=calls):
            alerts = check_warp_health()

        assert alerts, "Reachability probe failure must produce an alert"
        assert alerts[0].check == "warp_unreachable"
        assert alerts[0].severity == "critical"
        assert "SOCKS5 reachability probe" in alerts[0].message

    def test_probe_success_no_alert(self, monkeypatch) -> None:
        """When SOCKS5 works (curl returns HTTP response), no alert."""
        calls = _mock_active_and_listening()
        # curl succeeds (real WARP responds with HTTP/2 301 to youtube.com)
        curl_ok = MagicMock(stdout="HTTP/2 301 \n", stderr="", returncode=0)
        calls.append(curl_ok)

        with patch("subprocess.run", side_effect=calls):
            alerts = check_warp_health()

        assert not alerts, (
            f"Healthy WARP must produce no alerts. Got: {[a.check for a in alerts]}"
        )

    def test_port_closed_short_circuits_probe(self, monkeypatch) -> None:
        """When port 40000 isn't listening, we skip the probe entirely
        (already alerted warp_port_closed — no need to also fire probe
        alert)."""
        systemctl_active = MagicMock(
            stdout="LoadState=loaded\nActiveState=active\nSubState=running\n",
            stderr="",
            returncode=0,
        )
        ss_no_port = MagicMock(stdout="", stderr="", returncode=0)  # no LISTEN line
        with patch("subprocess.run", side_effect=[systemctl_active, ss_no_port]):
            alerts = check_warp_health()
        # Should get exactly one alert: warp_port_closed
        assert len(alerts) == 1
        assert alerts[0].check == "warp_port_closed"

    def test_curl_missing_does_not_error(self, monkeypatch) -> None:
        """If curl isn't installed (dev env), probe must fail gracefully
        without alerting or raising."""
        calls = _mock_active_and_listening()
        with patch("subprocess.run", side_effect=[*calls, FileNotFoundError("curl")]):
            alerts = check_warp_health()
        # No probe alert (probe couldn't run); daemon + port were healthy
        assert not any(a.check == "warp_unreachable" for a in alerts)
