"""Pre-publish aesthetic scorer (Phase 4.B session 3).

Loads the active per-niche logreg model + applies it to a fresh
render's 15 composition features. Returns a [0, 1] aesthetic
score (sigmoid of the linear combination) alongside the model
version that produced it.

## Design

  * ``load_active_model(conn, niche_id) -> ActiveModel | None`` —
    reads from ``aesthetic_model_versions`` where
    ``niche_id=? AND is_active=TRUE``. Cached-per-instance so a
    long-running caller doesn't re-query for every score.
  * ``score_video(video_path, niche_id, conn) -> AestheticScore`` —
    end-to-end: load model + extract features + compute + return.
  * Fail-open at every layer:
      - No active model → returns AestheticScore(ok=False,
        reason='no_active_model'). Caller treats as "unknown" and
        skips aesthetic weighting.
      - Feature extraction fails → returns ok=False.
      - Math failure (should never happen with 15 finite floats) →
        returns ok=False.

## Coefficient application

  score = sigmoid(intercept + sum(coefficients[feat] * value[feat]))

Feature order pinned by FEATURE_ORDER from aesthetic_trainer —
the sanity pin lives there so the coefficient dict + feature dict
can't drift out of alignment.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActiveModel:
    """Cached representation of the currently active model for
    one niche."""
    niche_id: str
    version: int
    coefficients: dict[str, float]
    intercept: float
    auc: float


@dataclass(frozen=True)
class AestheticScore:
    """Result of one scoring pass. ``score`` is [0, 1] when ok=True;
    None when no model / extraction failed."""
    ok: bool
    score: float | None = None
    model_version: int | None = None
    reason: str = ""


def _sigmoid(x: float) -> float:
    """Numerically stable sigmoid."""
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def load_active_model(conn, niche_id: str) -> ActiveModel | None:
    """Fetch the current active model for the niche. Fail-open to
    None (caller falls through to 'no aesthetic scoring available').
    """
    try:
        row = conn.execute(
            """
            SELECT version, coefficients, intercept, auc
            FROM aesthetic_model_versions
            WHERE niche_id = %s AND is_active = TRUE
            ORDER BY trained_at DESC
            LIMIT 1
            """,
            (niche_id,),
        ).fetchone()
    except Exception as exc:
        logger.warning(
            "[aesthetic_scorer] active-model query failed niche=%s: %s",
            niche_id, exc,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    if row is None:
        return None
    coefs = row.get("coefficients") if hasattr(row, "get") else row[1]
    if isinstance(coefs, str):
        import json
        try:
            coefs = json.loads(coefs)
        except Exception:
            return None
    if not isinstance(coefs, dict):
        return None
    return ActiveModel(
        niche_id=niche_id,
        version=int(row.get("version") if hasattr(row, "get") else row[0]),
        coefficients={k: float(v) for k, v in coefs.items()},
        intercept=float(row.get("intercept") if hasattr(row, "get") else row[2]),
        auc=float(row.get("auc") if hasattr(row, "get") else row[3]),
    )


def apply_model(model: ActiveModel, features: dict) -> float:
    """Apply the linear + sigmoid to a feature dict. Missing features
    default to 0 (same as trainer). Returns [0, 1].

    Iterates the model's coefficient keys (not FEATURE_ORDER) so a
    model trained on a subset of features can still score cleanly.
    """
    z = model.intercept
    for feature_name, weight in model.coefficients.items():
        z += weight * float(features.get(feature_name, 0.0) or 0.0)
    return _sigmoid(z)


def score_video(
    video_path: Path, niche_id: str, conn,
) -> AestheticScore:
    """End-to-end: load model + extract features + score."""
    from genlab_core.quality.aesthetic_features import (
        extract_aesthetic_features,
    )

    model = load_active_model(conn, niche_id)
    if model is None:
        return AestheticScore(ok=False, reason="no_active_model")

    feats = extract_aesthetic_features(video_path)
    if not feats.ok:
        return AestheticScore(
            ok=False, model_version=model.version,
            reason=f"feature_extract:{feats.reason}",
        )
    try:
        score = apply_model(model, feats.to_dict())
    except Exception as exc:
        logger.warning(
            "[aesthetic_scorer] apply_model failed niche=%s: %s",
            niche_id, exc,
        )
        return AestheticScore(
            ok=False, model_version=model.version,
            reason=f"apply:{exc}",
        )
    return AestheticScore(
        ok=True, score=score, model_version=model.version,
    )
