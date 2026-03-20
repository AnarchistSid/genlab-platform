"""Tests for PendingFeedbackStore CRUD operations."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from genlab_core.learning.pending_feedback_store import PendingFeedbackStore
from genlab_core.learning.pending_feedback_task import PendingFeedbackTask


@pytest.fixture
def mock_proxy():
    proxy = MagicMock()
    proxy.create.return_value = {"id": "sp_001", "fields": {}}
    proxy.all.return_value = []
    proxy.update.return_value = {"id": "sp_001", "fields": {}}
    return proxy


@pytest.fixture
def mock_client(mock_proxy):
    client = MagicMock()
    client.pending_feedback = mock_proxy
    return client


@pytest.fixture
def store(mock_client):
    return PendingFeedbackStore(mock_client)


def _make_task(**overrides) -> PendingFeedbackTask:
    defaults = {
        "content_id": "story_001",
        "platform": "instagram",
        "niche_id": "gaming",
        "published_at": datetime.now(UTC) - timedelta(hours=50),
        "platform_post_id": "ig_12345",
        "bandit_arm": "carousel_hook_v2",
        "bandit_context": {"hook_type": "question", "duration": "short"},
    }
    defaults.update(overrides)
    return PendingFeedbackTask(**defaults)


class TestCreate:
    def test_create_calls_proxy_with_sharepoint_fields(self, store, mock_proxy):
        task = _make_task()
        store.create(task)
        mock_proxy.create.assert_called_once()
        fields = mock_proxy.create.call_args[0][0]
        assert fields["Platform"] == "instagram"
        assert fields["BanditArm"] == "carousel_hook_v2"
        assert fields["NicheId"] == "gaming"

    def test_create_handles_failure_gracefully(self, store, mock_proxy):
        mock_proxy.create.side_effect = Exception("SharePoint down")
        task = _make_task()
        store.create(task)  # should not raise

    def test_create_includes_hook_fields(self, store, mock_proxy):
        task = _make_task(hook_text="You won't believe this play", hook_type="reaction")
        store.create(task)
        fields = mock_proxy.create.call_args[0][0]
        assert fields["HookText"] == "You won't believe this play"
        assert fields["HookLength"] == 27  # character count
        assert fields["HookType"] == "reaction"

    def test_create_raises_if_no_proxy(self):
        client = MagicMock(spec=[])  # no pending_feedback attr
        client.pending_feedback = None
        store = PendingFeedbackStore(client)
        task = _make_task()
        # Should log warning but not crash
        store.create(task)


class TestGetPending:
    def test_get_pending_queries_non_terminal_statuses(self, store, mock_proxy):
        item = {
            "id": "sp_001",
            "fields": {
                "PostID": "ig_123",
                "Platform": "instagram",
                "NicheId": "gaming",
                "PublishedAt": (datetime.now(UTC) - timedelta(hours=10)).isoformat(),
                "PostContentType": "reel",
                "HookType": "",
                "Status": "awaiting_6h",
            },
        }
        mock_proxy.all.return_value = [item]
        tasks = store.get_pending()
        assert len(tasks) >= 1
        assert tasks[0].platform == "instagram"
        assert tasks[0].sharepoint_id == "sp_001"

    def test_get_pending_filters_by_niche(self, store, mock_proxy):
        items = [
            {"id": "1", "fields": {"PostID": "ig_1", "Platform": "instagram", "NicheId": "gaming",
                                    "PublishedAt": datetime.now(UTC).isoformat(), "Status": "awaiting_6h"}},
            {"id": "2", "fields": {"PostID": "ig_2", "Platform": "instagram", "NicheId": "ai_creators",
                                    "PublishedAt": datetime.now(UTC).isoformat(), "Status": "awaiting_6h"}},
        ]
        mock_proxy.all.return_value = items
        tasks = store.get_pending(niche_id="ai_creators")
        assert all(t.niche_id == "ai_creators" for t in tasks)


class TestNextCollectionWindow:
    def test_returns_6h_when_old_enough(self, store):
        task = _make_task(
            published_at=datetime.now(UTC) - timedelta(hours=7),
        )
        window = store.next_collection_window(task)
        assert window == "6h"

    def test_returns_48h_when_6h_and_24h_collected(self, store):
        task = _make_task(
            published_at=datetime.now(UTC) - timedelta(hours=50),
            completed_windows=["6h", "24h"],
        )
        window = store.next_collection_window(task)
        assert window == "48h"

    def test_returns_none_when_all_collected(self, store):
        task = _make_task(
            completed_windows=["6h", "24h", "48h", "168h"],
        )
        window = store.next_collection_window(task)
        assert window is None

    def test_returns_none_when_too_young_for_next_window(self, store):
        task = _make_task(
            published_at=datetime.now(UTC) - timedelta(hours=3),
        )
        window = store.next_collection_window(task)
        assert window is None


class TestUpdateWindow:
    def test_update_marks_window_completed(self, store, mock_proxy):
        task = _make_task(sharepoint_id="sp_001")
        store.update_window(task, "6h")
        assert "6h" in task.completed_windows
        assert task.collection_status == "awaiting_24h"
        mock_proxy.update.assert_called_once_with("sp_001", {
            "collection_status": "awaiting_24h",
            "early_stop": False,
        })

    def test_update_48h_sets_reward(self, store, mock_proxy):
        task = _make_task(
            sharepoint_id="sp_002",
            completed_windows=["6h", "24h"],
            collection_status="awaiting_48h",
        )
        store.update_window(task, "48h", reward_48h=0.73)
        assert task.reward_48h == 0.73
        assert task.collection_status == "awaiting_168h"
        call_fields = mock_proxy.update.call_args[0][1]
        assert call_fields["reward_48h"] == 0.73

    def test_update_168h_completes(self, store, mock_proxy):
        task = _make_task(
            sharepoint_id="sp_003",
            completed_windows=["6h", "24h", "48h"],
            collection_status="awaiting_168h",
        )
        store.update_window(task, "168h")
        assert task.collection_status == "complete"
        assert task.is_complete

    def test_update_without_sharepoint_id_finds_by_title(self, store, mock_proxy):
        task = _make_task(sharepoint_id=None)
        mock_proxy.all.return_value = [{"id": "sp_found"}]
        store.update_window(task, "6h")
        # Should have searched by title
        mock_proxy.all.assert_called()
        mock_proxy.update.assert_called_once()
        assert mock_proxy.update.call_args[0][0] == "sp_found"


class TestBanditReadiness:
    def test_ready_when_48h_reward_set(self):
        task = _make_task(reward_48h=0.65)
        assert task.reward_48h is not None

    def test_not_ready_without_reward(self):
        task = _make_task()
        assert task.reward_48h is None
