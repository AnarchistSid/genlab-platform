"""Tests for the canonical YouTube metrics fetcher.

This is the contract the three legacy paths (script, pipeline stage,
metric collector) are migrating onto — exercise it carefully so the
shape stays stable across consumers.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests
from genlab_core.platforms.metrics import PlatformMetrics, fetch_youtube


def _ok_response(stats: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"items": [{"statistics": stats}]}
    return resp


class TestFetchYouTubeHappyPath:
    def test_returns_canonical_metrics_shape(self, monkeypatch):
        monkeypatch.setenv("YOUTUBE_API_KEY", "key123")
        resp = _ok_response({"viewCount": "1500", "likeCount": "42", "commentCount": "7"})

        with patch("requests.get", return_value=resp):
            out = fetch_youtube("abc123")

        assert out == PlatformMetrics(
            views=1500,
            likes=42,
            comments=7,
            reach=1500,  # YT alias
            engagement=49,  # likes + comments
        )

    def test_explicit_api_key_overrides_env(self, monkeypatch):
        # No env var set; pass key explicitly (the per-niche credentials path).
        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
        resp = _ok_response({"viewCount": "10", "likeCount": "2", "commentCount": "0"})

        with patch("requests.get", return_value=resp) as mock_get:
            out = fetch_youtube("v1", api_key="explicit-key")

        assert out is not None
        assert mock_get.call_args.kwargs["params"]["key"] == "explicit-key"


class TestFetchYouTubeFailureModes:
    def test_returns_none_when_api_key_missing(self, monkeypatch):
        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
        assert fetch_youtube("any") is None

    def test_returns_none_on_non_200(self, monkeypatch):
        monkeypatch.setenv("YOUTUBE_API_KEY", "k")
        resp = MagicMock()
        resp.status_code = 403
        with patch("requests.get", return_value=resp):
            assert fetch_youtube("any") is None

    def test_returns_none_on_empty_items_post_deleted(self, monkeypatch):
        monkeypatch.setenv("YOUTUBE_API_KEY", "k")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"items": []}
        with patch("requests.get", return_value=resp):
            assert fetch_youtube("deleted") is None

    def test_returns_none_on_connection_error(self, monkeypatch):
        monkeypatch.setenv("YOUTUBE_API_KEY", "k")
        with patch(
            "requests.get",
            side_effect=requests.ConnectionError("boom"),
        ):
            assert fetch_youtube("any") is None


class TestNumericCoercion:
    @pytest.mark.parametrize(
        "stats,expected_views,expected_likes,expected_comments",
        [
            ({}, 0, 0, 0),  # no fields
            ({"viewCount": "0"}, 0, 0, 0),  # zero string
            ({"viewCount": None}, 0, 0, 0),  # null value
            ({"viewCount": "1234567"}, 1234567, 0, 0),  # big number
        ],
    )
    def test_missing_or_zero_fields_coerce_safely(
        self, monkeypatch, stats, expected_views, expected_likes, expected_comments
    ):
        monkeypatch.setenv("YOUTUBE_API_KEY", "k")
        with patch("requests.get", return_value=_ok_response(stats)):
            out = fetch_youtube("any")

        assert out is not None
        assert out["views"] == expected_views
        assert out["likes"] == expected_likes
        assert out["comments"] == expected_comments
        # engagement is always derived
        assert out["engagement"] == expected_likes + expected_comments


class TestDelegationFromScript:
    """Ensures scripts.run_fetch_insights._fetch_youtube delegates to the
    canonical implementation. Catches the class of bug that bit Gap 2 — where
    a parallel implementation drifted from the central one."""

    def test_script_delegate_passes_through(self, monkeypatch):
        monkeypatch.setenv("YOUTUBE_API_KEY", "k")
        from genlab_core.scripts.run_fetch_insights import _fetch_youtube as script_fetch

        with patch(
            "genlab_core.platforms.metrics.fetch_youtube",
            return_value=PlatformMetrics(views=99, likes=3, comments=1, reach=99, engagement=4),
        ) as mock_canonical:
            out = script_fetch("abc")

        mock_canonical.assert_called_once_with("abc")
        assert out == {"views": 99, "likes": 3, "comments": 1, "reach": 99, "engagement": 4}
