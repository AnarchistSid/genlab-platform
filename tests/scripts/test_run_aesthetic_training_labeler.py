"""Pin Phase 4.B session 1 aesthetic training labeler runner.

Focus on the load-bearing logic — percentile-threshold computation,
label assignment, dedup via existing training row check. The
extractor itself has its own pin suite in
genlab-core/tests/quality/test_aesthetic_features.py.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "run_aesthetic_training_labeler",
    _ROOT / "scripts" / "run_aesthetic_training_labeler.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["run_aesthetic_training_labeler"] = _MOD
_SPEC.loader.exec_module(_MOD)


class TestActiveNiches:
    def test_five_niches(self):
        assert set(_MOD.ACTIVE_NICHES) == {
            "ai_creators", "anime", "gaming", "movies", "sports",
        }


class TestPercentileThresholds:
    def test_insufficient_data_returns_none(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "n": 5, "p20": 0.1, "p80": 0.9,
        }
        # n < 10 → treated as insufficient
        assert _MOD._compute_percentile_thresholds(conn, "gaming", 30) is None

    def test_null_p20_returns_none(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "n": 100, "p20": None, "p80": 0.9,
        }
        assert _MOD._compute_percentile_thresholds(conn, "gaming", 30) is None

    def test_valid_returns_tuple(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "n": 100, "p20": 0.1, "p80": 0.9,
        }
        result = _MOD._compute_percentile_thresholds(conn, "gaming", 30)
        assert result == (0.1, 0.9)

    def test_db_error_returns_none(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        assert _MOD._compute_percentile_thresholds(conn, "gaming", 30) is None


class TestFindCandidates:
    def test_returns_normalized_rows(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            {
                "bp_id": "bp-1", "story_id": "s-1",
                "reward_48h": 0.95, "label": 1,
            },
            {
                "bp_id": "bp-2", "story_id": "s-2",
                "reward_48h": 0.05, "label": 0,
            },
        ]
        rows = _MOD._find_labeled_candidates(conn, "gaming", 0.1, 0.9, 30)
        assert len(rows) == 2
        assert rows[0]["label"] == 1
        assert rows[0]["reward_48h"] == 0.95
        assert rows[1]["label"] == 0

    def test_db_error_returns_empty(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        assert _MOD._find_labeled_candidates(conn, "gaming", 0.1, 0.9, 30) == []


class TestResolveVideo:
    def test_missing_story_id_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        assert _MOD._resolve_video(None) is None
        assert _MOD._resolve_video("") is None

    def test_missing_runs_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        assert _MOD._resolve_video("a" * 64) is None

    def test_finds_via_convention(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        story_id = "b" * 64
        vis_dir = tmp_path / "runs" / "gaming_20260814" / "visuals" / story_id
        vis_dir.mkdir(parents=True)
        expected = vis_dir / f"{story_id[:16]}_reel.mp4"
        expected.write_bytes(b"fake")
        assert _MOD._resolve_video(story_id) == expected


class TestMainExit:
    def test_missing_dsn_exits_1(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert _MOD.main(["--dry-run"]) == 1
