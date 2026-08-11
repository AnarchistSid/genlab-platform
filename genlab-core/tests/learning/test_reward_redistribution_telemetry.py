"""Batch B (2026-07-01): reward-weight redistribution telemetry pins.

Blind Spot #2 from ``docs/AGENT-INTELLIGENCE-RESEARCH-2026-07.md``:
the reward shaper silently redistributes weight when metrics are
missing from the fetcher output. Concrete case: Instagram's
``dm_send_rate`` slice (30% of the IG reward vector) is unavailable
without webhook access — that 30% gets silently reallocated across
saves/shares, inflating the effective signal for the still-present
metrics. Bandit posteriors then update on partial evidence with no
operator visibility.

The Batch B fix makes it observable: WARNING when dropped weight
crosses 15%, DEBUG for any non-trivial drop. These pins guarantee
the telemetry is present so an accidental Edit doesn't restore the
silent behavior.
"""

from __future__ import annotations

import logging

import pytest
from genlab_core.learning.reward_shaper import RewardShaper


@pytest.fixture
def shaper() -> RewardShaper:
    return RewardShaper()


class TestWarningThreshold:
    """When ≥15% of the weight vector is dropped, we WARN so it's
    visible in prod logs. Below that, we DEBUG (quiet by default,
    accessible when investigating)."""

    def test_significant_drop_emits_warning(self, shaper, caplog):
        """IG's typical case: dm_send absent → ~30% of weight
        redistributed. Must emit a WARNING with the scale factor
        and the missing key names."""
        caplog.set_level(logging.WARNING, logger="genlab_core.learning.reward_shaper")

        # Feed IG metrics WITHOUT dm_send_rate/skip_rate so a big
        # slice is dropped. Values are chosen small so reward stays
        # bounded, telemetry is the assertion target.
        shaper.compute_reward(
            platform="instagram",
            metrics={"views": 100, "saves": 5, "shares": 2, "reach": 500},
            channel_metrics={"total_followers": 1000, "posts_last_30d": 20},
        )

        warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING and "REWARD" in r.message
        ]
        assert warnings, (
            "Expected a WARNING when ≥15% of the reward weight vector is "
            "redistributed from missing metrics. Without this, IG-style "
            "silent 30% shifts stay invisible to operators."
        )
        msg = warnings[0].message
        assert "redistribution" in msg
        assert "scale=" in msg
        assert "missing metrics" in msg

    def test_small_drop_no_warning(self, shaper, caplog):
        """When all IG core metrics are present, redistribution
        stays under 15% and no WARNING fires — else prod logs would
        drown in redistribution noise from routine calls."""
        caplog.set_level(logging.WARNING, logger="genlab_core.learning.reward_shaper")

        # Provide EVERY IG metric the shaper expects. Values don't
        # matter — presence is what suppresses the warning.
        # 2026-07-17: added follower_gained to keep this test in sync
        # with BASE_WEIGHTS after Layer 4 foundational wire.
        shaper.compute_reward(
            platform="instagram",
            metrics={
                "views": 100,
                "saves": 5,
                "shares": 2,
                "reach": 500,
                "dm_send_rate": 0.01,
                "skip_rate": 0.02,
                "follower_gained": 3,
                "profile_visits": 10,
                "follows": 1,
                "comments": 3,
                "likes": 20,
                "watch_time_pct": 0.6,
            },
            channel_metrics={"total_followers": 1000, "posts_last_30d": 20},
        )

        warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING and "REWARD" in r.message
        ]
        assert not warnings, (
            f"Full-metric compute_reward should NOT emit REWARD warnings "
            f"— else prod logs will drown. Got: {[w.message for w in warnings]}"
        )


class TestBoundedness:
    """The redistribution telemetry must not change the reward math.
    Adding logging must NOT alter the reward value the bandit sees."""

    def test_reward_still_bounded_0_1(self, shaper):
        # Small metrics with SOME positive values, missing dm_send/skip
        r_partial = shaper.compute_reward(
            platform="instagram",
            metrics={"views": 100, "saves": 5, "shares": 2},
        )
        # Any positive weighted metric → float reward (not None)
        assert r_partial is not None
        assert 0.0 <= r_partial <= 1.0

        # 2026-08-11 (task A): IG is a slow-distribution platform. Empty
        # metrics (or all-zero weighted values) → None (premature-fetch
        # signal) rather than 0.0, so bandit posterior isn't polluted by
        # algorithm-delayed distribution. Pre-fix contract was `== 0.0`.
        r_empty = shaper.compute_reward(platform="instagram", metrics={})
        assert r_empty is None


class TestSourcePinRegression:
    """Guard the specific keywords we grep on so an Edit that removes
    the telemetry (e.g. reverting to the pre-Batch-B state) fails."""

    def test_source_contains_dropped_pct_computation(self):
        import inspect

        src = inspect.getsource(RewardShaper.compute_reward)
        assert "dropped_pct" in src, (
            "compute_reward must compute ``dropped_pct`` for the Blind Spot #2 "
            "telemetry. Removing this variable regresses the invisibility bug."
        )
        assert "dropped_keys" in src, (
            "compute_reward must enumerate ``dropped_keys`` so the WARNING "
            "log names WHICH metrics were dropped, not just how much."
        )
        assert "dropped_pct >= 0.15" in src, (
            "The 15% threshold gates the WARNING vs DEBUG split. Changing "
            "it should be a deliberate decision, not a silent Edit."
        )
