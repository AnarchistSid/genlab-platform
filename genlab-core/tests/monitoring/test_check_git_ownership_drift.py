"""Pin tests for `check_git_ownership_drift` (2026-07-21).

Prevents recurrence of the class-of-bug that blocked deploy for
~40 min today: 365 files in `/opt/genlab/.git/objects` had drifted
to `root:root` ownership since July 17, causing `sudo -u genlab git
fetch` to fail with `insufficient permission for adding an object`.

Hit at least twice this week (2026-07-18 41 files → 2026-07-19 2
files → 2026-07-21 365 files). Silent until deploy time.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from genlab_core.monitoring.checks.infrastructure import check_git_ownership_drift


def _make_find_result(returncode: int, stdout: str = "") -> MagicMock:
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    return r


class TestOwnershipDrift:
    def test_no_drift_returns_no_alerts(self):
        """Zero mis-owned files → clean, no alert."""
        with patch("subprocess.run") as m:
            m.return_value = _make_find_result(0, "")
            alerts = check_git_ownership_drift()
        assert alerts == []

    def test_single_drifted_file_warns(self):
        """Even 1 mis-owned file is a signal — early warning."""
        with patch("subprocess.run") as m:
            m.return_value = _make_find_result(
                0, "/opt/genlab/.git/objects/a1/23abcdef\n"
            )
            alerts = check_git_ownership_drift()
        assert len(alerts) == 1
        assert alerts[0].severity == "warning"
        assert alerts[0].check == "git_ownership_drift"
        assert alerts[0].details["count"] == 1

    def test_many_drifted_files_critical(self):
        """≥100 mis-owned files = deploy-blocking imminent → critical."""
        many = "\n".join(f"/opt/genlab/.git/objects/aa/{i:08x}" for i in range(150))
        with patch("subprocess.run") as m:
            m.return_value = _make_find_result(0, many)
            alerts = check_git_ownership_drift()
        assert len(alerts) == 1
        assert alerts[0].severity == "critical"
        assert alerts[0].details["count"] == 150

    def test_alert_carries_actionable_auto_fix(self):
        """Auto-fix hint must give operator the exact chown to run."""
        with patch("subprocess.run") as m:
            m.return_value = _make_find_result(0, "/opt/genlab/.git/x\n")
            alerts = check_git_ownership_drift()
        assert "chown -R genlab:genlab" in alerts[0].auto_fix
        assert "/opt/genlab/.git" in alerts[0].auto_fix

    def test_sample_paths_in_details(self):
        """Details include up to 3 sample paths for operator spot-check."""
        lines = "\n".join(f"/opt/genlab/.git/objects/x{i}" for i in range(10))
        with patch("subprocess.run") as m:
            m.return_value = _make_find_result(0, lines)
            alerts = check_git_ownership_drift()
        assert len(alerts[0].details["sample"]) == 3

    def test_find_failure_returns_no_alerts(self):
        """If `find` fails (missing .git / perms), silent skip — no
        false alarm, no crash."""
        with patch("subprocess.run") as m:
            m.return_value = _make_find_result(2, "")
            alerts = check_git_ownership_drift()
        assert alerts == []

    def test_subprocess_exception_returns_no_alerts(self):
        """Monitor must NEVER crash on tooling failure."""
        with patch("subprocess.run", side_effect=OSError("no find binary")):
            alerts = check_git_ownership_drift()
        assert alerts == []

    def test_timeout_returns_no_alerts(self):
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="find", timeout=10),
        ):
            alerts = check_git_ownership_drift()
        assert alerts == []

    def test_respects_project_root_env(self, monkeypatch):
        """`GENLAB_PROJECT_ROOT` override honored so tests + non-prod
        boxes can use a different path."""
        monkeypatch.setenv("GENLAB_PROJECT_ROOT", "/opt/other")
        with patch("subprocess.run") as m:
            m.return_value = _make_find_result(0, "")
            check_git_ownership_drift()
        # First positional arg to subprocess.run is the argv list.
        call_argv = m.call_args[0][0]
        assert "/opt/other/.git" in call_argv
