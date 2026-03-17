"""Tests for genlab_core.media.quota_manager."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from genlab_core.media.quota_manager import QuotaManager, QuotaStatus, VideoWorkspace

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dir_with_file(base: Path, name: str, size: int, score: float | None = None) -> Path:
    """Create a subdirectory with a file of the given size and an optional .score file."""
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "data.bin").write_bytes(b"\x00" * size)
    if score is not None:
        (d / ".score").write_text(str(score))
    return d


# ---------------------------------------------------------------------------
# QuotaStatus
# ---------------------------------------------------------------------------

class TestQuotaStatusLevels:
    def test_ok(self, tmp_path: Path) -> None:
        qm = QuotaManager(tmp_path, max_bytes=1000, warning_pct=0.80, critical_pct=0.95)
        # 700 / 1000 = 70% -> ok
        _make_dir_with_file(tmp_path, "a", 700)
        status = qm.check()
        assert status.level == "ok"
        assert status.used_bytes == 700
        assert status.max_bytes == 1000

    def test_warning(self, tmp_path: Path) -> None:
        qm = QuotaManager(tmp_path, max_bytes=1000, warning_pct=0.80, critical_pct=0.95)
        # 850 / 1000 = 85% -> warning
        _make_dir_with_file(tmp_path, "a", 850)
        status = qm.check()
        assert status.level == "warning"

    def test_critical(self, tmp_path: Path) -> None:
        qm = QuotaManager(tmp_path, max_bytes=1000, warning_pct=0.80, critical_pct=0.95)
        # 960 / 1000 = 96% -> critical
        _make_dir_with_file(tmp_path, "a", 960)
        status = qm.check()
        assert status.level == "critical"


# ---------------------------------------------------------------------------
# VideoWorkspace
# ---------------------------------------------------------------------------

class TestVideoWorkspace:
    def test_creates_dir(self, tmp_path: Path) -> None:
        ws = VideoWorkspace(tmp_path, "run-001", score=1.5)
        assert not ws.path.exists()
        with ws:
            assert ws.path.exists()
            assert ws.path.is_dir()
            score_file = ws.path / ".score"
            assert score_file.exists()
            assert float(score_file.read_text()) == 1.5

    def test_score_update(self, tmp_path: Path) -> None:
        ws = VideoWorkspace(tmp_path, "run-002", score=0.0)
        with ws:
            assert float((ws.path / ".score").read_text()) == 0.0
            ws.write_score(9.5)
            assert ws.score == 9.5
            assert float((ws.path / ".score").read_text()) == 9.5

    def test_no_cleanup_on_exit(self, tmp_path: Path) -> None:
        ws = VideoWorkspace(tmp_path, "run-003")
        with ws:
            (ws.path / "video.mp4").write_bytes(b"\x00" * 100)
        # Dir should still exist after exiting context manager
        assert ws.path.exists()
        assert (ws.path / "video.mp4").exists()


# ---------------------------------------------------------------------------
# Eviction
# ---------------------------------------------------------------------------

class TestEviction:
    def test_eviction_by_score(self, tmp_path: Path) -> None:
        """Lowest-score dirs should be evicted first."""
        _make_dir_with_file(tmp_path, "high", 300, score=10.0)
        _make_dir_with_file(tmp_path, "mid", 300, score=5.0)
        _make_dir_with_file(tmp_path, "low", 300, score=1.0)
        # Total = 900, .score files add a few bytes each but we account for that
        # Set max so that we're over warning (80%) threshold
        # 900+ bytes used, set max_bytes=1000 -> ~90% -> above warning
        # Target = 800 bytes. Need to free at least ~100+ bytes.
        qm = QuotaManager(tmp_path, max_bytes=1000, warning_pct=0.80, critical_pct=0.95)

        freed = qm.evict_if_needed()
        assert freed > 0
        # low (score=1.0) should be evicted first
        assert not (tmp_path / "low").exists()
        # high (score=10.0) should still exist
        assert (tmp_path / "high").exists()

    def test_eviction_skips_protected(self, tmp_path: Path) -> None:
        """Protected dirs must not be evicted."""
        _make_dir_with_file(tmp_path, "protected", 400, score=0.0)
        _make_dir_with_file(tmp_path, "expendable", 400, score=0.0)
        # ~800+ bytes, max=900, warning at 80% = 720 target
        qm = QuotaManager(tmp_path, max_bytes=900, warning_pct=0.80, critical_pct=0.95)

        freed = qm.evict_if_needed(protected_dirs={tmp_path / "protected"})
        assert freed > 0
        assert (tmp_path / "protected").exists()
        assert not (tmp_path / "expendable").exists()

    def test_eviction_stops_at_target(self, tmp_path: Path) -> None:
        """Eviction should stop once usage drops below warning threshold."""
        _make_dir_with_file(tmp_path, "a", 200, score=1.0)
        _make_dir_with_file(tmp_path, "b", 200, score=2.0)
        _make_dir_with_file(tmp_path, "c", 200, score=3.0)
        _make_dir_with_file(tmp_path, "d", 200, score=4.0)
        # ~800+ total, max=900, warning at 80% = 720
        # Evicting "a" (200+ bytes) should bring us to ~600 which is below 720 -> stop
        qm = QuotaManager(tmp_path, max_bytes=900, warning_pct=0.80, critical_pct=0.95)

        qm.evict_if_needed()
        # "a" (lowest score) evicted
        assert not (tmp_path / "a").exists()
        # Remaining dirs should still exist (we're below target after evicting "a")
        assert (tmp_path / "b").exists()
        assert (tmp_path / "c").exists()
        assert (tmp_path / "d").exists()


# ---------------------------------------------------------------------------
# current_usage_bytes
# ---------------------------------------------------------------------------

class TestCurrentUsageBytes:
    def test_empty_dir(self, tmp_path: Path) -> None:
        qm = QuotaManager(tmp_path, max_bytes=1000)
        assert qm.current_usage_bytes() == 0

    def test_with_files(self, tmp_path: Path) -> None:
        (tmp_path / "file1.bin").write_bytes(b"\x00" * 100)
        (tmp_path / "file2.bin").write_bytes(b"\x00" * 250)
        qm = QuotaManager(tmp_path, max_bytes=1000)
        assert qm.current_usage_bytes() == 350

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        qm = QuotaManager(tmp_path / "does_not_exist", max_bytes=1000)
        assert qm.current_usage_bytes() == 0


# ---------------------------------------------------------------------------
# pressure_check
# ---------------------------------------------------------------------------

class TestPressureCheck:
    def test_critical_triggers_eviction(self, tmp_path: Path) -> None:
        qm = QuotaManager(tmp_path, max_bytes=1000, warning_pct=0.80, critical_pct=0.95)
        critical_status = QuotaStatus(used_bytes=960, max_bytes=1000, pct=0.96, level="critical")

        with patch.object(qm, "check", return_value=critical_status), \
             patch.object(qm, "evict_if_needed", return_value=200) as mock_evict:
            qm.pressure_check()
            mock_evict.assert_called_once()

    def test_ok_no_eviction(self, tmp_path: Path) -> None:
        qm = QuotaManager(tmp_path, max_bytes=1000, warning_pct=0.80, critical_pct=0.95)
        ok_status = QuotaStatus(used_bytes=500, max_bytes=1000, pct=0.50, level="ok")

        with patch.object(qm, "check", return_value=ok_status), \
             patch.object(qm, "evict_if_needed") as mock_evict:
            qm.pressure_check()
            mock_evict.assert_not_called()

    def test_warning_no_eviction(self, tmp_path: Path) -> None:
        qm = QuotaManager(tmp_path, max_bytes=1000, warning_pct=0.80, critical_pct=0.95)
        warning_status = QuotaStatus(used_bytes=850, max_bytes=1000, pct=0.85, level="warning")

        with patch.object(qm, "check", return_value=warning_status), \
             patch.object(qm, "evict_if_needed") as mock_evict:
            qm.pressure_check()
            mock_evict.assert_not_called()
