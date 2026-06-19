"""LinUCB contextual bandit implementation.

Upgrades the existing context-free Thompson Sampling to a contextual bandit
that uses a 6-dimensional feature vector (day_of_week, hour_utc, source_type,
duration_bucket, view_velocity, relevance_score).

Cold-start protection: arms with fewer than MIN_OBS_FOR_LINUCB observations
fall back to Thompson Sampling (existing alpha/beta posteriors).

LinUCB formula:
    p = theta^T x + alpha * sqrt(x^T A^{-1} x)
    where theta = A^{-1} b

References:
    Li et al. (2010) "A Contextual-Bandit Approach to Personalized News Article
    Recommendation", WWW 2010.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Context feature dimensionality (expanded from 6 to 12 — Break 12 fix)
CONTEXT_DIM = 12

# Minimum observations before LinUCB predictions are trusted.
# Below this threshold, fall back to Thompson Sampling.
MIN_OBS_FOR_LINUCB = 50

# Source type encoding map
_SOURCE_TYPE_MAP: dict[str, float] = {
    "youtube": 0.0,
    "reddit": 0.33,
    "rss": 0.66,
    "twitch": 1.0,
}
_SOURCE_TYPE_DEFAULT = 0.5


class LinUCBArm:
    """Single arm with d-dimensional context.

    Maintains:
        A: d x d matrix (identity + sum of outer products of context vectors)
        b: d-vector (sum of reward * context vectors)
        n_obs: number of observations (for cold-start detection)
    """

    def __init__(self, d: int, alpha: float = 1.0) -> None:
        self.A = np.eye(d, dtype=np.float64)
        self.b = np.zeros(d, dtype=np.float64)
        self.alpha = alpha
        self.n_obs = 0
        # Cached inverse of A. Invariant: when not None, equals
        # ``np.linalg.inv(self.A)``. Invalidated (set to None) whenever
        # ``A`` mutates — only in :meth:`update` and :meth:`from_dict`.
        # Avoids recomputing the d×d inversion (O(d³)) on every
        # :meth:`predict` call. With the bandit selecting from N arms
        # per scoring decision, the matrix is unchanged across the N
        # predicts but was being inverted N times pre-cache.
        self._A_inv_cache: np.ndarray | None = None

    def _get_A_inv(self) -> np.ndarray:
        """Return cached ``A^{-1}``, computing it on cache miss.

        Raises :class:`numpy.linalg.LinAlgError` if ``A`` is singular —
        caller (:meth:`predict`) catches and degrades gracefully.
        """
        if self._A_inv_cache is None:
            self._A_inv_cache = np.linalg.inv(self.A)
        return self._A_inv_cache

    def predict(self, x: np.ndarray) -> float:
        """Compute UCB score for this arm given context x.

        p = theta^T x + alpha * sqrt(x^T A^{-1} x)
        where theta = A^{-1} b

        Guards against two numerical edge cases:
          1. Singular/near-singular matrix — np.linalg.inv raises LinAlgError.
             Returns a neutral 0.5 so the Thompson fallback upstream picks
             this arm's score into the normal ranking.
          2. Negative value inside sqrt (can happen with floating-point
             error on near-singular matrices even after inversion succeeds)
             — clamp the inner product to >= 0 before sqrt so we never
             propagate NaN into the reward signal.
        """
        try:
            A_inv = self._get_A_inv()
        except np.linalg.LinAlgError:
            logger.warning(
                "[LinUCB] singular matrix in arm predict (n_obs=%d) — "
                "falling back to neutral score",
                self.n_obs,
            )
            return 0.5
        theta = A_inv @ self.b
        exploitation = float(theta @ x)
        inner = float(x @ A_inv @ x)
        # Clamp to 0 — a positive semi-definite matrix should always give
        # x^T A^{-1} x >= 0, but floating-point drift on degenerate arms
        # can tip it negative. NaN from sqrt(-eps) would then corrupt the
        # reward signal downstream.
        exploration = self.alpha * float(np.sqrt(max(0.0, inner)))
        score = exploitation + exploration
        if not np.isfinite(score):
            logger.warning(
                "[LinUCB] non-finite score (n_obs=%d) — neutral fallback",
                self.n_obs,
            )
            return 0.5
        return score

    def update(self, x: np.ndarray, reward: float) -> None:
        """Update arm with observed reward for context x.

        A += x x^T
        b += reward * x
        """
        self.A += np.outer(x, x)
        self.b += reward * x
        self.n_obs += 1
        # Invalidate cached inverse — A just mutated.
        self._A_inv_cache = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize arm state to a JSON-compatible dict."""
        return {
            "A_matrix": self.A.tolist(),
            "b_vector": self.b.tolist(),
            "alpha": self.alpha,
            "n_obs": self.n_obs,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LinUCBArm:
        """Restore arm state from a serialized dict."""
        A = np.array(data["A_matrix"], dtype=np.float64)
        d = A.shape[0]
        arm = cls(d=d, alpha=data.get("alpha", 1.0))
        arm.A = A
        arm.b = np.array(data["b_vector"], dtype=np.float64)
        arm.n_obs = data.get("n_obs", 0)
        # Don't pre-compute the inverse here — let the first predict
        # trigger it lazily. Restored arms are sometimes loaded but
        # never queried (e.g. cold-path persistence callers); paying
        # the inversion eagerly would waste it.
        arm._A_inv_cache = None
        return arm


class LinUCBBandit:
    """Contextual bandit with LinUCB algorithm.

    Manages multiple arms, each with their own A matrix and b vector.
    """

    def __init__(self, arm_ids: list[str], d: int, alpha: float = 1.0) -> None:
        self.d = d
        self.alpha = alpha
        self.arms: dict[str, LinUCBArm] = {aid: LinUCBArm(d, alpha) for aid in arm_ids}

    def select(self, context: np.ndarray) -> str:
        """Select the arm with the highest UCB score for the given context."""
        scores = {aid: arm.predict(context) for aid, arm in self.arms.items()}
        return max(scores, key=scores.get)  # type: ignore[arg-type]

    def update(self, arm_id: str, context: np.ndarray, reward: float) -> None:
        """Update the specified arm with the observed reward."""
        if arm_id not in self.arms:
            raise KeyError(f"Unknown arm: {arm_id}")
        self.arms[arm_id].update(context, reward)

    def add_arm(self, arm_id: str) -> None:
        """Add a new arm if it doesn't already exist."""
        if arm_id not in self.arms:
            self.arms[arm_id] = LinUCBArm(self.d, self.alpha)


_NICHE_ENCODING: dict[str, float] = {
    "ai_creators": 0.0,
    "gaming": 0.2,
    "sports": 0.4,
    "movies": 0.6,
    "anime": 0.8,
}


def build_content_context(
    story: dict[str, Any],
    niche_id: str,
    now: datetime | None = None,
) -> np.ndarray:
    """Build a 12-dimensional context feature vector for LinUCB.

    Dimensions (expanded from 6 — Break 12 fix):
        0: day_of_week [0, 1]
        1: hour_utc [0, 1]
        2: source_type [0, 1]
        3: duration_bucket [0, 1] — seconds / 60
        4: view_velocity [0, 1] — velocity / 5000 (scaled down for new channels)
        5: relevance_score [0, 1]
        6: hook_length [0, 1] — chars / 60
        7: niche_encoding [0, 1]
        8: has_affiliate [0, 1] — binary
        9: caption_length [0, 1] — chars / 200
       10: hashtag_count [0, 1] — count / 10
       11: trending_score [0, 1] — composite score

    Returns:
        np.ndarray of shape (CONTEXT_DIM,) with float64 values in [0, 1].
    """
    if now is None:
        now = datetime.now(UTC)

    # R-18: extract the content features from EITHER the nested story shape
    # (``content.hook`` / ``content.instagram.caption`` — what selection/predict
    # time passes) OR the flat blueprint shape (top-level ``hook`` / ``caption`` —
    # what the publisher carries at train time). Reading only the nested paths
    # silently zeroed dims 6 & 9 whenever a flat dict was passed, so the bandit
    # TRAINED on hook_length=0/caption_length=0 while PREDICTING on the real
    # lengths — a systematic predict/train mismatch on its two most important
    # content-quality signals.
    content = story.get("content", {})
    if not isinstance(content, dict):
        content = {}
    instagram = content.get("instagram", {})
    if not isinstance(instagram, dict):
        instagram = {}
    hook = content.get("hook") or story.get("hook") or story.get("hook_text") or ""
    caption = (
        instagram.get("caption") or story.get("caption") or story.get("instagram_caption") or ""
    )
    hashtags = story.get("hashtags", [])
    if isinstance(hashtags, str):
        hashtags = hashtags.split()

    return np.array(
        [
            now.weekday() / 6.0,
            now.hour / 23.0,
            _SOURCE_TYPE_MAP.get(
                story.get("source_type", story.get("source", "")), _SOURCE_TYPE_DEFAULT
            ),
            min(story.get("duration_seconds", 30) / 60.0, 1.0),
            min(story.get("view_velocity", 0) / 5000.0, 1.0),
            story.get("relevance_score", story.get("composite_score", 0.5)),
            min(len(hook) / 60.0, 1.0),
            _NICHE_ENCODING.get(niche_id, 0.5),
            1.0 if story.get("affiliate_product") else 0.0,
            min(len(caption) / 200.0, 1.0),
            min(len(hashtags) / 10.0, 1.0),
            min(story.get("composite_score", story.get("score", 0.5)), 1.0),
        ],
        dtype=np.float64,
    )
