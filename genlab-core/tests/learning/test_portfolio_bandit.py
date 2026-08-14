"""Pin the Phase 2.B portfolio bandit — 5-arm LinUCB above per-niche
bandits.
"""
from __future__ import annotations

import numpy as np
import pytest

from genlab_core.learning.portfolio_bandit import (
    ACTIVE_NICHES,
    FEATURE_DIM,
    NicheFeatures,
    PortfolioAllocation,
    PortfolioBandit,
    compute_reward_from_features,
    is_enabled,
    uniform_allocation,
)


def _make_features(niche_id, fgr=0.01, cost=50.0, ep=0.5, psr=1.0, cr=0.001):
    return NicheFeatures(
        niche_id=niche_id, follower_growth_rate=fgr, cost_usd=cost,
        engagement_percentile=ep, publish_success_rate=psr,
        conversion_rate=cr,
    )


class TestFeatureVector:
    def test_to_vector_returns_5d(self):
        f = _make_features("anime")
        v = f.to_vector()
        assert v.shape == (FEATURE_DIM,)
        assert v.dtype == float

    def test_cost_scaled(self):
        f = _make_features("anime", cost=100.0)
        v = f.to_vector()
        # cost_usd component divided by 100 to keep O(1)
        assert v[1] == pytest.approx(1.0)


class TestPortfolioBanditRecommend:
    def test_5_arms_configured_for_5_niches(self):
        b = PortfolioBandit()
        assert set(b.arms.keys()) == set(ACTIVE_NICHES)

    def test_cold_start_all_weights_near_uniform(self):
        """Fresh bandit with A=I, b=0 → all UCB scores equal → weights uniform."""
        b = PortfolioBandit()
        features = [_make_features(n) for n in ACTIVE_NICHES]
        allocations = b.recommend_weights(features)
        weights = [a.recommended_weight for a in allocations]
        # Fresh bandit + identical features = exactly uniform
        for w in weights:
            assert w == pytest.approx(1.0 / len(ACTIVE_NICHES), abs=0.01)

    def test_weights_sum_to_one(self):
        b = PortfolioBandit()
        features = [_make_features(n, fgr=0.01 * (i + 1))
                    for i, n in enumerate(ACTIVE_NICHES)]
        allocations = b.recommend_weights(features)
        total = sum(a.recommended_weight for a in allocations)
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_all_niches_in_output(self):
        b = PortfolioBandit()
        features = [_make_features(n) for n in ACTIVE_NICHES]
        allocations = b.recommend_weights(features)
        assert set(a.niche_id for a in allocations) == set(ACTIVE_NICHES)

    def test_missing_feature_gets_zero_ucb(self):
        """If a feature is absent, that niche still appears in output
        with weight from softmax over the score 0."""
        b = PortfolioBandit()
        # Only 3 features
        features = [_make_features(n) for n in ("anime", "gaming", "sports")]
        allocations = b.recommend_weights(features)
        assert len(allocations) == 5
        # Missing niches should get near-zero context but still valid weight
        movies = next(a for a in allocations if a.niche_id == "movies")
        assert movies.context_features == {}
        assert movies.recommended_weight > 0.0

    def test_update_breaks_uniform_allocation(self):
        """After updates, the allocation is no longer uniform. LinUCB's
        cold-start exploration bonus means an untouched arm keeps
        higher optimism than a warmed-up arm — so 'exploited arm gets
        MORE weight' isn't guaranteed. What IS guaranteed: uniformity
        breaks (some niches diverge from 0.2)."""
        b = PortfolioBandit(alpha=0.1)  # low alpha so exploitation dominates
        features = [_make_features(n, fgr=0.01) for n in ACTIVE_NICHES]
        ctx = _make_features("anime", fgr=0.01).to_vector()
        for _ in range(10):
            b.update("anime", ctx, reward=0.8)
        alloc = b.recommend_weights(features)
        weights = [a.recommended_weight for a in alloc]
        # Weights should no longer be exactly uniform 0.2 each
        assert max(weights) - min(weights) > 0.01

    def test_low_alpha_favors_exploitation(self):
        """With alpha near 0, LinUCB becomes pure exploitation.
        Multiple positive updates should push the learned niche's
        weight ABOVE uniform 0.2."""
        b = PortfolioBandit(alpha=0.01)
        features = [_make_features(n, fgr=0.01) for n in ACTIVE_NICHES]
        ctx = _make_features("anime", fgr=0.01).to_vector()
        for _ in range(20):
            b.update("anime", ctx, reward=0.9)
        alloc = b.recommend_weights(features)
        anime_weight = next(a.recommended_weight for a in alloc if a.niche_id == "anime")
        # With alpha=0.01 exploitation dominates → anime > uniform
        assert anime_weight > 0.20


class TestRewardComputation:
    def test_reward_dominated_by_follower_growth(self):
        """0.6 weight on follower_growth means it's the primary signal."""
        f_high = _make_features("anime", fgr=0.1, ep=0.0, cr=0.0)
        f_low = _make_features("anime", fgr=0.0, ep=1.0, cr=1.0)
        # High-follower dominates even with zero engagement/conversion
        assert compute_reward_from_features(f_high) > compute_reward_from_features(f_low)

    def test_reward_clipped_to_unit_interval(self):
        f_extreme = _make_features("anime", fgr=10.0, ep=10.0, cr=10.0)
        r = compute_reward_from_features(f_extreme)
        assert 0.0 <= r <= 1.0

    def test_zero_features_zero_reward(self):
        f = _make_features("anime", fgr=0.0, ep=0.0, cr=0.0)
        assert compute_reward_from_features(f) == 0.0


class TestFlagGate:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("GENLAB_PORTFOLIO_BANDIT_ENABLED", raising=False)
        assert not is_enabled()

    def test_enabled_recognizes_truthy_values(self, monkeypatch):
        for val in ("1", "true", "True", "yes", "ON"):
            monkeypatch.setenv("GENLAB_PORTFOLIO_BANDIT_ENABLED", val)
            assert is_enabled()


class TestUniformFallback:
    def test_uniform_returns_5_niches_with_equal_weight(self):
        allocations = uniform_allocation()
        assert len(allocations) == 5
        for a in allocations:
            assert a.recommended_weight == pytest.approx(0.20)
            assert a.ucb_score == 0.0
