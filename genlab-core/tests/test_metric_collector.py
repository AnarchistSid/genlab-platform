"""Tests for genlab_core.learning.metric_collector.

Covers platform fetchers and bandit_updater callback wiring.
All external dependencies (platform APIs, SharePoint store) are mocked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.learning import platform_reward_multipliers as _prm
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


@pytest.fixture(autouse=True)
def _stub_platform_multipliers_to_unity(monkeypatch):
    """Pin pre-D3.8 fractional Beta math by neutralising the platform
    multiplier in every test in this module.

    The D3.8 multiplier path is covered in
    ``test_platform_reward_multipliers.py``; tests here are about the
    bandit-updater wiring and assume reward feeds through unchanged.
    """
    monkeypatch.setattr(_prm, "_CACHE", {})
    monkeypatch.setattr(_prm, "get_multiplier", lambda _platform: 1.0)
    yield
    _prm.clear_cache()


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

        # discovery_share isn't surfaced by the Threads API; the fetcher
        # used to default it to 0.0 but that pinned the bandit reward's
        # 0.15 discovery_share weight to a permanent zero. Now omitted
        # so RewardShaper.compute_reward redistributes that share to
        # observed metrics (views/replies/reposts).
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

        # dm_send_rate and skip_rate aren't exposed by the basic Graph
        # API insights endpoints. The fetcher used to stub them at 0.0
        # but that left their (0.30 + -0.05) reward weight permanently
        # zeroed out. Omitting the keys lets compute_reward redistribute
        # their share to observed metrics.
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
        mock_resp.status_code = 200
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
        # video_insights helper now also runs; the mocked response has
        # no reel-insights fields → returns nothing → stub defaults stay.
        assert result == {
            "impressions": 10000,
            "engaged_users": 500,
            "video_views": 8000,
            "avg_watch_time": 12.5,
            "reach": 10000,  # falls back to impressions when post_impressions_unique absent
            "minutes_viewed": 1.67,
            "likes": 0,  # video object query gets 0 since mock doesn't include likes field
            "comments": 0,
            "shares": 0,
            "completion_rate": 0.0,
        }
        # Verify the metric param includes video metrics (first call = /insights)
        params = mock_get.call_args_list[0][1]["params"]
        assert "post_video_views" in params["metric"]
        assert "post_video_avg_time_watched" in params["metric"]

    def test_omits_video_keys_when_not_present(self, monkeypatch):
        monkeypatch.setenv("FB_PAGE_ACCESS_TOKEN", "tok_fb")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
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
        # Note: video_views gets added from the video object fallback path
        # even when insights didn't include it. Same for likes/comments.
        assert result == {
            "impressions": 2000,
            "engaged_users": 100,
            "reach": 2000,
            "minutes_viewed": 0.0,
            "likes": 0,
            "comments": 0,
            "video_views": 0,
            "shares": 0,
            "completion_rate": 0.0,
        }
        assert "avg_watch_time" not in result


class TestFetchFacebookReelsV23Migration:
    """PR #518 (2026-06-24): v23 retirement of post_video_views for FB
    Reels means callers must request fb_reels_total_plays as the primary
    Reels view counter. Pin both:
      - the request includes fb_reels_total_plays
      - parsing prefers fb_reels_total_plays over post_video_views when both
        return values (the v22 transition case)
    """

    def test_fb_reels_total_plays_in_metric_request(self, monkeypatch):
        """The /insights request must include fb_reels_total_plays."""
        monkeypatch.setenv("FB_PAGE_ACCESS_TOKEN", "tok_fb")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": []}
        with patch("requests.get", return_value=mock_resp) as mock_get:
            _fetch_facebook("fbpost_reels_check")
        params = mock_get.call_args_list[0][1]["params"]
        assert "fb_reels_total_plays" in params["metric"], (
            "v23 retirement of post_video_views for Reels requires "
            "fb_reels_total_plays to be in the request (PR #518)."
        )

    def test_fb_reels_total_plays_overrides_post_video_views(self, monkeypatch):
        """When both metrics return — v22 transition shape — prefer the
        Reels-specific count. Per the deprecation registry, post_video_views
        for Reels has unstable semantics; fb_reels_total_plays is the
        canonical replacement."""
        monkeypatch.setenv("FB_PAGE_ACCESS_TOKEN", "tok_fb")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {"name": "post_impressions", "values": [{"value": 10000}]},
                {"name": "post_video_views", "values": [{"value": 8000}]},
                {"name": "fb_reels_total_plays", "values": [{"value": 9500}]},
                {"name": "post_video_avg_time_watched", "values": [{"value": 10}]},
            ]
        }
        with patch("requests.get", return_value=mock_resp):
            result = _fetch_facebook("fbpost_both_views")
        assert result["video_views"] == 9500, (
            "fb_reels_total_plays (9500) must override post_video_views (8000) "
            "per PR #518 migration shape."
        )

    def test_zero_fb_reels_total_plays_falls_back_to_post_video_views(self, monkeypatch):
        """For NON-Reels posts, fb_reels_total_plays returns 0 — we must
        fall back to post_video_views so non-Reels videos still report
        view counts correctly."""
        monkeypatch.setenv("FB_PAGE_ACCESS_TOKEN", "tok_fb")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {"name": "post_video_views", "values": [{"value": 5000}]},
                {"name": "fb_reels_total_plays", "values": [{"value": 0}]},
            ]
        }
        with patch("requests.get", return_value=mock_resp):
            result = _fetch_facebook("fbpost_non_reel")
        assert result["video_views"] == 5000, (
            "When fb_reels_total_plays is 0 (non-Reel post), post_video_views "
            "should still drive the metric. PR #518 fallback path."
        )


class TestFetchFacebookVideoObject:
    """Reel-era FB fetch — recovers engagement from the video object directly
    when the legacy page-post /insights endpoint 400s on a Reel ID.
    """

    def _video_object_response(
        self,
        views: int = 99,
        likes: int = 0,
        comments: int = 1,
        length_s: float = 40.0,
    ) -> dict:
        return {
            "views": views,
            "length": length_s,
            "likes": {"data": [], "summary": {"total_count": likes}},
            "comments": {"data": [], "summary": {"total_count": comments}},
            "id": "1579623663118068",
        }

    def test_video_object_fills_in_when_insights_returns_400(self, monkeypatch):
        """Critical resilience test mirroring observed Hetzner behavior:
        /insights 400s on every Reel ID. The video object query recovers
        real views/likes/comments so the engagement bandit gets signal.
        """
        monkeypatch.setenv("CRITICALRUSH_FB_PAGE_ACCESS_TOKEN", "tok_fb")
        vobj = self._video_object_response(views=99, likes=0, comments=1, length_s=40.0)

        def fake_get(url, params=None, headers=None, timeout=None, **_):
            r = MagicMock()
            r.raise_for_status.return_value = None
            if "/insights" in url:
                r.status_code = 400  # observed in production
                r.json.return_value = {"error": {"message": "deprecated"}}
            else:
                # Video object query: likes.summary + comments.summary + views + length
                r.status_code = 200
                r.json.return_value = vobj
            return r

        with patch("requests.get", side_effect=fake_get):
            result = _fetch_facebook("1579623663118068", niche_id="gaming")

        # Engagement counts recovered from the video object
        assert result["likes"] == 0
        assert result["comments"] == 1
        assert result["impressions"] == 99  # falls back to views since /insights empty
        assert result["reach"] == 99
        assert result["video_views"] == 99
        assert result["video_length_s"] == 40.0
        # Shares + completion_rate stay at stub 0 (architecturally unavailable)
        assert result["shares"] == 0
        assert result["completion_rate"] == 0.0

    def test_insights_data_preferred_when_available(self, monkeypatch):
        """When /insights succeeds, don't overwrite its values with video-object
        data (insights is more granular for impressions vs. views).
        """
        monkeypatch.setenv("CRITICALRUSH_FB_PAGE_ACCESS_TOKEN", "tok_fb")

        def fake_get(url, params=None, headers=None, timeout=None, **_):
            r = MagicMock()
            r.raise_for_status.return_value = None
            r.status_code = 200
            if "/insights" in url:
                r.json.return_value = {
                    "data": [
                        {"name": "post_impressions", "values": [{"value": 5000}]},
                        {"name": "post_video_views", "values": [{"value": 4000}]},
                    ]
                }
            else:
                r.json.return_value = self._video_object_response(views=99)
            return r

        with patch("requests.get", side_effect=fake_get):
            result = _fetch_facebook("fb_both", niche_id="gaming")

        # /insights wins for impressions (real impressions metric, not view count)
        assert result["impressions"] == 5000
        # video_views still uses the insights value (was set during loop)
        assert result["video_views"] == 4000
        # likes/comments come from the video object (insights doesn't return them)
        assert result["likes"] == 0
        assert result["comments"] == 1

    def test_video_object_helper_returns_empty_on_403(self):
        """Token without permission → empty, caller keeps stub zeros."""
        from genlab_core.learning.metric_collector import _fetch_facebook_video_object

        def fake_get(*_a, **_k):
            r = MagicMock()
            r.status_code = 403
            return r

        with patch("requests.get", side_effect=fake_get):
            result = _fetch_facebook_video_object("p1", "tok")

        assert result == {}

    def test_video_object_helper_parses_summaries_correctly(self):
        """Direct parse of likes.summary.total_count + comments.summary.total_count."""
        from genlab_core.learning.metric_collector import _fetch_facebook_video_object

        def fake_get(*_a, **_k):
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {
                "views": 100,
                "length": 30.5,
                "likes": {"summary": {"total_count": 12}},
                "comments": {"summary": {"total_count": 3}},
            }
            return r

        with patch("requests.get", side_effect=fake_get):
            result = _fetch_facebook_video_object("p1", "tok")

        assert result == {
            "views": 100,
            "likes": 12,
            "comments": 3,
            "video_length_s": 30.5,
        }

    def test_both_endpoints_fail_returns_safe_dict(self, monkeypatch):
        """Worst case — both /insights and video object 400. Return zeros, no crash."""
        monkeypatch.setenv("CRITICALRUSH_FB_PAGE_ACCESS_TOKEN", "tok_fb")

        def fake_get(url, *_a, **_k):
            r = MagicMock()
            r.status_code = 400
            r.raise_for_status.return_value = None
            r.json.return_value = {"error": {"message": "x"}}
            return r

        with patch("requests.get", side_effect=fake_get):
            result = _fetch_facebook("fb_dead", niche_id="gaming")

        # Doesn't crash, returns a stable shape with zeros
        assert result["impressions"] == 0
        assert result["reach"] == 0
        assert result["minutes_viewed"] == 0.0
        assert result["shares"] == 0
        assert result["completion_rate"] == 0.0


class TestFetchFacebookReelInsights:
    """The third FB endpoint that actually works for Reels.

    /{reel_id}/video_insights returns: post_video_view_time,
    post_video_avg_time_watched, post_video_social_actions.
    Together with the video object, these give us shares,
    completion_rate, and authoritative minutes_viewed.
    """

    def _build_fake_get(
        self,
        *,
        total_view_time_ms: float = 433778.0,
        avg_view_time_ms: float = 4819.0,
        social_actions: dict | None = None,
        video_length_s: float = 40.0,
        views: int = 99,
        insights_fails: bool = True,
    ):
        """All 3 endpoints stubbed; production shape (insights fails, others succeed)."""
        if social_actions is None:
            social_actions = {"COMMENT": 1, "SHARE": 0, "REACTION": 0}

        def fake_get(url, params=None, headers=None, timeout=None, **_):
            r = MagicMock()
            r.raise_for_status.return_value = None
            if "/insights" in url and "/video_insights" not in url:
                # Legacy page-post /insights — production reality
                r.status_code = 400 if insights_fails else 200
                r.json.return_value = {"error": {"message": "x"}}
            elif "/video_insights" in url:
                r.status_code = 200
                r.json.return_value = {
                    "data": [
                        {"name": "post_video_view_time", "values": [{"value": total_view_time_ms}]},
                        {
                            "name": "post_video_avg_time_watched",
                            "values": [{"value": avg_view_time_ms}],
                        },
                        {
                            "name": "post_video_social_actions",
                            "values": [{"value": social_actions}],
                        },
                    ]
                }
            else:
                # Video object query
                r.status_code = 200
                r.json.return_value = {
                    "views": views,
                    "length": video_length_s,
                    "likes": {"summary": {"total_count": 0}},
                    "comments": {"summary": {"total_count": 1}},
                }
            return r

        return fake_get

    def test_full_reel_signal_recovered(self, monkeypatch):
        """All three endpoints succeed (modulo insights 400) → bandit gets
        shares, completion_rate, and authoritative minutes_viewed.
        """
        monkeypatch.setenv("CRITICALRUSH_FB_PAGE_ACCESS_TOKEN", "tok_fb")
        with patch(
            "requests.get",
            side_effect=self._build_fake_get(
                total_view_time_ms=433778.0,  # 7.23 min total
                avg_view_time_ms=4819.0,  # 4.819s avg
                social_actions={"COMMENT": 1, "SHARE": 5, "REACTION": 12},
                video_length_s=40.0,
            ),
        ):
            result = _fetch_facebook("real_reel_id", niche_id="gaming")

        assert result["shares"] == 5
        assert result["reactions"] == 12
        # 4819ms / 40000ms = 0.1205
        assert result["completion_rate"] == 0.1205
        # 433778 / 60000 = 7.2296... → 7.23
        assert result["minutes_viewed"] == 7.23

    def test_share_only_in_dict_when_present(self, monkeypatch):
        """Reels with no shares yet: social_actions has no SHARE key → 0."""
        monkeypatch.setenv("CRITICALRUSH_FB_PAGE_ACCESS_TOKEN", "tok_fb")
        with patch(
            "requests.get",
            side_effect=self._build_fake_get(social_actions={"COMMENT": 2}),
        ):
            result = _fetch_facebook("reel_no_shares", niche_id="gaming")

        assert result["shares"] == 0

    def test_completion_rate_clamped_to_one(self, monkeypatch):
        """avg_watch > length (looping replays) → completion capped at 1.0."""
        monkeypatch.setenv("CRITICALRUSH_FB_PAGE_ACCESS_TOKEN", "tok_fb")
        with patch(
            "requests.get",
            side_effect=self._build_fake_get(
                avg_view_time_ms=50000.0,  # 50s avg
                video_length_s=40.0,  # 40s length
            ),
        ):
            result = _fetch_facebook("reel_loop", niche_id="gaming")

        assert result["completion_rate"] == 1.0

    def test_video_insights_failure_keeps_stubs(self, monkeypatch):
        """If /video_insights also fails, shares + completion_rate stay 0."""
        monkeypatch.setenv("CRITICALRUSH_FB_PAGE_ACCESS_TOKEN", "tok_fb")

        def fake_get(url, params=None, headers=None, timeout=None, **_):
            r = MagicMock()
            r.raise_for_status.return_value = None
            if "/video_insights" in url:
                r.status_code = 403
                r.json.return_value = {"error": {"message": "no permission"}}
            elif "/insights" in url:
                r.status_code = 400
                r.json.return_value = {"error": {}}
            else:
                r.status_code = 200
                r.json.return_value = {
                    "views": 50,
                    "length": 30.0,
                    "likes": {"summary": {"total_count": 0}},
                    "comments": {"summary": {"total_count": 0}},
                }
            return r

        with patch("requests.get", side_effect=fake_get):
            result = _fetch_facebook("reel_no_perm", niche_id="gaming")

        # Still get reach from video object, but shares/completion_rate stub
        assert result["reach"] == 50
        assert result["shares"] == 0
        assert result["completion_rate"] == 0.0
        # minutes_viewed stays at the insights-derived value (0 here since both failed)
        assert result["minutes_viewed"] == 0.0

    def test_reel_insights_helper_parses_social_actions_dict(self):
        """Direct test of the COMMENT/SHARE/REACTION dict parsing."""
        from genlab_core.learning.metric_collector import _fetch_facebook_reel_insights

        def fake_get(*_a, **_k):
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {
                "data": [
                    {"name": "post_video_view_time", "values": [{"value": 100000}]},
                    {"name": "post_video_avg_time_watched", "values": [{"value": 2500}]},
                    {
                        "name": "post_video_social_actions",
                        "values": [{"value": {"COMMENT": 3, "SHARE": 7, "REACTION": 22}}],
                    },
                ]
            }
            return r

        with patch("requests.get", side_effect=fake_get):
            result = _fetch_facebook_reel_insights("reel_x", "tok")

        assert result == {
            "total_view_time_ms": 100000.0,
            "avg_view_time_ms": 2500.0,
            "shares": 7,
            "reactions": 22,
            "social_comments": 3,
        }

    def test_minutes_viewed_uses_authoritative_total(self, monkeypatch):
        """minutes_viewed comes from post_video_view_time (total ms),
        which is more authoritative than the (views × avg_watch) estimate
        the /insights path would produce.
        """
        monkeypatch.setenv("CRITICALRUSH_FB_PAGE_ACCESS_TOKEN", "tok_fb")
        with patch(
            "requests.get",
            side_effect=self._build_fake_get(total_view_time_ms=600000.0),  # 10 min total
        ):
            result = _fetch_facebook("reel_total", niche_id="gaming")

        # 600000 / 60000 = 10.0 min — exact, not an estimate
        assert result["minutes_viewed"] == 10.0


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

        # Production passes 5 args:
        # (niche_id, bandit_arm or content_type, platform, reward, bandit_context).
        # _make_task sets bandit_arm = f"{content_type}__{platform}" to match
        # the classified-arm shape that push_to_backlog._classify_arm writes.
        updater.assert_called_once_with(
            "sports",
            "highlight__instagram",
            "instagram",
            0.65,
            None,
        )

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

        with patch("genlab_core.learning.metric_collector.PendingFeedbackStore") as MockStore:
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
        proxy.all.return_value = [
            {
                "id": "row",
                "fields": {
                    "arm_id": "clip",
                    "niche_id": "gaming",
                    "alpha": 2.0,
                    "beta": 3.0,
                    "n_plays": 10,
                },
            }
        ]
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
            ("gameplay_clip", "gaming", 1.0, 1.0, 0),
            ("style:gaming:bold_claim", "gaming", 1.0, 1.0, 0),
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
            call.args[1]["arm_id"]: call.args[1] for call in proxy.update.call_args_list
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
            ("gameplay_clip", "gaming", 1.0, 1.0, 0),
            ("style:gaming:bold_claim", "gaming", 1.0, 1.0, 0),
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
        assert primary_linucb is not None, "Primary arm should have LinUCB state written"
        assert style_linucb is None, "Style arm must NOT receive LinUCB state"

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
        proxy.all.return_value = [
            {
                "id": "row_42",
                "fields": {"arm_id": "test_arm", "n_plays": 9},
            }
        ]

        save_arm(proxy, arm_id="test_arm", alpha=2.0, beta=3.0, n_plays=10)

        written = proxy.update.call_args[0][1]
        assert written["n_plays"] == 10

    def test_save_arm_omits_n_plays_when_not_provided_on_update(self):
        from genlab_core.learning.arm_loader import save_arm

        proxy = MagicMock()
        proxy.all.return_value = [
            {
                "id": "row_42",
                "fields": {"arm_id": "test_arm", "n_plays": 9},
            }
        ]

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


# ===========================================================================
# CTA bandit click-attribution
# ===========================================================================


class TestCTABanditClickAttribution:
    """The CTA bandit must learn from real click signal, not engagement reward.

    Mirrors the audit-discovered Bug F shape: passing engagement reward
    (always-truthy float) as ``clicked: bool`` corrupted α every cycle.
    The fix attributes clicks via ``affiliate_clicks.blueprint_id`` instead.
    """

    def _setup_backlog_for_blueprint(
        self,
        variant_field: str = "ig_link_in_bio,yt_get_here,fb_check_out",
        click_count: int = 0,
    ) -> MagicMock:
        """Build a fake backlog_client.find() that returns blueprint + clicks."""
        backlog = MagicMock()

        def fake_find(
            table: str,
            *,
            formula: str = "",
            niche_id: str = "",
            max_records: int | None = None,
            columns=None,
            **_,
        ):
            if table == "blueprints":
                return [{"affiliate_cta_variant": variant_field}]
            if table == "affiliate_clicks":
                return [{"id": f"click_{i}"} for i in range(click_count)]
            return []

        backlog.find.side_effect = fake_find
        return backlog

    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    @patch("genlab_core.monetization.cta_engine.get_bandit")
    def test_clicks_present_increments_alpha(self, mock_get_bandit, mock_fetch):
        """Click count > 0 → bandit.update called with clicked=True."""
        mock_fetch.return_value = {"views": 5000, "likes": 200}
        fake_bandit = MagicMock()
        mock_get_bandit.return_value = fake_bandit

        task = _make_task(platform="instagram", niche_id="gaming")
        store = _mock_store(next_window="48h")
        shaper = _mock_shaper(reward=0.6)
        backlog = self._setup_backlog_for_blueprint(click_count=3)

        process_pending_task(task, store, shaper, backlog_client=backlog)

        fake_bandit.update.assert_called_once_with("ig_link_in_bio", "instagram", True)

    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    @patch("genlab_core.monetization.cta_engine.get_bandit")
    def test_zero_clicks_increments_beta(self, mock_get_bandit, mock_fetch):
        """Zero clicks at 48h → bandit.update called with clicked=False."""
        mock_fetch.return_value = {"views": 1000}
        fake_bandit = MagicMock()
        mock_get_bandit.return_value = fake_bandit

        task = _make_task(platform="youtube", niche_id="gaming")
        store = _mock_store(next_window="48h")
        shaper = _mock_shaper(reward=0.4)
        backlog = self._setup_backlog_for_blueprint(click_count=0)

        process_pending_task(task, store, shaper, backlog_client=backlog)

        fake_bandit.update.assert_called_once_with("yt_get_here", "youtube", False)

    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    @patch("genlab_core.monetization.cta_engine.get_bandit")
    def test_picks_variant_matching_platform(self, mock_get_bandit, mock_fetch):
        """Comma-separated variants → only the one matching platform is credited."""
        mock_fetch.return_value = {"views": 5000}
        fake_bandit = MagicMock()
        mock_get_bandit.return_value = fake_bandit

        task = _make_task(platform="facebook", niche_id="sports")
        store = _mock_store(next_window="48h")
        shaper = _mock_shaper(reward=0.5)
        backlog = self._setup_backlog_for_blueprint(
            variant_field="ig_best_deal,yt_recommended,fb_must_have",
            click_count=1,
        )

        process_pending_task(task, store, shaper, backlog_client=backlog)

        # FB arm gets credit, not IG or YT
        fake_bandit.update.assert_called_once_with("fb_must_have", "facebook", True)

    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    @patch("genlab_core.monetization.cta_engine.get_bandit")
    def test_no_variant_field_skips_bandit(self, mock_get_bandit, mock_fetch):
        """Blueprint without affiliate_cta_variant → no bandit update."""
        mock_fetch.return_value = {"views": 5000}
        fake_bandit = MagicMock()
        mock_get_bandit.return_value = fake_bandit

        task = _make_task(platform="instagram")
        store = _mock_store(next_window="48h")
        shaper = _mock_shaper(reward=0.6)
        backlog = self._setup_backlog_for_blueprint(variant_field="", click_count=5)

        process_pending_task(task, store, shaper, backlog_client=backlog)

        fake_bandit.update.assert_not_called()

    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    @patch("genlab_core.monetization.cta_engine.get_bandit")
    def test_platform_without_variants_skips_bandit(self, mock_get_bandit, mock_fetch):
        """Twitter/threads/tiktok have no CTA variants → no bandit update."""
        mock_fetch.return_value = {"views": 5000}
        fake_bandit = MagicMock()
        mock_get_bandit.return_value = fake_bandit

        task = _make_task(platform="twitter", niche_id="gaming")
        store = _mock_store(next_window="48h")
        shaper = _mock_shaper(reward=0.6)
        backlog = self._setup_backlog_for_blueprint(click_count=10)

        process_pending_task(task, store, shaper, backlog_client=backlog)

        fake_bandit.update.assert_not_called()

    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    @patch("genlab_core.monetization.cta_engine.get_bandit")
    def test_no_engagement_reward_passed_to_cta_bandit(self, mock_get_bandit, mock_fetch):
        """Regression: engagement reward must NEVER be the third arg to cta_bandit.update.

        This is the Bug F shape: a float cast to clicked: bool that was
        always truthy.  We verify the third positional is a bool, never the
        reward.
        """
        mock_fetch.return_value = {"views": 5000}
        fake_bandit = MagicMock()
        mock_get_bandit.return_value = fake_bandit

        task = _make_task(platform="instagram")
        store = _mock_store(next_window="48h")
        shaper = _mock_shaper(reward=0.73)
        backlog = self._setup_backlog_for_blueprint(click_count=2)

        process_pending_task(task, store, shaper, backlog_client=backlog)

        call_args = fake_bandit.update.call_args[0]
        assert call_args[2] is True or call_args[2] is False
        assert call_args[2] != 0.73  # never the engagement reward

    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    @patch("genlab_core.monetization.cta_engine.get_bandit")
    def test_backlog_without_find_skips_gracefully(self, mock_get_bandit, mock_fetch):
        """SharePoint-mode backlog_client has no find() → skip, don't crash."""
        mock_fetch.return_value = {"views": 5000}
        fake_bandit = MagicMock()
        mock_get_bandit.return_value = fake_bandit

        task = _make_task(platform="instagram")
        store = _mock_store(next_window="48h")
        shaper = _mock_shaper(reward=0.6)

        # Backlog client with no find() method (legacy SharePoint mode)
        backlog = object()

        # Must not raise
        process_pending_task(task, store, shaper, backlog_client=backlog)

        fake_bandit.update.assert_not_called()


class TestCTABanditVariantMatching:
    """Unit tests for the _match_variant_for_platform helper."""

    def test_match_instagram(self):
        from genlab_core.learning.metric_collector import _match_variant_for_platform

        assert (
            _match_variant_for_platform("ig_link_in_bio,yt_get_here,fb_check_out", "instagram")
            == "ig_link_in_bio"
        )

    def test_match_youtube(self):
        from genlab_core.learning.metric_collector import _match_variant_for_platform

        assert (
            _match_variant_for_platform("ig_link_in_bio,yt_best_price,fb_check_out", "youtube")
            == "yt_best_price"
        )

    def test_match_facebook(self):
        from genlab_core.learning.metric_collector import _match_variant_for_platform

        assert (
            _match_variant_for_platform("ig_link_in_bio,yt_get_here,fb_must_have", "facebook")
            == "fb_must_have"
        )

    def test_no_match_for_unknown_platform(self):
        from genlab_core.learning.metric_collector import _match_variant_for_platform

        assert (
            _match_variant_for_platform("ig_link_in_bio,yt_get_here,fb_check_out", "twitter")
            is None
        )

    def test_no_match_when_variant_missing(self):
        from genlab_core.learning.metric_collector import _match_variant_for_platform

        # FB variant absent — facebook lookup returns None
        assert _match_variant_for_platform("ig_link_in_bio,yt_get_here", "facebook") is None

    def test_handles_whitespace(self):
        from genlab_core.learning.metric_collector import _match_variant_for_platform

        assert (
            _match_variant_for_platform(" ig_link_in_bio , yt_get_here ", "instagram")
            == "ig_link_in_bio"
        )


class TestCTABanditSaveStateCreatesDir:
    """The state file path may include a .tmp/ parent that doesn't exist."""

    def test_save_state_creates_parent_dir(self, tmp_path):
        from genlab_core.monetization.cta_bandit import CTABandit

        nested = tmp_path / "deep" / "nested" / "cta_state.json"
        assert not nested.parent.exists()

        bandit = CTABandit(state_path=nested)
        bandit.save_state()

        assert nested.exists()


# ===========================================================================
# YouTube Analytics extras (avg_view_duration, subscriber_gained, ...)
# ===========================================================================


class TestFetchYouTubeAnalyticsExtras:
    """The Analytics layer fills RewardShaper keys the Data API can't reach.

    Uses shared super-account credentials + per-niche channel ID.
    """

    def _patch_creds(self, monkeypatch):
        """Stub both per-niche and shared analytics credential resolvers."""
        monkeypatch.setattr(
            "genlab_core.publishing.niche_credentials.resolve_youtube_credentials",
            lambda niche_id: {
                "client_id": "cid",
                "client_secret": "csec",
                "refresh_token": "rtok",
            },
        )
        monkeypatch.setattr(
            "genlab_core.publishing.niche_credentials.resolve_youtube_analytics_credentials",
            lambda niche_id="": {
                "client_id": "shared_cid",
                "client_secret": "shared_csec",
                "refresh_token": "shared_analytics_rtok",
            },
        )
        monkeypatch.setattr(
            "genlab_core.publishing.niche_credentials.resolve_youtube_channel_id",
            lambda niche_id: f"UC_{niche_id}_test",
        )

    def _clear_caches(self):
        """Reset module-level YouTube token caches."""
        from genlab_core.learning import metric_collector as mc

        mc._yt_token_cache.update({"token": "", "niche": "", "ts": 0.0})
        # _yt_analytics_token_cache is now a per-niche dict keyed by
        # niche_id (each value {"token", "ts"}), so clear() it rather than
        # overwriting with the old flat {"token","ts"} shape.
        mc._yt_analytics_token_cache.clear()

    def test_analytics_extras_merged_into_result(self, monkeypatch):
        """Successful Analytics call replaces stub zeros."""
        from genlab_core.learning.metric_collector import _fetch_youtube

        self._patch_creds(monkeypatch)
        self._clear_caches()

        def fake_post(url, data=None, timeout=None, **_):
            r = MagicMock()
            r.json.return_value = {"access_token": "ya29.fake"}
            r.raise_for_status.return_value = None
            return r

        def fake_get(url, params=None, headers=None, timeout=None, **_):
            r = MagicMock()
            r.raise_for_status.return_value = None
            if "youtube/v3/videos" in url:
                r.json.return_value = {
                    "items": [
                        {
                            "statistics": {
                                "viewCount": "10000",
                                "likeCount": "500",
                                "commentCount": "50",
                            }
                        }
                    ]
                }
            elif "youtube/v3/channels" in url:
                r.json.return_value = {"items": [{"id": "UC_test_channel"}]}
            elif "youtubeanalytics.googleapis.com" in url:
                r.status_code = 200
                r.json.return_value = {
                    "columnHeaders": [
                        {"name": "video"},
                        {"name": "estimatedMinutesWatched"},
                        {"name": "averageViewDuration"},
                        {"name": "subscribersGained"},
                        {"name": "shares"},
                    ],
                    "rows": [["vid_abc", 320.5, 18.7, 12, 5]],
                }
            return r

        with patch("requests.post", side_effect=fake_post):
            with patch("requests.get", side_effect=fake_get):
                result = _fetch_youtube("vid_abc", niche_id="gaming")

        # Data API snapshot
        assert result["views"] == 10000
        assert result["likes"] == 500
        assert result["like_rate"] == 0.05
        # Analytics-filled fields (no longer stub zeros)
        assert result["avg_view_duration"] == 18.7
        assert result["subscriber_gained"] == 12
        assert result["minutes_viewed"] == 320.5
        assert result["shares"] == 5

    def test_analytics_empty_rows_keeps_stub_zeros(self, monkeypatch):
        """Early window: Analytics returns no rows → stub keys remain at 0."""
        from genlab_core.learning.metric_collector import _fetch_youtube

        self._patch_creds(monkeypatch)
        self._clear_caches()

        def fake_post(url, data=None, timeout=None, **_):
            r = MagicMock()
            r.json.return_value = {"access_token": "ya29.fake"}
            r.raise_for_status.return_value = None
            return r

        def fake_get(url, params=None, headers=None, timeout=None, **_):
            r = MagicMock()
            r.raise_for_status.return_value = None
            if "youtube/v3/videos" in url:
                r.json.return_value = {
                    "items": [
                        {
                            "statistics": {
                                "viewCount": "100",
                                "likeCount": "5",
                                "commentCount": "1",
                            }
                        }
                    ]
                }
            elif "youtube/v3/channels" in url:
                r.json.return_value = {"items": [{"id": "UC_test"}]}
            elif "youtubeanalytics.googleapis.com" in url:
                r.status_code = 200
                r.json.return_value = {"columnHeaders": [], "rows": []}
            return r

        with patch("requests.post", side_effect=fake_post):
            with patch("requests.get", side_effect=fake_get):
                result = _fetch_youtube("vid_new", niche_id="gaming")

        assert result["views"] == 100  # Data API still works
        assert result["avg_view_duration"] == 0.0  # stub kept
        assert result["subscriber_gained"] == 0  # stub kept
        # Optional fields not added when empty
        assert "minutes_viewed" not in result

    def test_analytics_403_scope_missing_soft_fails(self, monkeypatch):
        """If yt-analytics.readonly scope is missing, fetch returns Data API only."""
        from genlab_core.learning.metric_collector import _fetch_youtube

        self._patch_creds(monkeypatch)
        self._clear_caches()

        def fake_post(url, data=None, timeout=None, **_):
            r = MagicMock()
            r.json.return_value = {"access_token": "ya29.fake"}
            r.raise_for_status.return_value = None
            return r

        def fake_get(url, params=None, headers=None, timeout=None, **_):
            r = MagicMock()
            r.raise_for_status.return_value = None
            if "youtube/v3/videos" in url:
                r.json.return_value = {
                    "items": [
                        {"statistics": {"viewCount": "200", "likeCount": "10", "commentCount": "2"}}
                    ]
                }
            elif "youtube/v3/channels" in url:
                r.json.return_value = {"items": [{"id": "UC_test"}]}
            elif "youtubeanalytics.googleapis.com" in url:
                r.status_code = 403  # scope rejected
                r.json.return_value = {"error": {"message": "insufficient scope"}}
            return r

        with patch("requests.post", side_effect=fake_post):
            with patch("requests.get", side_effect=fake_get):
                result = _fetch_youtube("vid_x", niche_id="gaming")

        assert result["views"] == 200
        assert result["avg_view_duration"] == 0.0
        assert result["subscriber_gained"] == 0

    def test_analytics_token_cached_across_niches(self, monkeypatch):
        """Shared super-account token: one refresh covers all 5 niches."""
        from genlab_core.learning import metric_collector as mc

        self._clear_caches()
        monkeypatch.setattr(
            "genlab_core.publishing.niche_credentials.resolve_youtube_analytics_credentials",
            lambda niche_id="": {"client_id": "c", "client_secret": "s", "refresh_token": "r"},
        )

        post_count = {"n": 0}

        def fake_post(url, data=None, timeout=None, **_):
            r = MagicMock()
            if "oauth2.googleapis.com" in url:
                post_count["n"] += 1
            r.json.return_value = {"access_token": "ya29.shared"}
            r.raise_for_status.return_value = None
            return r

        with patch("requests.post", side_effect=fake_post):
            t1 = mc._get_yt_analytics_access_token()
            t2 = mc._get_yt_analytics_access_token()  # cached
            t3 = mc._get_yt_analytics_access_token()  # cached

        assert t1 == t2 == t3 == "ya29.shared"
        assert post_count["n"] == 1  # single token refresh shared

    def test_analytics_extras_helper_parses_columns_correctly(self, monkeypatch):
        """Direct test of column-header → field-name mapping."""
        from genlab_core.learning.metric_collector import _fetch_youtube_analytics_extras

        self._clear_caches()
        monkeypatch.setattr(
            "genlab_core.publishing.niche_credentials.resolve_youtube_analytics_credentials",
            lambda niche_id="": {"client_id": "c", "client_secret": "s", "refresh_token": "r"},
        )

        def fake_post(url, data=None, timeout=None, **_):
            r = MagicMock()
            r.json.return_value = {"access_token": "ya29.shared"}
            r.raise_for_status.return_value = None
            return r

        def fake_get(url, params=None, headers=None, timeout=None, **_):
            r = MagicMock()
            r.status_code = 200
            r.raise_for_status.return_value = None
            r.json.return_value = {
                "columnHeaders": [
                    {"name": "video"},
                    {"name": "estimatedMinutesWatched"},
                    {"name": "averageViewDuration"},
                    {"name": "subscribersGained"},
                    {"name": "shares"},
                ],
                "rows": [["v1", 1000.0, 25.4, 50, 12]],
            }
            return r

        with patch("requests.post", side_effect=fake_post):
            with patch("requests.get", side_effect=fake_get):
                extras = _fetch_youtube_analytics_extras("v1", "UC_x")

        assert extras["minutes_viewed"] == 1000.0
        assert extras["avg_view_duration"] == 25.4
        assert extras["subscriber_gained"] == 50
        assert extras["shares"] == 12

    def test_analytics_extras_returns_empty_without_channel_id(self):
        """No channel_id → no API call, returns {}."""
        from genlab_core.learning.metric_collector import _fetch_youtube_analytics_extras

        with patch("requests.get") as mock_get:
            extras = _fetch_youtube_analytics_extras("v1", "")

        assert extras == {}
        mock_get.assert_not_called()

    def test_analytics_extras_empty_when_no_shared_token(self, monkeypatch):
        """No YOUTUBE_ANALYTICS_REFRESH_TOKEN → skip, no API call."""
        from genlab_core.learning import metric_collector as mc
        from genlab_core.learning.metric_collector import _fetch_youtube_analytics_extras

        mc._yt_analytics_token_cache.update({"token": "", "ts": 0.0})
        monkeypatch.setattr(
            "genlab_core.publishing.niche_credentials.resolve_youtube_analytics_credentials",
            lambda niche_id="": {"client_id": "c", "client_secret": "s", "refresh_token": ""},
        )

        with patch("requests.get") as mock_get:
            extras = _fetch_youtube_analytics_extras("v1", "UC_x")

        assert extras == {}
        mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# get_channel_metrics — pool + TTL cache (audit P-1)
# ---------------------------------------------------------------------------


class TestGetChannelMetricsPoolingAndCache:
    """The audit found get_channel_metrics opened a fresh psycopg connection
    per (niche, platform) call — 150 connects per 48h-window batch on a
    typical day, 7.5 min worst-case on a slow-Postgres day. These tests guard
    the pool + TTL cache fix."""

    def _reset(self):
        from genlab_core.learning import metric_collector as mc

        mc._invalidate_channel_metrics_cache_for_tests()
        # Reset the pool sentinel so each test starts from a clean state.
        mc._PG_POOL = None

    def test_no_database_url_returns_empty_and_caches_pool_unavailability(
        self, monkeypatch
    ) -> None:
        self._reset()
        monkeypatch.delenv("DATABASE_URL", raising=False)
        from genlab_core.learning import metric_collector as mc

        assert mc.get_channel_metrics("gaming", "instagram") == {}
        # Second call should hit the sentinel — no re-attempt at pool init.
        assert mc._PG_POOL is False
        assert mc.get_channel_metrics("gaming", "instagram") == {}

    def test_ttl_cache_returns_same_value_within_window(self, monkeypatch) -> None:
        """The TTL cache must return the cached dict for the same key
        without hitting the pool a second time."""
        self._reset()
        from genlab_core.learning import metric_collector as mc

        call_count = {"n": 0}

        def fake_pool():
            class _FakeConn:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def cursor(self):
                    class _Cur:
                        def __enter__(self_inner):
                            return self_inner

                        def __exit__(self_inner, *a):
                            return False

                        def execute(self_inner, *a, **kw):
                            call_count["n"] += 1

                        def fetchall(self_inner):
                            return [("subs_count", 1234.0)]

                    return _Cur()

            class _FakePool:
                def connection(self):
                    return _FakeConn()

            return _FakePool()

        monkeypatch.setattr(mc, "_get_pg_pool", fake_pool)
        result_1 = mc.get_channel_metrics("gaming", "instagram")
        result_2 = mc.get_channel_metrics("gaming", "instagram")
        assert result_1 == {"subs_count": 1234.0}
        assert result_2 == {"subs_count": 1234.0}
        # Only one cursor.execute call across both invocations.
        assert call_count["n"] == 1

    def test_different_keys_each_query_the_pool(self, monkeypatch) -> None:
        """Distinct (niche, platform) keys must NOT share a cache entry."""
        self._reset()
        from genlab_core.learning import metric_collector as mc

        call_count = {"n": 0}

        def fake_pool():
            class _FakeConn:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def cursor(self):
                    class _Cur:
                        def __enter__(self_inner):
                            return self_inner

                        def __exit__(self_inner, *a):
                            return False

                        def execute(self_inner, *a, **kw):
                            call_count["n"] += 1

                        def fetchall(self_inner):
                            return []

                    return _Cur()

            class _FakePool:
                def connection(self):
                    return _FakeConn()

            return _FakePool()

        monkeypatch.setattr(mc, "_get_pg_pool", fake_pool)
        mc.get_channel_metrics("gaming", "instagram")
        mc.get_channel_metrics("gaming", "youtube")
        mc.get_channel_metrics("sports", "instagram")
        assert call_count["n"] == 3

    def test_pool_query_failure_returns_empty_and_does_not_cache(self, monkeypatch) -> None:
        """A failed query must NOT poison the cache with empty results —
        the next call should retry."""
        self._reset()
        from genlab_core.learning import metric_collector as mc

        def boom_pool():
            class _FakePool:
                def connection(self):
                    raise RuntimeError("DB down")

            return _FakePool()

        monkeypatch.setattr(mc, "_get_pg_pool", boom_pool)
        assert mc.get_channel_metrics("gaming", "instagram") == {}
        # Cache should be empty so the next call retries.
        assert ("gaming", "instagram") not in mc._CHANNEL_METRICS_CACHE

    def test_ttl_expiry_re_queries(self, monkeypatch) -> None:
        """Past the TTL window, a fresh call must re-query the pool."""
        self._reset()
        from genlab_core.learning import metric_collector as mc

        call_count = {"n": 0}

        def fake_pool():
            class _FakeConn:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def cursor(self):
                    class _Cur:
                        def __enter__(self_inner):
                            return self_inner

                        def __exit__(self_inner, *a):
                            return False

                        def execute(self_inner, *a, **kw):
                            call_count["n"] += 1

                        def fetchall(self_inner):
                            return [("x", 1.0)]

                    return _Cur()

            class _FakePool:
                def connection(self):
                    return _FakeConn()

            return _FakePool()

        monkeypatch.setattr(mc, "_get_pg_pool", fake_pool)
        mc.get_channel_metrics("gaming", "instagram")
        # Pretend 120 s have passed (> 60s TTL).
        mc._CHANNEL_METRICS_CACHE[("gaming", "instagram")] = (
            mc.time.monotonic() - 120.0,
            {"x": 1.0},
        )
        mc.get_channel_metrics("gaming", "instagram")
        assert call_count["n"] == 2


class TestLinUCBStateDictPsycopgFix:
    """Pin the 2026-06-15 audit fix.

    Bug: psycopg auto-decodes JSONB columns to Python dict. The
    metric_collector's LinUCB update site did
    ``LinUCBArm.from_dict(_json.loads(raw_state))`` unconditionally,
    which raised "JSON object must be str, bytes or bytearray, not
    dict" on every populated arm. The exception was caught and
    silenced as "falling back to Thompson", so EVERY LinUCB
    contextual update was lost across 18 arms in 5 niches — the
    LinUCB layer of the bandit (6-dim features per CLAUDE.md) was
    effectively dead on prod.
    """

    def test_dict_linucb_state_does_not_raise(self):
        """The headline pin: passing a dict (as psycopg would for
        JSONB) must not raise. Without the fix, _json.loads(dict)
        raises TypeError → exception → silenced fallback."""
        import json

        from genlab_core.learning.linucb import LinUCBArm

        # Simulate what psycopg returns from a JSONB linucb_state column
        state_as_dict = LinUCBArm(d=6).to_dict()

        # The fix's logic:
        raw_state = state_as_dict
        parsed = raw_state if isinstance(raw_state, dict) else json.loads(raw_state)
        arm = LinUCBArm.from_dict(parsed)
        # Must not raise + must produce a valid arm
        assert arm.n_obs == 0
        assert arm.A.shape == (6, 6)

    def test_string_linucb_state_still_works(self):
        """Back-compat: passing a JSON string (as SharePoint or legacy
        path would) must still work."""
        import json

        from genlab_core.learning.linucb import LinUCBArm

        state_as_str = json.dumps(LinUCBArm(d=6).to_dict())

        raw_state = state_as_str
        parsed = raw_state if isinstance(raw_state, dict) else json.loads(raw_state)
        arm = LinUCBArm.from_dict(parsed)
        assert arm.A.shape == (6, 6)


class TestCollectMetricsInjectsPercentileTargetsFn:
    """Pin the 2026-06-20 fix: ``collect_metrics`` MUST construct a per-task
    ``RewardShaper`` with ``percentile_targets_fn=get_percentile_target``
    and ``niche_id=<task.niche_id>``.

    Pre-fix, ``collect_metrics`` at ``metric_collector.py:572`` did
    ``shaper = RewardShaper()`` — no args. This silently disabled the
    percentile-relative target lookup shipped in 2026-06-13 commit
    ``ca7be9f``. The ``self._percentile_targets_fn is None or not
    self._niche_id`` guard at ``reward_shaper.py:344`` short-circuited
    to the hardcoded fallback for every reward computation in prod.

    Symptom: top-arm avg_reward stuck near 0.08 (vs the intended ~0.7
    for the 70th-percentile floor), bandit effectively unable to
    differentiate high- from low-performing arms within a niche.

    Fix: construct shaper per-task inside the loop, passing both
    ``percentile_targets_fn`` and the task's ``niche_id`` (which can
    differ per task; ``collect_metrics``'s ``niche_id`` arg is an
    optional FILTER, not a partition).
    """

    @patch("genlab_core.learning.metric_collector.process_pending_task")
    @patch("genlab_core.learning.metric_collector.PendingFeedbackStore")
    def test_shaper_passed_to_process_has_percentile_fn_and_niche(
        self, mock_store_cls, mock_process
    ):
        """When ``collect_metrics`` dispatches a task to
        ``process_pending_task``, the ``shaper`` argument MUST have
        both ``_percentile_targets_fn`` and ``_niche_id`` populated
        from the task. This is the pin that catches the regression
        if anyone removes the per-task construction.
        """
        from genlab_core.learning.metric_collector import collect_metrics
        from genlab_core.learning.percentile_targets import get_percentile_target

        # One pending task for the gaming niche
        task = _make_task(platform="instagram", niche_id="gaming", content_type="reaction")
        store_instance = MagicMock()
        store_instance.get_pending.return_value = [task]
        store_instance.next_collection_window.return_value = "48h"
        mock_store_cls.return_value = store_instance

        # process_pending_task is mocked — we only care WHAT was passed
        mock_process.return_value = True

        # Run with a stub backlog_client so we don't try to construct one
        collect_metrics(niche_id=None, backlog_client=MagicMock(), bandit_updater=None)

        mock_process.assert_called_once()
        passed_shaper = mock_process.call_args.args[2]  # (task, store, shaper, ...)

        # The two invariants — without either, percentile fallback fires
        assert passed_shaper._percentile_targets_fn is get_percentile_target, (
            "RewardShaper must be constructed with percentile_targets_fn=get_percentile_target "
            "or the 2026-06-13 ca7be9f fix is dead code in production"
        )
        assert passed_shaper._niche_id == "gaming", (
            "RewardShaper must carry the per-task niche_id, not the optional "
            "collect_metrics niche_id filter (which can be None)"
        )

    @patch("genlab_core.learning.metric_collector.process_pending_task")
    @patch("genlab_core.learning.metric_collector.PendingFeedbackStore")
    def test_per_task_niche_id_when_multiple_niches_in_one_run(self, mock_store_cls, mock_process):
        """When ``collect_metrics`` processes tasks from MULTIPLE niches
        in one call (because ``niche_id=None`` returns all pending),
        EACH task's shaper must carry that task's specific niche_id.

        Pin this because the bug-class "single shared shaper with one
        global niche_id" would silently apply the wrong percentile
        target to most tasks.
        """
        from genlab_core.learning.metric_collector import collect_metrics

        tasks = [
            _make_task(platform="instagram", niche_id="gaming", content_type="t1"),
            _make_task(platform="youtube", niche_id="sports", content_type="t2"),
            _make_task(platform="facebook", niche_id="movies", content_type="t3"),
        ]
        store_instance = MagicMock()
        store_instance.get_pending.return_value = tasks
        store_instance.next_collection_window.return_value = "48h"
        mock_store_cls.return_value = store_instance

        mock_process.return_value = True

        collect_metrics(niche_id=None, backlog_client=MagicMock(), bandit_updater=None)

        assert mock_process.call_count == 3
        # Each call's shaper must match the corresponding task's niche
        expected_niches = ["gaming", "sports", "movies"]
        for call, expected_niche in zip(mock_process.call_args_list, expected_niches, strict=True):
            shaper_arg = call.args[2]
            assert shaper_arg._niche_id == expected_niche, (
                f"Per-task shaper niche_id mismatch: expected {expected_niche}, "
                f"got {shaper_arg._niche_id}. Sharing one shaper across tasks "
                f"would cause this — the fix requires per-task construction."
            )

    @patch("genlab_core.learning.metric_collector.process_pending_task")
    @patch("genlab_core.learning.metric_collector.PendingFeedbackStore")
    def test_no_shaper_constructed_when_no_pending_tasks(self, mock_store_cls, mock_process):
        """When ``get_pending`` returns [], no shaper is constructed
        (early-return path at line 575). This pins the optimization —
        if someone moves the shaper construction back outside the loop,
        this test fails.
        """
        from genlab_core.learning.metric_collector import collect_metrics

        store_instance = MagicMock()
        store_instance.get_pending.return_value = []
        mock_store_cls.return_value = store_instance

        result = collect_metrics(niche_id=None, backlog_client=MagicMock(), bandit_updater=None)

        assert result == 0
        mock_process.assert_not_called()
