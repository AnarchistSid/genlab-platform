"""Tests for the git drift detection check in health_monitor.

The check fires when uncommitted edits accumulate on the production host
— the 2026-05-17 audit found 25+ Python files with real forward-fixes
sitting in the working tree for months, invisible to systemctl/journalctl.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.monitoring.health_monitor import check_git_drift


def _fake_git_status(modified_files: list[str]) -> MagicMock:
    """Build a fake subprocess.run result for `git status --porcelain`."""
    result = MagicMock()
    result.returncode = 0
    # Real git status --porcelain format: 'XY filename' (2-char status + space + path)
    result.stdout = "\n".join(f" M {p}" for p in modified_files)
    return result


class TestGitDriftCheck:
    def test_no_drift_returns_no_alerts(self):
        """Clean working tree → no alerts."""
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        with patch("subprocess.run", return_value=result):
            alerts = check_git_drift()
        assert alerts == []

    def test_few_python_files_no_alert(self):
        """Below warning threshold (5) → no alert."""
        files = [f"genlab-core/src/genlab_core/foo_{i}.py" for i in range(4)]
        with patch("subprocess.run", return_value=_fake_git_status(files)):
            alerts = check_git_drift()
        assert alerts == []

    def test_five_python_files_warning(self):
        """Hits warning threshold (5) → warning alert."""
        files = [f"genlab-core/src/genlab_core/foo_{i}.py" for i in range(5)]
        with patch("subprocess.run", return_value=_fake_git_status(files)):
            alerts = check_git_drift()
        assert len(alerts) == 1
        assert alerts[0].severity == "warning"
        assert alerts[0].check == "git_drift"
        assert alerts[0].details["py_count"] == 5

    def test_fifteen_python_files_critical(self):
        """Hits critical threshold (15) → critical alert."""
        files = [f"genlab-core/src/genlab_core/foo_{i}.py" for i in range(15)]
        with patch("subprocess.run", return_value=_fake_git_status(files)):
            alerts = check_git_drift()
        assert len(alerts) == 1
        assert alerts[0].severity == "critical"
        assert alerts[0].check == "git_drift"

    def test_yaml_drift_separate_alert(self):
        """30+ yaml configs → separate yaml-specific warning."""
        files = [f"config/niche_{i}.yaml" for i in range(30)]
        with patch("subprocess.run", return_value=_fake_git_status(files)):
            alerts = check_git_drift()
        # Yaml-only drift fires the yaml alert, not the python one
        assert len(alerts) == 1
        assert alerts[0].check == "git_drift_yaml"
        assert alerts[0].severity == "warning"

    def test_yaml_drift_below_threshold_no_alert(self):
        """Yaml drift below 30 is expected (per-host overrides) → silent."""
        files = [f"config/niche_{i}.yaml" for i in range(10)]
        with patch("subprocess.run", return_value=_fake_git_status(files)):
            alerts = check_git_drift()
        assert alerts == []

    def test_mixed_drift_both_alerts(self):
        """Both python and yaml drift can fire simultaneously."""
        files = [f"genlab-core/src/genlab_core/file_{i}.py" for i in range(16)] + [
            f"config/niche_{i}.yaml" for i in range(35)
        ]
        with patch("subprocess.run", return_value=_fake_git_status(files)):
            alerts = check_git_drift()
        checks = {a.check for a in alerts}
        assert checks == {"git_drift", "git_drift_yaml"}

    def test_untracked_files_ignored(self):
        """`??` (untracked) entries are noisy — skipped from python count."""
        result = MagicMock()
        result.returncode = 0
        # Mix of 4 modified + 5 untracked python files
        result.stdout = "\n".join(
            [
                *[f" M genlab-core/src/genlab_core/m_{i}.py" for i in range(4)],
                *[f"?? genlab-core/src/genlab_core/u_{i}.py" for i in range(5)],
            ]
        )
        with patch("subprocess.run", return_value=result):
            alerts = check_git_drift()
        # Only 4 tracked-modified counted, below threshold
        assert alerts == []

    def test_non_genlab_python_paths_ignored(self):
        """A modified .py outside the project source dirs doesn't count."""
        result = MagicMock()
        result.returncode = 0
        # All in unrelated paths — outside genlab-core/, scripts/, dashboard/, etc.
        result.stdout = "\n".join(f" M random/external/path_{i}.py" for i in range(20))
        with patch("subprocess.run", return_value=result):
            alerts = check_git_drift()
        assert alerts == []

    def test_git_failure_silent(self):
        """If `git status` errors (no git, not a repo), no alert — no spam."""
        result = MagicMock()
        result.returncode = 128
        result.stdout = ""
        with patch("subprocess.run", return_value=result):
            alerts = check_git_drift()
        assert alerts == []

    def test_alert_details_include_top_files(self):
        """Alert details should surface the top-N modified files for triage."""
        files = [f"genlab-core/src/genlab_core/m_{i:02d}.py" for i in range(20)]
        with patch("subprocess.run", return_value=_fake_git_status(files)):
            alerts = check_git_drift()
        assert len(alerts) == 1
        # Details should list a reasonable preview (capped at 10)
        assert "py_files" in alerts[0].details
        assert len(alerts[0].details["py_files"]) <= 10
        # First 3 should appear in the message for at-a-glance triage
        assert "m_00.py" in alerts[0].message
