"""Tests for Intervention 2 — cross-niche hierarchical Bayes transfer.

Coverage:
- Feature-flag semantics
- ``extract_style_suffix`` across the four arm-id shapes
- ``_moment_match_beta``:
    * Perfect match on canonical (mean, variance) → returns
      (mean * effective_n, (1-mean) * effective_n)
    * Degenerate cases return None (variance ≤ 0, variance ≥
      max_var, effective_n outside clamp)
- ``compute_transferred_priors``:
    * At least 2 niches required per style
    * Niches below _MIN_OBS_PER_NICHE dropped
    * Non-style arms ignored
    * Happy path with 3 niches per style produces a prior
    * Underpowered styles absent from output
- Persistence round-trip
- ``get_transferred_prior``:
    * Flag off → None regardless of persisted state
    * Non-style arm → None
    * Style not in persisted priors → None
    * Match returns (alpha, beta)
"""

from __future__ import annotations

import json

import pytest
from genlab_core.learning import cross_niche_transfer as cnt

# ── Feature flag ────────────────────────────────────────────────────


class TestFeatureFlag:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", raising=False)
        assert not cnt._integration_enabled()

    @pytest.mark.parametrize("val", ["true", "TRUE", "True"])
    def test_exact_true_activates(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", val)
        assert cnt._integration_enabled()

    @pytest.mark.parametrize("val", ["1", "yes", "on", "TRUE_", "", "false"])
    def test_non_exact_true_stays_off(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", val)
        assert not cnt._integration_enabled()


# ── Style extraction ──────────────────────────────────────────────


class TestExtractStyleSuffix:
    """The four arm-id shapes the bandit uses in production, all
    documented in bandit_platform_split.py + arm_loader.py."""

    def test_two_part_style_arm(self):
        assert cnt.extract_style_suffix("style:revelation") == "revelation"

    def test_three_part_style_arm(self):
        assert cnt.extract_style_suffix("style:gaming:revelation") == "revelation"

    def test_platform_suffix_stripped(self):
        assert cnt.extract_style_suffix("style:gaming:revelation__youtube") == "revelation"

    def test_hour_arm_returns_none(self):
        assert cnt.extract_style_suffix("hour:12:instagram:gaming") is None

    def test_source_arm_returns_none(self):
        assert cnt.extract_style_suffix("source:youtube_trending__facebook") is None

    def test_empty_style_returns_none(self):
        assert cnt.extract_style_suffix("style:") is None


# ── Moment matching ───────────────────────────────────────────────


class TestMomentMatchBeta:
    def test_reasonable_input_produces_beta(self):
        """μ=0.15, σ²=0.0075 → effective_n = 0.1275/0.0075 - 1 = 16
        (inside the clamp). α = 0.15 * 17 = 2.4, β = 0.85 * 17 = 13.6."""
        result = cnt._moment_match_beta(0.15, 0.0075)
        assert result is not None
        alpha, beta = result
        assert alpha + beta == pytest.approx(16.0)
        assert alpha / (alpha + beta) == pytest.approx(0.15)

    def test_zero_variance_returns_none(self):
        assert cnt._moment_match_beta(0.5, 0.0) is None

    def test_negative_variance_returns_none(self):
        assert cnt._moment_match_beta(0.5, -0.01) is None

    def test_mean_at_boundary_returns_none(self):
        """Mean=0 or 1 breaks the Beta parameterisation. This
        shouldn't happen with real posteriors but the guard is
        cheap."""
        assert cnt._moment_match_beta(0.0, 0.01) is None
        assert cnt._moment_match_beta(1.0, 0.01) is None

    def test_variance_above_max_returns_none(self):
        """When variance ≥ mean(1-mean), no Beta exists. The
        cross-niche sample is too disparate — skip transfer."""
        # max variance at mean=0.5 is 0.25
        assert cnt._moment_match_beta(0.5, 0.3) is None

    def test_effective_n_below_clamp_returns_none(self):
        """Very high variance → effective_n < 2 → skip."""
        # mean=0.5, variance=0.2 → effective_n = 0.25/0.2 - 1 = 0.25
        assert cnt._moment_match_beta(0.5, 0.2) is None

    def test_effective_n_above_clamp_is_clamped_not_rejected(self):
        """2026-08-19 change (deep-dive gap-fix): very low variance →
        effective_n > 20 used to REJECT (return None) as over-
        confident. Diagnosis found this dropped 49 of 51 candidate
        priors on prod data (5-niche tight-agreement regime).
        New behavior: CLAMP effective_n down to the upper bound;
        preserves the mean signal, bounds the pseudo-obs count."""
        # mean=0.5, variance=0.001 → effective_n = 0.25/0.001 - 1 = 249
        # Pre-fix: None. Post-fix: clamped to 20 → α=β=10.
        result = cnt._moment_match_beta(0.5, 0.001)
        assert result is not None, (
            "high effective_n should now clamp, not reject — "
            "unlocks cross-niche transfer priors in the "
            "tight-agreement regime"
        )
        alpha, beta = result
        # After clamp to 20: α = mean * 20 = 10, β = (1-mean) * 20 = 10
        assert abs(alpha - 10.0) < 1e-9, f"α={alpha}, expected 10.0"
        assert abs(beta - 10.0) < 1e-9, f"β={beta}, expected 10.0"
        assert abs((alpha + beta) - 20.0) < 1e-9, (
            "clamped effective_n must equal the upper bound (20)"
        )

    def test_effective_n_at_upper_bound_exact(self):
        """Boundary: effective_n exactly 20 stays at 20 (no clamp
        needed but result matches the clamp path)."""
        # Solve for variance where eff_n = 20: variance = 0.25 / 21
        variance = 0.25 / 21.0
        result = cnt._moment_match_beta(0.5, variance)
        assert result is not None
        alpha, beta = result
        assert abs(alpha - 10.0) < 1e-6
        assert abs(beta - 10.0) < 1e-6


# ── compute_transferred_priors ────────────────────────────────────


class TestComputeTransferredPriors:
    def test_two_niches_produce_prior(self):
        """The minimum: 2 niches, both above MIN_OBS_PER_NICHE, with
        different observed rates → prior emitted.

        Values chosen so the empirical variance sits in the range
        where effective_n lands inside the [2, 20] clamp. Mean=0.15,
        variance≈0.02 → effective_n ≈ 6 (μ(1-μ)/σ² - 1 = 0.1275/0.02 - 1 ≈ 5.4).
        """
        state = {
            "gaming": {
                # observed_rate = 0.25, n_obs = 10
                "style:gaming:revelation": (3.0, 9.0),
            },
            "anime": {
                # observed_rate = 0.05, n_obs = 10
                "style:anime:revelation": (0.6, 11.4),
            },
        }
        priors = cnt.compute_transferred_priors(state)
        assert "revelation" in priors, priors
        prior = priors["revelation"]
        assert prior.n_niches == 2
        assert prior.observed_rate_mean == pytest.approx(0.15)
        assert prior.alpha + prior.beta >= 2.0
        assert prior.alpha + prior.beta <= 20.0

    def test_one_niche_skipped(self):
        """Below the 2-niche minimum → style is absent from output.
        Consumers see no key → fall back to Beta(1,1)."""
        state = {"gaming": {"style:gaming:revelation": (5.0, 20.0)}}
        priors = cnt.compute_transferred_priors(state)
        assert "revelation" not in priors

    def test_niches_below_min_obs_dropped(self):
        """A niche with only 3 observations of the style is too
        noisy to contribute. If the other two niches drop below
        the 2-niche count as a result, the style is skipped."""
        state = {
            # 3 obs — below _MIN_OBS_PER_NICHE = 5 → dropped
            "gaming": {"style:gaming:revelation": (1.5, 2.5)},
            # 10 obs — kept
            "anime": {"style:anime:revelation": (2.0, 10.0)},
        }
        priors = cnt.compute_transferred_priors(state)
        # After dropping gaming, only 1 niche remains → skipped
        assert "revelation" not in priors

    def test_non_style_arms_ignored(self):
        # Same rate values as test_two_niches_produce_prior (mean=0.15,
        # variance≈0.02 → effective_n ≈ 5.4, inside the clamp) so the
        # style prior actually emits; we just add a bunch of non-style
        # arms that should be filtered out entirely.
        state = {
            "gaming": {
                "style:gaming:revelation": (3.0, 9.0),
                "hour:12:instagram:gaming": (5.0, 10.0),
                "source:youtube_trending": (4.0, 20.0),
            },
            "anime": {
                "style:anime:revelation": (0.6, 11.4),
                "hour:14:youtube:anime": (2.0, 8.0),
            },
        }
        priors = cnt.compute_transferred_priors(state)
        assert set(priors.keys()) == {"revelation"}


# ── Persistence round-trip ────────────────────────────────────────


class TestPersistence:
    def test_persist_and_load(self, tmp_path):
        p = cnt.TransferredPrior(
            style="revelation",
            alpha=2.4,
            beta=13.6,
            observed_rate_mean=0.15,
            observed_rate_variance=0.005,
            n_niches=3,
        )
        priors = {"revelation": p}
        path = tmp_path / "priors.json"
        cnt.persist_priors(priors, output_path=path)
        got = cnt.load_persisted_priors(input_path=path)
        assert "revelation" in got
        assert got["revelation"].alpha == pytest.approx(2.4)
        assert got["revelation"].beta == pytest.approx(13.6)

    def test_load_missing_returns_empty(self, tmp_path):
        got = cnt.load_persisted_priors(input_path=tmp_path / "no.json")
        assert got == {}

    def test_load_malformed_returns_empty(self, tmp_path):
        path = tmp_path / "priors.json"
        path.write_text("{corrupt")
        got = cnt.load_persisted_priors(input_path=path)
        assert got == {}


# ── Consumer read side ────────────────────────────────────────────


class TestGetTransferredPrior:
    def test_flag_off_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", raising=False)
        # Even with valid persisted priors, off flag → None. Consumer
        # falls back to Beta(1,1) — same behaviour as today.
        p = cnt.TransferredPrior(
            style="revelation",
            alpha=2.4,
            beta=13.6,
            observed_rate_mean=0.15,
            observed_rate_variance=0.005,
            n_niches=3,
        )
        path = tmp_path / "priors.json"
        cnt.persist_priors({"revelation": p}, output_path=path)
        assert (
            cnt.get_transferred_prior(
                "ai_creators",
                "style:ai_creators:revelation",
                input_path=path,
            )
            is None
        )

    def test_non_style_arm_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", "true")
        assert (
            cnt.get_transferred_prior(
                "gaming",
                "hour:12:instagram:gaming",
                input_path=tmp_path / "priors.json",
            )
            is None
        )

    def test_flag_on_style_match_returns_prior(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", "true")
        p = cnt.TransferredPrior(
            style="revelation",
            alpha=2.4,
            beta=13.6,
            observed_rate_mean=0.15,
            observed_rate_variance=0.005,
            n_niches=3,
        )
        path = tmp_path / "priors.json"
        cnt.persist_priors({"revelation": p}, output_path=path)
        result = cnt.get_transferred_prior(
            "ai_creators",
            "style:ai_creators:revelation",
            input_path=path,
        )
        assert result is not None
        alpha, beta = result
        assert alpha == pytest.approx(2.4)
        assert beta == pytest.approx(13.6)

    def test_flag_on_style_absent_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", "true")
        # Empty priors file
        (tmp_path / "priors.json").write_text(json.dumps({"priors": {}}))
        assert (
            cnt.get_transferred_prior(
                "ai_creators",
                "style:ai_creators:comparison",
                input_path=tmp_path / "priors.json",
            )
            is None
        )
