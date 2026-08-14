"""Portfolio-level LinUCB above per-niche bandits.

Phase 2.B of the Genius Program Roadmap. Decides "how much effort
to spend on each of the 5 niches this week" based on their measured
ROI.

## Design

5-arm LinUCB where each arm = one niche. Context features (per week):

  * follower_growth_rate — new followers / total followers, last 7d
  * cost_usd — total pipeline + LLM cost, last 7d
  * engagement_percentile — median reward vs cross-niche median
  * publish_success_rate — successful publishes / attempts, last 7d
  * conversion_rate — affiliate clicks / views (proxy for monetization)

Reward at t+7d:
  * follower_delta × 0.6 (primary — north-star metric)
  * engagement_percentile × 0.3
  * conversion_rate × 0.1

## Output

`recommend_weights()` returns a 5-tuple summing to 1.0 — the
recommended share of effort per niche. Consumer maps this to:

  * fetch depth per niche (more candidates for high-weight niches)
  * publish frequency (weight × baseline_publishes_per_week)
  * LLM budget allocation

**Observation-only in v1**: `recommend_weights()` writes to
`portfolio_allocations` but doesn't act. Consumer wire is a
follow-up single-line pipeline_runner change once operator confirms
the weights look sensible.

## Fail-safe

If any component fails (missing metrics, bandit state corrupted),
falls back to uniform 20%/20%/20%/20%/20% allocation — the pre-Phase
2.B default behavior.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import numpy as np

logger = logging.getLogger(__name__)

ACTIVE_NICHES = ("ai_creators", "anime", "gaming", "movies", "sports")
FEATURE_DIM = 5  # matches _extract_features output length

# Reward weighting per Phase 2.B roadmap: prioritize follower growth
# because that's the north-star metric (100K followers per channel
# by Phase 5).
_REWARD_WEIGHT_FOLLOWERS = 0.6
_REWARD_WEIGHT_ENGAGEMENT = 0.3
_REWARD_WEIGHT_CONVERSION = 0.1

# Uniform fallback used on any error — preserves pre-Phase-2.B behavior
_UNIFORM_WEIGHT = 1.0 / len(ACTIVE_NICHES)


@dataclass(frozen=True)
class NicheFeatures:
    """Per-niche feature snapshot at portfolio decision time."""

    niche_id: str
    follower_growth_rate: float
    cost_usd: float
    engagement_percentile: float
    publish_success_rate: float
    conversion_rate: float

    def to_vector(self) -> np.ndarray:
        """Order matches FEATURE_DIM. Any change here MUST bump the
        alembic migration for bandit state (fresh A/b matrices)."""
        return np.array([
            self.follower_growth_rate,
            self.cost_usd / 100.0,  # scale to O(1)
            self.engagement_percentile,
            self.publish_success_rate,
            self.conversion_rate,
        ], dtype=float)


@dataclass
class PortfolioAllocation:
    """One niche's recommendation for a given week."""

    niche_id: str
    recommended_weight: float
    ucb_score: float
    context_features: dict


class PortfolioBandit:
    """5-arm LinUCB where each arm is one niche.

    Not persistent across runs in v1 — the bandit refits from
    historical portfolio_allocations rows on each fire. This is
    fine because weekly cadence means state grows slowly and cold-
    start (uniform weights) is a safe fallback.
    """

    def __init__(self, alpha: float = 1.0) -> None:
        self.alpha = alpha
        # A: FEATURE_DIM x FEATURE_DIM matrix, b: FEATURE_DIM vector
        # per arm.
        self.arms: dict[str, tuple[np.ndarray, np.ndarray]] = {
            niche_id: (
                np.eye(FEATURE_DIM),
                np.zeros(FEATURE_DIM),
            ) for niche_id in ACTIVE_NICHES
        }

    def update(self, niche_id: str, context: np.ndarray, reward: float) -> None:
        """Standard LinUCB update: A += x x^T, b += reward * x."""
        if niche_id not in self.arms:
            return
        A, b = self.arms[niche_id]
        self.arms[niche_id] = (
            A + np.outer(context, context),
            b + reward * context,
        )

    def _ucb_score(self, niche_id: str, context: np.ndarray) -> float:
        """LinUCB score: theta.x + alpha * sqrt(x^T A^{-1} x)."""
        A, b = self.arms[niche_id]
        A_inv = np.linalg.inv(A)
        theta = A_inv @ b
        exploration = self.alpha * np.sqrt(context.T @ A_inv @ context)
        return float(theta @ context + exploration)

    def recommend_weights(
        self, features: list[NicheFeatures],
    ) -> list[PortfolioAllocation]:
        """Return per-niche allocation summing to 1.0. Softmax over
        UCB scores — smoother than argmax (which would send 100% of
        budget to one niche and starve everything else)."""
        by_niche = {f.niche_id: f for f in features}
        scores = {}
        contexts = {}
        for niche_id in ACTIVE_NICHES:
            f = by_niche.get(niche_id)
            if f is None:
                # Missing feature → uniform fallback for this niche
                scores[niche_id] = 0.0
                contexts[niche_id] = {}
                continue
            ctx = f.to_vector()
            scores[niche_id] = self._ucb_score(niche_id, ctx)
            contexts[niche_id] = {
                "follower_growth_rate": f.follower_growth_rate,
                "cost_usd": f.cost_usd,
                "engagement_percentile": f.engagement_percentile,
                "publish_success_rate": f.publish_success_rate,
                "conversion_rate": f.conversion_rate,
            }

        # Softmax with temperature 1.0 — moderate exploitation
        # (temperature 0 = argmax, 1 = uniform-ish, ∞ = uniform)
        temp = 1.0
        max_score = max(scores.values()) if scores else 0.0
        exp_scores = {
            n: np.exp((s - max_score) / temp) for n, s in scores.items()
        }
        z = sum(exp_scores.values()) or 1.0
        weights = {n: v / z for n, v in exp_scores.items()}

        return [
            PortfolioAllocation(
                niche_id=n,
                recommended_weight=weights[n],
                ucb_score=scores[n],
                context_features=contexts[n],
            )
            for n in ACTIVE_NICHES
        ]


def compute_reward_from_features(f: NicheFeatures) -> float:
    """Combine multi-metric features into a scalar reward for the
    bandit update. Weights match roadmap intent: follower growth
    dominates because it's the north-star metric.

    Reward clipped to [0, 1] so bandit stays well-conditioned.
    """
    r = (
        _REWARD_WEIGHT_FOLLOWERS * min(1.0, max(0.0, f.follower_growth_rate * 10))
        + _REWARD_WEIGHT_ENGAGEMENT * min(1.0, max(0.0, f.engagement_percentile))
        + _REWARD_WEIGHT_CONVERSION * min(1.0, max(0.0, f.conversion_rate * 100))
    )
    return min(1.0, max(0.0, r))


def uniform_allocation() -> list[PortfolioAllocation]:
    """Fallback when the bandit can't produce a decision — every niche
    gets the pre-Phase-2.B default 20% share."""
    return [
        PortfolioAllocation(
            niche_id=n,
            recommended_weight=_UNIFORM_WEIGHT,
            ucb_score=0.0,
            context_features={},
        )
        for n in ACTIVE_NICHES
    ]


def is_enabled() -> bool:
    """Portfolio bandit CONSUMER flag. Independent from the runner
    flag: runner can compute + persist without the consumer acting."""
    return os.environ.get(
        "GENLAB_PORTFOLIO_BANDIT_ENABLED", "",
    ).strip().lower() in ("1", "true", "yes", "on")


__all__ = [
    "ACTIVE_NICHES",
    "FEATURE_DIM",
    "NicheFeatures",
    "PortfolioAllocation",
    "PortfolioBandit",
    "compute_reward_from_features",
    "is_enabled",
    "uniform_allocation",
]
