"""Regression tests for the per-niche file lock in GenericPipelineRunner.

The lock prevents two pipeline runs for the same niche from racing on
shared state: .tmp/runs/ directory, Postgres stories upserts, content_memory
writes, and the in-memory seen_urls set.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from genlab_core.pipeline.pipeline_runner import _NicheLock, _NicheLockError


def test_lock_acquire_and_release(tmp_path: Path) -> None:
    lock = _NicheLock("test", tmp_path)
    lock.__enter__()
    lock_file = tmp_path / ".tmp" / "locks" / "pipeline-test.lock"
    assert lock_file.exists()
    import os
    assert int(lock_file.read_text().strip()) == os.getpid()
    lock.__exit__(None, None, None)


def test_second_acquire_on_same_niche_blocks(tmp_path: Path) -> None:
    lock1 = _NicheLock("test", tmp_path)
    lock1.__enter__()
    try:
        lock2 = _NicheLock("test", tmp_path)
        with pytest.raises(_NicheLockError, match="already running"):
            lock2.__enter__()
    finally:
        lock1.__exit__(None, None, None)


def test_release_then_reacquire_succeeds(tmp_path: Path) -> None:
    lock1 = _NicheLock("test", tmp_path)
    lock1.__enter__()
    lock1.__exit__(None, None, None)
    lock2 = _NicheLock("test", tmp_path)
    lock2.__enter__()
    lock2.__exit__(None, None, None)


def test_different_niches_can_hold_locks_simultaneously(tmp_path: Path) -> None:
    lock_a = _NicheLock("anime", tmp_path)
    lock_b = _NicheLock("movies", tmp_path)
    lock_a.__enter__()
    try:
        lock_b.__enter__()
        lock_b.__exit__(None, None, None)
    finally:
        lock_a.__exit__(None, None, None)


def test_lock_error_message_includes_holder_pid(tmp_path: Path) -> None:
    lock1 = _NicheLock("test", tmp_path)
    lock1.__enter__()
    try:
        lock2 = _NicheLock("test", tmp_path)
        with pytest.raises(_NicheLockError) as exc_info:
            lock2.__enter__()
        import os
        assert str(os.getpid()) in str(exc_info.value)
    finally:
        lock1.__exit__(None, None, None)
