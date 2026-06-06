"""Tests for the canonical Facebook metrics fetcher."""

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


class TestFetchFacebookHappyPath:
    def test_returns_canonical_shape(self):
        payload = _video_insights(
            post_video_views=1234,
            post_video_view_time=5_000_000,
            post_video_likes_by_reaction_type={"Like": 40, "Love": 5, "Wow": 2},
        )
        with patch("requests.get", return_value=_resp(200, payload)):
            out = fetch_facebook("pageid_postnum", token="t")

        assert out == PlatformMetrics(
            views=1234,
            reach=1234,
            likes=47,  # 40 + 5 + 2
            watch_time_ms=5_000_000,
            engagement=1281,  # likes + views
        )

    def test_missing_reaction_type_field_yields_zero_likes(self):
        payload = _video_insights(post_video_views=100, post_video_view_time=0)
        with patch("requests.get", return_value=_resp(200, payload)):
            out = fetch_facebook("p", token="t")

        assert out is not None
        assert out["likes"] == 0
        assert out["views"] == 100
        assert out["engagement"] == 100  # 0 + 100


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
        with patch("requests.get", return_value=_resp(200, {"data": []})):
            out = fetch_facebook("p", token="t")
        assert out == PlatformMetrics(views=0, reach=0, likes=0, watch_time_ms=0, engagement=0)

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
