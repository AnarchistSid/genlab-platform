"""Tests for the canonical Instagram metrics fetcher."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests
from genlab_core.platforms.metrics import PlatformMetrics, fetch_instagram


def _resp(status: int, json_data: dict) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.json.return_value = json_data
    m.text = "(mocked)"
    return m


def _insights_payload(**by_name: int) -> dict:
    return {"data": [{"name": k, "values": [{"value": v}]} for k, v in by_name.items()]}


class TestFetchInstagramHappyPath:
    def test_returns_canonical_shape(self):
        basic = _resp(200, {"like_count": 5, "comments_count": 1, "media_type": "VIDEO"})
        insights = _resp(
            200,
            _insights_payload(
                reach=1500,
                saved=30,
                shares=4,
                likes=42,
                comments=7,
                total_interactions=83,
                ig_reels_video_view_total_time=600_000,
            ),
        )

        with patch("requests.get", side_effect=[basic, insights]):
            out = fetch_instagram("18054219725706846", token="t", ig_user_id="u")

        assert out == PlatformMetrics(
            views=1500,
            reach=1500,
            impressions=1500,
            likes=42,
            comments=7,
            shares=4,
            saves=30,
            watch_time_ms=600_000,
            engagement=83,
        )

    def test_basic_endpoint_falls_back_when_insights_missing_fields(self):
        # Insights returns 200 with only `reach` — the rest fall back to basic.
        basic = _resp(200, {"like_count": 11, "comments_count": 3})
        insights = _resp(200, _insights_payload(reach=200))

        with patch("requests.get", side_effect=[basic, insights]):
            out = fetch_instagram("123", token="t", ig_user_id="u")

        assert out is not None
        assert out["likes"] == 11
        assert out["comments"] == 3
        assert out["reach"] == 200
        # engagement is derived because total_interactions is absent
        assert out["engagement"] == 0 + 0 + 0 + 0  # all insights fields absent

    def test_insights_500_is_non_fatal_basic_still_returned(self):
        basic = _resp(200, {"like_count": 9, "comments_count": 2})
        insights = _resp(500, {})

        with patch("requests.get", side_effect=[basic, insights]):
            out = fetch_instagram("123", token="t", ig_user_id="u")

        assert out is not None
        assert out["likes"] == 9
        assert out["comments"] == 2
        assert out["reach"] == 0


class TestFetchInstagramFailureModes:
    def test_returns_none_when_no_token(self):
        # niche_credentials will resolve to "", "" when no env vars are present.
        with patch(
            "genlab_core.platforms.metrics.instagram._resolve_credentials",
            return_value=("", ""),
        ):
            assert fetch_instagram("abc") is None

    def test_returns_none_when_shortcode_unresolvable(self):
        # Non-digit post_id + no ig_user_id => can't resolve
        assert fetch_instagram("DWif8mKEjst", token="t", ig_user_id="") is None

    def test_returns_none_on_basic_non_200(self):
        basic = _resp(403, {})
        media_lookup = _resp(200, {"data": [{"id": "x", "shortcode": "abc"}]})

        # post_id is the shortcode "abc", needs media lookup first
        with patch("requests.get", side_effect=[media_lookup, basic]):
            assert fetch_instagram("abc", token="t", ig_user_id="user123") is None

    def test_returns_none_on_connection_error(self):
        with patch("requests.get", side_effect=requests.ConnectionError("boom")):
            assert fetch_instagram("123", token="t", ig_user_id="u") is None

    def test_explicit_token_skips_niche_resolution(self):
        basic = _resp(200, {"like_count": 0, "comments_count": 0})
        insights = _resp(200, {"data": []})

        with (
            patch("genlab_core.platforms.metrics.instagram._resolve_credentials") as mock_resolve,
            patch("requests.get", side_effect=[basic, insights]),
        ):
            out = fetch_instagram("123", token="explicit", ig_user_id="u")

        assert out is not None
        mock_resolve.assert_not_called()


class TestDelegationFromScript:
    def test_script_delegate_adds_legacy_aliases(self):
        from genlab_core.scripts.run_fetch_insights import (
            _fetch_instagram as script_fetch,
        )

        canonical = PlatformMetrics(
            views=1500,
            reach=1500,
            impressions=1500,
            likes=42,
            comments=7,
            shares=4,
            saves=30,
            watch_time_ms=600_000,
            engagement=83,
        )
        with patch(
            "genlab_core.platforms.metrics.fetch_instagram",
            return_value=canonical,
        ):
            out = script_fetch("abc", niche_id="sports")

        # Canonical keys preserved
        assert out["likes"] == 42
        assert out["reach"] == 1500
        assert out["saves"] == 30
        # Legacy aliases added
        assert out["saved"] == 30
        assert out["watch_time_minutes"] == 10.0  # 600000 / 60000
