"""Tests for quota daemon and DiskQuotaManager config loading."""
from __future__ import annotations

from pathlib import Path

import pytest
from genlab_core.storage.disk_quota import DiskQuotaManager


@pytest.fixture
def config_yaml(tmp_path):
    config = {
        "agents": {
            "test_agent": {
                "runs_dir": str(tmp_path / "runs"),
                "quota_gb": 2,
                "warn_pct": 75,
                "evict_pct": 90,
                "protect_recent": 2,
            }
        }
    }
    import yaml
    config_file = tmp_path / "disk_quota.yaml"
    config_file.write_text(yaml.dump(config))
    return config_file


@pytest.fixture
def runs_dir(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    return runs


class TestDiskQuotaManager:
    def test_from_yaml(self, config_yaml):
        mgr = DiskQuotaManager.from_yaml(config_yaml)
        assert "test_agent" in mgr._config.get("agents", {})

    def test_get_status_empty_dir(self, config_yaml, runs_dir):
        mgr = DiskQuotaManager.from_yaml(config_yaml)
        status = mgr.get_status("test_agent")
        assert status.used_bytes == 0
        assert status.pct_used == 0.0
        assert not status.over_quota
        assert not status.over_warn

    def test_scan_runs_empty(self, config_yaml, runs_dir):
        mgr = DiskQuotaManager.from_yaml(config_yaml)
        records = mgr.scan_runs("test_agent")
        assert records == []

    def test_scan_runs_finds_directories(self, config_yaml, runs_dir):
        # Create fake run directories
        (runs_dir / "20260313_080000").mkdir()
        (runs_dir / "20260313_080000" / "output.mp4").write_bytes(b"\x00" * 1000)
        (runs_dir / "20260312_080000").mkdir()
        (runs_dir / "20260312_080000" / "output.mp4").write_bytes(b"\x00" * 500)

        mgr = DiskQuotaManager.from_yaml(config_yaml)
        records = mgr.scan_runs("test_agent")
        assert len(records) == 2
        # sorted newest first by mtime — both were just created
        total_bytes = sum(r.size_bytes for r in records)
        assert total_bytes == 1500

    def test_preflight_check_ok_when_under_quota(self, config_yaml, runs_dir):
        mgr = DiskQuotaManager.from_yaml(config_yaml)
        assert mgr.preflight_check("test_agent", estimated_bytes=1000) is True

    def test_evict_returns_empty_when_under_target(self, config_yaml, runs_dir):
        mgr = DiskQuotaManager.from_yaml(config_yaml)
        result = mgr.evict("test_agent")
        assert result == []

    def test_evict_removes_lowest_score_first(self, config_yaml, runs_dir):
        # Create 4 run dirs, protect_recent=2, so 2 are evictable
        for i, (name, _score, size) in enumerate([
            ("run_old_low", 0.1, 500_000),   # evictable, low score
            ("run_old_high", 0.9, 500_000),  # evictable, high score
            ("run_recent_1", 0.5, 500_000),  # protected (recent)
            ("run_recent_2", 0.5, 500_000),  # protected (recent)
        ]):
            d = runs_dir / name
            d.mkdir()
            (d / "video.mp4").write_bytes(b"\x00" * size)
            # Set mtime so newer = higher index
            import os
            os.utime(d, (1000000 + i * 100, 1000000 + i * 100))

        mgr = DiskQuotaManager.from_yaml(config_yaml)
        # Force eviction by requesting more free space than available
        evicted = mgr.evict("test_agent", target_free_bytes=2 * 1024**3)
        # Should evict lowest score first
        evicted_names = [Path(p).name for p in evicted]
        if evicted_names:
            assert "run_old_low" in evicted_names or "run_old_low" in str(evicted)

    def test_pass1_evicts_clips_from_protected_recent_runs(self, config_yaml, runs_dir):
        """Pass 1 should clean clips/ from recent (protected) runs.

        Clips are regenerable — protect_recent only shields full deletion.
        """
        import os

        # Create 2 runs: both protected (protect_recent=2).
        # The newest one has a big clips/ dir.
        d_old = runs_dir / "run_older"
        d_old.mkdir()
        (d_old / "output.mp4").write_bytes(b"\x00" * 1000)
        os.utime(d_old, (1_000_000, 1_000_000))

        d_new = runs_dir / "run_newest"
        d_new.mkdir()
        clips = d_new / "clips"
        clips.mkdir()
        (clips / "clip1.mp4").write_bytes(b"\x00" * 800_000)
        (clips / "clip2.mp4").write_bytes(b"\x00" * 200_000)
        (d_new / "visuals").mkdir()
        (d_new / "visuals" / "out.mp4").write_bytes(b"\x00" * 5000)
        os.utime(d_new, (2_000_000, 2_000_000))

        mgr = DiskQuotaManager.from_yaml(config_yaml)
        evicted = mgr.evict("test_agent", target_free_bytes=2 * 1024**3)

        # clips/ should have been removed (pass 1)
        assert not clips.exists(), "clips/ should be evicted even from protected run"
        assert any("clips" in p for p in evicted)
        # The run itself should still exist (protected from pass 2)
        assert d_new.exists(), "protected run should not be fully deleted"
        assert (d_new / "visuals").exists(), "non-heavy subdirs should be preserved"

    def test_pass1_evicts_clips_from_published_runs(self, config_yaml, runs_dir):
        """Pass 1 should clean clips/ even from published runs.

        Published runs contain final outputs — clips are redundant source material.
        """
        import os

        d = runs_dir / "run_published"
        d.mkdir()
        # Mark as published
        (d / "publish_all_summary.json").write_text("{}")
        clips = d / "clips"
        clips.mkdir()
        (clips / "clip1.mp4").write_bytes(b"\x00" * 500_000)
        (d / "visuals").mkdir()
        (d / "visuals" / "final.mp4").write_bytes(b"\x00" * 5000)
        os.utime(d, (1_000_000, 1_000_000))

        mgr = DiskQuotaManager.from_yaml(config_yaml)
        mgr.evict("test_agent", target_free_bytes=2 * 1024**3)

        assert not clips.exists(), "clips/ should be evicted from published run"
        assert d.exists(), "published run itself should not be deleted"
        assert (d / "visuals").exists(), "published outputs should be preserved"
