"""Tests for core.publishing_queue.PublishingQueueManager."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "server"))

from core.publishing_queue import (
    QUEUE_STATUS_APPROVED,
    QUEUE_STATUS_FAILED,
    QUEUE_STATUS_HELD,
    QUEUE_STATUS_PENDING,
    QUEUE_STATUS_PUBLISHED,
    PublishingQueueManager,
    _derive_queue_status,
    _lock_key,
)


class TestLockKey:
    """R-22: the advisory-lock key must be deterministic across processes."""

    def test_lock_key_is_stable_sha256_not_salted_hash(self):
        import hashlib

        expected = int.from_bytes(hashlib.sha256(b"gaming").digest()[:4], "big") & 0x7FFFFFFF
        # Deterministic sha256-derived key (NOT the per-process-salted built-in hash()).
        assert _lock_key("gaming") == expected

    def test_lock_key_deterministic_and_distinct(self):
        assert _lock_key("gaming") == _lock_key("gaming")
        assert _lock_key("gaming") != _lock_key("movies")
        # Positive 31-bit int, valid for pg_advisory_xact_lock.
        assert 0 <= _lock_key("anime") <= 0x7FFFFFFF


class TestDeriveQueueStatus:
    def test_published_status(self):
        assert _derive_queue_status({"status": "PUBLISHED"}) == QUEUE_STATUS_PUBLISHED

    def test_approved_no_error(self):
        assert (
            _derive_queue_status({"status": "VISUAL_READY", "action_taken": "approved"})
            == QUEUE_STATUS_APPROVED
        )

    def test_approved_with_publish_error(self):
        assert (
            _derive_queue_status(
                {
                    "status": "VISUAL_READY",
                    "action_taken": "approved",
                    "error_log": "timeout",
                }
            )
            == QUEUE_STATUS_FAILED
        )

    def test_held(self):
        assert (
            _derive_queue_status({"status": "VISUAL_READY", "action_taken": "held"})
            == QUEUE_STATUS_HELD
        )

    def test_pending_blank_action(self):
        assert (
            _derive_queue_status({"status": "VISUAL_READY", "action_taken": ""})
            == QUEUE_STATUS_PENDING
        )

    def test_pending_no_action(self):
        assert _derive_queue_status({"status": "VISUAL_READY"}) == QUEUE_STATUS_PENDING

    def test_case_insensitive_action(self):
        assert (
            _derive_queue_status({"status": "VISUAL_READY", "action_taken": "Approved"})
            == QUEUE_STATUS_APPROVED
        )

    def test_whitespace_in_action(self):
        assert (
            _derive_queue_status({"status": "VISUAL_READY", "action_taken": " approved "})
            == QUEUE_STATUS_APPROVED
        )


class TestPublishingQueueManager:
    def _make_manager(self, records):
        """Build a manager with patched _fetch_blueprints_sync."""
        self._records = records
        self._fetch_patcher = patch(
            "core.publishing_queue._fetch_blueprints_sync",
            side_effect=lambda status_filter="VISUAL_READY": [
                r
                for r in self._records
                if r.get("fields", {}).get("status", "VISUAL_READY") == status_filter
            ],
        )
        self._update_patcher = patch("core.publishing_queue._update_blueprint_sync")
        self._fetch_patcher.start()
        self._mock_update = self._update_patcher.start()
        return PublishingQueueManager()

    def teardown_method(self):
        try:
            self._fetch_patcher.stop()
            self._update_patcher.stop()
        except Exception:
            pass

    def _record(self, id_, status="VISUAL_READY", action_taken="", niche_id="ai_creators", **extra):
        fields = {"status": status, "action_taken": action_taken, "niche_id": niche_id, **extra}
        return {"id": id_, "fields": fields}

    def test_get_queue_returns_all(self):
        mgr = self._make_manager(
            [
                self._record("1", action_taken=""),
                self._record("2", action_taken="approved"),
                self._record("3", action_taken="held"),
            ]
        )
        items = mgr.get_queue()
        assert len(items) == 3

    def test_get_queue_filter_by_status(self):
        mgr = self._make_manager(
            [
                self._record("1", action_taken=""),
                self._record("2", action_taken="approved"),
                self._record("3", action_taken="held"),
            ]
        )
        pending = mgr.get_queue(queue_status="PENDING_APPROVAL")
        assert len(pending) == 1
        assert pending[0]["id"] == "1"

    def test_get_queue_sorts_pending_first(self):
        mgr = self._make_manager(
            [
                self._record("1", action_taken="approved"),
                self._record("2", action_taken=""),
            ]
        )
        items = mgr.get_queue()
        assert items[0]["queue_status"] == QUEUE_STATUS_PENDING

    def test_get_queue_niche_filter(self):
        mgr = self._make_manager(
            [
                self._record("1", niche_id="ai_creators"),
                self._record("2", niche_id="gaming"),
            ]
        )
        items = mgr.get_queue(niche_id="gaming")
        assert len(items) == 1
        assert items[0]["id"] == "2"

    def test_get_queue_respects_limit(self):
        records = [self._record(str(i)) for i in range(10)]
        mgr = self._make_manager(records)
        items = mgr.get_queue(limit=3)
        assert len(items) == 3

    def test_get_stats(self):
        mgr = self._make_manager(
            [
                self._record("1", action_taken=""),
                self._record("2", action_taken=""),
                self._record("3", action_taken="approved"),
                self._record("4", action_taken="held"),
                self._record("5", status="PUBLISHED"),
            ]
        )
        stats = mgr.get_stats()
        assert stats["pending"] == 2
        assert stats["approved"] == 1
        assert stats["held"] == 1
        assert stats["published"] == 1
        assert stats["total"] == 5

    @patch("core.publishing_queue._update_blueprint_sync")
    def test_approve_sets_action_taken(self, mock_update):
        mgr = PublishingQueueManager()
        mgr.approve("42", notes="LGTM")
        mock_update.assert_called_once()
        call_args = mock_update.call_args
        assert call_args[0][0] == "42"
        assert call_args[0][1]["action_taken"] == "approved"
        assert call_args[0][1]["review_notes"] == "LGTM"

    @patch("core.publishing_queue._update_blueprint_sync")
    def test_hold_sets_action_held(self, mock_update):
        mgr = PublishingQueueManager()
        mgr.hold("42", reason="Needs more context")
        call_args = mock_update.call_args
        assert call_args[0][1]["action_taken"] == "held"
        assert call_args[0][1]["review_notes"] == "Needs more context"

    @patch("core.publishing_queue._update_blueprint_sync")
    def test_release_clears_action(self, mock_update):
        mgr = PublishingQueueManager()
        mgr.release("42")
        call_args = mock_update.call_args
        # release() sets action_taken to None (cleared) or empty string
        assert call_args[0][1]["action_taken"] in ("", None)

    def test_is_publishable_approved(self):
        mock_client = MagicMock()
        mock_client.blueprints.get.return_value = {
            "id": "42",
            "fields": {"action_taken": "approved"},
        }
        mgr = PublishingQueueManager(client=mock_client)
        assert mgr.is_publishable("42") is True

    def test_is_publishable_not_approved(self):
        mock_client = MagicMock()
        mock_client.blueprints.get.return_value = {"id": "42", "fields": {"action_taken": "held"}}
        mgr = PublishingQueueManager(client=mock_client)
        assert mgr.is_publishable("42") is False

    def test_is_publishable_blank(self):
        mock_client = MagicMock()
        mock_client.blueprints.get.return_value = {"id": "42", "fields": {"action_taken": ""}}
        mgr = PublishingQueueManager(client=mock_client)
        assert mgr.is_publishable("42") is False

    def test_is_publishable_on_error_returns_false(self):
        mock_client = MagicMock()
        mock_client.blueprints.get.side_effect = RuntimeError("API down")
        mgr = PublishingQueueManager(client=mock_client)
        assert mgr.is_publishable("42") is False


class TestPublishGateInvariant:
    """The most critical invariant: nothing publishes without approval."""

    def test_gate_blocks_pending(self):
        mock_client = MagicMock()
        mock_client.blueprints.get.return_value = {"id": "1", "fields": {"action_taken": ""}}
        mgr = PublishingQueueManager(client=mock_client)
        assert mgr.is_publishable("1") is False

    def test_gate_blocks_held(self):
        mock_client = MagicMock()
        mock_client.blueprints.get.return_value = {"id": "1", "fields": {"action_taken": "held"}}
        mgr = PublishingQueueManager(client=mock_client)
        assert mgr.is_publishable("1") is False

    def test_gate_allows_approved(self):
        mock_client = MagicMock()
        mock_client.blueprints.get.return_value = {
            "id": "1",
            "fields": {"action_taken": "approved"},
        }
        mgr = PublishingQueueManager(client=mock_client)
        assert mgr.is_publishable("1") is True
