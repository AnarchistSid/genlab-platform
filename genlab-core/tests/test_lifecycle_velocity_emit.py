"""Pin: lifecycle_tracker.analyze_velocity wired into 48h reward emit.

The 2026-06-22 audit found ``intelligence/lifecycle_tracker.py``
shipped with `analyze_velocity()` but ZERO production callers. The
function classifies engagement velocity (viral / steady / flat /
bombing) by comparing 6h vs 48h reach — useful RCA signal that
was being computed nowhere.

This wire activates it: at the 48h window (which has both 6h + 48h
snapshots), augment the reward_window_closed episodic event payload
with velocity classification. Future RCA queries can join velocity
classification with feedback_category from operator_review events
without joining 3 tables — e.g. "what % of 'bombing'-classified
posts had operators flag 'weak_hook'?"

Fail-OPEN: if analyze_velocity errors or returns None (insufficient
snapshots), payload lacks velocity keys — readers tolerate missing
fields.

These pins guard:
- velocity data is added to the 48h emit when available
- velocity errors do NOT prevent the base emit
- source-level wire is structurally present
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


class TestLifecycleVelocityWired:
    def test_velocity_keys_in_payload_when_available(self):
        """Pin: when analyze_velocity returns a classification, the
        4 velocity_* keys appear in the emitted payload."""
        from datetime import UTC, datetime
        from unittest.mock import MagicMock

        from genlab_core.learning import episodic_memory, metric_collector
        from genlab_core.learning.pending_feedback_store import PendingFeedbackTask

        emitted = []

        def fake_record_event(**kwargs):
            emitted.append(kwargs)

        task = PendingFeedbackTask(
            task_id="t1",
            blueprint_id="bp1",
            content_id="bp1",
            platform="instagram",
            platform_post_id="ig_post_1",
            niche_id="gaming",
            published_at=datetime(2026, 6, 22, 0, 0, tzinfo=UTC),
            pending_windows=["48h"],
            collection_status="awaiting_48h",
            bandit_arm="highlight_play",
            content_type="highlight_play",
        )

        store = MagicMock()
        store.next_collection_window.return_value = "48h"

        velocity_payload = {
            "velocity": "bombing",
            "growth_rate": -0.5,
            "6h_reach": 100,
            "48h_reach": 50,
        }

        with patch(
            "genlab_core.learning.metric_collector.fetch_platform_metrics",
            return_value={"views": 50, "likes": 1},
        ):
            with patch(
                "genlab_core.learning.metric_collector.compute_reward",
                return_value=0.05,
            ):
                with patch(
                    "genlab_core.intelligence.lifecycle_tracker.analyze_velocity",
                    return_value=velocity_payload,
                ):
                    with patch.object(
                        episodic_memory, "record_event", side_effect=fake_record_event
                    ):
                        metric_collector.process_pending_task(
                            task_record=task,
                            store=store,
                            shaper=MagicMock(),
                        )

        # One event should fire
        reward_events = [e for e in emitted if e.get("event_type") == "reward_window_closed"]
        assert len(reward_events) >= 1, f"Expected reward_window_closed; got {emitted}"
        payload = reward_events[0]["payload"]
        # All 4 velocity_* keys present
        assert payload.get("velocity_class") == "bombing"
        assert payload.get("velocity_growth_rate") == -0.5
        assert payload.get("velocity_6h_reach") == 100
        assert payload.get("velocity_48h_reach") == 50

    def test_velocity_none_omits_keys(self):
        """Pin: analyze_velocity returning None (insufficient snapshots)
        → payload lacks velocity_* keys (NOT explicit nulls). Existing
        readers see the same shape they always saw."""
        from datetime import UTC, datetime
        from unittest.mock import MagicMock

        from genlab_core.learning import episodic_memory, metric_collector
        from genlab_core.learning.pending_feedback_store import PendingFeedbackTask

        emitted = []

        def fake_record_event(**kwargs):
            emitted.append(kwargs)

        task = PendingFeedbackTask(
            task_id="t1",
            blueprint_id="bp1",
            content_id="bp1",
            platform="instagram",
            platform_post_id="ig_post_2",
            niche_id="gaming",
            published_at=datetime(2026, 6, 22, 0, 0, tzinfo=UTC),
            pending_windows=["48h"],
            collection_status="awaiting_48h",
            bandit_arm="x",
            content_type="x",
        )

        store = MagicMock()
        store.next_collection_window.return_value = "48h"

        with patch(
            "genlab_core.learning.metric_collector.fetch_platform_metrics",
            return_value={"views": 50},
        ):
            with patch(
                "genlab_core.learning.metric_collector.compute_reward",
                return_value=0.05,
            ):
                with patch(
                    "genlab_core.intelligence.lifecycle_tracker.analyze_velocity",
                    return_value=None,  # insufficient snapshots
                ):
                    with patch.object(
                        episodic_memory, "record_event", side_effect=fake_record_event
                    ):
                        metric_collector.process_pending_task(
                            task_record=task,
                            store=store,
                            shaper=MagicMock(),
                        )

        reward_events = [e for e in emitted if e.get("event_type") == "reward_window_closed"]
        assert len(reward_events) >= 1
        payload = reward_events[0]["payload"]
        # The base keys are always present
        assert "platform" in payload
        assert "reward_48h" in payload
        # The velocity_* keys are NOT in the payload (not even as None)
        assert "velocity_class" not in payload
        assert "velocity_growth_rate" not in payload

    def test_velocity_exception_does_not_block_emit(self):
        """Pin: analyze_velocity raising MUST NOT prevent the
        reward_window_closed event from firing. Augmentation must
        never block the base signal."""
        from datetime import UTC, datetime
        from unittest.mock import MagicMock

        from genlab_core.learning import episodic_memory, metric_collector
        from genlab_core.learning.pending_feedback_store import PendingFeedbackTask

        emitted = []

        def fake_record_event(**kwargs):
            emitted.append(kwargs)

        task = PendingFeedbackTask(
            task_id="t1",
            blueprint_id="bp1",
            content_id="bp1",
            platform="instagram",
            platform_post_id="ig_post_3",
            niche_id="gaming",
            published_at=datetime(2026, 6, 22, 0, 0, tzinfo=UTC),
            pending_windows=["48h"],
            collection_status="awaiting_48h",
            bandit_arm="x",
            content_type="x",
        )

        store = MagicMock()
        store.next_collection_window.return_value = "48h"

        with patch(
            "genlab_core.learning.metric_collector.fetch_platform_metrics",
            return_value={"views": 50},
        ):
            with patch(
                "genlab_core.learning.metric_collector.compute_reward",
                return_value=0.05,
            ):
                with patch(
                    "genlab_core.intelligence.lifecycle_tracker.analyze_velocity",
                    side_effect=RuntimeError("DB down"),
                ):
                    with patch.object(
                        episodic_memory, "record_event", side_effect=fake_record_event
                    ):
                        # MUST NOT raise
                        result = metric_collector.process_pending_task(
                            task_record=task,
                            store=store,
                            shaper=MagicMock(),
                        )

        # Pipeline completed
        assert result is True
        # Base event still fired (without velocity keys)
        reward_events = [e for e in emitted if e.get("event_type") == "reward_window_closed"]
        assert len(reward_events) >= 1


class TestSourceWire:
    def test_metric_collector_source_imports_analyze_velocity(self):
        """Source-level pin: the wire is structurally present + uses
        analyze_velocity. Guards against accidental removal in
        future refactors."""
        src = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "genlab_core"
            / "learning"
            / "metric_collector.py"
        )
        content = src.read_text()
        assert (
            "from genlab_core.intelligence.lifecycle_tracker import analyze_velocity" in content
        ), (
            "metric_collector.py is missing the analyze_velocity import. "
            "Velocity classification is wired into the 48h reward emit."
        )
        # Payload-merge pattern must be present
        assert "**velocity_data" in content, (
            "metric_collector.py is missing the velocity_data spread in the 48h emit payload."
        )
