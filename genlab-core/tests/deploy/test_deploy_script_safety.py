"""R-02: pin the safety pre-flight in ``deploy/scripts/deploy.sh``.

The script's pre-flight runs BEFORE any network egress, so we can
exercise it in CI without an ssh target:

  * empty argv → usage exit 1
  * absolute path → exit 1 (no flat staging escape)
  * '..' in path → exit 1 (no path traversal)
  * non-existent file → exit 1
  * non-regular file (a dir) → exit 1

Cluster A (2026-05-18) was an absolute/staging-dir scp; these pins
make the same shape physically impossible from this wrapper.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_GENLAB_ROOT = Path(__file__).resolve().parents[3]
_DEPLOY_SH = _GENLAB_ROOT / "deploy" / "scripts" / "deploy.sh"


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run deploy.sh and capture exit code + stderr.

    We force ``GENLAB_DEPLOY_HOST`` to an unrouteable value so a
    misbehaving pre-flight (one that lets a bad arg through) can't
    accidentally touch a real host from the test runner.
    """
    return subprocess.run(
        [str(_DEPLOY_SH), *argv],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/tmp",
            "GENLAB_DEPLOY_HOST": "0.0.0.0",
            "GENLAB_DEPLOY_USER": "nonexistent-test-user",
        },
        check=False,
        timeout=15,
    )


def test_r02_no_args_shows_usage_and_exits_1() -> None:
    result = _run([])
    assert result.returncode == 1
    assert "Usage:" in result.stderr


def test_r02_absolute_path_rejected() -> None:
    """Absolute paths are the Cluster A vector — must be hard-rejected."""
    result = _run(["/etc/passwd"])
    assert result.returncode == 1
    assert "absolute path" in result.stderr.lower()


def test_r02_path_traversal_rejected() -> None:
    """'..' in a path could escape the repo-relative contract."""
    result = _run(["foo/../etc/passwd"])
    assert result.returncode == 1
    assert "path traversal" in result.stderr.lower() or ".." in result.stderr


def test_r02_nonexistent_file_rejected() -> None:
    """Pre-flight fails before any network egress."""
    result = _run(["this-file-does-not-exist-anywhere.yaml"])
    assert result.returncode == 1
    assert "not a regular file" in result.stderr.lower()


def test_r02_directory_rejected() -> None:
    """Sending a directory would silently miss files — the wrapper
    requires you to list files explicitly (Cluster A lesson)."""
    result = _run(["deploy"])  # exists but is a directory
    assert result.returncode == 1
    assert "not a regular file" in result.stderr.lower()
