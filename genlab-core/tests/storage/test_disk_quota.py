"""Tests for genlab_core.storage.disk_quota — score-weighted eviction."""

from __future__ import annotations

import json
import os
import shutil
import time

import pytest
from genlab_core.storage.disk_quota import (
    DiskQuotaManager,
)

# ── Helpers ──────────────────────────────────────────────────


def _make_run(
    runs_dir,
    name: str,
    *,
    size_kb: int = 10,
    clips_kb: int = 0,
    score: float | None = None,
    published: bool = False,
    mtime_offset: float = 0,
):
    """Create a run directory with optional clips/ and run_report.json."""
    run = runs_dir / name
    run.mkdir(parents=True, exist_ok=True)

    # Write filler data to hit target size
    filler = run / "data.bin"
    filler.write_bytes(b"x" * (size_kb * 1024))

    if clips_kb > 0:
        clips = run / "clips"
        clips.mkdir(exist_ok=True)
        (clips / "clip.mp4").write_bytes(b"v" * (clips_kb * 1024))

    if score is not None:
        report = {
            "run_id": name,
            "metrics": {"score_distribution": {"top5_avg": score}},
        }
        (run / "run_report.json").write_text(json.dumps(report))

    if published:
        (run / "publish_all_summary.json").write_text("{}")

    # Adjust mtime so tests control ordering
    if mtime_offset != 0:
        now = time.time()
        os.utime(run, (now + mtime_offset, now + mtime_offset))

    return run


def _cfg(runs_dir, quota_kb: int = 100, protect_recent: int = 1):
    """Build a minimal config dict pointing at runs_dir with a tiny quota."""
    return {
        "agents": {
            "test": {
                "runs_dir": str(runs_dir),
                "quota_gb": quota_kb / (1024**2),  # KB → GB
                "warn_pct": 60,
                "evict_pct": 80,
                "protect_recent": protect_recent,
            }
        }
    }


# ── 1. scan_runs discovers directories ──────────────────────


class TestScanRuns:
    def test_discovers_all_directories(self, tmp_path):
        runs = tmp_path / "runs"
        _make_run(runs, "run_a", size_kb=5)
        _make_run(runs, "run_b", size_kb=5)
        _make_run(runs, "run_c", size_kb=5)

        mgr = DiskQuotaManager(_cfg(runs))
        records = mgr.scan_runs("test")

        assert len(records) == 3
        names = {r.run_id for r in records}
        assert names == {"run_a", "run_b", "run_c"}

    def test_extracts_score_from_run_report(self, tmp_path):
        """Score comes from metrics.score_distribution.top5_avg."""
        runs = tmp_path / "runs"
        _make_run(runs, "scored", score=0.87)

        mgr = DiskQuotaManager(_cfg(runs))
        records = mgr.scan_runs("test")

        assert len(records) == 1
        assert records[0].score == pytest.approx(0.87)

    def test_falls_back_to_blueprint_score(self, tmp_path):
        """When no run_report, use mean priority_score from blueprint_pack."""
        runs = tmp_path / "runs"
        run = runs / "bp_run"
        run.mkdir(parents=True)
        (run / "data.bin").write_bytes(b"x" * 100)
        bp = [
            {"priority_score": 0.6},
            {"priority_score": 0.8},
        ]
        (run / "blueprint_pack.json").write_text(json.dumps(bp))

        mgr = DiskQuotaManager(_cfg(runs))
        records = mgr.scan_runs("test")

        assert records[0].score == pytest.approx(0.7)

    def test_detects_published_by_summary(self, tmp_path):
        runs = tmp_path / "runs"
        _make_run(runs, "pub_run", published=True)
        _make_run(runs, "normal_run")

        mgr = DiskQuotaManager(_cfg(runs))
        records = mgr.scan_runs("test")

        pub = [r for r in records if r.run_id == "pub_run"][0]
        normal = [r for r in records if r.run_id == "normal_run"][0]
        assert pub.is_published is True
        assert normal.is_published is False

    def test_detects_published_by_suffix(self, tmp_path):
        runs = tmp_path / "runs"
        _make_run(runs, "20260301_pub")

        mgr = DiskQuotaManager(_cfg(runs))
        records = mgr.scan_runs("test")

        assert records[0].is_published is True

    def test_filesystem_backstop_protects_rendered_mp4(self, tmp_path):
        """Pin the filesystem-level backstop restored 2026-07-18.

        Scenario reproduces the 2026-07-18 GC bug: a run dir with a
        rendered composite MP4 under ``visuals/`` — no
        ``publish_all_summary.json``, no ``_pub`` suffix, no DB
        protection needed. Filesystem check alone must mark it
        published so eviction skips it.

        This is the audit-facing invariant. If the DB check in
        ``_get_pending_publish_run_ids`` fails open on a DB blip,
        this backstop keeps rendered media from being purged.
        """
        runs = tmp_path / "runs"
        run = runs / "movies_20260627_091638"
        (run / "visuals" / "40670df795dd7e03").mkdir(parents=True)
        (run / "visuals" / "40670df795dd7e03" / "reel.mp4").write_bytes(b"v" * 100)

        mgr = DiskQuotaManager(_cfg(runs))
        records = mgr.scan_runs("test")

        assert records[0].is_published is True, (
            "Run with rendered MP4 under visuals/ must be protected "
            "(regressed 2026-03-17 in commit 9082e724, restored 2026-07-18)"
        )

    def test_filesystem_backstop_protects_visuals_v2(self, tmp_path):
        runs = tmp_path / "runs"
        run = runs / "rerendered_run"
        (run / "visuals_v2").mkdir(parents=True)
        (run / "visuals_v2" / "reel.mp4").write_bytes(b"v" * 100)

        mgr = DiskQuotaManager(_cfg(runs))
        records = mgr.scan_runs("test")

        assert records[0].is_published is True

    def test_filesystem_backstop_protects_clips_only(self, tmp_path):
        """A run with only source clips (no rendered composite yet) is
        still worth protecting — the clips are the recovery path if
        the operator wants to re-render."""
        runs = tmp_path / "runs"
        run = runs / "clips_only_run"
        (run / "clips").mkdir(parents=True)
        (run / "clips" / "source.mp4").write_bytes(b"v" * 100)

        mgr = DiskQuotaManager(_cfg(runs))
        records = mgr.scan_runs("test")

        assert records[0].is_published is True

    def test_filesystem_backstop_no_mp4_not_protected(self, tmp_path):
        """Negative case: run dir with visuals/ subdir but no MP4
        inside should NOT be marked published. Otherwise the backstop
        becomes over-eager and protects empty scratch dirs forever."""
        runs = tmp_path / "runs"
        run = runs / "empty_visuals_run"
        (run / "visuals").mkdir(parents=True)
        (run / "visuals" / "notes.txt").write_text("no mp4 here")

        mgr = DiskQuotaManager(_cfg(runs))
        records = mgr.scan_runs("test")

        assert records[0].is_published is False


# ── 2. Eviction logic ───────────────────────────────────────


class TestEviction:
    def test_clips_deleted_before_full_runs(self, tmp_path):
        """Pass 1 should remove clips/ without deleting the run directory."""
        runs = tmp_path / "runs"
        # Recent run (protected)
        _make_run(runs, "recent", size_kb=5, mtime_offset=10)
        # Old run with large clips (eviction target)
        _make_run(runs, "old_with_clips", size_kb=5, clips_kb=50, score=0.3, mtime_offset=-100)

        # Quota is 80 KB; used = 5 + 5 + 50 = 60 KB. evict_pct=80% of 80KB = 64KB.
        # We're under evict_pct so evict() with explicit target should still work.
        mgr = DiskQuotaManager(_cfg(runs, quota_kb=80, protect_recent=1))
        evicted = mgr.evict("test", target_free_bytes=50 * 1024)

        # Clips should be gone but run directory survives
        assert not (runs / "old_with_clips" / "clips").exists()
        assert (runs / "old_with_clips").exists()
        assert any("clips" in p for p in evicted)

    def test_full_run_deleted_when_clips_insufficient(self, tmp_path):
        """Pass 2 deletes entire runs when clips alone don't free enough."""
        runs = tmp_path / "runs"
        _make_run(runs, "recent", size_kb=5, mtime_offset=10)
        _make_run(runs, "old_small", size_kb=20, score=0.2, mtime_offset=-100)

        # Need 30 KB free, old_small has no clips, must delete the whole run
        mgr = DiskQuotaManager(_cfg(runs, quota_kb=30, protect_recent=1))
        evicted = mgr.evict("test", target_free_bytes=25 * 1024)

        assert not (runs / "old_small").exists()
        assert len(evicted) >= 1

    def test_never_touches_published(self, tmp_path):
        """Published runs must survive even if they're the lowest-scoring."""
        runs = tmp_path / "runs"
        _make_run(runs, "recent", size_kb=5, score=0.9, mtime_offset=10)
        _make_run(runs, "published", size_kb=30, score=0.1, published=True, mtime_offset=-200)
        _make_run(runs, "expendable", size_kb=20, score=0.2, mtime_offset=-100)

        mgr = DiskQuotaManager(_cfg(runs, quota_kb=60, protect_recent=1))
        mgr.evict("test", target_free_bytes=30 * 1024)

        # Published run must survive
        assert (runs / "published").exists()

    def test_protects_recent_runs(self, tmp_path):
        """The N most recent runs are never evicted regardless of score."""
        runs = tmp_path / "runs"
        _make_run(runs, "newest", size_kb=20, score=0.1, mtime_offset=20)
        _make_run(runs, "second", size_kb=20, score=0.1, mtime_offset=10)
        _make_run(runs, "oldest", size_kb=20, score=0.9, mtime_offset=-100)

        # protect_recent=2: newest and second are protected
        mgr = DiskQuotaManager(_cfg(runs, quota_kb=70, protect_recent=2))
        mgr.evict("test", target_free_bytes=30 * 1024)

        assert (runs / "newest").exists()
        assert (runs / "second").exists()
        # oldest is evictable even though it has the highest score — it's the only candidate
        assert not (runs / "oldest").exists()

    def test_lowest_score_evicted_first(self, tmp_path):
        """Among evictable runs, the lowest-scoring is deleted first."""
        runs = tmp_path / "runs"
        _make_run(runs, "recent", size_kb=5, score=0.5, mtime_offset=10)
        _make_run(runs, "low_score", size_kb=30, score=0.1, mtime_offset=-200)
        _make_run(runs, "high_score", size_kb=20, score=0.9, mtime_offset=-100)

        # Used ~55 KB, quota 100 KB, need 60 KB free.
        # current_free ~45 KB, need_to_free ~15 KB.
        # low_score (30 KB) is enough — high_score (20 KB) survives.
        mgr = DiskQuotaManager(_cfg(runs, quota_kb=100, protect_recent=1))
        mgr.evict("test", target_free_bytes=60 * 1024)

        assert not (runs / "low_score").exists()
        assert (runs / "high_score").exists()


# ── 3. Quota status ─────────────────────────────────────────


class TestQuotaStatus:
    def test_status_calculation(self, tmp_path):
        runs = tmp_path / "runs"
        _make_run(runs, "run1", size_kb=40, mtime_offset=10)
        _make_run(runs, "run2", size_kb=40, mtime_offset=-10)

        # quota=100KB, used=80KB → 80%, warn=60, evict=80
        mgr = DiskQuotaManager(_cfg(runs, quota_kb=100, protect_recent=1))
        status = mgr.get_status("test")

        assert status.agent == "test"
        assert status.run_count == 2
        assert status.evictable_count == 1  # only run2 (run1 is protected)
        assert status.over_warn is True  # 80% >= 60%
        assert status.over_quota is True  # 80% >= 80%


# ── 4. Pre-flight check ────────────────────────────────────


class TestPreflightCheck:
    def test_passes_when_under_quota(self, tmp_path):
        runs = tmp_path / "runs"
        _make_run(runs, "small", size_kb=10, mtime_offset=10)

        # quota=100KB, used=10KB, estimated=20KB → projected 30% < 80%
        mgr = DiskQuotaManager(_cfg(runs, quota_kb=100))
        assert mgr.preflight_check("test", estimated_bytes=20 * 1024) is True

    def test_triggers_eviction_when_over(self, tmp_path):
        """Pre-flight should auto-evict and then pass if enough freed."""
        runs = tmp_path / "runs"
        _make_run(runs, "recent", size_kb=10, score=0.9, mtime_offset=10)
        _make_run(runs, "old", size_kb=60, score=0.2, mtime_offset=-100)

        # quota=100KB, used=70KB, estimated=30KB → projected 100% >= 80%
        # After evicting "old" (60KB): used=10KB, projected=40% < 80%
        mgr = DiskQuotaManager(_cfg(runs, quota_kb=100, protect_recent=1))
        result = mgr.preflight_check("test", estimated_bytes=30 * 1024)

        assert result is True
        assert not (runs / "old").exists()


# ---------------------------------------------------------------------------
# sweep_extra_dirs — audit P-9 (.tmp/rerender + .tmp/cache + .tmp/logs leak)
# ---------------------------------------------------------------------------


class TestSweepExtraDirs:
    """The audit found 408 MB of ``.tmp/rerender`` from 2026-03-27 leaked
    because the per-agent ``scan_runs`` only walked ``runs_dir``. These
    tests guard the new sweep that closes that leak."""

    def _setup(self, tmp_path, mtime_offset_days: float, files: int = 1):
        """Build an extra-dir with ``files`` children, all mtime'd to
        ``mtime_offset_days`` ago (negative = past)."""
        d = tmp_path / "rerender"
        d.mkdir()
        now = time.time()
        for i in range(files):
            child = d / f"job_{i}"
            child.mkdir()
            (child / "data.bin").write_bytes(b"x" * 1024)  # 1 KB each
            past = now + mtime_offset_days * 86400
            for entry in [child, child / "data.bin"]:
                os.utime(entry, (past, past))
        return d

    def test_old_children_deleted(self, tmp_path):
        d = self._setup(tmp_path, mtime_offset_days=-10, files=3)
        mgr = DiskQuotaManager({"extra_dirs": [{"path": str(d), "max_age_days": 7}]})
        freed = mgr.sweep_extra_dirs()
        assert freed[str(d)] >= 3 * 1024
        assert list(d.iterdir()) == []

    def test_recent_children_kept(self, tmp_path):
        d = self._setup(tmp_path, mtime_offset_days=-2, files=2)
        mgr = DiskQuotaManager({"extra_dirs": [{"path": str(d), "max_age_days": 7}]})
        freed = mgr.sweep_extra_dirs()
        assert freed[str(d)] == 0
        assert len(list(d.iterdir())) == 2

    def test_mixed_old_and_recent(self, tmp_path):
        old = self._setup(tmp_path, mtime_offset_days=-10, files=2)
        fresh = old / "fresh"
        fresh.mkdir()
        (fresh / "x.bin").write_bytes(b"y" * 1024)
        mgr = DiskQuotaManager({"extra_dirs": [{"path": str(old), "max_age_days": 7}]})
        mgr.sweep_extra_dirs()
        assert fresh.exists()
        assert not (old / "job_0").exists()
        assert not (old / "job_1").exists()

    def test_missing_directory_is_noop(self, tmp_path):
        """A configured path that doesn't exist on this host (e.g. a niche
        that has never run) is silently skipped — not a leak."""
        mgr = DiskQuotaManager(
            {"extra_dirs": [{"path": str(tmp_path / "ghost"), "max_age_days": 7}]}
        )
        freed = mgr.sweep_extra_dirs()
        assert freed == {}

    def test_zero_max_age_skipped_with_warning(self, tmp_path):
        """A misconfigured ``max_age_days: 0`` would delete EVERYTHING.
        Refuse and log instead — never silently mass-delete."""
        d = self._setup(tmp_path, mtime_offset_days=-100, files=1)
        mgr = DiskQuotaManager({"extra_dirs": [{"path": str(d), "max_age_days": 0}]})
        mgr.sweep_extra_dirs()
        assert (d / "job_0").exists()

    def test_no_extra_dirs_config_is_noop(self):
        mgr = DiskQuotaManager({"agents": {}})
        assert mgr.sweep_extra_dirs() == {}

    def test_per_file_delete_failure_doesnt_block_siblings(self, tmp_path, monkeypatch):
        """One failed rmtree must NOT stop the sweep of sibling entries."""
        d = self._setup(tmp_path, mtime_offset_days=-10, files=3)
        original_rmtree = shutil.rmtree
        call_count = {"n": 0}

        def flaky_rmtree(path, ignore_errors=False):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise OSError("simulated")
            return original_rmtree(path, ignore_errors=ignore_errors)

        monkeypatch.setattr("genlab_core.storage.disk_quota.shutil.rmtree", flaky_rmtree)
        mgr = DiskQuotaManager({"extra_dirs": [{"path": str(d), "max_age_days": 7}]})
        mgr.sweep_extra_dirs()
        assert call_count["n"] == 3
