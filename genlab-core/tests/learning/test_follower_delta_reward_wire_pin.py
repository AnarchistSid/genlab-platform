"""Pin the 2026-07-17 follower_gained reward-signal wire.

## What broke pre-fix

`reward_shaper.BASE_WEIGHTS["tiktok"]` declared `follower_gained: 0.20`
weight since 2026-05, and `youtube` declared `subscriber_gained: 0.2`.
BUT the metric collection chain NEVER populated these fields.

Grep confirmation from audit round 4:
    /reward_shaper.py:106:  "follower_gained": 0.20  (declared)
    /reward_shaper.py:154:  "subscriber_gained": {"youtube": 2}  (target)
    Everywhere else:  ZERO writes to metrics["follower_gained"]
                      ZERO writes to metrics["subscriber_gained"]

Effect: the bandit optimized purely for engagement (views, saves,
shares, watch time). A hook that got high engagement but drove ZERO
new follows scored EXACTLY the same as a hook with equal engagement
that grew the audience by 50 followers.

For the strategic target of 100K followers per channel, this is
catastrophic — the agent wasn't learning what drives follower growth
because the reward signal never included follower growth.

## Fix contract (this test locks it)

1. `follower_delta.get_follower_delta` reads audience_snapshots deltas
2. `follower_delta.augment_metrics_with_follower_delta` writes the
   platform-appropriate key (`subscriber_gained` for YT, `follower_gained`
   for IG/FB/Threads) into a metrics dict
3. `metric_collector.fetch_platform_metrics` calls the augment at
   window=='168h' (168h = 7d, when follower growth becomes attributable)
4. reward_shaper.BASE_WEIGHTS entries exist for instagram, facebook,
   threads (were missing before) with `follower_gained` weight
5. METRIC_TARGETS has `follower_gained` entries for early-stage
   scaling (2-5 per post window)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def test_augment_writes_subscriber_gained_for_youtube() -> None:
    from unittest.mock import patch

    from genlab_core.learning.metrics.follower_delta import (
        augment_metrics_with_follower_delta,
    )

    with patch(
        "genlab_core.learning.metrics.follower_delta.get_follower_delta",
        return_value=7,
    ):
        metrics = {"views": 100}
        augment_metrics_with_follower_delta(
            metrics, "ai_creators", "youtube", datetime.now(UTC),
        )
    assert metrics.get("subscriber_gained") == 7, (
        "YouTube augment must write 'subscriber_gained' (matches "
        "reward_shaper.BASE_WEIGHTS key for YouTube)"
    )
    assert "follower_gained" not in metrics, (
        "YouTube uses 'subscriber_gained' not 'follower_gained' key"
    )


def test_augment_writes_follower_gained_for_instagram() -> None:
    from unittest.mock import patch

    from genlab_core.learning.metrics.follower_delta import (
        augment_metrics_with_follower_delta,
    )

    with patch(
        "genlab_core.learning.metrics.follower_delta.get_follower_delta",
        return_value=3,
    ):
        metrics = {"views": 100}
        augment_metrics_with_follower_delta(
            metrics, "gaming", "instagram", datetime.now(UTC),
        )
    assert metrics.get("follower_gained") == 3


def test_augment_writes_follower_gained_for_facebook_and_threads() -> None:
    from unittest.mock import patch

    from genlab_core.learning.metrics.follower_delta import (
        augment_metrics_with_follower_delta,
    )

    for platform in ("facebook", "threads"):
        with patch(
            "genlab_core.learning.metrics.follower_delta.get_follower_delta",
            return_value=5,
        ):
            metrics: dict = {}
            augment_metrics_with_follower_delta(
                metrics, "movies", platform, datetime.now(UTC),
            )
        assert metrics.get("follower_gained") == 5, (
            f"{platform} augment must write 'follower_gained'"
        )


def test_augment_clamps_negative_delta_to_zero() -> None:
    """Follower loss shouldn't NEGATIVELY score arms — losses tend
    to come from causes unrelated to any single post (spam-flag,
    algo penalty, mass-unfollow event). Zero = no growth attributed."""
    from unittest.mock import patch

    from genlab_core.learning.metrics.follower_delta import (
        augment_metrics_with_follower_delta,
    )

    with patch(
        "genlab_core.learning.metrics.follower_delta.get_follower_delta",
        return_value=-15,
    ):
        metrics: dict = {}
        augment_metrics_with_follower_delta(
            metrics, "sports", "instagram", datetime.now(UTC),
        )
    assert metrics.get("follower_gained") == 0, (
        "Negative deltas must clamp to 0 to avoid mis-attributing "
        "losses to specific bandit arms"
    )


def test_augment_omits_key_when_delta_is_none() -> None:
    """Missing snapshot data must leave the metric ABSENT (not zero) so
    RewardShaper redistributes the weight instead of pinning to fake zero
    (same reward-shape discipline as the other metrics fetchers)."""
    from unittest.mock import patch

    from genlab_core.learning.metrics.follower_delta import (
        augment_metrics_with_follower_delta,
    )

    with patch(
        "genlab_core.learning.metrics.follower_delta.get_follower_delta",
        return_value=None,
    ):
        metrics = {"views": 100}
        augment_metrics_with_follower_delta(
            metrics, "anime", "instagram", datetime.now(UTC),
        )
    assert "follower_gained" not in metrics, (
        "None delta must NOT insert the key — RewardShaper must "
        "redistribute the weight, not pin to a fake zero"
    )


def test_augment_no_op_when_publish_time_none() -> None:
    """Callers with unknown publish_time (e.g. legacy posts before we
    tracked it) get a pass-through — never crash the metric collection."""
    from genlab_core.learning.metrics.follower_delta import (
        augment_metrics_with_follower_delta,
    )

    metrics = {"views": 100}
    result = augment_metrics_with_follower_delta(
        metrics, "gaming", "instagram", None,
    )
    assert result is metrics
    assert "follower_gained" not in result


def test_reward_shaper_has_follower_gained_on_ig_fb_threads() -> None:
    """Pin the BASE_WEIGHTS additions — this is the 'never regress' pin.
    IG/FB/Threads had ZERO follower-growth signal before 2026-07-17."""
    from genlab_core.learning.reward_shaper import BASE_WEIGHTS

    for platform in ("instagram", "facebook", "threads"):
        assert "follower_gained" in BASE_WEIGHTS[platform], (
            f"{platform} MUST have follower_gained in BASE_WEIGHTS. "
            "If removing, prove that follower growth on that platform "
            "should NOT drive bandit optimization."
        )
        assert BASE_WEIGHTS[platform]["follower_gained"] > 0, (
            f"{platform} follower_gained weight must be positive"
        )


def test_reward_shaper_targets_include_early_stage_scaling() -> None:
    """Targets must be low (2-10 followers per post window) for early-
    stage channels — otherwise the normalized reward is ~0 and the
    bandit never sees the growth signal."""
    from genlab_core.learning.reward_shaper import _METRIC_TARGETS

    targets = _METRIC_TARGETS["follower_gained"]
    for platform in ("instagram", "facebook", "threads"):
        assert platform in targets, (
            f"follower_gained target missing for {platform}"
        )
        assert 1 <= targets[platform] <= 10, (
            f"{platform} target {targets[platform]} outside early-stage "
            "band 1-10. Higher targets produce near-zero normalized "
            "rewards for new channels — the exact issue Break 4 fixed."
        )
