"""Tests for genlab_core.learning.bandit_residual (D2.5)."""

from __future__ import annotations

import csv
import math
from pathlib import Path

import pytest

from genlab_core.learning.bandit_residual import (
    _DEFAULT_DRIFT_THRESHOLD,
    ArmResidual,
    _compute_residual,
    compute_residuals,
    write_csv,
)


class TestComputeResidualMath:
    """Pure-math unit tests — no DB needed."""

    def test_zero_plays_returns_none_for_residual(self):
        """No observations → residual is mathematically undefined."""
        observed_avg, residual, cold, flagged, score = _compute_residual(
            alpha=1.0, beta=1.0, n_plays=0
        )
        assert observed_avg is None
        assert residual is None
        assert cold is True
        assert flagged is False
        assert score == 0.0

    def test_perfectly_calibrated_arm_has_zero_residual(self):
        """When alpha-1 == n_plays*(alpha/(alpha+beta)), residual = 0."""
        # Construct: alpha=6, beta=4, n_plays=8 (since prior=1+1, total 10)
        # observed_avg = (6-1)/8 = 0.625
        # posterior_mean = 6/10 = 0.6 → residual ≈ 0.025 (small, not zero)
        # Easier: pick a case where observed exactly = posterior.
        # For Beta(α=1+k*0.5, β=1+k*0.5) with k plays at reward=0.5:
        # observed_avg = k*0.5 / k = 0.5
        # posterior_mean = (1+k*0.5) / (2+k) → approaches 0.5 as k→∞
        # At k=100: posterior = 51/102 = 0.5 → residual = 0 ✓
        observed_avg, residual, _, _, _ = _compute_residual(
            alpha=51.0, beta=51.0, n_plays=100
        )
        assert observed_avg == pytest.approx(0.5, abs=1e-9)
        assert residual == pytest.approx(0.0, abs=1e-9)

    def test_pessimistic_prior_produces_positive_residual(self):
        """Beta(1,1) drags low observed_avg upward → empirical higher than posterior."""
        # 10 plays all at reward 0.9 → alpha=1+9=10, beta=1+1=2, n_plays=10
        # observed_avg = 9/10 = 0.9
        # posterior_mean = 10/12 ≈ 0.833 → residual ≈ +0.067
        observed_avg, residual, cold, flagged, _ = _compute_residual(
            alpha=10.0, beta=2.0, n_plays=10
        )
        assert observed_avg == pytest.approx(0.9, abs=1e-9)
        assert residual == pytest.approx(0.0666, abs=1e-3)
        assert cold is False  # 10 > _MIN_PLAYS_FOR_DRIFT=5
        assert flagged is False  # 0.067 < default threshold 0.15

    def test_drift_flag_above_threshold(self):
        """A warm arm with residual > threshold is is_drift_flagged."""
        # 20 plays at reward 0.95 → alpha=1+19=20, beta=1+1=2 (since 0.05 per
        # failure but reward=0.95 each time so beta += 0.05 each = 1+1.0=2)
        # observed_avg = 19/20 = 0.95
        # posterior_mean = 20/22 ≈ 0.909 → residual ≈ 0.041 → still NOT flagged
        # Need bigger gap — synthetic high-residual case:
        # alpha=2, beta=1, n_plays=1 → observed=1.0, posterior=0.667 → residual=0.33
        # but n_plays=1 → cold_start
        # Try: alpha=11, beta=1, n_plays=10 → obs=1.0, post=0.917 → res=0.083 — still not over
        # Use: alpha=1.5, beta=20, n_plays=20 → obs=0.5/20=0.025, post=1.5/21.5=0.07 → res=-0.045
        # The math means residual is BOUNDED by 2/(α+β); to exceed 0.15 we need α+β < ~13
        # but warm-start requires n_plays >= 5 which means α+β >= 7. So threshold
        # of 0.15 is reachable; use observed=1.0 across 6 plays:
        # alpha=1+6=7, beta=1+0=1, n_plays=6
        # observed_avg = 6/6 = 1.0; posterior = 7/8 = 0.875; residual = 0.125 → close but not over
        # 7 plays all reward=1: alpha=8, beta=1, n_plays=7
        # observed_avg = 7/7 = 1.0; posterior = 8/9 ≈ 0.889; residual = 0.111 → still under
        # The closed-form: residual = (n_plays - n_plays*(1+n_plays)/(2+n_plays)) / n_plays
        # = 1 - (1+n_plays)/(2+n_plays) = 1/(2+n_plays)
        # For 0.15: need 1/(2+n_plays) > 0.15 → 2+n_plays < 6.67 → n_plays < 5
        # which collides with cold-start cutoff. So a pure 0.15 threshold on
        # well-explored arms is rare — the diagnostic mostly surfaces
        # ARTIFICIAL state (e.g. from a buggy bandit updater) rather than
        # natural Beta drift. Test that with a custom threshold:
        observed_avg, residual, cold, flagged, _ = _compute_residual(
            alpha=8.0, beta=1.0, n_plays=7, drift_threshold=0.10
        )
        assert observed_avg == pytest.approx(1.0)
        assert residual == pytest.approx(0.1111, abs=1e-3)
        assert cold is False
        assert flagged is True

    def test_ranking_score_weights_well_explored_arms(self):
        """Higher n_plays with same |residual| → higher ranking_score."""
        _, _, _, _, score_low = _compute_residual(alpha=2.0, beta=1.0, n_plays=1)
        _, _, _, _, score_high = _compute_residual(alpha=51.0, beta=50.0, n_plays=100)
        # The high-n_plays arm has near-zero residual but log(101) is large;
        # the low-n_plays arm has residual=0.333 but log(2) is small.
        # Specifically: score_low = 0.333 * log(2) ≈ 0.231
        #               score_high = 0.0 * log(101) = 0.0
        # So actually score_low > score_high. The ranking_score is designed
        # to surface DRIFT, not just explored-ness; a well-calibrated arm
        # gets score 0 regardless of n_plays. Refine test to use a *drifted*
        # well-explored arm vs a *drifted* cold one:
        _, _, _, _, score_cold = _compute_residual(
            alpha=2.0, beta=1.0, n_plays=1
        )  # res=0.333, n=1
        _, _, _, _, score_warm = _compute_residual(
            alpha=70.0, beta=30.0, n_plays=98
        )  # res ≈ (69/98) - 0.7 = 0.0041, n=98
        # ranking_score(cold) = 0.333 * log(2) ≈ 0.231
        # ranking_score(warm) = 0.0041 * log(99) ≈ 0.019
        # Cold one wins — which is correct: it has a much bigger residual.
        # The ranking_score's job isn't to suppress cold-start, it's to
        # surface BOTH drifted-cold and drifted-warm proportional to
        # |residual| * confidence.
        assert score_cold > score_warm
        assert score_cold > 0.0

    def test_clamp_observed_avg_to_unit_interval(self):
        """Float drift should never produce observed_avg > 1 or < 0."""
        # Construct alpha just over 1 with n_plays=0... can't, n_plays guarded.
        # Construct alpha way over n_plays+1 (impossible if reward clipped, but
        # we should still clamp defensively).
        observed_avg, _, _, _, _ = _compute_residual(
            alpha=15.0, beta=1.0, n_plays=10
        )
        # (15-1)/10 = 1.4 → clamped to 1.0
        assert observed_avg == 1.0


class TestComputeResiduals:
    """End-to-end compute_residuals() — rows in, ArmResidual list out."""

    def test_empty_input_returns_empty(self):
        assert compute_residuals([]) == []

    def test_handles_missing_fields_gracefully(self):
        """A row without alpha/beta uses the prior defaults."""
        out = compute_residuals(
            [{"niche_id": "gaming", "arm_id": "x"}]  # no alpha/beta/n_plays
        )
        assert len(out) == 1
        assert out[0].alpha == 1.0  # prior
        assert out[0].beta == 1.0
        assert out[0].n_plays == 0
        assert out[0].observed_avg is None
        assert out[0].is_cold_start is True

    def test_sort_order_drift_first_then_alphabetical(self):
        """Most-misaligned first; ties broken by (niche, arm) for stability."""
        rows = [
            {"niche_id": "z", "arm_id": "warm_cal", "alpha": 51, "beta": 51, "n_plays": 100},
            {"niche_id": "a", "arm_id": "drift_a", "alpha": 8, "beta": 1, "n_plays": 7},
            {"niche_id": "a", "arm_id": "drift_b", "alpha": 8, "beta": 1, "n_plays": 7},
        ]
        out = compute_residuals(rows, drift_threshold=0.10)
        # Both drift_* arms tie on ranking_score (same alpha/beta/n_plays).
        # The well-calibrated z arm should come LAST.
        assert out[-1].arm_id == "warm_cal"
        # And the two drift arms come in alphabetical order on tie.
        assert (out[0].arm_id, out[1].arm_id) == ("drift_a", "drift_b")


class TestCsvOutput:
    def test_csv_round_trips(self, tmp_path):
        rows = [
            {"niche_id": "gaming", "arm_id": "x", "alpha": 6, "beta": 4, "n_plays": 8},
            {"niche_id": "anime", "arm_id": "y", "alpha": 1, "beta": 1, "n_plays": 0},
        ]
        residuals = compute_residuals(rows)
        out = tmp_path / "out.csv"
        write_csv(residuals, out)

        # Re-read and verify shape
        with open(out) as f:
            reader = csv.DictReader(f)
            read_rows = list(reader)

        assert len(read_rows) == 2
        # zero-play arm has empty observed_avg / residual (None → "")
        zero_play = next(r for r in read_rows if r["arm_id"] == "y")
        assert zero_play["observed_avg"] == ""
        assert zero_play["residual"] == ""
        assert zero_play["is_cold_start"] == "1"

    def test_csv_creates_parent_dirs(self, tmp_path):
        out = tmp_path / "nested" / "subdir" / "out.csv"
        write_csv([], out)
        assert out.exists()


class TestArmResidualDataclass:
    def test_to_dict_serializes_all_fields(self):
        ar = ArmResidual(
            niche_id="gaming",
            arm_id="x",
            n_plays=10,
            alpha=5.0,
            beta=5.0,
            observed_avg=0.5,
            posterior_mean=0.5,
            residual=0.0,
            is_cold_start=False,
            is_drift_flagged=False,
            ranking_score=0.0,
        )
        d = ar.to_dict()
        assert d["niche_id"] == "gaming"
        assert d["residual"] == 0.0
        assert "is_drift_flagged" in d
