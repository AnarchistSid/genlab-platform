"""Threads poller tests — Sprint 29."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.engagement.poller import (
    THREADS_POLL_INTERVAL,
    poll_threads_comments,
)


# Shared fixture: mock resolve_threads_credentials to return a valid token
@pytest.fixture
def mock_threads_creds(monkeypatch):
    """Mock the credential resolver to return valid Threads credentials."""
    monkeypatch.setattr(
        "genlab_core.publishing.niche_credentials.resolve_threads_credentials",
        lambda niche_id: ("tok123", "myaccount"),
    )


def _make_me_resp(username="myaccount"):
    """Mock for the /me endpoint (Step 0: resolve own username)."""
    resp = MagicMock()
    resp.ok = True
    resp.json.return_value = {"username": username}
    return resp


def _make_media_resp(post_ids):
    """Mock for the /threads endpoint (Step 1: fetch recent posts)."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"data": [{"id": pid} for pid in post_ids]}
    return resp


def _make_replies_resp(replies, status_code=200):
    """Mock for the /replies endpoint (Step 2: fetch replies)."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"data": replies}
    return resp


class TestThreadsPollingConstant:
    def test_threads_poll_interval_is_10_minutes(self):
        assert THREADS_POLL_INTERVAL == 600

    def test_youtube_polls_less_frequently_than_threads(self):
        from genlab_core.engagement.poller import YOUTUBE_POLL_INTERVAL
        assert YOUTUBE_POLL_INTERVAL > THREADS_POLL_INTERVAL


class TestThreadsPollerFiltersOwnReplies:
    """Self-replies (from the polled account) must be excluded."""

    def test_own_replies_filtered_out(self, mock_threads_creds):
        me_resp = _make_me_resp("myaccount")
        media_resp = _make_media_resp(["post_1"])
        replies_resp = _make_replies_resp([
            {"id": "r_self", "text": "Thanks for watching!", "username": "myaccount", "timestamp": "2026-03-10T12:00:00Z"},
            {"id": "r_fan", "text": "Great content!", "username": "superfan", "timestamp": "2026-03-10T12:05:00Z"},
        ])

        def mock_get(url, **kwargs):
            if "/me" in url:
                return me_resp
            if "/threads" in url and "/replies" not in url:
                return media_resp
            return replies_resp

        with patch("requests.get", side_effect=mock_get):
            result = asyncio.run(poll_threads_comments("anime", "myaccount"))

        assert len(result) == 1
        assert result[0]["comment_id"] == "r_fan"
        assert result[0]["author_name"] == "superfan"
        assert result[0]["platform"] == "threads"

    def test_all_self_replies_returns_empty(self, mock_threads_creds):
        """If every reply is from the account owner, return empty."""
        me_resp = _make_me_resp("myaccount")
        media_resp = _make_media_resp(["post_1"])
        replies_resp = _make_replies_resp([
            {"id": "r1", "text": "First!", "username": "myaccount", "timestamp": "2026-03-10T12:00:00Z"},
        ])

        def mock_get(url, **kwargs):
            if "/me" in url:
                return me_resp
            if "/threads" in url and "/replies" not in url:
                return media_resp
            return replies_resp

        with patch("requests.get", side_effect=mock_get):
            result = asyncio.run(poll_threads_comments("anime", "myaccount"))

        assert result == []


class TestThreadsPollerHandlesPagination:
    """Poller must handle multiple posts with replies."""

    def test_collects_replies_across_multiple_posts(self, mock_threads_creds):
        me_resp = _make_me_resp("framedrift")
        media_resp = _make_media_resp(["post_1", "post_2"])

        replies_post1 = _make_replies_resp([
            {"id": "r1", "text": "Nice outfit!", "username": "user_a", "timestamp": "2026-03-10T10:00:00Z"},
        ])
        replies_post2 = _make_replies_resp([
            {"id": "r2", "text": "Where can I buy this?", "username": "user_b", "timestamp": "2026-03-10T11:00:00Z"},
        ])

        call_count = {"replies": 0}

        def mock_get(url, **kwargs):
            if "/me" in url:
                return me_resp
            if "/threads" in url and "/replies" not in url:
                return media_resp
            call_count["replies"] += 1
            return replies_post1 if call_count["replies"] == 1 else replies_post2

        with patch("requests.get", side_effect=mock_get):
            result = asyncio.run(poll_threads_comments("anime", "framedrift"))

        assert len(result) == 2
        assert result[0]["post_id"] == "post_1"
        assert result[1]["post_id"] == "post_2"
        assert result[0]["is_question"] is False
        assert result[1]["is_question"] is True

    def test_no_posts_returns_empty(self, mock_threads_creds):
        """If the account has no recent posts, return empty."""
        me_resp = _make_me_resp("framedrift")
        media_resp = _make_media_resp([])

        def mock_get(url, **kwargs):
            if "/me" in url:
                return me_resp
            return media_resp

        with patch("requests.get", side_effect=mock_get):
            result = asyncio.run(poll_threads_comments("anime", "framedrift"))

        assert result == []


class TestThreadsPollerErrorHandling:
    """API errors must not crash the poller."""

    def test_returns_empty_on_api_error(self, mock_threads_creds):
        """Network/API errors should return empty list, not raise."""
        import requests as _requests

        me_resp = _make_me_resp()
        media_resp = MagicMock()
        media_resp.raise_for_status.side_effect = _requests.exceptions.HTTPError(
            "401 Unauthorized"
        )

        def mock_get(url, **kwargs):
            if "/me" in url:
                return me_resp
            return media_resp

        with patch("requests.get", side_effect=mock_get):
            result = asyncio.run(poll_threads_comments("anime", "framedrift"))

        assert result == []

    def test_skips_posts_with_failed_replies(self, mock_threads_creds):
        """If replies fetch fails for one post, other posts still get processed."""
        me_resp = _make_me_resp("framedrift")
        media_resp = _make_media_resp(["post_ok", "post_fail"])

        ok_resp = _make_replies_resp([
            {"id": "r1", "text": "Love it", "username": "fan", "timestamp": "2026-03-10T10:00:00Z"},
        ])
        fail_resp = _make_replies_resp([], status_code=403)

        call_count = {"replies": 0}

        def mock_get(url, **kwargs):
            if "/me" in url:
                return me_resp
            if "/threads" in url and "/replies" not in url:
                return media_resp
            call_count["replies"] += 1
            return ok_resp if call_count["replies"] == 1 else fail_resp

        with patch("requests.get", side_effect=mock_get):
            result = asyncio.run(poll_threads_comments("anime", "framedrift"))

        assert len(result) == 1
        assert result[0]["comment_id"] == "r1"

    def test_missing_credentials_returns_empty(self, monkeypatch):
        """No token set should skip gracefully."""
        monkeypatch.setattr(
            "genlab_core.publishing.niche_credentials.resolve_threads_credentials",
            lambda niche_id: (None, None),
        )

        result = asyncio.run(poll_threads_comments("anime", "framedrift"))
        assert result == []

    def test_falls_back_to_meta_access_token(self, monkeypatch):
        """If THREADS_ACCESS_TOKEN is unset, fall back to META_ACCESS_TOKEN."""
        # The credential resolver handles fallback internally;
        # test that the token it returns is actually used in API calls.
        monkeypatch.setattr(
            "genlab_core.publishing.niche_credentials.resolve_threads_credentials",
            lambda niche_id: ("meta_tok", "framedrift"),
        )

        me_resp = _make_me_resp("framedrift")
        media_resp = _make_media_resp(["post_1"])
        replies_resp = _make_replies_resp([
            {"id": "r1", "text": "Cool", "username": "fan", "timestamp": "2026-03-10T10:00:00Z"},
        ])

        captured_params = {}

        def mock_get(url, **kwargs):
            captured_params["last_params"] = kwargs.get("params", {})
            if "/me" in url:
                return me_resp
            if "/threads" in url and "/replies" not in url:
                return media_resp
            return replies_resp

        with patch("requests.get", side_effect=mock_get):
            result = asyncio.run(poll_threads_comments("anime", "framedrift"))

        assert len(result) == 1
        assert captured_params["last_params"].get("access_token") == "meta_tok"
