"""XGBoost-based hook quality classifier.

Predicts the probability that a hook will perform above the 75th
percentile of engagement (reward_48h). Gracefully degrades when:
  - xgboost is not installed (returns 0.5)
  - Model file does not exist (returns 0.5)
  - Training data < MIN_EXAMPLES (skips training)

Model is persisted as JSON at:
    genlab-core/models/hook_classifier_{niche_id}.json
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from genlab_core.learning.hook_features import build_feature_vector
from genlab_core.learning.hook_training_data import MIN_EXAMPLES

logger = logging.getLogger(__name__)

# Resolve model directory relative to genlab-core package root
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_MODELS_DIR = _PACKAGE_ROOT / "models"

# Feature order must be consistent between training and inference
FEATURE_NAMES = [
    "word_count",
    "has_question",
    "has_number",
    "emoji_count",
    "has_superlative",
    "starts_with_you",
    "avg_word_length",
    "unique_word_ratio",
]

# Check xgboost availability
try:
    import xgboost as xgb

    _HAS_XGBOOST = True
except ImportError:
    xgb = None  # type: ignore[assignment]
    _HAS_XGBOOST = False

try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False


class HookClassifier:
    """Pre-publish hook quality predictor.

    Wraps an XGBoost binary classifier that predicts the probability
    of a hook achieving above-median engagement. Falls back to neutral
    scores (0.5) when xgboost is unavailable or no model is loaded.

    Args:
        niche_id: Niche identifier (e.g. "ai_creators", "gaming").
            Determines which model file to load.
        models_dir: Override for model directory. Defaults to
            genlab-core/models/.
    """

    def __init__(
        self,
        niche_id: str = "ai_creators",
        models_dir: Path | None = None,
    ) -> None:
        self.niche_id = niche_id
        self._models_dir = models_dir or _MODELS_DIR
        self._model: Any = None
        self._loaded = False

        # Attempt to load existing model
        self._try_load()

    @property
    def model_path(self) -> Path:
        return self._models_dir / f"hook_classifier_{self.niche_id}.json"

    def _try_load(self) -> None:
        """Attempt to load a persisted model from disk."""
        if not _HAS_XGBOOST:
            logger.debug("[hook_clf] xgboost not installed — using neutral fallback")
            return

        path = self.model_path
        if not path.exists():
            logger.debug("[hook_clf] No model at %s — using neutral fallback", path)
            return

        try:
            model = xgb.XGBClassifier()
            model.load_model(str(path))
            self._model = model
            self._loaded = True
            logger.info("[hook_clf] Loaded model from %s", path)
        except Exception as exc:
            logger.warning("[hook_clf] Failed to load model from %s: %s", path, exc)

    def predict_proba(self, features: dict[str, float]) -> float:
        """Predict probability of high engagement for a feature vector.

        Args:
            features: Dict of feature_name -> float from build_feature_vector().

        Returns:
            Probability in [0, 1]. Returns 0.5 (neutral) when no model
            is available.
        """
        if not self._loaded or self._model is None:
            return 0.5

        if not _HAS_NUMPY:
            return 0.5

        try:
            # Build ordered feature array
            x = np.array(
                [[features.get(f, 0.0) for f in FEATURE_NAMES]],
                dtype=np.float32,
            )
            proba = self._model.predict_proba(x)
            # proba shape: (1, 2) — [P(class=0), P(class=1)]
            return float(proba[0][1])
        except Exception as exc:
            logger.warning("[hook_clf] predict_proba failed: %s", exc)
            return 0.5

    def score_hook(self, hook_text: str) -> float:
        """Score a single hook text.

        Convenience wrapper that extracts features and predicts.

        Args:
            hook_text: The hook text to score.

        Returns:
            Probability in [0, 1]. Returns 0.5 on any error.
        """
        if not hook_text or not hook_text.strip():
            return 0.5

        try:
            features = build_feature_vector(hook_text)
            if not features:
                return 0.5
            return self.predict_proba(features)
        except Exception as exc:
            logger.warning("[hook_clf] score_hook failed: %s", exc)
            return 0.5


def train_and_save(
    examples: list[Any],
    labels: list[int],
    niche_id: str = "ai_creators",
    models_dir: Path | None = None,
) -> bool:
    """Train an XGBoost classifier on hook examples and save to disk.

    Args:
        examples: List of HookExample instances.
        labels: Binary labels (0/1) from compute_engagement_labels().
        niche_id: Niche identifier for the model filename.
        models_dir: Override for model directory.

    Returns:
        True if training succeeded, False otherwise.
    """
    if not _HAS_XGBOOST:
        logger.warning(
            "[hook_clf] xgboost not installed — cannot train. "
            "Install with: pip install xgboost"
        )
        return False

    if not _HAS_NUMPY:
        logger.warning("[hook_clf] numpy not installed — cannot train")
        return False

    if len(examples) < MIN_EXAMPLES:
        logger.info(
            "[hook_clf] Only %d examples (need %d) — skipping training",
            len(examples),
            MIN_EXAMPLES,
        )
        return False

    if len(examples) != len(labels):
        logger.error(
            "[hook_clf] examples (%d) and labels (%d) length mismatch",
            len(examples),
            len(labels),
        )
        return False

    try:
        # Build feature matrix
        feature_dicts = [build_feature_vector(ex.hook_text) for ex in examples]
        X = np.array(
            [[fd.get(f, 0.0) for f in FEATURE_NAMES] for fd in feature_dicts],
            dtype=np.float32,
        )
        y = np.array(labels, dtype=np.int32)

        # Train XGBoost
        model = xgb.XGBClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.1,
            eval_metric="logloss",
            use_label_encoder=False,
        )
        model.fit(X, y)

        # Save model
        save_dir = models_dir or _MODELS_DIR
        save_dir.mkdir(parents=True, exist_ok=True)
        model_path = save_dir / f"hook_classifier_{niche_id}.json"
        model.save_model(str(model_path))

        # Sidecar meta file consumed by the dashboard's
        # /api/v1/learning/hook-classifier-status endpoint. Without it
        # the UI shows every niche as "Not trained" even when the model
        # file is on disk. Track n_examples + pos_rate so operators can
        # see the training distribution at a glance.
        import json
        from datetime import UTC, datetime
        pos_rate = float(np.mean(y)) if len(y) > 0 else 0.0
        meta_path = save_dir / f"hook_classifier_{niche_id}.meta.json"
        meta_path.write_text(json.dumps({
            "niche_id": niche_id,
            "n_examples": len(examples),
            "pos_rate": round(pos_rate, 4),
            "feature_names": FEATURE_NAMES,
            "trained_at": datetime.now(UTC).isoformat(),
        }))

        logger.info(
            "[hook_clf] Trained on %d examples, saved to %s",
            len(examples),
            model_path,
        )
        return True

    except Exception as exc:
        logger.error("[hook_clf] Training failed: %s", exc)
        return False
