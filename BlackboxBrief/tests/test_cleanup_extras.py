"""Tests for _cleanup_extras in scripts/cleanup_runs.py (N-29 retention policies)."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def tmp_tree(tmp_path: Path):
    """Build a fake .tmp/ tree matching BB layout."""
    audio = tmp_path / "audio"
    audio.mkdir()
    logs = tmp_path / "logs"
    logs.mkdir()
    renders = tmp_path / "renders"
    renders.mkdir()
    return tmp_path


def _make_file(directory: Path, name: str, size_bytes: int = 100, age_days: float = 0) -> Path:
    """Create a file with given size and age."""
    f = directory / name
    f.write_bytes(b"x" * size_bytes)
    if age_days > 0:
        mtime = time.time() - (age_days * 86400)
        import os
        os.utime(f, (mtime, mtime))
    return f


class TestCleanupExtrasAge:
    """Phase 2: age-based deletion."""

    def test_old_audio_deleted(self, tmp_tree: Path):
        from scripts.cleanup_runs import _cleanup_extras

        audio_dir = tmp_tree / "audio"
        old = _make_file(audio_dir, "old.mp3", 1000, age_days=16)
        new = _make_file(audio_dir, "new.mp3", 1000, age_days=1)

        with patch("scripts.cleanup_runs.TMP_DIR", tmp_tree):
            _cleanup_extras(dry_run=False)

        assert not old.exists(), "File older than 14d should be deleted"
        assert new.exists(), "File younger than 14d should be kept"

    def test_old_renders_deleted(self, tmp_tree: Path):
        from scripts.cleanup_runs import _cleanup_extras

        renders_dir = tmp_tree / "renders"
        old = _make_file(renders_dir, "frame.png", 500, age_days=10)
        new = _make_file(renders_dir, "recent.png", 500, age_days=1)

        with patch("scripts.cleanup_runs.TMP_DIR", tmp_tree):
            _cleanup_extras(dry_run=False)

        assert not old.exists(), "Render older than 7d should be deleted"
        assert new.exists(), "Render younger than 7d should be kept"

    def test_old_logs_deleted(self, tmp_tree: Path):
        from scripts.cleanup_runs import _cleanup_extras

        logs_dir = tmp_tree / "logs"
        old = _make_file(logs_dir, "old.log", 500, age_days=20)
        new = _make_file(logs_dir, "new.log", 500, age_days=1)

        with patch("scripts.cleanup_runs.TMP_DIR", tmp_tree):
            _cleanup_extras(dry_run=False)

        assert not old.exists(), "Log older than 14d should be deleted"
        assert new.exists(), "Log younger than 14d should be kept"


class TestCleanupExtrasSizeCap:
    """Phase 3: size-cap enforcement."""

    def test_audio_cap_enforced(self, tmp_tree: Path):
        from scripts.cleanup_runs import _cleanup_extras

        audio_dir = tmp_tree / "audio"
        # Create files totaling ~600 MB (over 500 MB cap), all recent
        big = _make_file(audio_dir, "big.mp3", 400 * 1024 * 1024, age_days=0)
        medium = _make_file(audio_dir, "medium.mp3", 200 * 1024 * 1024, age_days=0)

        with patch("scripts.cleanup_runs.TMP_DIR", tmp_tree):
            _cleanup_extras(dry_run=False)

        # Largest should be deleted to get under 500 MB
        assert not big.exists(), "Largest file should be evicted to meet cap"
        assert medium.exists(), "Remaining files should stay under cap"


class TestCleanupExtrasTruncation:
    """Phase 1: log truncation."""

    def test_large_log_truncated(self, tmp_tree: Path):
        from scripts.cleanup_runs import _cleanup_extras

        logs_dir = tmp_tree / "logs"
        log_file = logs_dir / "big.log"
        # Write 15000 lines, each ~80 bytes to exceed 1 MB total
        log_file.write_bytes((b"x" * 78 + b"\n") * 15000)

        original_size = log_file.stat().st_size

        with patch("scripts.cleanup_runs.TMP_DIR", tmp_tree):
            _cleanup_extras(dry_run=False)

        assert log_file.exists(), "Log file should still exist (truncated, not deleted)"
        new_size = log_file.stat().st_size
        assert new_size < original_size, "Log should be smaller after truncation"

    def test_small_log_not_truncated(self, tmp_tree: Path):
        from scripts.cleanup_runs import _cleanup_extras

        logs_dir = tmp_tree / "logs"
        log_file = logs_dir / "small.log"
        log_file.write_bytes(b"just a small log\n" * 100)
        original_size = log_file.stat().st_size

        with patch("scripts.cleanup_runs.TMP_DIR", tmp_tree):
            _cleanup_extras(dry_run=False)

        assert log_file.stat().st_size == original_size, "Small log should not be truncated"


class TestCleanupExtrasDryRun:
    """Dry run should not delete or truncate anything."""

    def test_dry_run_preserves_files(self, tmp_tree: Path):
        from scripts.cleanup_runs import _cleanup_extras

        audio_dir = tmp_tree / "audio"
        old = _make_file(audio_dir, "old.mp3", 1000, age_days=10)

        with patch("scripts.cleanup_runs.TMP_DIR", tmp_tree):
            _cleanup_extras(dry_run=True)

        assert old.exists(), "Dry run should not delete files"


class TestCleanupExtrasEmptyDirs:
    """Empty directories should be removed after cleanup."""

    def test_empty_subdirs_removed(self, tmp_tree: Path):
        from scripts.cleanup_runs import _cleanup_extras

        audio_dir = tmp_tree / "audio"
        sub = audio_dir / "session1"
        sub.mkdir()
        old = _make_file(sub, "clip.wav", 100, age_days=16)

        with patch("scripts.cleanup_runs.TMP_DIR", tmp_tree):
            _cleanup_extras(dry_run=False)

        assert not old.exists()
        assert not sub.exists(), "Empty subdir should be removed"


class TestCleanupExtrasMissingDirs:
    """Missing directories should be silently skipped."""

    def test_missing_dir_no_error(self, tmp_tree: Path):
        from scripts.cleanup_runs import _cleanup_extras

        # Remove renders dir to simulate missing
        (tmp_tree / "renders").rmdir()

        with patch("scripts.cleanup_runs.TMP_DIR", tmp_tree):
            _cleanup_extras(dry_run=False)  # Should not raise
