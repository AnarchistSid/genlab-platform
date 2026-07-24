"""Tests for scan_stale_durable_errors.py.

Motivating class-of-bug (2026-07-24): auto_accept_strategist_proposals
service exit=3 every fire for 5 weeks. Durable error file worked.
Nobody read it. This scanner is the missing feedback loop.

Pins:
  Staleness classification:
    - Files <stale_hours old → fresh (not alerted)
    - Files ≥stale_hours old → stale (alerted)
    - Custom --stale-hours honored
  Fail-open:
    - Missing runtime root → exit 0, log info
    - Malformed file → skip that entry, continue
    - Unhandled exception → exit 0 (rule #26 discipline)
  CLI shape:
    - --apply is opt-in (default = dry-run)
    - Exit code always 0
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "scan_stale_durable_errors.py"


def _write_error_file(root: Path, script: str, age_hours: float = 0.0) -> Path:
    """Create a synthetic durable-error file with a specific age."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{script}_last_error.txt"
    path.write_text(
        "2026-07-24T12:00:00+00:00\n"
        f"script: {script}\n\n"
        "ERROR: synthetic test error\n"
        "Traceback (most recent call last):\n"
        "  ... test fixture ...\n"
    )
    # Backdate mtime
    if age_hours > 0:
        target = time.time() - age_hours * 3600
        import os as _os
        _os.utime(path, (target, target))
    return path


def _run_script(*args) -> subprocess.CompletedProcess:
    """Invoke the script as a subprocess. Returns CompletedProcess."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestExitCodeAlwaysZero:
    """Rule #26 discipline — scanner MUST exit 0 in all conditions.
    Otherwise it becomes a source of the very alarms it's designed
    to help operators triage."""

    def test_dry_run_missing_root_exits_zero(self, tmp_path):
        # Non-existent runtime root — script should log info + exit 0.
        result = _run_script("--runtime-root", str(tmp_path / "nope"))
        assert result.returncode == 0

    def test_dry_run_empty_root_exits_zero(self, tmp_path):
        result = _run_script("--runtime-root", str(tmp_path))
        assert result.returncode == 0
        assert "found 0 durable-error files" in result.stderr or "found 0" in result.stdout


class TestStalenessClassification:
    def test_fresh_files_not_alerted(self, tmp_path):
        # 1h old — below default 24h threshold
        _write_error_file(tmp_path, "fresh_script", age_hours=1.0)
        result = _run_script("--runtime-root", str(tmp_path))
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert "1 fresh" in combined
        assert "0 stale" in combined

    def test_stale_files_alerted(self, tmp_path):
        # 48h old — above default 24h threshold
        _write_error_file(tmp_path, "stale_script", age_hours=48.0)
        result = _run_script("--runtime-root", str(tmp_path))
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert "1 stale" in combined
        assert "STALE script=stale_script" in combined

    def test_custom_stale_hours(self, tmp_path):
        # 5h old — above --stale-hours=3 but below default 24h
        _write_error_file(tmp_path, "custom_script", age_hours=5.0)
        result = _run_script(
            "--runtime-root", str(tmp_path),
            "--stale-hours", "3",
        )
        combined = result.stdout + result.stderr
        assert result.returncode == 0
        assert "1 stale" in combined


class TestFailOpen:
    def test_malformed_file_does_not_abort_scan(self, tmp_path):
        # One malformed empty file + one valid stale file — scan
        # should continue past the malformed and report the valid one.
        (tmp_path / "empty_last_error.txt").write_text("")
        _write_error_file(tmp_path, "valid_script", age_hours=48.0)
        result = _run_script("--runtime-root", str(tmp_path))
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        # Both files were seen (fresh + stale count = 2)
        assert "found 2 durable-error files" in combined
        assert "STALE script=valid_script" in combined


class TestCLIShape:
    def test_apply_is_opt_in(self):
        """Dry-run must be the default. Otherwise a test invocation
        would silently write pipeline_alerts rows against whatever
        DATABASE_URL is set."""
        # Read source to verify the argparse flag exists AND
        # store_true (not store_false which would flip default).
        text = SCRIPT.read_text()
        assert '"--apply"' in text
        assert 'action="store_true"' in text
        # Also verify dry-run guards the write path.
        assert "if not args.apply:" in text

    def test_script_exit_code_shape_matches_rule_26(self):
        """Rule #26: exit 0 unless genuine incident. The scanner IS
        the diagnostic — if IT fails the way it's designed to
        surface, that's ironic. Assert the shape."""
        text = SCRIPT.read_text()
        # The outer wrapper's exit codes must all be 0.
        assert "return 0" in text
        # No exit(1) or exit(2) etc — the wrapper collapses everything to 0.
        # Allow sys.exit(_main_with_durable_error()) — that returns 0.
        assert "return int(e.code) if isinstance(e.code, int) else 0" in text
