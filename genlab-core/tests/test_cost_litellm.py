"""Tests for litellm-primary cost computation."""
from unittest.mock import patch
from genlab_core.intelligence.cost_accumulator import _compute_cost


class TestComputeCostLitellm:
    def test_uses_litellm_as_primary(self):
        """litellm should be tried first for known models."""
        cost = _compute_cost("claude-haiku-4-5-20251001", 1000, 200)
        # litellm should return a positive cost
        assert cost > 0

    def test_falls_back_on_litellm_exception(self):
        """If litellm raises, fall back to local table."""
        with patch(
            "litellm.completion_cost",
            side_effect=Exception("litellm broken"),
        ):
            cost = _compute_cost("claude-haiku-4-5-20251001", 1000, 200)
            assert cost > 0  # Local table should work

    def test_falls_back_on_zero_cost(self):
        """If litellm returns 0, fall back to local table."""
        with patch(
            "litellm.completion_cost",
            return_value=0,
        ):
            cost = _compute_cost("claude-haiku-4-5-20251001", 1000, 200)
            assert cost > 0

    def test_unknown_model_uses_fallback(self):
        """Unknown model should still return a positive cost from fallback."""
        cost = _compute_cost("totally-unknown-model-xyz", 1000, 200)
        assert cost > 0
