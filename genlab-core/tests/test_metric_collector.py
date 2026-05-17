"""Tests for genlab_core.learning.metric_collector.

Covers platform fetchers and bandit_updater callback wiring.
All external dependencies (platform APIs, SharePoint store) are mocked.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
            "discovery_share": 0.0,  # RewardShaper-aligned stub
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

        # Regular fetcher always adds RewardShaper-aligned stubs
        assert result == {"dm_send_rate": 0.0, "skip_rate": 0.0}

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

        # RewardShaper-aligned output: minutes_viewed, reach, shares,
        # completion_rate added alongside raw counts.
        # avg_watch_time is treated as milliseconds → minutes_viewed
        # = video_views * avg_watch_time / 60_000.
        # 8000 * 12.5 / 60000 = 1.666... → rounded to 1.67
        assert result == {
            "impressions": 10000,
            "engaged_users": 500,
            "video_views": 8000,
            "avg_watch_time": 12.5,
            "reach": 10000,  # falls back to impressions when post_impressions_unique absent
            "minutes_viewed": 1.67,
            "shares": 0,
            "completion_rate": 0.0,
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

        # video keys absent → minutes_viewed=0 stub. reach falls back to
        # impressions when post_impressions_unique not present.
        assert result == {
            "impressions": 2000,
            "engaged_users": 100,
            "reach": 2000,
            "minutes_viewed": 0.0,
            "shares": 0,
            "completion_rate": 0.0,
        }
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
    pub = datetime.now(UTC) - timedelta(hours=published_hours_ago)
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

        # Production passes 5 args: (niche_id, content_type, platform, reward, bandit_context)
        updater.assert_called_once_with("sports", "highlight", "instagram", 0.65, None)

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


# ===========================================================================
# 2026-05-16 audit fixes — regression tests
# ===========================================================================


class TestEarlyStopDoesNotFireBandit:
    """Bug F (2026-05-16): early-stop sent reward=0.05 which hit the
    adaptive threshold floor and incremented α, treating a flop as a
    success. The fix is to skip the bandit update at 6h entirely and
    rely on the 48h reward path."""

    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    def test_early_stop_does_not_call_bandit_updater(self, mock_fetch):
        # Views well below the gaming niche floor (30) → triggers early stop
        mock_fetch.return_value = {"views": 5}

        task = _make_task(
            published_hours_ago=7.0,
            collection_status="awaiting_6h",
            completed_windows=[],
            niche_id="gaming",
        )
        store = _mock_store(next_window="6h")
        shaper = _mock_shaper()
        updater = MagicMock()

        result = process_pending_task(task, store, shaper, bandit_updater=updater)

        # Early-stop happened (returned True), but bandit was NOT touched.
        assert result is True
        updater.assert_not_called()
        # Task marked as early_stopped with reward 0.0 (not 0.05).
        assert task.collection_status == "early_stopped"
        assert task.reward_48h == 0.0


class TestDefaultBanditUpdaterFractionalMath:
    """Bug B+C (2026-05-16): updater binarized reward against threshold
    instead of α += r; β += (1−r). For reward in [0,1] the fractional
    update preserves signal magnitude."""

    def test_fractional_update_preserves_reward_gradient(self):
        from genlab_core.learning.metric_collector import _default_bandit_updater

        # Stand in for the BacklogClient + bandit_arms proxy.
        proxy = MagicMock()
        existing_arm = {
            "id": "arm_row_1",
            "fields": {
                "arm_id": "gameplay_clip",
                "niche_id": "gaming",
                "alpha": 1.0,
                "beta": 1.0,
                "n_plays": 5,
            },
        }
        proxy.all.return_value = [existing_arm]
        proxy.update = MagicMock()
        proxy.create = MagicMock()

        client = MagicMock()
        client.bandit_arms = proxy

        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=client,
        ):
            _default_bandit_updater(
                niche_id="gaming",
                content_type="gameplay_clip",
                platform="youtube",
                reward=0.4,
            )

        # Verify the update written:
        #   alpha 1.0 + 0.4 = 1.4
        #   beta  1.0 + 0.6 = 1.6
        #   n_plays 5 + 1 = 6
        proxy.update.assert_called_once()
        written_fields = proxy.update.call_args[0][1]
        assert abs(written_fields["alpha"] - 1.4) < 1e-9
        assert abs(written_fields["beta"] - 1.6) < 1e-9
        assert written_fields["n_plays"] == 6

    def test_reward_clipped_to_unit_interval(self):
        from genlab_core.learning.metric_collector import _default_bandit_updater

        proxy = MagicMock()
        proxy.all.return_value = [{
            "id": "row",
            "fields": {
                "arm_id": "clip",
                "niche_id": "gaming",
                "alpha": 2.0,
                "beta": 3.0,
                "n_plays": 10,
            },
        }]
        client = MagicMock()
        client.bandit_arms = proxy

        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=client,
        ):
            # Reward beyond [0,1] must be clipped.
            _default_bandit_updater(
                niche_id="gaming",
                content_type="clip",
                platform="youtube",
                reward=1.7,
            )

        fields = proxy.update.call_args[0][1]
        # Clipped to 1.0 → α += 1, β += 0
        assert abs(fields["alpha"] - 3.0) < 1e-9
        assert abs(fields["beta"] - 3.0) < 1e-9
        assert fields["n_plays"] == 11


class TestMultiArmUpdate:
    """Closing the loop (2026-05-17): _default_bandit_updater applies
    the same reward to multiple arms when bandit_context carries
    extra_arms. This is how hook-style arms receive feedback."""

    def _proxy_with_arms(self, *arms):
        """Build a proxy whose .all() returns one row per arm spec.

        Each arm spec is (arm_id, niche_id, alpha, beta, n_plays).
        """
        proxy = MagicMock()
        proxy.all.return_value = [
            {
                "id": f"row_{i}",
                "fields": {
                    "arm_id": arm_id,
                    "niche_id": niche,
                    "alpha": a,
                    "beta": b,
                    "n_plays": n,
                },
            }
            for i, (arm_id, niche, a, b, n) in enumerate(arms)
        ]
        return proxy

    def test_extra_arms_get_same_reward_applied(self):
        from genlab_core.learning.metric_collector import _default_bandit_updater

        proxy = self._proxy_with_arms(
            ("gameplay_clip",            "gaming", 1.0, 1.0, 0),
            ("style:gaming:bold_claim",  "gaming", 1.0, 1.0, 0),
        )
        client = MagicMock()
        client.bandit_arms = proxy

        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=client,
        ):
            _default_bandit_updater(
                niche_id="gaming",
                content_type="gameplay_clip",
                platform="youtube",
                reward=0.5,
                bandit_context={"extra_arms": ["style:gaming:bold_claim"]},
            )

        # save_arm called once per matched target — twice total
        assert proxy.update.call_count == 2
        written_by_arm = {
            call.args[1]["arm_id"]: call.args[1]
            for call in proxy.update.call_args_list
        }
        assert "gameplay_clip" in written_by_arm
        assert "style:gaming:bold_claim" in written_by_arm
        for arm_id, fields in written_by_arm.items():
            assert abs(fields["alpha"] - 1.5) < 1e-9, arm_id
            assert abs(fields["beta"] - 1.5) < 1e-9, arm_id
            assert fields["n_plays"] == 1

    def test_linucb_state_only_written_to_primary_arm(self):
        """The 12-dim feature vector describes the content. Mixing it
        into the style arm's posterior would learn confounded signal."""
        from genlab_core.learning.linucb import CONTEXT_DIM
        from genlab_core.learning.metric_collector import _default_bandit_updater

        proxy = self._proxy_with_arms(
            ("gameplay_clip",            "gaming", 1.0, 1.0, 0),
            ("style:gaming:bold_claim",  "gaming", 1.0, 1.0, 0),
        )
        client = MagicMock()
        client.bandit_arms = proxy

        ctx_vec = [0.5] * CONTEXT_DIM
        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=client,
        ):
            _default_bandit_updater(
                niche_id="gaming",
                content_type="gameplay_clip",
                platform="youtube",
                reward=0.6,
                bandit_context={
                    "extra_arms": ["style:gaming:bold_claim"],
                    "linucb_context": ctx_vec,
                },
            )

        # Inspect save_arm calls — only the primary should carry
        # linucb_state in its fields dict.
        primary_linucb = None
        style_linucb = "PRESENT"  # sentinel; we expect None
        for call in proxy.update.call_args_list:
            fields = call.args[1]
            if fields["arm_id"] == "gameplay_clip":
                primary_linucb = fields.get("linucb_state")
            elif fields["arm_id"] == "style:gaming:bold_claim":
                style_linucb = fields.get("linucb_state", None)
        assert primary_linucb is not None, \
            "Primary arm should have LinUCB state written"
        assert style_linucb is None, \
            "Style arm must NOT receive LinUCB state"

    def test_missing_extra_arm_logs_warning_but_does_not_fail(self):
        from genlab_core.learning.metric_collector import _default_bandit_updater

        proxy = self._proxy_with_arms(
            ("gameplay_clip", "gaming", 1.0, 1.0, 0),
            # No style arm row — represents not-yet-seeded scenario
        )
        client = MagicMock()
        client.bandit_arms = proxy

        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=client,
        ):
            # Should not raise even though style arm is missing
            _default_bandit_updater(
                niche_id="gaming",
                content_type="gameplay_clip",
                platform="youtube",
                reward=0.3,
                bandit_context={"extra_arms": ["style:gaming:bold_claim"]},
            )

        # Primary arm still updated
        assert proxy.update.call_count == 1
        assert proxy.update.call_args.args[1]["arm_id"] == "gameplay_clip"

    def test_no_extra_arms_still_works_legacy(self):
        """Backwards-compat: bandit_context without extra_arms still
        updates only the primary arm."""
        from genlab_core.learning.metric_collector import _default_bandit_updater

        proxy = self._proxy_with_arms(
            ("gameplay_clip", "gaming", 1.0, 1.0, 0),
            ("style:gaming:bold_claim", "gaming", 5.0, 5.0, 10),
        )
        client = MagicMock()
        client.bandit_arms = proxy

        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=client,
        ):
            _default_bandit_updater(
                niche_id="gaming",
                content_type="gameplay_clip",
                platform="youtube",
                reward=0.4,
                bandit_context=None,
            )

        # Only the primary arm should be touched
        assert proxy.update.call_count == 1
        assert proxy.update.call_args.args[1]["arm_id"] == "gameplay_clip"

    def test_other_niche_arms_not_touched(self):
        """Even if extra_arms references an arm name that exists in
        another niche, the niche_id filter must prevent updating it."""
        from genlab_core.learning.metric_collector import _default_bandit_updater

        proxy = self._proxy_with_arms(
            ("gameplay_clip", "gaming", 1.0, 1.0, 0),
            # Same arm name but in a different niche
            ("style:gaming:bold_claim", "movies", 1.0, 1.0, 0),
        )
        client = MagicMock()
        client.bandit_arms = proxy

        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=client,
        ):
            _default_bandit_updater(
                niche_id="gaming",
                content_type="gameplay_clip",
                platform="youtube",
                reward=0.5,
                bandit_context={"extra_arms": ["style:gaming:bold_claim"]},
            )

        # Only the gaming arm gets touched (the movies-niche row
        # with the same arm_id is filtered out)
        assert proxy.update.call_count == 1
        assert proxy.update.call_args.args[1]["arm_id"] == "gameplay_clip"


class TestSaveArmPreservesNPlays:
    """Bug D (2026-05-16): save_arm hardcoded n_plays=0 on every write,
    overwriting prior counts. Fix: accept optional n_plays; if None,
    omit from update payload so existing value is preserved."""

    def test_save_arm_writes_explicit_n_plays(self):
        from genlab_core.learning.arm_loader import save_arm

        proxy = MagicMock()
        proxy.all.return_value = [{
            "id": "row_42",
            "fields": {"arm_id": "test_arm", "n_plays": 9},
        }]

        save_arm(proxy, arm_id="test_arm", alpha=2.0, beta=3.0, n_plays=10)

        written = proxy.update.call_args[0][1]
        assert written["n_plays"] == 10

    def test_save_arm_omits_n_plays_when_not_provided_on_update(self):
        from genlab_core.learning.arm_loader import save_arm

        proxy = MagicMock()
        proxy.all.return_value = [{
            "id": "row_42",
            "fields": {"arm_id": "test_arm", "n_plays": 9},
        }]

        save_arm(proxy, arm_id="test_arm", alpha=2.0, beta=3.0)

        # n_plays must NOT appear in the update payload — preserves the
        # existing value rather than clobbering with 0.
        written = proxy.update.call_args[0][1]
        assert "n_plays" not in written

    def test_save_arm_sets_zero_n_plays_on_create_when_not_provided(self):
        from genlab_core.learning.arm_loader import save_arm

        proxy = MagicMock()
        proxy.all.return_value = []  # no existing arm → create path

        save_arm(proxy, arm_id="new_arm", alpha=1.0, beta=1.0)

        written = proxy.create.call_args[0][0]
        assert written["n_plays"] == 0
