"""Tests for genlab_core.learning.hook_training_data.

Tests verify the engagement label computation (75th percentile threshold)
and the tiny-dataset fallback behavior.
"""
from __future__ import annotations


from genlab_core.learning.hook_training_data import (
    HookExample,
    compute_engagement_labels,
)


def _make_examples(rewards: list[float]) -> list[HookExample]:
    """Helper to create HookExample instances from a list of reward values."""
    return [
        HookExample(
            hook_text=f"Hook text {i}",
            platform="instagram",
            niche_id="ai_creators",
            reward_48h=r,
        )
        for i, r in enumerate(rewards)
    ]


class TestComputeEngagementLabelsThresholdAt75thPercentile:
    def test_quartile_split(self):
        """Top 25% of rewards should get label=1."""
        # 20 examples with rewards 0.0 to 0.95
        rewards = [i * 0.05 for i in range(20)]
        examples = _make_examples(rewards)
        labels = compute_engagement_labels(examples)

        assert len(labels) == 20

        # 75th percentile of [0.0, 0.05, ..., 0.95] = 0.7125
        # Rewards >= 0.7125: 0.75, 0.80, 0.85, 0.90, 0.95 => 5 labels of 1
        count_positive = sum(labels)
        assert count_positive >= 4  # At least ~25% are positive
        assert count_positive <= 6  # Not more than ~30%

    def test_all_same_rewards(self):
        """When all rewards are identical, all should be label=1 (all >= threshold)."""
        rewards = [0.5] * 10
        examples = _make_examples(rewards)
        labels = compute_engagement_labels(examples)

        # 75th percentile = 0.5, all rewards == 0.5 >= 0.5 => all label=1
        assert all(l == 1 for l in labels)

    def test_empty_returns_empty(self):
        """Empty input returns empty labels."""
        assert compute_engagement_labels([]) == []


class TestComputeEngagementLabelsWithTinyDatasetFallback:
    def test_fewer_than_5_returns_all_zeros(self):
        """Tiny datasets (< 5 examples) should fall back to all-zero labels."""
        rewards = [0.1, 0.9, 0.5]
        examples = _make_examples(rewards)
        labels = compute_engagement_labels(examples)

        assert len(labels) == 3
        assert all(l == 0 for l in labels)

    def test_exactly_5_uses_real_threshold(self):
        """5 examples is the minimum for real percentile computation."""
        rewards = [0.1, 0.2, 0.3, 0.4, 1.0]
        examples = _make_examples(rewards)
        labels = compute_engagement_labels(examples)

        assert len(labels) == 5
        # 75th percentile of [0.1, 0.2, 0.3, 0.4, 1.0] = 0.4
        # At least the 1.0 should be label=1
        assert labels[-1] == 1  # The 1.0 reward
        assert sum(labels) >= 1
