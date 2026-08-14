"""Aesthetic quality model trainer (Phase 4.B session 2).

Reads labeled examples from ``aesthetic_training_data``, fits a
per-niche logistic regression on the 15 composition features,
returns a trained model + held-out AUC.

## Design

  * Per-niche models — session 3 loads the ``is_active=TRUE`` row
    for the given niche. Different niches have different composition
    conventions (gaming's fast cuts vs anime's cinematic beats),
    so a single global model would blur the signal.
  * sklearn LogisticRegression with L2 regularization (C=1.0
    default). Balanced class weights so imbalanced label
    distributions (per session-1 finding, most rewards are 0.0 →
    heavy label=0 skew) don't wash out the positive class.
  * 80/20 train/test split with fixed random_state so the AUC is
    reproducible across retrainer runs.
  * AUC gate: caller decides whether to persist (roadmap gate is
    0.60; runner enforces).

## Deps

sklearn.linear_model + sklearn.metrics + sklearn.model_selection.
Lazy-imported inside :func:`train_model` so hosts without the
``scoring`` extra (rare — sklearn is on prod) don't fail at import
time. Returns ``None`` if the import fails.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# Canonical feature order — must match AestheticFeatures dataclass
# field order for the coefficient vector to be interpretable at
# scoring time. Pin-tested against AestheticFeatures.to_dict().
FEATURE_ORDER: tuple[str, ...] = (
    "rot_horizontal_score",
    "rot_vertical_score",
    "horizontal_symmetry",
    "vertical_symmetry",
    "edge_density",
    "hue_variance",
    "saturation_mean",
    "saturation_variance",
    "brightness_mean",
    "brightness_variance",
    "brightness_entropy",
    "center_weight",
    "top_bottom_balance",
    "left_right_balance",
    "aspect_ratio",
)


@dataclass(frozen=True)
class TrainedModel:
    """Result of one training run. Coefficients + intercept are
    the sklearn LogisticRegression outputs; caller persists to
    ``aesthetic_model_versions`` if ``auc`` passes the gate."""
    niche_id: str
    coefficients: dict[str, float]  # feature_name → weight
    intercept: float
    auc: float
    n_train: int
    n_test: int


def _flatten_features(
    features_json: dict, feature_order: tuple[str, ...] = FEATURE_ORDER,
) -> list[float]:
    """Extract feature values in canonical order. Missing keys
    default to 0.0 — sklearn will treat as neutral input."""
    return [float(features_json.get(k, 0.0) or 0.0) for k in feature_order]


def train_model(
    niche_id: str,
    training_rows: list[dict],
    *,
    test_size: float = 0.20,
    random_state: int = 42,
    min_samples: int = 20,
) -> TrainedModel | None:
    """Fit + evaluate a logistic-regression model on labeled examples.

    ``training_rows`` — list of dicts with keys ``label`` (0/1) and
    ``features`` (dict matching AestheticFeatures.to_dict()).

    Returns None when:
      * < ``min_samples`` rows available (too small for a stable
        train/test split)
      * fewer than 2 positive AND 2 negative samples in either
        train or test split (sklearn refuses / AUC undefined)
      * sklearn import fails (missing extra)
      * fit or predict raises

    Never raises — caller checks ``is None``.
    """
    if len(training_rows) < min_samples:
        logger.info(
            "[trainer] niche=%s: only %d samples < min=%d, skipping",
            niche_id, len(training_rows), min_samples,
        )
        return None

    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import roc_auc_score
        from sklearn.model_selection import train_test_split
    except ImportError as exc:
        logger.warning(
            "[trainer] sklearn missing (install `scoring` extra): %s", exc,
        )
        return None

    # Materialize X / y matrices
    X = np.array([
        _flatten_features(r.get("features") or {}) for r in training_rows
    ])
    y = np.array([int(r.get("label", 0)) for r in training_rows])

    n_pos = int(y.sum())
    n_neg = int(len(y) - n_pos)
    if n_pos < 4 or n_neg < 4:
        logger.info(
            "[trainer] niche=%s: imbalance too extreme "
            "(n_pos=%d, n_neg=%d), skipping",
            niche_id, n_pos, n_neg,
        )
        return None

    try:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=test_size, random_state=random_state,
            stratify=y,  # preserve class balance in both splits
        )
    except ValueError as exc:
        logger.warning(
            "[trainer] niche=%s: split failed: %s", niche_id, exc,
        )
        return None

    if len(set(y_train)) < 2 or len(set(y_test)) < 2:
        logger.info(
            "[trainer] niche=%s: post-split single class in train or test",
            niche_id,
        )
        return None

    try:
        model = LogisticRegression(
            class_weight="balanced",
            random_state=random_state,
            max_iter=1000,
        )
        model.fit(X_train, y_train)
    except Exception as exc:
        logger.warning("[trainer] niche=%s: fit failed: %s", niche_id, exc)
        return None

    try:
        y_pred_proba = model.predict_proba(X_test)[:, 1]
        auc = float(roc_auc_score(y_test, y_pred_proba))
    except Exception as exc:
        logger.warning("[trainer] niche=%s: AUC failed: %s", niche_id, exc)
        return None

    coefficients = {
        FEATURE_ORDER[i]: float(model.coef_[0][i])
        for i in range(len(FEATURE_ORDER))
    }
    return TrainedModel(
        niche_id=niche_id,
        coefficients=coefficients,
        intercept=float(model.intercept_[0]),
        auc=auc,
        n_train=len(y_train),
        n_test=len(y_test),
    )
