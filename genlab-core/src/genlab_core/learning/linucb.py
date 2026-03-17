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

# Context feature dimensionality
CONTEXT_DIM = 6

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

    def predict(self, x: np.ndarray) -> float:
        """Compute UCB score for this arm given context x.

        p = theta^T x + alpha * sqrt(x^T A^{-1} x)
        where theta = A^{-1} b
        """
        A_inv = np.linalg.inv(self.A)
        theta = A_inv @ self.b
        exploitation = float(theta @ x)
        exploration = self.alpha * float(np.sqrt(x @ A_inv @ x))
        return exploitation + exploration

    def update(self, x: np.ndarray, reward: float) -> None:
        """Update arm with observed reward for context x.

        A += x x^T
        b += reward * x
        """
        self.A += np.outer(x, x)
        self.b += reward * x
        self.n_obs += 1

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
        return arm


class LinUCBBandit:
    """Contextual bandit with LinUCB algorithm.

    Manages multiple arms, each with their own A matrix and b vector.
    """

    def __init__(self, arm_ids: list[str], d: int, alpha: float = 1.0) -> None:
        self.d = d
        self.alpha = alpha
        self.arms: dict[str, LinUCBArm] = {
            aid: LinUCBArm(d, alpha) for aid in arm_ids
        }

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


def build_content_context(
    story: dict[str, Any],
    niche_id: str,
    now: datetime | None = None,
) -> np.ndarray:
    """Build a 6-dimensional context feature vector for LinUCB.

    Dimensions:
        0: day_of_week [0, 1] — Monday=0/6, Sunday=6/6
        1: hour_utc [0, 1] — 0:00=0/23, 23:00=23/23
        2: source_type [0, 1] — youtube=0, reddit=0.33, rss=0.66, twitch=1.0
        3: duration_bucket [0, 1] — seconds / 60, capped at 1.0
        4: view_velocity [0, 1] — velocity / 10000, capped at 1.0
        5: relevance_score [0, 1] — as-is from story

    Args:
        story: Dict with optional keys: source_type, duration_seconds,
            view_velocity, relevance_score.
        niche_id: Niche identifier (reserved for future per-niche features).
        now: Override for current time (useful for testing).

    Returns:
        np.ndarray of shape (CONTEXT_DIM,) with float64 values in [0, 1].
    """
    if now is None:
        now = datetime.now(UTC)

    return np.array([
        now.weekday() / 6.0,
        now.hour / 23.0,
        _SOURCE_TYPE_MAP.get(story.get("source_type", ""), _SOURCE_TYPE_DEFAULT),
        min(story.get("duration_seconds", 30) / 60.0, 1.0),
        min(story.get("view_velocity", 0) / 10000.0, 1.0),
        story.get("relevance_score", 0.5),
    ], dtype=np.float64)
