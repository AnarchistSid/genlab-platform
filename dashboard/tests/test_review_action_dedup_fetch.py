"""Pin: ``_execute_review_action`` fetches the blueprint EXACTLY ONCE.

Perf fix (2026-06-21): the calibration block at the bottom of
``_execute_review_action`` used to re-fetch the blueprint with a
"Simpler to reload" comment, even though the approve branch above
already had it loaded for the niche-id lookup. That meant every
operator click on Focus Review burned **two** SharePoint round-trips
(~150-300ms total) on the hot review path instead of one.

The fix hoists the fetch above the action-branching so both the
slot-calc and calibration block reuse the same record. These pins
verify:

  1. Happy path: ``client.blueprints.get(record_id)`` fires exactly
     once per ``_execute_review_action`` call (regression guard against
     anyone re-introducing the second fetch in the calibration block).
  2. Unhappy path: if the hoisted fetch raises, the calibration block
     falls back to its own fetch — prior behavior is preserved so the
     calibration writer never silently loses a row.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    """``_execute_review_action`` gates on auth; bypass for unit-tests."""
    from server import review_server as review_server_module

    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


def _fake_client(get_side_effect=None, get_returns=None) -> MagicMock:
    """Build a SyncBacklogClient mock matching the shape
    ``_execute_review_action`` calls into."""
    bp = MagicMock()
    if get_side_effect is not None:
        bp.get.side_effect = get_side_effect
    else:
        bp.get.return_value = get_returns or {
            "id": "10001",
            "fields": {"niche_id": "gaming"},
        }
    bp.update.return_value = None
    client = MagicMock()
    client.blueprints = bp
    return client


class TestSingleBlueprintFetchPerReviewAction:
    """Regression: exactly ONE ``blueprints.get`` call per review action."""

    def test_approve_fetches_blueprint_once_not_twice(self, monkeypatch):
        """Approve action: was 2 fetches (niche_id lookup + calibration
        re-fetch), is now 1 (hoisted fetch reused by both)."""
        from server.review_server import _execute_review_action

        # Silence the dashboard_events emitter (orthogonal concern).
        monkeypatch.setattr(
            "genlab_core.observability.dashboard_events.push_event",
            lambda *a, **k: None,
        )
        # Silence the calibration logger Postgres write (we only care
        # about the SharePoint fetch count here).
        monkeypatch.setattr(
            "genlab_core.scheduling.calibration_logger.log",
            lambda **k: None,
        )
        # Silence slot calc — it does its own SharePoint queries and
        # we'd be counting those too, defeating the test.
        monkeypatch.setattr(
            "server.core.publishing_queue._next_available_slot",
            lambda **k: None,
        )

        fake = _fake_client(
            get_returns={
                "id": "10001",
                "fields": {
                    "niche_id": "gaming",
                    "composite_score": 0.7,
                    "virality_score": 0.5,
                },
            }
        )
        with patch("server.core.graph_sync.get_sync_client", return_value=fake):
            _execute_review_action("10001", "approved", notes="ok")

        # The pin: blueprints.get is called EXACTLY ONCE per review action.
        # Before the perf fix this was 2; if it ever climbs again, this
        # test fails loudly.
        assert fake.blueprints.get.call_count == 1, (
            f"Expected 1 blueprints.get call on approve, got "
            f"{fake.blueprints.get.call_count}. Did someone re-introduce "
            f"the calibration-block re-fetch?"
        )
        # Sanity: the fetch was for the right record id.
        fake.blueprints.get.assert_called_with("10001")

    def test_reject_fetches_blueprint_once(self, monkeypatch):
        """Reject path also benefits: previously did 1 fetch (calibration
        only), now also 1. Pins that the perf fix didn't accidentally
        regress non-approve actions by adding a fetch they didn't need."""
        from server.review_server import _execute_review_action

        monkeypatch.setattr(
            "genlab_core.observability.dashboard_events.push_event",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(
            "genlab_core.scheduling.calibration_logger.log",
            lambda **k: None,
        )

        fake = _fake_client()
        with patch("server.core.graph_sync.get_sync_client", return_value=fake):
            _execute_review_action("10002", "rejected", feedback_issue="not_relevant")

        assert fake.blueprints.get.call_count == 1, (
            f"Expected 1 blueprints.get call on reject, got {fake.blueprints.get.call_count}."
        )


class TestUnhappyPathFallback:
    """If the hoisted fetch fails, calibration block re-fetches (fallback)."""

    def test_first_fetch_fails_calibration_retries(self, monkeypatch):
        """When the up-front fetch raises, the calibration block falls
        back to its own ``client.blueprints.get(record_id)`` — preserves
        prior calibration-coverage on transient SharePoint hiccups."""
        from server.review_server import _execute_review_action

        monkeypatch.setattr(
            "genlab_core.observability.dashboard_events.push_event",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(
            "genlab_core.scheduling.calibration_logger.log",
            lambda **k: None,
        )

        # First call raises (simulating transient SharePoint failure),
        # second call (the fallback in the calibration block) returns a
        # valid blueprint.
        first_call_raised = {"flag": False}

        def _get_with_one_failure(record_id):
            if not first_call_raised["flag"]:
                first_call_raised["flag"] = True
                raise RuntimeError("transient SharePoint failure")
            return {"id": record_id, "fields": {"niche_id": "movies"}}

        fake = _fake_client(get_side_effect=_get_with_one_failure)
        with patch("server.core.graph_sync.get_sync_client", return_value=fake):
            # Use "skipped" — no slot calc, simpler call path.
            _execute_review_action("10003", "skipped")

        # 2 calls: 1 hoisted (failed) + 1 calibration fallback (succeeded).
        # This confirms the fallback in the calibration block works.
        assert fake.blueprints.get.call_count == 2, (
            f"Expected fallback re-fetch when hoisted fetch fails; got "
            f"{fake.blueprints.get.call_count} calls total."
        )
