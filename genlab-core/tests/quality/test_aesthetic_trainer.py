"""Pin Phase 4.B session 2 aesthetic trainer:

  * FEATURE_ORDER matches AestheticFeatures dataclass field order
  * _flatten_features handles missing keys as 0
  * train_model returns None below min_samples
  * train_model returns None on extreme class imbalance
  * train_model returns TrainedModel with valid AUC on separable data
  * Coefficient dict keyed by FEATURE_ORDER
"""
from __future__ import annotations

import random

import pytest

from genlab_core.quality.aesthetic_features import AestheticFeatures
from genlab_core.quality.aesthetic_trainer import (
    FEATURE_ORDER,
    TrainedModel,
    _flatten_features,
    train_model,
)


class TestFeatureOrder:
    def test_15_features(self):
        assert len(FEATURE_ORDER) == 15

    def test_matches_dataclass_field_order(self):
        """Critical pin: coefficient vector at scoring time must
        align with the feature vector. If AestheticFeatures adds a
        new field but FEATURE_ORDER isn't updated, the model would
        silently use wrong weights."""
        dc_fields = list(AestheticFeatures.__dataclass_fields__.keys())
        # Drop `ok` and `reason` from the field list (not features)
        dc_features = [f for f in dc_fields if f not in {"ok", "reason"}]
        assert list(FEATURE_ORDER) == dc_features


class TestFlattenFeatures:
    def test_all_present(self):
        features = {name: float(i) for i, name in enumerate(FEATURE_ORDER)}
        result = _flatten_features(features)
        assert result == [float(i) for i in range(15)]

    def test_missing_defaults_to_zero(self):
        result = _flatten_features({"rot_horizontal_score": 0.5})
        assert result[0] == 0.5
        assert result[1] == 0.0  # missing rot_vertical_score

    def test_none_value_defaults_to_zero(self):
        """JSONB can store null; must coerce to 0.0 not TypeError."""
        result = _flatten_features({"edge_density": None})
        assert result[FEATURE_ORDER.index("edge_density")] == 0.0


def _fake_rows(n_pos: int, n_neg: int, separable: bool = True):
    """Generate labeled synthetic training rows. When separable=True,
    positive class has feature[0] near 0.8, negative near 0.2 — a
    logistic regression should learn this perfectly."""
    rng = random.Random(42)
    rows = []
    for _ in range(n_pos):
        feats = {name: rng.uniform(0.2, 0.5) for name in FEATURE_ORDER}
        if separable:
            feats["rot_horizontal_score"] = rng.uniform(0.7, 0.9)
        rows.append({"label": 1, "features": feats})
    for _ in range(n_neg):
        feats = {name: rng.uniform(0.2, 0.5) for name in FEATURE_ORDER}
        if separable:
            feats["rot_horizontal_score"] = rng.uniform(0.1, 0.3)
        rows.append({"label": 0, "features": feats})
    rng.shuffle(rows)
    return rows


class TestTrainModel:
    def test_below_min_samples_returns_none(self):
        rows = _fake_rows(5, 5)  # 10 total, min=20
        assert train_model("gaming", rows, min_samples=20) is None

    def test_extreme_imbalance_returns_none(self):
        rows = _fake_rows(1, 100)  # only 1 positive
        assert train_model("gaming", rows) is None

    def test_separable_data_high_auc(self):
        rows = _fake_rows(30, 30, separable=True)
        model = train_model("gaming", rows, min_samples=20)
        assert model is not None
        assert isinstance(model, TrainedModel)
        # Perfectly separable → AUC should be close to 1.0
        assert model.auc > 0.90
        # 60 total, 20% test = 12 test samples
        assert model.n_test == 12
        assert model.n_train == 48

    def test_coefficient_keys_match_feature_order(self):
        rows = _fake_rows(30, 30)
        model = train_model("gaming", rows, min_samples=20)
        assert model is not None
        assert set(model.coefficients.keys()) == set(FEATURE_ORDER)

    def test_random_data_low_auc(self):
        """Non-separable random labels → AUC near 0.5."""
        rows = _fake_rows(30, 30, separable=False)
        model = train_model("gaming", rows, min_samples=20)
        # Model can still fit; AUC just shouldn't be >0.9 like the
        # separable case
        if model is not None:  # may skip if sklearn imbalance guard trips
            assert model.auc < 0.90

    def test_returned_intercept_is_float(self):
        rows = _fake_rows(30, 30)
        model = train_model("gaming", rows, min_samples=20)
        assert model is not None
        assert isinstance(model.intercept, float)
