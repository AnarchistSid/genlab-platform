#!/usr/bin/env python3
"""Tests for ScheduleGuardedProxy — protects scheduled posts from cleanup.

These are pure unit tests with no external dependencies.
The underlying GraphTableProxy is mocked to avoid API calls.
"""

from unittest.mock import MagicMock

import pytest
from genlab_core.http.backlog_client import (
    ScheduledPostProtectionError,
    ScheduleGuardedProxy,
    allow_scheduled_updates,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _make_proxy(get_return=None):
    """Create a mock GraphTableProxy with configurable .get() return."""
    proxy = MagicMock()
    proxy.get.return_value = get_return or {"id": "1", "fields": {}}
    proxy.update.return_value = {"id": "1", "fields": {}}
    proxy.delete.return_value = None
    proxy.batch_update.return_value = []
    return proxy


def _scheduled_record(
    rec_id="42",
    status="VISUAL_READY",
    scheduled_for="2026-03-10T10:00:00",
    visual_paths='["/tmp/video.mp4"]',
):
    """Return a record dict that looks scheduled."""
    return {
        "id": rec_id,
        "fields": {
            "status": status,
            "scheduled_for": scheduled_for,
            "visual_paths": visual_paths,
            "candidate_id": "abc123",
        },
    }


def _unscheduled_record(rec_id="99", status="DRAFTED"):
    """Return a record dict with no schedule date."""
    return {
        "id": rec_id,
        "fields": {
            "status": status,
            "scheduled_for": "",
            "visual_paths": "",
            "candidate_id": "xyz789",
        },
    }


# ── Read operations pass through unchanged ────────────────────────


class TestDelegation:
    """ScheduleGuardedProxy delegates read ops transparently."""

    def test_all_delegated(self):
        inner = _make_proxy()
        inner.all.return_value = [{"id": "1"}]
        guard = ScheduleGuardedProxy(inner)

        result = guard.all(formula="{status}='DRAFTED'")
        inner.all.assert_called_once_with(formula="{status}='DRAFTED'")
        assert result == [{"id": "1"}]

    def test_get_delegated(self):
        inner = _make_proxy()
        guard = ScheduleGuardedProxy(inner)

        guard.get("42")
        inner.get.assert_called_once_with("42")

    def test_create_delegated(self):
        inner = _make_proxy()
        inner.create.return_value = {"id": "1", "fields": {"status": "DRAFTED"}}
        guard = ScheduleGuardedProxy(inner)

        guard.create({"status": "DRAFTED"})
        inner.create.assert_called_once_with({"status": "DRAFTED"})

    def test_batch_create_delegated(self):
        inner = _make_proxy()
        guard = ScheduleGuardedProxy(inner)

        guard.batch_create([{"status": "DRAFTED"}])
        inner.batch_create.assert_called_once_with([{"status": "DRAFTED"}])


# ── Update guards ────────────────────────────────────────────────


class TestUpdateGuard:
    """update() blocks destructive changes on scheduled posts."""

    def test_demotion_blocked_on_scheduled_post(self):
        inner = _make_proxy(get_return=_scheduled_record(status="VISUAL_READY"))
        guard = ScheduleGuardedProxy(inner)

        with pytest.raises(ScheduledPostProtectionError, match="Cannot demote"):
            guard.update("42", {"status": "DRAFTED"})

        inner.update.assert_not_called()

    def test_demotion_blocked_to_intel_ready(self):
        inner = _make_proxy(get_return=_scheduled_record(status="VISUAL_READY"))
        guard = ScheduleGuardedProxy(inner)

        with pytest.raises(ScheduledPostProtectionError, match="VISUAL_READY.*INTEL_READY"):
            guard.update("42", {"status": "INTEL_READY"})

    def test_promotion_allowed_on_scheduled_post(self):
        """Forward status changes (e.g., VISUAL_READY → PUBLISHED) are always OK."""
        inner = _make_proxy(get_return=_scheduled_record(status="VISUAL_READY"))
        guard = ScheduleGuardedProxy(inner)

        guard.update("42", {"status": "PUBLISHED"})
        inner.update.assert_called_once_with("42", {"status": "PUBLISHED"}, False)

    def test_clearing_visual_paths_blocked(self):
        inner = _make_proxy(get_return=_scheduled_record())
        guard = ScheduleGuardedProxy(inner)

        with pytest.raises(ScheduledPostProtectionError, match="visual_paths"):
            guard.update("42", {"visual_paths": ""})

    def test_clearing_visual_paths_empty_json_blocked(self):
        inner = _make_proxy(get_return=_scheduled_record())
        guard = ScheduleGuardedProxy(inner)

        with pytest.raises(ScheduledPostProtectionError, match="visual_paths"):
            guard.update("42", {"visual_paths": "[]"})

    def test_clearing_visual_paths_none_blocked(self):
        inner = _make_proxy(get_return=_scheduled_record())
        guard = ScheduleGuardedProxy(inner)

        with pytest.raises(ScheduledPostProtectionError, match="visual_paths"):
            guard.update("42", {"visual_paths": None})

    def test_setting_visual_paths_allowed(self):
        """Setting visual_paths to a real path is fine."""
        inner = _make_proxy(get_return=_scheduled_record())
        guard = ScheduleGuardedProxy(inner)

        guard.update("42", {"visual_paths": '["/tmp/new_video.mp4"]'})
        inner.update.assert_called_once()

    def test_clearing_scheduled_for_blocked(self):
        inner = _make_proxy(get_return=_scheduled_record())
        guard = ScheduleGuardedProxy(inner)

        with pytest.raises(ScheduledPostProtectionError, match="scheduled_for"):
            guard.update("42", {"scheduled_for": ""})

    def test_clearing_scheduled_for_none_blocked(self):
        inner = _make_proxy(get_return=_scheduled_record())
        guard = ScheduleGuardedProxy(inner)

        with pytest.raises(ScheduledPostProtectionError, match="scheduled_for"):
            guard.update("42", {"scheduled_for": None})

    def test_unscheduled_post_allows_everything(self):
        """Posts without scheduled_for should have no restrictions."""
        inner = _make_proxy(get_return=_unscheduled_record(status="VISUAL_READY"))
        guard = ScheduleGuardedProxy(inner)

        guard.update("99", {"status": "DRAFTED"})
        inner.update.assert_called_once()

    def test_non_guarded_fields_skip_api_call(self):
        """Updates to safe fields should NOT trigger a get() call."""
        inner = _make_proxy()
        guard = ScheduleGuardedProxy(inner)

        guard.update("42", {"platform_publish_status": '{"ig": "ok"}'})

        inner.get.assert_not_called()  # no fetch needed
        inner.update.assert_called_once()

    def test_typecast_passed_through(self):
        inner = _make_proxy(get_return=_unscheduled_record())
        guard = ScheduleGuardedProxy(inner)

        guard.update("99", {"status": "DRAFTED"}, typecast=True)
        inner.update.assert_called_once_with("99", {"status": "DRAFTED"}, True)


# ── Delete guards ────────────────────────────────────────────────


class TestDeleteGuard:
    """delete() blocks deletion of scheduled posts."""

    def test_delete_blocked_on_scheduled_post(self):
        inner = _make_proxy(get_return=_scheduled_record())
        guard = ScheduleGuardedProxy(inner)

        with pytest.raises(ScheduledPostProtectionError, match="Cannot delete"):
            guard.delete("42")

        inner.delete.assert_not_called()

    def test_delete_allowed_on_unscheduled_post(self):
        inner = _make_proxy(get_return=_unscheduled_record())
        guard = ScheduleGuardedProxy(inner)

        guard.delete("99")
        inner.delete.assert_called_once_with("99")


# ── Batch update guards ──────────────────────────────────────────


class TestBatchUpdateGuard:
    """batch_update() checks each record individually."""

    def test_batch_blocks_if_any_record_scheduled(self):
        """If even one record in the batch is scheduled, the whole batch fails."""
        inner = _make_proxy()
        # First call returns scheduled, second unscheduled
        inner.get.side_effect = [
            _scheduled_record(rec_id="42", status="VISUAL_READY"),
        ]
        guard = ScheduleGuardedProxy(inner)

        records = [
            {"id": "42", "fields": {"status": "DRAFTED"}},
            {"id": "99", "fields": {"status": "DRAFTED"}},
        ]
        with pytest.raises(ScheduledPostProtectionError):
            guard.batch_update(records)

        inner.batch_update.assert_not_called()

    def test_batch_allowed_for_unscheduled(self):
        inner = _make_proxy()
        inner.get.side_effect = [
            _unscheduled_record(rec_id="1", status="VISUAL_READY"),
            _unscheduled_record(rec_id="2", status="VISUAL_READY"),
        ]
        guard = ScheduleGuardedProxy(inner)

        records = [
            {"id": "1", "fields": {"status": "DRAFTED"}},
            {"id": "2", "fields": {"status": "DRAFTED"}},
        ]
        guard.batch_update(records)
        inner.batch_update.assert_called_once()

    def test_batch_skips_check_for_safe_fields(self):
        """If no guarded fields are in any record, skip all get() calls."""
        inner = _make_proxy()
        guard = ScheduleGuardedProxy(inner)

        records = [
            {"id": "1", "fields": {"platform_publish_status": "ok"}},
            {"id": "2", "fields": {"caption": "new caption"}},
        ]
        guard.batch_update(records)
        inner.get.assert_not_called()
        inner.batch_update.assert_called_once()


# ── Force override mechanisms ────────────────────────────────────


class TestForceOverride:
    """force=True and allow_scheduled_updates() bypass the guard."""

    def test_force_flag_on_update(self):
        inner = _make_proxy(get_return=_scheduled_record(status="VISUAL_READY"))
        guard = ScheduleGuardedProxy(inner)

        # Would normally raise ScheduledPostProtectionError
        guard.update("42", {"status": "DRAFTED"}, force=True)
        inner.update.assert_called_once()

    def test_force_flag_on_delete(self):
        inner = _make_proxy(get_return=_scheduled_record())
        guard = ScheduleGuardedProxy(inner)

        guard.delete("42", force=True)
        inner.delete.assert_called_once()

    def test_force_flag_on_batch_update(self):
        inner = _make_proxy(get_return=_scheduled_record(status="VISUAL_READY"))
        guard = ScheduleGuardedProxy(inner)

        records = [{"id": "42", "fields": {"status": "DRAFTED"}}]
        guard.batch_update(records, force=True)
        inner.batch_update.assert_called_once()

    def test_context_manager_allows_update(self):
        inner = _make_proxy(get_return=_scheduled_record(status="VISUAL_READY"))
        guard = ScheduleGuardedProxy(inner)

        with allow_scheduled_updates():
            guard.update("42", {"status": "DRAFTED"})

        inner.update.assert_called_once()

    def test_context_manager_allows_delete(self):
        inner = _make_proxy(get_return=_scheduled_record())
        guard = ScheduleGuardedProxy(inner)

        with allow_scheduled_updates():
            guard.delete("42")

        inner.delete.assert_called_once()

    def test_context_manager_resets_after_exit(self):
        """Guard is re-armed after context manager exits."""
        inner = _make_proxy(get_return=_scheduled_record(status="VISUAL_READY"))
        guard = ScheduleGuardedProxy(inner)

        with allow_scheduled_updates():
            guard.update("42", {"status": "DRAFTED"})

        # Outside the context manager — should block again
        with pytest.raises(ScheduledPostProtectionError):
            guard.update("42", {"status": "INTEL_READY"})

    def test_context_manager_resets_on_exception(self):
        """Guard is re-armed even if the context manager body raises."""
        inner = _make_proxy(get_return=_scheduled_record(status="VISUAL_READY"))
        guard = ScheduleGuardedProxy(inner)

        try:
            with allow_scheduled_updates():
                raise RuntimeError("something broke")
        except RuntimeError:
            pass

        with pytest.raises(ScheduledPostProtectionError):
            guard.update("42", {"status": "DRAFTED"})


# ── Edge cases ───────────────────────────────────────────────────


class TestEdgeCases:
    """Boundary conditions and edge cases."""

    def test_error_states_not_in_status_order(self):
        """ERROR/NEEDS_REVIEW are unknown statuses — should be allowed through."""
        inner = _make_proxy(get_return=_scheduled_record(status="VISUAL_READY"))
        guard = ScheduleGuardedProxy(inner)

        # Moving to ERROR is not in _STATUS_ORDER → _is_demotion returns False
        guard.update("42", {"status": "ERROR"})
        inner.update.assert_called_once()

    def test_empty_scheduled_for_is_not_scheduled(self):
        """Empty string scheduled_for means not scheduled."""
        rec = _scheduled_record()
        rec["fields"]["scheduled_for"] = ""
        inner = _make_proxy(get_return=rec)
        guard = ScheduleGuardedProxy(inner)

        guard.update("42", {"status": "DRAFTED"})
        inner.update.assert_called_once()

    def test_null_string_visual_paths_is_clearing(self):
        """'null' string should be treated as clearing visual_paths."""
        inner = _make_proxy(get_return=_scheduled_record())
        guard = ScheduleGuardedProxy(inner)

        with pytest.raises(ScheduledPostProtectionError, match="visual_paths"):
            guard.update("42", {"visual_paths": "null"})

    def test_whitespace_only_is_clearing(self):
        inner = _make_proxy(get_return=_scheduled_record())
        guard = ScheduleGuardedProxy(inner)

        with pytest.raises(ScheduledPostProtectionError, match="visual_paths"):
            guard.update("42", {"visual_paths": "   "})

    def test_multiple_guarded_fields_first_violation_raises(self):
        """When update touches multiple guarded fields, first violation raises."""
        inner = _make_proxy(get_return=_scheduled_record(status="VISUAL_READY"))
        guard = ScheduleGuardedProxy(inner)

        with pytest.raises(ScheduledPostProtectionError):
            guard.update("42", {"status": "DRAFTED", "visual_paths": ""})

    def test_is_demotion_same_status(self):
        """Same status is not a demotion."""
        assert not ScheduleGuardedProxy._is_demotion("DRAFTED", "DRAFTED")

    def test_is_demotion_forward(self):
        assert not ScheduleGuardedProxy._is_demotion("DRAFTED", "VISUAL_READY")

    def test_is_demotion_backward(self):
        assert ScheduleGuardedProxy._is_demotion("VISUAL_READY", "DRAFTED")

    def test_is_demotion_unknown_status(self):
        """Unknown statuses should not be treated as demotion."""
        assert not ScheduleGuardedProxy._is_demotion("VISUAL_READY", "UNKNOWN")
        assert not ScheduleGuardedProxy._is_demotion("UNKNOWN", "DRAFTED")


# ── Exception is NOT in BACKLOG_OP_ERRORS ────────────────────────


class TestExceptionNotSwallowed:
    """ScheduledPostProtectionError must NOT be caught by BACKLOG_OP_ERRORS."""

    def test_not_in_backlog_op_errors(self):
        from genlab_core.http.backlog_client import BACKLOG_OP_ERRORS

        assert not issubclass(ScheduledPostProtectionError, tuple(BACKLOG_OP_ERRORS))

    def test_not_caught_by_generic_handler(self):
        """Simulate a typical pipeline error handler."""
        from genlab_core.http.backlog_client import BACKLOG_OP_ERRORS

        caught = False
        try:
            raise ScheduledPostProtectionError("test")
        except BACKLOG_OP_ERRORS:
            caught = True
        except ScheduledPostProtectionError:
            pass  # expected — propagates past BACKLOG_OP_ERRORS

        assert not caught, "ScheduledPostProtectionError was swallowed by BACKLOG_OP_ERRORS!"
