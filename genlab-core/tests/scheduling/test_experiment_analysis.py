"""Pin Phase 3.D session 1 Bayesian experiment analysis:

  * probability_b_beats_a converges on known analytical answers
  * Symmetric priors → P(B>A) ≈ 0.5
  * Strong B advantage → P(B>A) ≈ 1.0
  * compute_verdict routes to INSUFFICIENT_SAMPLES below floor
  * compute_verdict routes to B_WINS when B genuinely better
  * compute_verdict routes to A_WINS when A genuinely better
  * compute_verdict routes to NO_SIGNAL when effect too small
  * recommend_sample_size grows with target power
  * recommend_sample_size caps at max_n when lift too small
  * Empty ArmObservations doesn't crash
"""
from __future__ import annotations

import random

import pytest

from genlab_core.scheduling.experiment_analysis import (
    ArmObservations,
    ExperimentVerdict,
    VerdictResult,
    compute_verdict,
    probability_b_beats_a,
    recommend_sample_size,
)


# Deterministic RNG for all Monte Carlo checks — flaky tests are
# worse than tests that never run in this territory.
def _rng(seed: int = 42) -> random.Random:
    return random.Random(seed)


class TestProbabilityBBeatsA:
    def test_symmetric_priors_ish_half(self):
        """Beta(10,10) vs Beta(10,10) → P(B>A) ≈ 0.5."""
        p = probability_b_beats_a(10, 10, 10, 10, _random=_rng())
        assert 0.45 < p < 0.55

    def test_b_strongly_better(self):
        """Beta(2,20) vs Beta(20,2) → B ≈ 0.91 >> A ≈ 0.09.
        P(B>A) should be very close to 1."""
        p = probability_b_beats_a(2, 20, 20, 2, _random=_rng())
        assert p > 0.99

    def test_a_strongly_better(self):
        """Mirror check: Beta(20,2) vs Beta(2,20) → P(B>A) ≈ 0."""
        p = probability_b_beats_a(20, 2, 2, 20, _random=_rng())
        assert p < 0.01

    def test_moderate_b_advantage(self):
        """Beta(6,4) vs Beta(8,2) → B slightly better. Should be
        around 0.7-0.85 depending on sampling. Deterministic RNG
        pins the middle-band verdict."""
        p = probability_b_beats_a(6, 4, 8, 2, _random=_rng())
        assert 0.65 < p < 0.90


class TestComputeVerdict:
    def _obs(self, arm_id: str, rewards: list[float]) -> ArmObservations:
        return ArmObservations(arm_id=arm_id, rewards=tuple(rewards))

    def test_insufficient_samples_short_a(self):
        result = compute_verdict(
            self._obs("a", [0.6, 0.7, 0.8]),  # n=3
            self._obs("b", [0.5] * 20),
            _random=_rng(),
        )
        assert result.verdict == ExperimentVerdict.INSUFFICIENT_SAMPLES
        assert "insufficient samples" in result.reason

    def test_insufficient_samples_short_b(self):
        result = compute_verdict(
            self._obs("a", [0.5] * 20),
            self._obs("b", [0.6, 0.7]),  # n=2
            _random=_rng(),
        )
        assert result.verdict == ExperimentVerdict.INSUFFICIENT_SAMPLES

    def test_b_wins_clear_lift(self):
        """A rewards mostly < 0.5 baseline; B rewards mostly > 0.5."""
        a_rewards = [0.3, 0.4, 0.45, 0.35, 0.2, 0.3, 0.4, 0.4, 0.35, 0.3,
                     0.4, 0.35, 0.4, 0.3, 0.25]  # n=15, 0 successes
        b_rewards = [0.7, 0.8, 0.75, 0.85, 0.9, 0.7, 0.65, 0.8, 0.75, 0.7,
                     0.8, 0.75, 0.85, 0.7, 0.8]  # n=15, 15 successes
        result = compute_verdict(
            self._obs("a", a_rewards), self._obs("b", b_rewards),
            _random=_rng(),
        )
        assert result.verdict == ExperimentVerdict.B_WINS
        assert result.prob_b_beats_a > 0.95

    def test_a_wins_clear_lift(self):
        b_rewards = [0.3, 0.4, 0.45, 0.35, 0.2, 0.3, 0.4, 0.4, 0.35, 0.3,
                     0.4, 0.35, 0.4, 0.3, 0.25]
        a_rewards = [0.7, 0.8, 0.75, 0.85, 0.9, 0.7, 0.65, 0.8, 0.75, 0.7,
                     0.8, 0.75, 0.85, 0.7, 0.8]
        result = compute_verdict(
            self._obs("a", a_rewards), self._obs("b", b_rewards),
            _random=_rng(),
        )
        assert result.verdict == ExperimentVerdict.A_WINS
        assert result.prob_b_beats_a < 0.05

    def test_no_signal_when_effect_tiny(self):
        """Both arms hover around baseline — no meaningful lift.
        Should return NO_SIGNAL, not INSUFFICIENT."""
        # 15 samples each, half above / half below baseline in both
        a_rewards = [0.6, 0.4, 0.55, 0.45, 0.6, 0.4, 0.55, 0.45,
                     0.6, 0.4, 0.55, 0.45, 0.5, 0.5, 0.5]
        b_rewards = [0.55, 0.45, 0.6, 0.4, 0.55, 0.45, 0.6, 0.4,
                     0.55, 0.45, 0.6, 0.4, 0.5, 0.5, 0.5]
        result = compute_verdict(
            self._obs("a", a_rewards), self._obs("b", b_rewards),
            _random=_rng(),
        )
        assert result.verdict == ExperimentVerdict.NO_SIGNAL

    def test_result_carries_probability(self):
        """Verdict caller may want to persist P(B>A) alongside the
        enum — this pin catches a schema regression where the
        raw signal gets dropped."""
        result = compute_verdict(
            self._obs("a", [0.5] * 15), self._obs("b", [0.5] * 15),
            _random=_rng(),
        )
        assert isinstance(result.prob_b_beats_a, float)
        assert 0.0 <= result.prob_b_beats_a <= 1.0
        assert result.n_a == 15
        assert result.n_b == 15


class TestRecommendSampleSize:
    def test_larger_lift_needs_fewer_samples(self):
        """Detecting a 30% lift requires fewer samples than a 5% lift.
        Doesn't need to hit an exact number — just directionally correct."""
        n_big = recommend_sample_size(
            5, 5, detectable_lift=0.30, target_power=0.8,
            _random=_rng(),
        )
        n_small = recommend_sample_size(
            5, 5, detectable_lift=0.05, target_power=0.8,
            _random=_rng(),
        )
        assert n_big < n_small

    def test_returns_max_n_when_unattainable(self):
        """A 0.5% lift with target_power=0.99 is basically
        undetectable in the ladder we search — should hit max_n
        cap rather than return None or crash."""
        n = recommend_sample_size(
            50, 50, detectable_lift=0.005, target_power=0.99,
            max_n=100, _random=_rng(),
        )
        assert n == 100


class TestArmObservations:
    def test_n_property(self):
        obs = ArmObservations(arm_id="x", rewards=(0.1, 0.2, 0.3))
        assert obs.n == 3

    def test_empty_n_zero(self):
        obs = ArmObservations(arm_id="x", rewards=())
        assert obs.n == 0

    def test_empty_hits_insufficient_samples(self):
        """Empty arm should route to INSUFFICIENT, never divide by
        zero downstream."""
        result = compute_verdict(
            ArmObservations("a", ()), ArmObservations("b", (0.5,) * 20),
            _random=_rng(),
        )
        assert result.verdict == ExperimentVerdict.INSUFFICIENT_SAMPLES


class TestVerdictEnum:
    def test_all_four_states_defined(self):
        assert ExperimentVerdict.B_WINS.value == "B_WINS"
        assert ExperimentVerdict.A_WINS.value == "A_WINS"
        assert ExperimentVerdict.NO_SIGNAL.value == "NO_SIGNAL"
        assert ExperimentVerdict.INSUFFICIENT_SAMPLES.value == "INSUFFICIENT_SAMPLES"
