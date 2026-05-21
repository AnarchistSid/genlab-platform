"""Tests for the publish lock mechanism in cleanup_runs.py."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure scripts/ is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from cleanup_runs import (
    acquire_publish_lock,
    is_publish_locked,
    release_publish_lock,
)


def test_acquire_creates_lock_file(tmp_path: Path):
    lock = acquire_publish_lock(tmp_path)
    assert lock.exists()
    assert lock.name == ".publishing_lock"


def test_is_publish_locked_true(tmp_path: Path):
    (tmp_path / ".publishing_lock").touch()
    assert is_publish_locked(tmp_path) is True


def test_is_publish_locked_false(tmp_path: Path):
    assert is_publish_locked(tmp_path) is False


def test_release_removes_lock(tmp_path: Path):
    lock = acquire_publish_lock(tmp_path)
    assert lock.exists()
    release_publish_lock(lock)
    assert not lock.exists()


def test_release_missing_lock_is_noop(tmp_path: Path):
    lock = tmp_path / ".publishing_lock"
    release_publish_lock(lock)  # should not raise


def test_cleanup_skips_locked_directory(tmp_path: Path):
    """Simulate cleanup skipping a locked directory."""
    render_dir = tmp_path / "renders"
    render_dir.mkdir()
    video = render_dir / "test.mp4"
    video.write_bytes(b"\x00" * 100)

    acquire_publish_lock(render_dir)

    # Simulate cleanup logic: check lock before deleting
    files_to_delete = []
    for f in render_dir.rglob("*"):
        if f.is_file() and f.name != ".publishing_lock":
            if not is_publish_locked(f.parent):
                files_to_delete.append(f)

    assert len(files_to_delete) == 0, "Locked directory files should not be collected"
    assert video.exists(), "Video should NOT have been deleted"
