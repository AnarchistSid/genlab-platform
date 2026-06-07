"""Tests for :mod:`genlab_core.publishing.pid_lock`.

Most of the lock's logic only triggers under genuine inter-process
contention — the easiest way to test that without a second process is to
hand-write a PID file with a non-existent PID and assert the stale path.

These tests live in their own file (paralleling the new module home in PR
1/N of the publish_all_platforms decomposition) so the regression surface
is right next to the code under test, not embedded in the omnibus
``test_publish_all_platforms``.
"""

from __future__ import annotations

import os

import pytest
from genlab_core.publishing.pid_lock import PidLock


class TestAcquireRelease:
    def test_acquire_returns_true_when_no_lock_file(self, tmp_path):
        lock = PidLock("gaming", lock_dir=tmp_path)
        assert lock.acquire() is True
        assert lock.path.exists()
        assert lock.path.read_text().strip() == str(os.getpid())

    def test_release_removes_lock_file(self, tmp_path):
        lock = PidLock("gaming", lock_dir=tmp_path)
        lock.acquire()
        lock.release()
        assert not lock.path.exists()

    def test_release_is_idempotent(self, tmp_path):
        """release() must be safe to call when the lock file is already gone
        (e.g. cleaned up by a sibling process or os hooks)."""
        lock = PidLock("gaming", lock_dir=tmp_path)
        lock.release()  # never acquired
        lock.release()  # release twice — second is a no-op
        # No exception means we're good.

    def test_second_acquire_blocks_when_holder_alive(self, tmp_path):
        """Same-process double-acquire: the second attempt sees its own PID
        in the file and (correctly) reports the lock held."""
        lock_a = PidLock("gaming", lock_dir=tmp_path)
        lock_b = PidLock("gaming", lock_dir=tmp_path)
        assert lock_a.acquire() is True
        assert lock_b.acquire() is False  # holder PID is alive (it's us)


class TestStaleLockHandling:
    def test_stale_pid_file_is_replaced(self, tmp_path):
        """A lock file referencing a non-existent PID is treated as stale —
        ``acquire`` removes it and succeeds on the retry."""
        lock = PidLock("gaming", lock_dir=tmp_path)
        # PID 999999 is unlikely to exist on any test host.
        lock.path.write_text("999999")
        assert lock.acquire() is True
        assert lock.path.read_text().strip() == str(os.getpid())

    def test_unreadable_lock_file_is_replaced(self, tmp_path):
        """A non-numeric body is also treated as stale — ValueError from
        ``int()`` falls into the same removal branch."""
        lock = PidLock("gaming", lock_dir=tmp_path)
        lock.path.write_text("not a pid")
        assert lock.acquire() is True
        assert lock.path.read_text().strip() == str(os.getpid())


class TestPathConstruction:
    def test_default_lock_dir_is_tmp(self):
        """Default lock_dir is /tmp — the deploy convention. If the default
        ever changes, every operator's mental model of where the lock lives
        changes too; a regression here would silently let two publishers
        run at once."""
        lock = PidLock("gaming")
        assert str(lock.path).startswith("/tmp/")

    @pytest.mark.parametrize("niche", ["gaming", "sports", "ai_creators"])
    def test_per_niche_lock_filename(self, niche, tmp_path):
        lock = PidLock(niche, lock_dir=tmp_path)
        assert lock.path.name == f"publisher-{niche}.lock"
