"""Pin the transformation multi-arm reward router (PR 5).

Covers:
  1. Empty arm_ids_by_dimension → 0 updates, no bandit_updater calls
  2. N dimensions → N bandit_updater calls with correct args
  3. compute_dimension_reward (scalar-only in PR 5, per-dim in PR 6)
  4. Fail-open per arm — one failure doesn't abort the rest
  5. PendingFeedbackTask round-trip includes arm_ids_by_dimension
  6. PendingFeedbackTask serializes as JSON string for SharePoint compat
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from genlab_core.learning.pending_feedback_task import PendingFeedbackTask
from genlab_core.learning.transformation_reward_router import (
    compute_dimension_reward,
    route_transformation_rewards,
)


class TestRouteEmpty:
    def test_empty_dict_returns_zero(self) -> None:
        updater = MagicMock()
        n = route_transformation_rewards(
            niche_id="gaming",
            platform="instagram",
            arm_ids_by_dimension={},
            scalar_reward=0.5,
            bandit_updater=updater,
        )
        assert n == 0
        updater.assert_not_called()

    def test_none_arm_id_skipped(self) -> None:
        """Malformed dict with an empty arm_id string still returns 0
        + doesn't call updater with empty arg."""
        updater = MagicMock()
        n = route_transformation_rewards(
            niche_id="gaming",
            platform="instagram",
            arm_ids_by_dimension={"music_mood": ""},
            scalar_reward=0.5,
            bandit_updater=updater,
        )
        assert n == 0
        updater.assert_not_called()


class TestRouteBasic:
    def test_two_dimensions_two_updates(self) -> None:
        updater = MagicMock()
        arm_ids = {
            "music_mood": "transform__music_mood__cinematic",
            "caption_style": "transform__caption_style__word_by_word",
        }
        n = route_transformation_rewards(
            niche_id="gaming",
            platform="instagram",
            arm_ids_by_dimension=arm_ids,
            scalar_reward=0.7,
            bandit_updater=updater,
            bandit_context={"weekday": 0.5, "hour": 0.8},
        )
        assert n == 2
        assert updater.call_count == 2

    def test_updater_called_with_correct_args(self) -> None:
        updater = MagicMock()
        arm_ids = {"music_mood": "transform__music_mood__cinematic"}
        route_transformation_rewards(
            niche_id="movies",
            platform="youtube",
            arm_ids_by_dimension=arm_ids,
            scalar_reward=0.62,
            bandit_updater=updater,
            bandit_context={"x": 1},
        )
        updater.assert_called_once_with(
            "movies",
            "transform__music_mood__cinematic",
            "youtube",
            0.62,
            {"x": 1},
        )

    def test_returns_count_of_updates(self) -> None:
        updater = MagicMock()
        arm_ids = {
            "music_mood": "arm1",
            "caption_style": "arm2",
            "hook_framing": "arm3",
            "pan_zoom": "arm4",
            "outro_cta": "arm5",
        }
        n = route_transformation_rewards(
            niche_id="gaming",
            platform="instagram",
            arm_ids_by_dimension=arm_ids,
            scalar_reward=0.5,
            bandit_updater=updater,
        )
        assert n == 5


class TestRouteFailOpen:
    def test_single_failure_doesnt_stop_others(self) -> None:
        """One failing arm update MUST NOT unwind the rest — matches
        the per-platform-split fail-open policy."""
        call_count = 0

        def flaky_updater(niche, arm, platform, reward, ctx):
            nonlocal call_count
            call_count += 1
            if arm == "arm_bad":
                raise RuntimeError("simulated DB blip")

        arm_ids = {
            "music_mood": "arm_a",
            "caption_style": "arm_bad",  # This one fails
            "hook_framing": "arm_c",
        }
        n = route_transformation_rewards(
            niche_id="gaming",
            platform="instagram",
            arm_ids_by_dimension=arm_ids,
            scalar_reward=0.5,
            bandit_updater=flaky_updater,
        )
        # 3 attempts, 1 failed → 2 succeeded
        assert call_count == 3
        assert n == 2

    def test_all_fail_returns_zero(self) -> None:
        def always_fails(*args, **kwargs):
            raise RuntimeError("always fails")

        arm_ids = {"music_mood": "arm_a", "caption_style": "arm_b"}
        n = route_transformation_rewards(
            niche_id="gaming",
            platform="instagram",
            arm_ids_by_dimension=arm_ids,
            scalar_reward=0.5,
            bandit_updater=always_fails,
        )
        assert n == 0


class TestComputeDimensionReward:
    def test_scalar_only_pr5(self) -> None:
        """PR 5 posture — returns scalar_reward unchanged for every dim.
        This test pins the scalar-only behavior; the assertion will
        need updating when PR 6 branches on dimension + metrics."""
        for dim in [
            "music_mood",
            "caption_style",
            "hook_framing",
            "pan_zoom",
            "outro_cta",
        ]:
            assert compute_dimension_reward(dim, 0.42) == 0.42

    def test_metrics_arg_accepted_but_ignored_pr5(self) -> None:
        """PR 5 accepts the metrics kwarg for API stability. PR 6 will
        start reading it. Pin that scalar is preserved regardless."""
        assert compute_dimension_reward(
            "music_mood", 0.5, metrics={"plays_past_15s": 100}
        ) == 0.5


class TestPendingFeedbackTaskRoundTrip:
    """arm_ids_by_dimension must survive the task → dict → task loop."""

    def _make_task(self, arm_ids: dict[str, str]) -> PendingFeedbackTask:
        return PendingFeedbackTask(
            content_id="c123",
            platform="instagram",
            niche_id="gaming",
            published_at=datetime(2026, 7, 5, 12, 0, tzinfo=UTC),
            platform_post_id="ig_abc",
            bandit_arm="gameplay_clip",
            arm_ids_by_dimension=arm_ids,
        )

    def test_empty_dict_serializes_absent(self) -> None:
        """Empty arm_ids_by_dimension MUST NOT appear in the payload —
        preserves backward-compat with rows that predate the column
        (they get the SQL DEFAULT '{}' instead of an explicit write)."""
        task = self._make_task({})
        payload = task.to_sharepoint_fields()
        assert "arm_ids_by_dimension" not in payload

    def test_populated_dict_serializes_as_json_string(self) -> None:
        arms = {
            "music_mood": "transform__music_mood__cinematic",
            "caption_style": "transform__caption_style__word_by_word",
        }
        task = self._make_task(arms)
        payload = task.to_sharepoint_fields()
        assert "arm_ids_by_dimension" in payload
        # Serialized as JSON string (bandit_context pattern).
        parsed = json.loads(payload["arm_ids_by_dimension"])
        assert parsed == arms

    def test_default_field_is_empty_dict_not_none(self) -> None:
        """Field default must be {} so ``if task.arm_ids_by_dimension``
        works cleanly — None would raise on iteration."""
        task = PendingFeedbackTask(
            content_id="c123",
            platform="instagram",
            published_at=datetime(2026, 7, 5, 12, 0, tzinfo=UTC),
            platform_post_id="ig_abc",
        )
        assert task.arm_ids_by_dimension == {}
        assert not task.arm_ids_by_dimension


class TestPendingFeedbackStoreRoundTrip:
    """The store's _from_sharepoint_item parses arm_ids from BOTH
    JSONB dict (Postgres auto-parses) AND JSON string (SharePoint)."""

    def test_postgres_dict_shape_parses(self) -> None:
        from genlab_core.learning.pending_feedback_store import (
            PendingFeedbackStore,
        )

        item = {
            "fields": {
                "post_id": "ig_abc",
                "platform": "instagram",
                "niche_id": "gaming",
                "publish_time": "2026-07-05T12:00:00+00:00",
                "collection_status": "awaiting_6h",
                "arm_ids_by_dimension": {  # Postgres-shape (already dict)
                    "music_mood": "transform__music_mood__cinematic",
                },
            }
        }
        task = PendingFeedbackStore._from_sharepoint_item(item)
        assert task.arm_ids_by_dimension == {
            "music_mood": "transform__music_mood__cinematic"
        }

    def test_json_string_shape_parses(self) -> None:
        """SharePoint stores JSONB as JSON string — legacy compat."""
        from genlab_core.learning.pending_feedback_store import (
            PendingFeedbackStore,
        )

        item = {
            "fields": {
                "post_id": "ig_abc",
                "platform": "instagram",
                "niche_id": "gaming",
                "publish_time": "2026-07-05T12:00:00+00:00",
                "collection_status": "awaiting_6h",
                "arm_ids_by_dimension": json.dumps(
                    {"music_mood": "transform__music_mood__cinematic"}
                ),
            }
        }
        task = PendingFeedbackStore._from_sharepoint_item(item)
        assert task.arm_ids_by_dimension == {
            "music_mood": "transform__music_mood__cinematic"
        }

    def test_missing_column_gives_empty_dict(self) -> None:
        """Pre-sprint publishes have no arm_ids_by_dimension in DB.
        Store must default to empty dict, not None."""
        from genlab_core.learning.pending_feedback_store import (
            PendingFeedbackStore,
        )

        item = {
            "fields": {
                "post_id": "ig_abc",
                "platform": "instagram",
                "niche_id": "gaming",
                "publish_time": "2026-07-05T12:00:00+00:00",
                "collection_status": "awaiting_6h",
            }
        }
        task = PendingFeedbackStore._from_sharepoint_item(item)
        assert task.arm_ids_by_dimension == {}

    def test_malformed_json_string_gives_empty_dict(self) -> None:
        """Corrupted JSON in DB shouldn't crash the store — degrade to
        empty (router will just skip this reel's multi-arm attribution)."""
        from genlab_core.learning.pending_feedback_store import (
            PendingFeedbackStore,
        )

        item = {
            "fields": {
                "post_id": "ig_abc",
                "platform": "instagram",
                "niche_id": "gaming",
                "publish_time": "2026-07-05T12:00:00+00:00",
                "collection_status": "awaiting_6h",
                "arm_ids_by_dimension": "not{valid:json}",
            }
        }
        task = PendingFeedbackStore._from_sharepoint_item(item)
        assert task.arm_ids_by_dimension == {}


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
