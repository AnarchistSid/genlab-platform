"""Bayesian A/B experiment analysis primitives (Phase 3.D session 1).

Sits alongside ``auto_experiment.py`` (scaffold, 2026-07-23) as the
statistical layer that turns raw arm-reward samples into
verdicts + sample-size recommendations. The scaffold's
``complete_experiment(result)`` will be fed by
``compute_verdict()`` here.

## Design

The reward signal is ``reward_48h`` per blueprint, valued in
[0, 1] after the RewardShaper normalises. Treating that as a
Beta-distributed rate (fraction of "successful" reward, where
success = above per-niche baseline) lets us use the closed-form
Beta-Beta conjugate model:

    prior_arm_i    ~ Beta(alpha_prior, beta_prior)  # from bandit state
    observed_i     : successes = sum(rewards_i > baseline),
                     failures  = n - successes
    posterior_arm_i ~ Beta(alpha_prior + successes,
                           beta_prior  + failures)

Then P(B > A) via Monte Carlo (10K draws) — fast enough for a
runner + more robust than the closed-form integral when priors
are tight.

## Session 1 scope

* ``probability_b_beats_a`` — Monte Carlo P(B > A)
* ``recommend_sample_size`` — Bayesian power calc for target
  probability threshold
* ``compute_verdict`` — turns (arm_a_samples, arm_b_samples)
  into ``ExperimentVerdict`` enum + explanation
* ``ExperimentVerdict`` — B_WINS, A_WINS, NO_SIGNAL,
  INSUFFICIENT_SAMPLES

## Session 2 scope (deferred)

* Runner integrating this module with ``auto_experiment`` scaffold
* Systemd timer for periodic check
* Early-stop poll cadence

## Session 3 scope (deferred)

* Strategist ``active_experiments`` state field wire
* Dashboard card + endpoint enrichment
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from enum import Enum
from typing import Final

logger = logging.getLogger(__name__)

# Defaults tuned to the roadmap success criteria:
# "Zero 'ran for 2 weeks, still no signal' wasted experiments".
# Verdict thresholds match the Bayesian conventions in
# bandit / product-selector modules for consistency.

DEFAULT_MC_DRAWS: Final[int] = 10_000
DEFAULT_WIN_THRESHOLD: Final[float] = 0.95  # P(B > A) or P(A > B)
DEFAULT_FUTILITY_THRESHOLD: Final[float] = 0.05  # both sides below this = NO_SIGNAL
DEFAULT_MIN_SAMPLES_PER_ARM: Final[int] = 10


class ExperimentVerdict(str, Enum):
    """Terminal decisions for an experiment run.

    * ``B_WINS`` — P(B > A) >= win_threshold (default 0.95)
    * ``A_WINS`` — P(A > B) >= win_threshold
    * ``NO_SIGNAL`` — both probabilities in the middle band
      (0.05 <= P(B > A) <= 0.95). Experiment ran long enough
      but the effect (if any) is too small to detect at this
      sample size. Stop and free up traffic.
    * ``INSUFFICIENT_SAMPLES`` — not enough data yet; keep
      running (until max duration).
    """
    B_WINS = "B_WINS"
    A_WINS = "A_WINS"
    NO_SIGNAL = "NO_SIGNAL"
    INSUFFICIENT_SAMPLES = "INSUFFICIENT_SAMPLES"


@dataclass(frozen=True)
class ArmObservations:
    """Raw evidence for one arm.

    ``rewards`` are the raw [0, 1] reward_48h values. Baseline is
    subtracted at analysis time so a "success" is above-baseline
    reward (matches the RewardShaper convention).
    """
    arm_id: str
    rewards: tuple[float, ...]

    @property
    def n(self) -> int:
        return len(self.rewards)


@dataclass(frozen=True)
class VerdictResult:
    """Return of :func:`compute_verdict`. Includes both the enum
    verdict AND the probability tuple so callers can log the raw
    signal alongside the decision."""
    verdict: ExperimentVerdict
    prob_b_beats_a: float
    n_a: int
    n_b: int
    posterior_a_mean: float
    posterior_b_mean: float
    reason: str


def _beta_sample(alpha: float, beta_: float) -> float:
    """One Beta(alpha, beta) sample via the standard random module
    so this module is dep-free. Correctness parity with numpy
    verified in pin tests."""
    return random.betavariate(alpha, beta_)


def probability_b_beats_a(
    alpha_a: float, beta_a: float,
    alpha_b: float, beta_b: float,
    *,
    n_draws: int = DEFAULT_MC_DRAWS,
    _random: random.Random | None = None,
) -> float:
    """Monte Carlo estimate of P(B > A) where both are Beta-
    distributed. Uses 10K draws by default — the sampling error
    (±0.005 at p=0.95) is tighter than any verdict threshold
    difference we care about, and this runs in ~5ms.

    Deterministic when ``_random`` is passed (seeded); production
    callers omit for real randomness.
    """
    rng = _random or random
    b_wins = 0
    for _ in range(n_draws):
        a = rng.betavariate(alpha_a, beta_a)
        b = rng.betavariate(alpha_b, beta_b)
        if b > a:
            b_wins += 1
    return b_wins / n_draws


def recommend_sample_size(
    prior_alpha: float, prior_beta: float,
    *,
    detectable_lift: float = 0.10,
    target_power: float = 0.80,
    win_threshold: float = DEFAULT_WIN_THRESHOLD,
    max_n: int = 500,
    _random: random.Random | None = None,
) -> int:
    """Bayesian sample-size calc: how many samples per arm to
    achieve ``target_power`` chance of correctly declaring B_WINS
    when the true lift is ``detectable_lift``?

    Sim: for a range of n_per_arm values, generate hypothetical
    posterior samples assuming true rate_A = prior_mean,
    rate_B = prior_mean + detectable_lift, and count how often
    P(B > A) >= win_threshold. Returns the smallest n where this
    hits target_power.

    Returns ``max_n`` if target_power never reached — signal to
    the caller that the requested lift is too small to detect
    within the experiment budget.
    """
    rng = _random or random
    prior_mean = prior_alpha / (prior_alpha + prior_beta)
    true_a = min(max(prior_mean, 0.01), 0.99)
    true_b = min(max(prior_mean + detectable_lift, 0.01), 0.99)

    for n in (20, 40, 60, 80, 100, 150, 200, 300, 500):
        if n > max_n:
            break
        # 200 sim-runs per n; count P(B > A) >= win_threshold
        detected = 0
        n_sims = 200
        for _ in range(n_sims):
            # Simulate observed successes
            successes_a = sum(1 for _ in range(n) if rng.random() < true_a)
            successes_b = sum(1 for _ in range(n) if rng.random() < true_b)
            post_alpha_a = prior_alpha + successes_a
            post_beta_a = prior_beta + (n - successes_a)
            post_alpha_b = prior_alpha + successes_b
            post_beta_b = prior_beta + (n - successes_b)
            p = probability_b_beats_a(
                post_alpha_a, post_beta_a,
                post_alpha_b, post_beta_b,
                n_draws=1000, _random=rng,
            )
            if p >= win_threshold:
                detected += 1
        power = detected / n_sims
        if power >= target_power:
            return n
    return max_n


def compute_verdict(
    arm_a: ArmObservations, arm_b: ArmObservations,
    *,
    baseline: float = 0.5,
    prior_alpha: float = 1.0, prior_beta: float = 1.0,
    win_threshold: float = DEFAULT_WIN_THRESHOLD,
    min_samples_per_arm: int = DEFAULT_MIN_SAMPLES_PER_ARM,
    _random: random.Random | None = None,
) -> VerdictResult:
    """Analyze two arms' observed rewards. Returns the verdict
    enum + probabilities so callers can persist the raw signal.

    ``baseline`` — a reward above this counts as "success". Default
    0.5 matches the RewardShaper's normalisation midpoint; niches
    with skewed reward distributions should pass their empirical
    median.
    """
    if arm_a.n < min_samples_per_arm or arm_b.n < min_samples_per_arm:
        return VerdictResult(
            verdict=ExperimentVerdict.INSUFFICIENT_SAMPLES,
            prob_b_beats_a=0.5,
            n_a=arm_a.n, n_b=arm_b.n,
            posterior_a_mean=prior_alpha / (prior_alpha + prior_beta),
            posterior_b_mean=prior_alpha / (prior_alpha + prior_beta),
            reason=(
                f"insufficient samples: n_a={arm_a.n} n_b={arm_b.n} "
                f"< min_per_arm={min_samples_per_arm}"
            ),
        )

    successes_a = sum(1 for r in arm_a.rewards if r > baseline)
    failures_a = arm_a.n - successes_a
    successes_b = sum(1 for r in arm_b.rewards if r > baseline)
    failures_b = arm_b.n - successes_b

    post_alpha_a = prior_alpha + successes_a
    post_beta_a = prior_beta + failures_a
    post_alpha_b = prior_alpha + successes_b
    post_beta_b = prior_beta + failures_b

    p_b_wins = probability_b_beats_a(
        post_alpha_a, post_beta_a,
        post_alpha_b, post_beta_b,
        _random=_random,
    )
    post_a_mean = post_alpha_a / (post_alpha_a + post_beta_a)
    post_b_mean = post_alpha_b / (post_alpha_b + post_beta_b)

    if p_b_wins >= win_threshold:
        return VerdictResult(
            verdict=ExperimentVerdict.B_WINS,
            prob_b_beats_a=p_b_wins,
            n_a=arm_a.n, n_b=arm_b.n,
            posterior_a_mean=post_a_mean,
            posterior_b_mean=post_b_mean,
            reason=f"P(B>A)={p_b_wins:.3f} >= {win_threshold}",
        )
    if p_b_wins <= 1 - win_threshold:
        return VerdictResult(
            verdict=ExperimentVerdict.A_WINS,
            prob_b_beats_a=p_b_wins,
            n_a=arm_a.n, n_b=arm_b.n,
            posterior_a_mean=post_a_mean,
            posterior_b_mean=post_b_mean,
            reason=f"P(A>B)={1-p_b_wins:.3f} >= {win_threshold}",
        )
    return VerdictResult(
        verdict=ExperimentVerdict.NO_SIGNAL,
        prob_b_beats_a=p_b_wins,
        n_a=arm_a.n, n_b=arm_b.n,
        posterior_a_mean=post_a_mean,
        posterior_b_mean=post_b_mean,
        reason=(
            f"P(B>A)={p_b_wins:.3f} in middle band "
            f"[{1-win_threshold}, {win_threshold}] — no significant signal"
        ),
    )
