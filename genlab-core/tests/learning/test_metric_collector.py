"""Tests for genlab_core.learning.metric_collector."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.learning.metric_collector import (
    collect_metrics,
    compute_reward,
    fetch_platform_metrics,
    process_pending_task,
)
from genlab_core.learning.pending_feedback_task import PendingFeedbackTask


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(
    platform: str = "youtube",
    post_id: str = "vid123",
    published_hours_ago: float = 50.0,
    status: str = "awaiting_6h",
    completed_windows: list | None = None,
) -> PendingFeedbackTask:
    return PendingFeedbackTask(
        content_id="cand_abc",
        platform=platform,
        niche_id="ai_news",
        published_at=datetime.now(timezone.utc) - timedelta(hours=published_hours_ago),
        platform_post_id=post_id,
        content_type="reel",
        collection_status=status,
        completed_windows=completed_windows or [],
    )


def _mock_store(pending_tasks: list[PendingFeedbackTask] | None = None):
    store = MagicMock()
    store.get_pending.return_value = pending_tasks or []
    # next_collection_window returns "6h" by default
    store.next_collection_window.return_value = "6h"
    store.update_window.return_value = None
    return store


def _mock_shaper(reward: float = 0.5):
    shaper = MagicMock()
    shaper.compute_reward.return_value = reward
    return shaper


# ---------------------------------------------------------------------------
# Test: collect_metrics flow calls get_pending, next_collection_window,
#        update_window in sequence
# ---------------------------------------------------------------------------

class TestCollectMetricsFlow:
    """Test the main collect_metrics flow orchestration."""

    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    @patch("genlab_core.learning.metric_collector.PendingFeedbackStore")
    @patch("genlab_core.learning.metric_collector.RewardShaper")
    def test_calls_store_methods_in_sequence(
        self, mock_shaper_cls, mock_store_cls, mock_fetch
    ):
        """get_pending -> next_collection_window -> fetch -> update_window."""
        task = _make_task()
        store = _mock_store([task])
        mock_store_cls.return_value = store
        mock_shaper_cls.return_value = _mock_shaper()
        mock_fetch.return_value = {"views": 100}

        client = MagicMock()
        result = collect_metrics(backlog_client=client)

        assert result == 1
        store.get_pending.assert_called_once()
        store.next_collection_window.assert_called_once()
        store.update_window.assert_called_once()

    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    @patch("genlab_core.learning.metric_collector.PendingFeedbackStore")
    @patch("genlab_core.learning.metric_collector.RewardShaper")
    def test_niche_filter_passed_to_get_pending(
        self, mock_shaper_cls, mock_store_cls, mock_fetch
    ):
        store = _mock_store([])
        mock_store_cls.return_value = store
        mock_shaper_cls.return_value = _mock_shaper()

        collect_metrics(niche_id="ai_news", backlog_client=MagicMock())

        store.get_pending.assert_called_once_with(niche_id="ai_news")


# ---------------------------------------------------------------------------
# Test: 48h window triggers reward computation
# ---------------------------------------------------------------------------

class TestRewardComputation:

    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    def test_48h_window_triggers_reward(self, mock_fetch):
        """When next window is 48h, compute_reward is called and passed to update_window."""
        task = _make_task(
            published_hours_ago=50,
            status="awaiting_48h",
            completed_windows=["6h", "24h"],
        )
        store = _mock_store()
        store.next_collection_window.return_value = "48h"
        shaper = _mock_shaper(reward=0.72)
        mock_fetch.return_value = {"views": 5000, "likes": 200}

        result = process_pending_task(task, store, shaper)

        assert result is True
        store.update_window.assert_called_once()
        call_args = store.update_window.call_args
        assert call_args[0][1] == "48h"  # window arg
        assert call_args[1]["reward_48h"] == 0.72

    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    def test_non_48h_window_no_reward(self, mock_fetch):
        """For windows other than 48h, reward_48h should be None."""
        task = _make_task()
        store = _mock_store()
        store.next_collection_window.return_value = "6h"
        shaper = _mock_shaper()
        mock_fetch.return_value = {"views": 100}

        process_pending_task(task, store, shaper)

        call_args = store.update_window.call_args
        assert call_args[1]["reward_48h"] is None

    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    def test_48h_empty_metrics_no_reward(self, mock_fetch):
        """If 48h fetch returns empty metrics, reward_48h is None."""
        task = _make_task(
            published_hours_ago=50,
            status="awaiting_48h",
            completed_windows=["6h", "24h"],
        )
        store = _mock_store()
        store.next_collection_window.return_value = "48h"
        shaper = _mock_shaper()
        mock_fetch.return_value = {}

        process_pending_task(task, store, shaper)

        call_args = store.update_window.call_args
        assert call_args[1]["reward_48h"] is None


# ---------------------------------------------------------------------------
# Test: graceful handling when no pending tasks
# ---------------------------------------------------------------------------

class TestNoPendingTasks:

    @patch("genlab_core.learning.metric_collector.PendingFeedbackStore")
    @patch("genlab_core.learning.metric_collector.RewardShaper")
    def test_returns_zero_when_no_tasks(self, mock_shaper_cls, mock_store_cls):
        store = _mock_store([])
        mock_store_cls.return_value = store
        mock_shaper_cls.return_value = _mock_shaper()

        result = collect_metrics(backlog_client=MagicMock())

        assert result == 0
        store.get_pending.assert_called_once()


# ---------------------------------------------------------------------------
# Test: no window ready yet
# ---------------------------------------------------------------------------

class TestNoWindowReady:

    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    def test_no_window_ready_returns_false(self, mock_fetch):
        """When next_collection_window returns None, nothing is processed."""
        task = _make_task(published_hours_ago=1)  # Too recent
        store = _mock_store()
        store.next_collection_window.return_value = None
        shaper = _mock_shaper()

        result = process_pending_task(task, store, shaper)

        assert result is False
        mock_fetch.assert_not_called()
        store.update_window.assert_not_called()


# ---------------------------------------------------------------------------
# Test: process_pending_task handles fetch failure gracefully
# ---------------------------------------------------------------------------

class TestFetchFailure:

    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    def test_fetch_returns_empty_still_updates_window(self, mock_fetch):
        """Even if metrics are empty, the window is still marked as collected."""
        task = _make_task()
        store = _mock_store()
        store.next_collection_window.return_value = "6h"
        shaper = _mock_shaper()
        mock_fetch.return_value = {}

        result = process_pending_task(task, store, shaper)

        assert result is True
        store.update_window.assert_called_once()


# ---------------------------------------------------------------------------
# Test: compute_reward delegates to RewardShaper
# ---------------------------------------------------------------------------

class TestComputeReward:

    def test_delegates_to_shaper(self):
        shaper = _mock_shaper(reward=0.85)
        metrics = {"views": 10000, "likes": 500}

        result = compute_reward(metrics, "youtube", shaper)

        assert result == 0.85
        shaper.compute_reward.assert_called_once_with(
            platform="youtube", metrics=metrics,
        )
