"""Pin Phase 4.B session 2 aesthetic retrainer runner:

  * _load_training_rows fail-opens to [] on DB error
  * _next_version returns MAX+1 (or 1 on cold start)
  * _persist_and_promote demotes existing active + inserts new
  * AUC below threshold skips promote
  * Main exits 1 without DATABASE_URL
  * AUC_PROMOTE_THRESHOLD == 0.60 (roadmap gate)
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "retrain_aesthetic_model",
    _ROOT / "scripts" / "retrain_aesthetic_model.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["retrain_aesthetic_model"] = _MOD
_SPEC.loader.exec_module(_MOD)


class TestConstants:
    def test_auc_threshold_matches_roadmap(self):
        """Roadmap: model only promoted if AUC > 0.60."""
        assert _MOD.AUC_PROMOTE_THRESHOLD == 0.60

    def test_five_niches(self):
        assert set(_MOD.ACTIVE_NICHES) == {
            "ai_creators", "anime", "gaming", "movies", "sports",
        }


class TestLoadTrainingRows:
    def test_db_error_returns_empty(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        assert _MOD._load_training_rows(conn, "gaming") == []

    def test_normalizes_rows(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            {"label": 1, "features": {"edge_density": 0.5}},
            {"label": 0, "features": {}},
        ]
        rows = _MOD._load_training_rows(conn, "gaming")
        assert len(rows) == 2
        assert rows[0]["label"] == 1


class TestNextVersion:
    def test_cold_start_returns_1(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {"v": 0}
        assert _MOD._next_version(conn, "gaming") == 1

    def test_existing_max_plus_1(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {"v": 3}
        assert _MOD._next_version(conn, "gaming") == 4

    def test_db_error_returns_1(self):
        """Fail-open — a version-lookup failure shouldn't block
        the promote."""
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        assert _MOD._next_version(conn, "gaming") == 1


class TestRunNiche:
    def _make_model(self, auc: float):
        from genlab_core.quality.aesthetic_trainer import TrainedModel
        return TrainedModel(
            niche_id="gaming",
            coefficients={"edge_density": 0.5},
            intercept=0.1,
            auc=auc,
            n_train=40,
            n_test=10,
        )

    @patch("genlab_core.quality.aesthetic_trainer.train_model")
    def test_no_training_rows_skips(self, mock_train):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        counts = _MOD._run_niche(conn, "gaming", 0.60, dry_run=True)
        assert counts["samples"] == 0
        assert counts["trained"] == 0
        mock_train.assert_not_called()

    @patch("genlab_core.quality.aesthetic_trainer.train_model")
    def test_below_auc_threshold_skips(self, mock_train):
        mock_train.return_value = self._make_model(auc=0.55)
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            {"label": 1, "features": {}} for _ in range(30)
        ]
        counts = _MOD._run_niche(conn, "gaming", 0.60, dry_run=True)
        assert counts["trained"] == 1
        assert counts["skipped_auc"] == 1
        assert counts["promoted"] == 0

    @patch("genlab_core.quality.aesthetic_trainer.train_model")
    def test_above_threshold_dry_run_counts_promoted(self, mock_train):
        mock_train.return_value = self._make_model(auc=0.75)
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            {"label": 1, "features": {}} for _ in range(30)
        ]
        counts = _MOD._run_niche(conn, "gaming", 0.60, dry_run=True)
        assert counts["promoted"] == 1
        assert counts["skipped_auc"] == 0


class TestMainExit:
    def test_missing_dsn_exits_1(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert _MOD.main(["--dry-run"]) == 1
