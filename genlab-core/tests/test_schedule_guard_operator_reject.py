"""Pin the operator-intent reject bypass in the schedule guard.

Per the 2026-06-14 operator-reported issue: every Reject click in the
dashboard's Content Review surfaced ``Cannot move scheduled blueprint
... would discard its queued slot``, including for unapproved blueprints
that the operator had never committed to a slot. Root cause: PR #191
pre-sets ``scheduled_for`` on every new VISUAL_READY blueprint as a
hint, so the guard saw all of them as "scheduled" — indistinguishable
from operator-committed schedules.

The fix is an atomic-write contract: status→ARCHIVED + scheduled_for=None
in the SAME update dict signals "operator intentionally unscheduling +
archiving" and the guard allows it. Partial writes (only one of the two)
remain protected.

These pins lock in the contract:
  1. Atomic reject (both writes together) → ALLOWED
  2. Status-only archive → BLOCKED (the original safety case)
  3. scheduled_for-only clear → BLOCKED (the original safety case)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from genlab_core.http.backlog_client import (
    ScheduledPostProtectionError,
    ScheduleGuardedProxy,
)


def _make_proxy_with_record(
    scheduled_for: str = "2026-06-14T06:30:00+00:00", status: str = "VISUAL_READY"
) -> tuple[ScheduleGuardedProxy, MagicMock]:
    """Build a ScheduleGuardedProxy wrapping a mock that returns a
    blueprint with the given scheduled state."""
    inner = MagicMock()
    inner.get.return_value = {
        "id": "rec_test",
        "fields": {"status": status, "scheduled_for": scheduled_for},
    }
    return ScheduleGuardedProxy(inner), inner


class TestOperatorRejectBypass:
    def test_atomic_archive_plus_clear_schedule_allowed(self):
        """Operator-intent reject: both writes in the same dict →
        guard recognizes the atomic action and allows it. This is the
        pattern dashboard's _execute_review_action sends on Reject."""
        proxy, inner = _make_proxy_with_record()
        proxy.update(
            "rec_test",
            {
                "status": "ARCHIVED",
                "scheduled_for": None,
                "feedback_issue": "rejected_in_review",
            },
        )
        # Inner proxy's update was called (i.e. the write went through)
        inner.update.assert_called_once()

    def test_atomic_archive_with_empty_string_schedule_also_allowed(self):
        """The ``_is_empty`` helper treats ``""``, ``None``, and ``"null"``
        as cleared. The bypass must work for any of these shapes the
        BacklogClient backends might use."""
        for empty_value in (None, "", "null"):
            proxy, inner = _make_proxy_with_record()
            proxy.update(
                "rec_t2",
                {
                    "status": "ARCHIVED",
                    "scheduled_for": empty_value,
                },
            )
            inner.update.assert_called_once()


class TestPartialWriteStillProtected:
    def test_status_only_archive_still_blocked(self):
        """Status-only ARCHIVED without clearing scheduled_for → still
        blocked. This is the original safety case the guard was built
        for: an accidental archive that would leave an orphaned slot."""
        proxy, _ = _make_proxy_with_record()
        with pytest.raises(ScheduledPostProtectionError) as exc:
            proxy.update("rec_t3", {"status": "ARCHIVED"})
        # The error message guides the caller to the fix
        assert "scheduled_for=None" in str(exc.value), (
            "error message must suggest the atomic-write pattern; "
            "without it, a future caller hits this and has no clue "
            "how to recover"
        )

    def test_clearing_schedule_only_still_blocked(self):
        """Clearing scheduled_for without also archiving → still blocked.
        The original safety case: removing the schedule but leaving
        the post at VISUAL_READY would orphan it (no longer queued,
        but also not rejected — a dead row in the workflow)."""
        proxy, _ = _make_proxy_with_record()
        with pytest.raises(ScheduledPostProtectionError):
            proxy.update("rec_t4", {"scheduled_for": None})


class TestUnscheduledBlueprintsUnchanged:
    def test_no_scheduled_for_no_guard(self):
        """The guard early-returns when scheduled_for is empty —
        nothing to protect, all writes pass through. Pin this so the
        bypass change doesn't accidentally tighten unscheduled writes."""
        proxy, inner = _make_proxy_with_record(scheduled_for="")
        proxy.update("rec_t5", {"status": "ARCHIVED"})
        inner.update.assert_called_once()

    def test_publish_progress_still_allowed(self):
        """Forward publishing transitions (VISUAL_READY → PUBLISHING,
        PUBLISHING → PUBLISHED) on a scheduled post are NOT destructive
        — the post is fulfilling its slot, not discarding it. Must
        still be allowed."""
        proxy, inner = _make_proxy_with_record()
        proxy.update("rec_t6", {"status": "PUBLISHING"})
        inner.update.assert_called_once()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
