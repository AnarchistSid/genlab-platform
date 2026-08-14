"""Pin Phase 4.B session 3 pre-publish aesthetic scorer:

  * _sigmoid boundary behavior + numerical stability
  * load_active_model fail-opens on DB error / missing row / bad JSONB
  * apply_model dot-products + sigmoid on real coefficient dict
  * score_video: no active model → ok=False reason=no_active_model
  * score_video: missing file → ok=False reason=file_not_found
"""
from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.quality.aesthetic_scorer import (
    ActiveModel,
    AestheticScore,
    _sigmoid,
    apply_model,
    load_active_model,
    score_video,
)


class TestSigmoid:
    def test_zero_returns_half(self):
        assert _sigmoid(0.0) == pytest.approx(0.5)

    def test_large_positive_saturates_to_1(self):
        assert _sigmoid(100.0) == pytest.approx(1.0)
        # Numerically stable — no math.exp(-inf) overflow
        assert _sigmoid(1e10) == pytest.approx(1.0)

    def test_large_negative_saturates_to_0(self):
        assert _sigmoid(-100.0) == pytest.approx(0.0)
        assert _sigmoid(-1e10) == pytest.approx(0.0)

    def test_monotonic(self):
        assert _sigmoid(-1.0) < _sigmoid(0.0) < _sigmoid(1.0)


class TestApplyModel:
    def _model(self, intercept: float = 0.0, **coefs) -> ActiveModel:
        return ActiveModel(
            niche_id="gaming",
            version=1,
            coefficients=coefs,
            intercept=intercept,
            auc=0.75,
        )

    def test_all_zero_features_returns_sigmoid_of_intercept(self):
        m = self._model(intercept=0.0, edge_density=1.0)
        assert apply_model(m, {"edge_density": 0.0}) == pytest.approx(0.5)

    def test_missing_feature_treated_as_zero(self):
        """Trainer used missing-key default of 0; scorer must match."""
        m = self._model(intercept=0.0, edge_density=1.0)
        assert apply_model(m, {}) == pytest.approx(0.5)

    def test_none_feature_value_treated_as_zero(self):
        m = self._model(intercept=0.0, edge_density=1.0)
        assert apply_model(m, {"edge_density": None}) == pytest.approx(0.5)

    def test_positive_coefficient_positive_feature_score_gt_half(self):
        m = self._model(intercept=0.0, edge_density=2.0)
        assert apply_model(m, {"edge_density": 1.0}) > 0.5

    def test_negative_coefficient_pushes_score_down(self):
        m = self._model(intercept=0.0, edge_density=-2.0)
        assert apply_model(m, {"edge_density": 1.0}) < 0.5


class TestLoadActiveModel:
    def test_no_row_returns_none(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None
        assert load_active_model(conn, "gaming") is None

    def test_db_error_returns_none(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        assert load_active_model(conn, "gaming") is None

    def test_valid_dict_row_returns_active_model(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "version": 3,
            "coefficients": {"edge_density": 0.42, "motion_energy": -0.15},
            "intercept": 0.1,
            "auc": 0.72,
        }
        model = load_active_model(conn, "gaming")
        assert isinstance(model, ActiveModel)
        assert model.version == 3
        assert model.coefficients == {
            "edge_density": 0.42, "motion_energy": -0.15,
        }
        assert model.intercept == 0.1
        assert model.auc == 0.72

    def test_string_coefficients_parsed_from_jsonb(self):
        """psycopg sometimes returns JSONB as str; loader must parse."""
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "version": 1,
            "coefficients": '{"edge_density": 0.5}',
            "intercept": 0.0,
            "auc": 0.65,
        }
        model = load_active_model(conn, "gaming")
        assert model is not None
        assert model.coefficients == {"edge_density": 0.5}

    def test_malformed_coefficients_returns_none(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "version": 1,
            "coefficients": "not json {{",
            "intercept": 0.0,
            "auc": 0.65,
        }
        assert load_active_model(conn, "gaming") is None


class TestScoreVideo:
    def test_no_active_model_ok_false(self, tmp_path):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None
        result = score_video(tmp_path / "x.mp4", "gaming", conn)
        assert isinstance(result, AestheticScore)
        assert result.ok is False
        assert result.reason == "no_active_model"

    def test_model_present_but_no_video_returns_extract_fail(self, tmp_path):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "version": 1,
            "coefficients": {"edge_density": 0.5},
            "intercept": 0.0,
            "auc": 0.65,
        }
        result = score_video(tmp_path / "nope.mp4", "gaming", conn)
        assert result.ok is False
        assert result.model_version == 1
        assert "file_not_found" in result.reason
