"""Pin operator-action emission to ``dashboard_events``.

Autonomy gap doc (Week 2): operator activity (approve/reject/revise/
skip) was invisible to Mission Control because ``_execute_review_action``
wrote to the backlog + calibration log but never emitted to
``dashboard_events``. The table only carried system events
(pipeline runs, ingestion cycles).

These pins lock in the operator-event emission so:

  1. Every review action produces a granular ``review_{action}``
     event (not a single bucket — dashboards can filter rejections
     separately from approvals).
  2. The blueprint id, niche id, and operator notes are carried in
     the event so Mission Control's timeline links back to the
     blueprint and surfaces the operator's reasoning.
  3. A failure of the event-push MUST NEVER undo the backlog or
     calibration writes that already succeeded (best-effort,
     fail-open — same shape as the calibration writer it sits
     beside).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    """The endpoints exercised here gate on auth; bypass for unit-tests."""
    from server import review_server as review_server_module

    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


def _build_fake_sync_client(get_returns: dict | None = None) -> MagicMock:
    """Build a SyncBacklogClient mock matching the shape
    ``_execute_review_action`` calls into."""
    bp = MagicMock()
    bp.get.return_value = get_returns or {
        "id": "10001",
        "fields": {"niche_id": "gaming"},
    }
    bp.update.return_value = None
    client = MagicMock()
    client.blueprints = bp
    return client


class TestReviewActionEmitsDashboardEvent:
    def test_approve_emits_review_approved_event(self, monkeypatch):
        """Approve action emits ``review_approved`` with blueprint id +
        niche id + the canonical title."""
        from server.review_server import _execute_review_action

        captured_events: list[dict] = []

        def _fake_push(event_type, title, body="", **kw):
            captured_events.append(
                {
                    "event_type": event_type,
                    "title": title,
                    "body": body,
                    **kw,
                }
            )

        monkeypatch.setattr(
            "genlab_core.observability.dashboard_events.push_event",
            _fake_push,
        )

        fake_client = _build_fake_sync_client()
        with patch("server.core.graph_sync.get_sync_client", return_value=fake_client):
            _execute_review_action("10001", "approved", notes="looks good")

        assert len(captured_events) == 1
        ev = captured_events[0]
        assert ev["event_type"] == "review_approved"
        assert ev["title"] == "Blueprint approved"
        assert "looks good" in ev["body"]
        assert ev["entity_id"] == "10001"
        assert ev["entity_type"] == "blueprint"
        assert ev["niche_id"] == "gaming"

    def test_reject_emits_review_rejected_with_issue(self, monkeypatch):
        """Reject action emits ``review_rejected`` and carries the
        feedback_issue tag in the body so dashboards can group
        rejections by reason without re-parsing the backlog."""
        from server.review_server import _execute_review_action

        captured_events: list[dict] = []
        monkeypatch.setattr(
            "genlab_core.observability.dashboard_events.push_event",
            lambda **kw: captured_events.append(kw),
        )

        fake_client = _build_fake_sync_client()
        with patch("server.core.graph_sync.get_sync_client", return_value=fake_client):
            _execute_review_action(
                "10002",
                "rejected",
                feedback_issue="hook_too_generic",
                feedback_notes="reads as boilerplate",
            )

        assert len(captured_events) == 1
        ev = captured_events[0]
        assert ev["event_type"] == "review_rejected"
        assert ev["title"] == "Blueprint rejected"
        assert "hook_too_generic" in ev["body"]
        assert ev["entity_id"] == "10002"

    def test_revise_skipped_use_action_specific_titles(self, monkeypatch):
        """All four actions have distinct event_type + title so the
        Mission Control timeline can render each with its own icon /
        treatment without server-side translation."""
        from server.review_server import _execute_review_action

        captured: list[dict] = []
        monkeypatch.setattr(
            "genlab_core.observability.dashboard_events.push_event",
            lambda **kw: captured.append(kw),
        )

        fake_client = _build_fake_sync_client()
        with patch("server.core.graph_sync.get_sync_client", return_value=fake_client):
            _execute_review_action("10003", "revised", feedback_issue="needs_polish")
            _execute_review_action("10004", "skipped")

        assert {ev["event_type"] for ev in captured} == {
            "review_revised",
            "review_skipped",
        }
        titles = {ev["title"] for ev in captured}
        assert "Blueprint sent for revision" in titles
        assert "Blueprint skipped" in titles

    def test_push_event_failure_does_not_block_review(self, monkeypatch):
        """The whole point of the fail-open guard: if push_event raises,
        the backlog write + calibration write must still have succeeded
        and the caller must still get its update_fields dict back."""
        from server.review_server import _execute_review_action

        def _raising_push(**kw):
            raise RuntimeError("simulated dashboard-events outage")

        monkeypatch.setattr(
            "genlab_core.observability.dashboard_events.push_event",
            _raising_push,
        )

        fake_client = _build_fake_sync_client()
        with patch("server.core.graph_sync.get_sync_client", return_value=fake_client):
            # Must not raise — the review action completes despite
            # the event-push failure.
            result = _execute_review_action("10005", "approved")

        assert result["action_taken"] == "approved"
        assert "reviewed_at" in result
        # Backlog update was called once (the real write) — the test
        # fixture's bp.update tracks .called.
        assert fake_client.blueprints.update.called


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
