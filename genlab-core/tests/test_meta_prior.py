"""Tests for genlab_core.learning.meta_prior.

Verifies confidence-scaled Beta transfer, exploration inflation,
and the apply_warm_start safety guard (never overwrite real observations).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from genlab_core.learning.meta_prior import (
    TRANSFER_MATRIX,
    apply_warm_start,
    compute_warm_start_prior,
)

# ---------------------------------------------------------------------------
# 1. Low confidence scales toward uniform Beta(1,1)
# ---------------------------------------------------------------------------


class TestComputeWarmStartScaling:
    def test_compute_warm_start_scales_toward_uniform_with_low_confidence(self):
        """With confidence=0.1, a strong source posterior (20,5) should
        produce priors close to Beta(1,1) + inflation."""
        alpha, beta = compute_warm_start_prior(20.0, 5.0, confidence=0.1)
        # alpha_warm = 1 + 0.1*(20-1) + 0.5 = 1 + 1.9 + 0.5 = 3.4
        # beta_warm  = 1 + 0.1*(5-1)  + 0.5 = 1 + 0.4 + 0.5 = 1.9
        assert abs(alpha - 3.4) < 1e-6
        assert abs(beta - 1.9) < 1e-6


# ---------------------------------------------------------------------------
# 2. Exploration inflation is added to both alpha and beta
# ---------------------------------------------------------------------------


class TestExplorationInflation:
    def test_compute_warm_start_adds_exploration_inflation(self):
        """Inflation of 0.5 (default) is added to both alpha and beta."""
        # With confidence=1.0 and source Beta(10, 3):
        # alpha = 1 + 1*(10-1) + 0.5 = 10.5
        # beta  = 1 + 1*(3-1)  + 0.5 = 3.5
        alpha, beta = compute_warm_start_prior(10.0, 3.0, confidence=1.0)
        assert abs(alpha - 10.5) < 1e-6
        assert abs(beta - 3.5) < 1e-6

        # Without inflation:
        alpha_no, beta_no = compute_warm_start_prior(
            10.0, 3.0, confidence=1.0, exploration_inflation=0.0
        )
        assert abs(alpha_no - 10.0) < 1e-6
        assert abs(beta_no - 3.0) < 1e-6

        # Inflation adds to both
        assert alpha - alpha_no == pytest.approx(0.5)
        assert beta - beta_no == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# 3. apply_warm_start skips arms with existing observations
# ---------------------------------------------------------------------------


class TestApplyWarmStartSkipsExisting:
    def test_apply_warm_start_skips_arms_with_existing_observations(self):
        """Arms with total_obs > 2.0 (alpha-1 + beta-1 > 2) are skipped."""
        mock_proxy = MagicMock()
        # Target already has an arm with real observations: alpha=5, beta=3
        # total_obs = (5-1)+(3-1) = 6 > 2
        mock_proxy.all.return_value = [
            {
                "id": "rec1",
                "fields": {"Title": "controversy__instagram", "Alpha": 5.0, "Beta": 3.0},
            }
        ]

        source_arms = {
            "controversy__instagram": (15.0, 4.0),
        }
        results = apply_warm_start("sports", source_arms, mock_proxy)
        assert results["controversy__instagram"] == "skipped_has_obs"
        # create should NOT have been called
        mock_proxy.create.assert_not_called()


# ---------------------------------------------------------------------------
# 4. apply_warm_start updates cold arms via save_arm
# ---------------------------------------------------------------------------


class TestApplyWarmStartUpdatesCold:
    def test_apply_warm_start_updates_cold_arms(self):
        """Arms at Beta(1,1) or close to it should be updated."""
        mock_proxy = MagicMock()
        # Target has no existing arms
        mock_proxy.all.return_value = []

        source_arms = {
            "viral_moment__youtube": (8.0, 3.0),
            "patch_notes__instagram": (4.0, 6.0),
        }
        results = apply_warm_start("sports", source_arms, mock_proxy)

        assert results["viral_moment__youtube"] == "updated"
        assert results["patch_notes__instagram"] == "updated"
        assert mock_proxy.create.call_count == 2

        # Verify the written alpha values are correctly scaled
        # For sports <- gaming, confidence=0.45
        # viral_moment__youtube: alpha = 1 + 0.45*(8-1) + 0.5 = 1 + 3.15 + 0.5 = 4.65
        calls = mock_proxy.create.call_args_list
        fields_0 = calls[0][0][0]
        assert fields_0["Title"] == "viral_moment__youtube"
        assert abs(fields_0["Alpha"] - 4.65) < 1e-6


# ---------------------------------------------------------------------------
# 5. Transfer matrix defines correct source niches
# ---------------------------------------------------------------------------


class TestTransferMatrix:
    def test_transfer_matrix_defines_correct_source_niches(self):
        assert "sports" in TRANSFER_MATRIX
        assert "movies" in TRANSFER_MATRIX
        assert "anime" in TRANSFER_MATRIX

        # Sports pulls from gaming
        assert "gaming" in TRANSFER_MATRIX["sports"]
        assert TRANSFER_MATRIX["sports"]["gaming"] == 0.45

        # Movies pulls from ai_creators
        assert "ai_creators" in TRANSFER_MATRIX["movies"]
        assert TRANSFER_MATRIX["movies"]["ai_creators"] == 0.35

        # Anime pulls from ai_creators
        assert "ai_creators" in TRANSFER_MATRIX["anime"]
        assert TRANSFER_MATRIX["anime"]["ai_creators"] == 0.30


# ---------------------------------------------------------------------------
# 6. compute_warm_start_prior never sets below 1.0
# ---------------------------------------------------------------------------


class TestNeverBelowOne:
    def test_compute_warm_start_never_sets_below_1(self):
        """Even with a source posterior below 1 (shouldn't happen in practice,
        but we guard against it), output alpha/beta >= 1.0."""
        # source_alpha=0.5 (below 1, abnormal)
        alpha, beta = compute_warm_start_prior(0.5, 0.5, confidence=0.9)
        assert alpha >= 1.0
        assert beta >= 1.0

        # With zero inflation and low source
        alpha, beta = compute_warm_start_prior(
            0.3, 0.3, confidence=1.0, exploration_inflation=0.0
        )
        assert alpha >= 1.0
        assert beta >= 1.0
