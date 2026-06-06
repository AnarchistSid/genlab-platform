"""Tests for the canonical X (Twitter) metrics fetcher."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests
from genlab_core.platforms.metrics import PlatformMetrics, fetch_twitter


def _resp(status: int, json_data: dict) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.json.return_value = json_data
    m.text = "(mocked)"
    return m


def _tweet(**metrics) -> dict:
    return {"data": {"public_metrics": metrics}}


class TestFetchTwitterHappyPath:
    def test_returns_canonical_shape(self, monkeypatch):
        monkeypatch.setenv("X_BEARER_TOKEN", "BEARER")
        payload = _tweet(
            like_count=80,
            retweet_count=12,
            reply_count=5,
            impression_count=10_000,
        )
        with patch("requests.get", return_value=_resp(200, payload)) as mock_get:
            out = fetch_twitter("17841234")

        assert out == PlatformMetrics(
            views=10_000,
            impressions=10_000,
            reach=10_000,
            likes=80,
            comments=5,
            shares=12,
            engagement=97,
        )
        # Bearer-token Authorization header is set correctly.
        assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer BEARER"

    def test_explicit_bearer_token_overrides_env(self, monkeypatch):
        monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
        with patch(
            "requests.get",
            return_value=_resp(200, _tweet(like_count=1, retweet_count=0, reply_count=0)),
        ) as mock_get:
            out = fetch_twitter("id", bearer_token="from-kwarg")

        assert out is not None
        assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer from-kwarg"


class TestFetchTwitterFailureModes:
    def test_returns_none_when_no_token(self, monkeypatch):
        monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
        assert fetch_twitter("id") is None

    def test_returns_none_on_non_200(self, monkeypatch):
        monkeypatch.setenv("X_BEARER_TOKEN", "B")
        with patch("requests.get", return_value=_resp(401, {})):
            assert fetch_twitter("id") is None

    def test_returns_none_on_connection_error(self, monkeypatch):
        monkeypatch.setenv("X_BEARER_TOKEN", "B")
        with patch("requests.get", side_effect=requests.ConnectionError("boom")):
            assert fetch_twitter("id") is None

    def test_handles_missing_data_or_public_metrics(self, monkeypatch):
        monkeypatch.setenv("X_BEARER_TOKEN", "B")
        with patch("requests.get", return_value=_resp(200, {})):
            out = fetch_twitter("id")
        # All fields zero — but the shape is still returned.
        assert out == PlatformMetrics(
            views=0, impressions=0, reach=0, likes=0, comments=0, shares=0, engagement=0
        )


class TestNumericCoercion:
    @pytest.mark.parametrize(
        "raw,expected_likes,expected_views",
        [
            ({}, 0, 0),
            ({"like_count": None}, 0, 0),
            ({"like_count": 0, "impression_count": 0}, 0, 0),
            (
                {"like_count": 999_999_999, "impression_count": 999_999_999},
                999_999_999,
                999_999_999,
            ),
        ],
    )
    def test_coerces_safely(self, monkeypatch, raw, expected_likes, expected_views):
        monkeypatch.setenv("X_BEARER_TOKEN", "B")
        with patch("requests.get", return_value=_resp(200, _tweet(**raw))):
            out = fetch_twitter("id")
        assert out is not None
        assert out["likes"] == expected_likes
        assert out["views"] == expected_views


class TestDelegationFromScript:
    def test_script_delegate_adds_legacy_aliases(self, monkeypatch):
        from genlab_core.scripts.run_fetch_insights import (
            _fetch_twitter as script_fetch,
        )

        canonical = PlatformMetrics(
            views=10_000,
            impressions=10_000,
            reach=10_000,
            likes=80,
            comments=5,
            shares=12,
            engagement=97,
        )
        with patch(
            "genlab_core.platforms.metrics.fetch_twitter",
            return_value=canonical,
        ) as mock_canonical:
            out = script_fetch("id")

        mock_canonical.assert_called_once_with("id")
        # Canonical preserved
        assert out["likes"] == 80
        assert out["comments"] == 5
        assert out["shares"] == 12
        # Legacy aliases
        assert out["retweets"] == 12
        assert out["replies"] == 5
