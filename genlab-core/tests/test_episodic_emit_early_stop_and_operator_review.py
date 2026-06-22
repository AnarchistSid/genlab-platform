"""Pin: episodic emit on early-stop branch + operator-review wire.

The 2026-06-22 autonomy audit found Loop 10 (episodic memory) had
only 1 event in 7d on prod despite 3 emit-call wires shipping in PRs
#451/#452. Root causes:

1. ``reward_window_closed`` was structurally unreachable on early-
   stop tasks. metric_collector.process_pending_task returned True
   from the 6h-floor branch BEFORE the 48h window's emit at line
   ~765 could fire. Posts that bombed at 6h (most of them) produced
   ZERO episodic signal — exactly the wrong dimension to lose data
   on (a post that bombed early is the MOST interesting RCA case).

2. ``operator_review`` was never emitted at all. The dashboard's
   _execute_review_action wrote to dashboard_events (operator-facing
   notifications) but never to episodic_events (learning-facing
   structured signal).

This module pins both wires. The early-stop emit must fire BEFORE
the early-stop return; the operator_review emit must fire on every
review action regardless of approve/reject/revise/skip.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch


class TestEarlyStopEpisodicEmit:
    """Pin: 6h early-stop branch emits reward_window_closed BEFORE
    returning, with early_stopped=True + views_6h + floor in payload."""

    def test_early_stop_emits_reward_window_closed_with_flag(self, monkeypatch):
        """Pin: when 6h views < niche floor, episodic emit fires with
        early_stopped=True + the diagnostic counts (views_6h, floor)
        so RCA can identify which posts hit which threshold."""
        from genlab_core.learning import metric_collector
        from genlab_core.learning.pending_feedback_store import PendingFeedbackTask

        emitted = []

        def fake_record_event(**kwargs):
            emitted.append(kwargs)

        # Build a task that will trigger 6h early-stop:
        # - window resolves to "6h"
        # - fetch_platform_metrics returns views below the gaming floor (30)
        task = PendingFeedbackTask(
            task_id="t1",
            blueprint_id="bp1",
            content_id="bp1",
            platform="instagram",
            platform_post_id="ig_post_1",
            niche_id="gaming",
            published_at=datetime(2026, 6, 22, 0, 0, tzinfo=UTC),
            pending_windows=["6h", "24h", "48h", "168h"],
            collection_status="awaiting_6h",
            bandit_arm="highlight_play",
            content_type="highlight_play",
        )

        store = MagicMock()
        store.next_collection_window.return_value = "6h"
        # store.update_window is called with (task, window, reward_48h=0.0)
        store.update_window = MagicMock()

        shaper = MagicMock()

        with patch(
            "genlab_core.learning.metric_collector.fetch_platform_metrics",
            return_value={"views": 10},  # below gaming floor=30
        ):
            with patch(
                "genlab_core.learning.episodic_memory.record_event",
                side_effect=fake_record_event,
            ):
                result = metric_collector.process_pending_task(
                    task_record=task,
                    store=store,
                    shaper=shaper,
                )

        assert result is True
        # Task was marked early-stopped
        assert task.collection_status == "early_stopped"
        # Exactly one reward_window_closed event must have fired with
        # the early-stop signature
        early_stop_events = [
            e
            for e in emitted
            if e.get("event_type") == "reward_window_closed"
            and e.get("payload", {}).get("early_stopped") is True
        ]
        assert len(early_stop_events) == 1, f"Expected 1 early-stop episodic event; got: {emitted}"
        payload = early_stop_events[0]["payload"]
        assert payload["reward_48h"] == 0.0
        assert payload["platform"] == "instagram"
        assert payload["views_6h"] == 10
        assert payload["floor"] == 30
        assert payload["bandit_arm"] == "highlight_play"
        # niche_id + blueprint_id are top-level on the event
        assert early_stop_events[0]["niche_id"] == "gaming"
        assert early_stop_events[0]["blueprint_id"] == "bp1"

    def test_early_stop_emit_failure_is_silent(self, monkeypatch):
        """Pin: if episodic record_event raises (DB outage etc.) the
        early-stop branch MUST STILL return True. Episodic emit MUST
        NEVER block the metric_collector pipeline."""
        from genlab_core.learning import metric_collector
        from genlab_core.learning.pending_feedback_store import PendingFeedbackTask

        task = PendingFeedbackTask(
            task_id="t1",
            blueprint_id="bp1",
            content_id="bp1",
            platform="instagram",
            platform_post_id="ig_post_2",
            niche_id="gaming",
            published_at=datetime(2026, 6, 22, 0, 0, tzinfo=UTC),
            pending_windows=["6h", "24h", "48h", "168h"],
            collection_status="awaiting_6h",
            bandit_arm="x",
            content_type="x",
        )

        store = MagicMock()
        store.next_collection_window.return_value = "6h"

        with patch(
            "genlab_core.learning.metric_collector.fetch_platform_metrics",
            return_value={"views": 5},
        ):
            with patch(
                "genlab_core.learning.episodic_memory.record_event",
                side_effect=RuntimeError("DB outage"),
            ):
                # MUST NOT raise
                result = metric_collector.process_pending_task(
                    task_record=task,
                    store=store,
                    shaper=MagicMock(),
                )

        # Early-stop still happened despite emit failure
        assert result is True
        assert task.collection_status == "early_stopped"


class TestEarlyStopEmitSourceCheck:
    """Source-level pin: the early-stop emit call is structurally
    PRESENT in metric_collector.py before the `return True`.

    This is a defense against accidental removal during future
    refactors — if someone tightens the early-stop branch without
    preserving the emit, the prod symptom (silent loss of bomb
    signal) is hard to detect from runtime tests alone (the emit
    failure is fail-open by design). Source-level pin = unmissable
    in the diff."""

    def test_metric_collector_source_has_early_stop_emit(self):
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "genlab_core"
            / "learning"
            / "metric_collector.py"
        )
        content = src.read_text()
        # Critical: the early-stop emit must be inside the
        # `if 0 < views_6h < floor:` branch
        assert "early-stop episodic emit failed" in content, (
            "metric_collector.py is missing the early-stop episodic "
            "emit failure log line. Did a refactor drop the emit?"
        )
        # Payload must include early_stopped=True + views_6h + floor
        assert '"early_stopped": True' in content
        assert '"views_6h": views_6h' in content
        assert '"floor": floor' in content


class TestOperatorReviewEmitSourceCheck:
    """Source-level pin: dashboard/server/review_server.py emits
    operator_review after every review action.

    The dashboard test surface is heavier than a single-import test,
    so we pin via source-level grep until a focused integration test
    can be added in a follow-up. Pattern matches the
    'audit-fix tests can be source-level' precedent from earlier
    autonomy PRs (e.g. test_poller_audit_messages_present_in_source)."""

    def test_review_server_source_has_operator_review_emit(self):
        from pathlib import Path

        src = Path(__file__).resolve().parents[2] / "dashboard" / "server" / "review_server.py"
        content = src.read_text()
        # The emit call site
        assert "EVENT_OPERATOR_REVIEW" in content, (
            "review_server.py is missing the EVENT_OPERATOR_REVIEW emit. "
            "operator_review episodic signal is unwired."
        )
        # Fail-open guard (must never block the review)
        assert "operator_review emit skipped (non-fatal)" in content, (
            "review_server.py is missing the fail-open guard around the "
            "operator_review emit. Episodic emits MUST NEVER block reviews."
        )
        # Payload must include action + feedback_issue + dwell time
        assert '"action": action' in content
        assert '"feedback_issue": feedback_issue or ""' in content
        assert '"review_duration_ms": review_duration_ms' in content
