"""Tests for genlab_core.learning.metric_collector.

Covers platform fetchers and bandit_updater callback wiring.
All external dependencies (platform APIs, SharePoint store) are mocked.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch


from genlab_core.learning.metric_collector import (
    _fetch_facebook,
    _fetch_instagram_reels_6h,
    _fetch_threads,
    _fetch_tiktok,
    collect_metrics,
    fetch_platform_metrics,
    process_pending_task,
)
from genlab_core.learning.pending_feedback_store import PendingFeedbackStore
from genlab_core.learning.pending_feedback_task import PendingFeedbackTask
from genlab_core.learning.reward_shaper import RewardShaper


# ---------------------------------------------------------------------------
# _fetch_tiktok
# ---------------------------------------------------------------------------
class TestFetchTikTok:
    def test_returns_empty_without_token(self, monkeypatch):
        monkeypatch.delenv("TIKTOK_ACCESS_TOKEN", raising=False)
        assert _fetch_tiktok("vid123") == {}

    def test_returns_empty_with_blank_token(self, monkeypatch):
        monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "   ")
        assert _fetch_tiktok("vid123") == {}

    @patch("genlab_core.learning.metric_collector.requests", create=True)
    def test_successful_fetch(self, mock_req_module, monkeypatch):
        # Patch at the point of use — the function does `import requests` inline
        monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "tok_abc")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": {
                "videos": [
                    {
                        "id": "vid123",
                        "view_count": 5000,
                        "like_count": 300,
                        "comment_count": 40,
                        "share_count": 15,
                    }
                ]
            }
        }
        # We need to patch requests.post inside the function's local import
        with patch("requests.post", return_value=mock_resp) as mock_post:
            result = _fetch_tiktok("vid123")

        assert result == {"views": 5000, "likes": 300, "comments": 40, "shares": 15}
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "open.tiktokapis.com" in call_kwargs[0][0]
        assert call_kwargs[1]["json"]["filters"]["video_ids"] == ["vid123"]

    def test_api_error_returns_empty(self, monkeypatch):
        monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "tok_abc")
        with patch("requests.post", side_effect=Exception("API down")):
            assert _fetch_tiktok("vid123") == {}

    def test_empty_videos_returns_empty(self, monkeypatch):
        monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "tok_abc")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"videos": []}}
        with patch("requests.post", return_value=mock_resp):
            assert _fetch_tiktok("vid123") == {}


# ---------------------------------------------------------------------------
# _fetch_threads
# ---------------------------------------------------------------------------
class TestFetchThreads:
    def test_returns_empty_without_token(self, monkeypatch):
        monkeypatch.delenv("THREADS_ACCESS_TOKEN", raising=False)
        assert _fetch_threads("post456") == {}

    def test_returns_empty_with_blank_token(self, monkeypatch):
        monkeypatch.setenv("THREADS_ACCESS_TOKEN", "  ")
        assert _fetch_threads("post456") == {}

    def test_successful_fetch(self, monkeypatch):
        monkeypatch.setenv("THREADS_ACCESS_TOKEN", "tok_threads")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {"name": "views", "values": [{"value": 1200}]},
                {"name": "likes", "values": [{"value": 80}]},
                {"name": "replies", "values": [{"value": 12}]},
                {"name": "reposts", "values": [{"value": 5}]},
                {"name": "quotes", "values": [{"value": 3}]},
            ]
        }
        with patch("requests.get", return_value=mock_resp) as mock_get:
            result = _fetch_threads("post456")

        assert result == {
            "views": 1200,
            "likes": 80,
            "replies": 12,
            "reposts": 5,
            "quotes": 3,
        }
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "graph.threads.net" in call_args[0][0]

    def test_api_error_returns_empty(self, monkeypatch):
        monkeypatch.setenv("THREADS_ACCESS_TOKEN", "tok_threads")
        with patch("requests.get", side_effect=Exception("network")):
            assert _fetch_threads("post456") == {}


# ---------------------------------------------------------------------------
# _fetch_instagram_reels_6h
# ---------------------------------------------------------------------------
class TestFetchInstagramReels6h:
    def test_returns_empty_without_token(self, monkeypatch):
        monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)
        assert _fetch_instagram_reels_6h("reel789") == {}

    def test_successful_fetch(self, monkeypatch):
        monkeypatch.setenv("META_ACCESS_TOKEN", "tok_meta")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {"name": "ig_reels_avg_watch_time", "values": [{"value": 4.2}]},
                {"name": "ig_reels_video_view_total_time", "values": [{"value": 86000}]},
                {"name": "plays", "values": [{"value": 2500}]},
            ]
        }
        with patch("requests.get", return_value=mock_resp) as mock_get:
            result = _fetch_instagram_reels_6h("reel789")

        assert result == {
            "avg_watch_time": 4.2,
            "total_watch_time": 86000,
            "views": 2500,
        }
        # Verify uses graph.facebook.com (NOT graph.instagram.com)
        url = mock_get.call_args[0][0]
        assert "graph.facebook.com" in url
        assert "graph.instagram.com" not in url
        # Verify correct metrics requested
        params = mock_get.call_args[1]["params"]
        assert "ig_reels_avg_watch_time" in params["metric"]
        assert "ig_reels_video_view_total_time" in params["metric"]


# ---------------------------------------------------------------------------
# fetch_platform_metrics — window-based routing
# ---------------------------------------------------------------------------
class TestFetchPlatformMetricsRouting:
    def test_instagram_6h_routes_to_reels_fetcher(self, monkeypatch):
        monkeypatch.setenv("META_ACCESS_TOKEN", "tok_meta")
        reels_resp = MagicMock()
        reels_resp.json.return_value = {
            "data": [
                {"name": "ig_reels_avg_watch_time", "values": [{"value": 3.5}]},
                {"name": "plays", "values": [{"value": 1000}]},
            ]
        }
        with patch("requests.get", return_value=reels_resp):
            result = fetch_platform_metrics("instagram", "reel001", "6h")

        assert "avg_watch_time" in result
        assert "views" in result
        # Should NOT have 'reach', 'saved' etc. from the regular fetcher
        assert "reach" not in result

    def test_instagram_24h_routes_to_regular_fetcher(self, monkeypatch):
        monkeypatch.setenv("META_ACCESS_TOKEN", "tok_meta")
        regular_resp = MagicMock()
        regular_resp.json.return_value = {
            "data": [
                {"name": "plays", "values": [{"value": 5000}]},
                {"name": "reach", "values": [{"value": 3000}]},
                {"name": "likes", "values": [{"value": 200}]},
            ]
        }
        with patch("requests.get", return_value=regular_resp):
            result = fetch_platform_metrics("instagram", "post001", "24h")

        assert result.get("views") == 5000
        assert result.get("reach") == 3000
        assert result.get("likes") == 200

    def test_instagram_48h_routes_to_regular_fetcher(self, monkeypatch):
        monkeypatch.setenv("META_ACCESS_TOKEN", "tok_meta")
        regular_resp = MagicMock()
        regular_resp.json.return_value = {"data": []}
        with patch("requests.get", return_value=regular_resp):
            result = fetch_platform_metrics("instagram", "post001", "48h")

        assert result == {}

    def test_instagram_6h_exception_returns_empty(self, monkeypatch):
        monkeypatch.setenv("META_ACCESS_TOKEN", "tok_meta")
        with patch("requests.get", side_effect=Exception("boom")):
            result = fetch_platform_metrics("instagram", "reel001", "6h")
        assert result == {}

    def test_unknown_platform_returns_empty(self):
        result = fetch_platform_metrics("mastodon", "abc", "6h")
        assert result == {}


# ---------------------------------------------------------------------------
# _fetch_facebook — enriched with video metrics
# ---------------------------------------------------------------------------
class TestFetchFacebook:
    def test_returns_empty_without_token(self, monkeypatch):
        monkeypatch.delenv("FB_PAGE_ACCESS_TOKEN", raising=False)
        assert _fetch_facebook("fbpost1") == {}

    def test_includes_video_views_when_present(self, monkeypatch):
        monkeypatch.setenv("FB_PAGE_ACCESS_TOKEN", "tok_fb")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {"name": "post_impressions", "values": [{"value": 10000}]},
                {"name": "post_engaged_users", "values": [{"value": 500}]},
                {"name": "post_video_views", "values": [{"value": 8000}]},
                {"name": "post_video_avg_time_watched", "values": [{"value": 12.5}]},
            ]
        }
        with patch("requests.get", return_value=mock_resp) as mock_get:
            result = _fetch_facebook("fbpost1")

        assert result == {
            "impressions": 10000,
            "engaged_users": 500,
            "video_views": 8000,
            "avg_watch_time": 12.5,
        }
        # Verify the metric param includes video metrics
        params = mock_get.call_args[1]["params"]
        assert "post_video_views" in params["metric"]
        assert "post_video_avg_time_watched" in params["metric"]

    def test_omits_video_keys_when_not_present(self, monkeypatch):
        monkeypatch.setenv("FB_PAGE_ACCESS_TOKEN", "tok_fb")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {"name": "post_impressions", "values": [{"value": 2000}]},
                {"name": "post_engaged_users", "values": [{"value": 100}]},
            ]
        }
        with patch("requests.get", return_value=mock_resp):
            result = _fetch_facebook("fbpost2")

        assert result == {"impressions": 2000, "engaged_users": 100}
        assert "video_views" not in result
        assert "avg_watch_time" not in result


# ===========================================================================
# Bandit updater callback tests
# ===========================================================================


def _make_task(
    platform: str = "youtube",
    niche_id: str = "gaming",
    content_type: str = "clip",
    published_hours_ago: float = 50.0,
    collection_status: str = "awaiting_48h",
    completed_windows: list | None = None,
) -> PendingFeedbackTask:
    """Create a PendingFeedbackTask for testing."""
    pub = datetime.now(timezone.utc) - timedelta(hours=published_hours_ago)
    return PendingFeedbackTask(
        content_id="test_content_001",
        platform=platform,
        niche_id=niche_id,
        published_at=pub,
        platform_post_id="post_abc123",
        content_type=content_type,
        hook_type="controversy",
        bandit_arm=f"{content_type}__{platform}",
        collection_status=collection_status,
        completed_windows=completed_windows or ["6h", "24h"],
    )


def _mock_store(next_window: str | None = "48h") -> MagicMock:
    """Create a mock PendingFeedbackStore."""
    store = MagicMock(spec=PendingFeedbackStore)
    store.next_collection_window.return_value = next_window
    store.update_window.return_value = None
    return store


def _mock_shaper(reward: float = 0.72) -> MagicMock:
    """Create a mock RewardShaper that returns a fixed reward."""
    shaper = MagicMock(spec=RewardShaper)
    shaper.compute_reward.return_value = reward
    return shaper


# ---------------------------------------------------------------------------
# 1. bandit_updater IS called at 48h window with correct args
# ---------------------------------------------------------------------------


class TestBanditUpdaterCalled:
    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    def test_updater_called_at_48h_with_correct_args(self, mock_fetch):
        mock_fetch.return_value = {"views": 5000, "likes": 200}

        task = _make_task(
            platform="instagram",
            niche_id="sports",
            content_type="highlight",
        )
        store = _mock_store(next_window="48h")
        shaper = _mock_shaper(reward=0.65)
        updater = MagicMock()

        process_pending_task(task, store, shaper, bandit_updater=updater)

        updater.assert_called_once_with("sports", "highlight", "instagram", 0.65)

    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    def test_updater_receives_computed_reward(self, mock_fetch):
        mock_fetch.return_value = {"views": 10000, "likes": 500}

        task = _make_task()
        store = _mock_store(next_window="48h")
        shaper = _mock_shaper(reward=0.88)
        updater = MagicMock()

        process_pending_task(task, store, shaper, bandit_updater=updater)

        # Verify the reward value passed to the updater matches the shaper output
        call_args = updater.call_args[0]
        assert call_args[3] == 0.88


# ---------------------------------------------------------------------------
# 2. bandit_updater NOT called when window != "48h"
# ---------------------------------------------------------------------------


class TestBanditUpdaterNotCalledNon48h:
    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    def test_updater_not_called_at_6h_window(self, mock_fetch):
        mock_fetch.return_value = {"views": 1000}

        task = _make_task(
            published_hours_ago=7.0,
            collection_status="awaiting_6h",
            completed_windows=[],
        )
        store = _mock_store(next_window="6h")
        shaper = _mock_shaper()
        updater = MagicMock()

        process_pending_task(task, store, shaper, bandit_updater=updater)

        updater.assert_not_called()

    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    def test_updater_not_called_at_24h_window(self, mock_fetch):
        mock_fetch.return_value = {"views": 3000}

        task = _make_task(
            published_hours_ago=25.0,
            collection_status="awaiting_24h",
            completed_windows=["6h"],
        )
        store = _mock_store(next_window="24h")
        shaper = _mock_shaper()
        updater = MagicMock()

        process_pending_task(task, store, shaper, bandit_updater=updater)

        updater.assert_not_called()

    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    def test_updater_not_called_at_168h_window(self, mock_fetch):
        mock_fetch.return_value = {"views": 20000}

        task = _make_task(
            published_hours_ago=170.0,
            collection_status="awaiting_168h",
            completed_windows=["6h", "24h", "48h"],
        )
        store = _mock_store(next_window="168h")
        shaper = _mock_shaper()
        updater = MagicMock()

        process_pending_task(task, store, shaper, bandit_updater=updater)

        updater.assert_not_called()


# ---------------------------------------------------------------------------
# 3. bandit_updater NOT called when reward is None (empty metrics)
# ---------------------------------------------------------------------------


class TestBanditUpdaterNotCalledEmptyMetrics:
    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    def test_updater_not_called_when_metrics_empty(self, mock_fetch):
        mock_fetch.return_value = {}  # Empty metrics

        task = _make_task()
        store = _mock_store(next_window="48h")
        shaper = _mock_shaper()
        updater = MagicMock()

        process_pending_task(task, store, shaper, bandit_updater=updater)

        # reward_48h is None when metrics are empty, so updater should not fire
        updater.assert_not_called()
        # shaper should not even be called
        shaper.compute_reward.assert_not_called()


# ---------------------------------------------------------------------------
# 4. bandit_updater NOT called when not provided (backward compat)
# ---------------------------------------------------------------------------


class TestBanditUpdaterBackwardCompat:
    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    def test_no_error_when_updater_is_none(self, mock_fetch):
        mock_fetch.return_value = {"views": 5000, "likes": 200}

        task = _make_task()
        store = _mock_store(next_window="48h")
        shaper = _mock_shaper(reward=0.72)

        # No bandit_updater passed — should not raise
        result = process_pending_task(task, store, shaper)
        assert result is True
        # Store should still be updated
        store.update_window.assert_called_once()


# ---------------------------------------------------------------------------
# 5. bandit_updater exception is caught (does not break processing)
# ---------------------------------------------------------------------------


class TestBanditUpdaterExceptionCaught:
    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    def test_updater_exception_does_not_break_processing(self, mock_fetch):
        mock_fetch.return_value = {"views": 5000, "likes": 200}

        task = _make_task()
        store = _mock_store(next_window="48h")
        shaper = _mock_shaper(reward=0.72)
        updater = MagicMock(side_effect=RuntimeError("bandit store offline"))

        # Should not raise
        result = process_pending_task(task, store, shaper, bandit_updater=updater)
        assert result is True
        # Store update should still happen
        store.update_window.assert_called_once()


# ---------------------------------------------------------------------------
# 6. collect_metrics passes bandit_updater through to process_pending_task
# ---------------------------------------------------------------------------


class TestCollectMetricsPassthrough:
    @patch("genlab_core.learning.metric_collector.process_pending_task")
    def test_bandit_updater_forwarded(self, mock_process):
        mock_process.return_value = True

        mock_client = MagicMock()
        updater = MagicMock()

        with patch(
            "genlab_core.learning.metric_collector.PendingFeedbackStore"
        ) as MockStore:
            mock_store_inst = MockStore.return_value
            mock_store_inst.get_pending.return_value = [_make_task()]

            collect_metrics(
                niche_id="gaming",
                backlog_client=mock_client,
                bandit_updater=updater,
            )

        # Verify bandit_updater was passed to process_pending_task
        call_kwargs = mock_process.call_args[1]
        assert call_kwargs["bandit_updater"] is updater


# ---------------------------------------------------------------------------
# 7. No window due -> returns False, no updater call
# ---------------------------------------------------------------------------


class TestNoWindowDue:
    def test_returns_false_when_no_window(self):
        task = _make_task()
        store = _mock_store(next_window=None)
        shaper = _mock_shaper()
        updater = MagicMock()

        result = process_pending_task(task, store, shaper, bandit_updater=updater)
        assert result is False
        updater.assert_not_called()
        store.update_window.assert_not_called()
