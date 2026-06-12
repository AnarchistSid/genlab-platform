"""Tests for the canonical Facebook metrics fetcher.

Updated 2026-06-13 (fix/reward-chain-meta-metrics-v23) to cover the v23+
metric set: `fb_reels_total_plays` + `blue_reels_play_count` (fallback)
+ `post_impressions_unique` (reach) + `post_video_social_actions`
(combined comments/shares) + the sibling post-node breakdown call.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests
from genlab_core.platforms.metrics import PlatformMetrics, fetch_facebook


def _resp(status: int, json_data: dict) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.json.return_value = json_data
    m.text = "(mocked)"
    return m


def _video_insights(**by_name) -> dict:
    return {"data": [{"name": k, "values": [{"value": v}]} for k, v in by_name.items()]}


def _post_node(comments: int = 0, shares: int = 0) -> dict:
    """Build a post-node breakdown response shape."""
    return {
        "comments": {"summary": {"total_count": comments}},
        "shares": {"count": shares},
        "reactions": {"summary": {"total_count": comments + shares}},
    }


class TestFetchFacebookHappyPath:
    def test_returns_canonical_shape_v23_metrics(self):
        """Primary path: fb_reels_total_plays as views, reaction dict, breakdown."""
        insights = _video_insights(
            fb_reels_total_plays=1234,
            blue_reels_play_count=1000,
            post_impressions_unique=900,
            post_video_view_time=5_000_000,
            post_video_likes_by_reaction_type={"like": 40, "love": 5, "wow": 2},
            post_video_social_actions=12,
        )
        post = _post_node(comments=5, shares=7)

        # Two requests fire — video_insights then the post-node breakdown.
        with patch("requests.get", side_effect=[_resp(200, insights), _resp(200, post)]):
            out = fetch_facebook("pageid_postnum", token="t")

        assert out == PlatformMetrics(
            views=1234,  # fb_reels_total_plays preferred over blue_reels_play_count
            reach=900,  # post_impressions_unique
            likes=47,  # 40 + 5 + 2 — reaction dict summed
            comments=5,
            shares=7,
            watch_time_ms=5_000_000,
            engagement=59,  # 47 + 5 + 7
        )

    def test_falls_back_to_blue_reels_when_total_plays_zero(self):
        """blue_reels_play_count is the fallback views number for very new posts."""
        insights = _video_insights(
            blue_reels_play_count=500,
            post_video_likes_by_reaction_type={"like": 10},
        )
        with patch("requests.get", side_effect=[_resp(200, insights), _resp(200, _post_node())]):
            out = fetch_facebook("p", token="t")

        assert out is not None
        assert out["views"] == 500

    def test_social_actions_folded_into_shares_when_breakdown_empty(self):
        """When the post-node breakdown can't separate comments/shares, the
        combined `post_video_social_actions` count flows into `shares` so the
        engagement signal isn't lost."""
        insights = _video_insights(
            fb_reels_total_plays=100,
            post_video_social_actions=15,  # combined
        )
        # Breakdown returns empty (post deleted / no access / new post).
        with patch("requests.get", side_effect=[_resp(200, insights), _resp(200, {})]):
            out = fetch_facebook("p", token="t")

        assert out is not None
        assert out["comments"] == 0
        assert out["shares"] == 15  # folded
        assert out["engagement"] == 15  # 0 + 0 + 15

    def test_missing_reaction_type_field_yields_zero_likes(self):
        insights = _video_insights(fb_reels_total_plays=100, post_video_view_time=0)
        with patch("requests.get", side_effect=[_resp(200, insights), _resp(200, _post_node())]):
            out = fetch_facebook("p", token="t")

        assert out is not None
        assert out["likes"] == 0
        assert out["views"] == 100

    def test_request_includes_explicit_metric_param(self):
        """v23 requires the metric= param — without it the endpoint errors.
        Pin the request shape so a future refactor can't silently drop it."""
        captured: dict[str, object] = {}

        def fake_get(url, params=None, timeout=None):  # noqa: ARG001
            captured.setdefault("first", params)
            return _resp(200, {"data": []})

        with patch("requests.get", side_effect=fake_get):
            fetch_facebook("p", token="t")

        params = captured["first"]
        assert isinstance(params, dict)
        # All v23 canonical metrics must be in the request — otherwise we
        # regress to the pre-fix silent-zero behavior.
        assert "metric" in params
        metric_csv = params["metric"]
        assert "fb_reels_total_plays" in metric_csv
        assert "blue_reels_play_count" in metric_csv
        assert "post_impressions_unique" in metric_csv
        assert "post_video_likes_by_reaction_type" in metric_csv
        assert "post_video_social_actions" in metric_csv


class TestFetchFacebookFailureModes:
    def test_returns_none_when_no_token(self):
        with patch(
            "genlab_core.platforms.metrics.facebook._resolve_credentials",
            return_value="",
        ):
            assert fetch_facebook("p") is None

    def test_returns_none_on_non_200(self):
        with patch("requests.get", return_value=_resp(400, {})):
            assert fetch_facebook("p", token="t") is None

    def test_returns_none_on_connection_error(self):
        with patch("requests.get", side_effect=requests.ConnectionError("boom")):
            assert fetch_facebook("p", token="t") is None

    def test_empty_data_array_returns_zero_metrics(self):
        with patch("requests.get", side_effect=[_resp(200, {"data": []}), _resp(200, {})]):
            out = fetch_facebook("p", token="t")
        assert out == PlatformMetrics(
            views=0,
            reach=0,
            likes=0,
            comments=0,
            shares=0,
            watch_time_ms=0,
            engagement=0,
        )

    def test_explicit_token_skips_niche_resolution(self):
        with (
            patch("genlab_core.platforms.metrics.facebook._resolve_credentials") as mock_resolve,
            patch("requests.get", return_value=_resp(200, {"data": []})),
        ):
            fetch_facebook("p", token="explicit")
        mock_resolve.assert_not_called()


class TestDelegationFromScript:
    def test_script_delegate_passes_through(self):
        from genlab_core.scripts.run_fetch_insights import (
            _fetch_facebook as script_fetch,
        )

        canonical = PlatformMetrics(
            views=100, reach=100, likes=20, watch_time_ms=1000, engagement=120
        )
        with patch(
            "genlab_core.platforms.metrics.fetch_facebook",
            return_value=canonical,
        ) as mock_canonical:
            out = script_fetch("p", niche_id="anime")

        mock_canonical.assert_called_once_with("p", niche_id="anime")
        assert out == dict(canonical)
