"""Threads poller tests — Sprint 29."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch


from genlab_core.engagement.poller import (
    THREADS_POLL_INTERVAL,
    poll_threads_comments,
)


class TestThreadsPollingConstant:
    def test_threads_poll_interval_is_10_minutes(self):
        assert THREADS_POLL_INTERVAL == 600

    def test_youtube_polls_less_frequently_than_threads(self):
        from genlab_core.engagement.poller import YOUTUBE_POLL_INTERVAL
        # YouTube polls less often (30min) to conserve quota
        assert YOUTUBE_POLL_INTERVAL > THREADS_POLL_INTERVAL


class TestThreadsPollerFiltersOwnReplies:
    """Self-replies (from the polled account) must be excluded."""

    def test_own_replies_filtered_out(self, monkeypatch):
        monkeypatch.setenv("THREADS_ACCESS_TOKEN", "tok123")

        # Mock media listing — 1 post
        media_resp = MagicMock()
        media_resp.raise_for_status = MagicMock()
        media_resp.json.return_value = {"data": [{"id": "post_1"}]}

        # Mock replies — one self-reply + one external reply
        replies_resp = MagicMock()
        replies_resp.status_code = 200
        replies_resp.json.return_value = {
            "data": [
                {"id": "r_self", "text": "Thanks for watching!", "username": "myaccount", "timestamp": "2026-03-10T12:00:00Z"},
                {"id": "r_fan", "text": "Great content!", "username": "superfan", "timestamp": "2026-03-10T12:05:00Z"},
            ]
        }

        def mock_get(url, **kwargs):
            if "/threads" in url and "/replies" not in url:
                return media_resp
            return replies_resp

        with patch("requests.get", side_effect=mock_get):
            result = asyncio.run(poll_threads_comments("anime", "myaccount"))

        assert len(result) == 1
        assert result[0]["comment_id"] == "r_fan"
        assert result[0]["author_name"] == "superfan"
        assert result[0]["platform"] == "threads"

    def test_all_self_replies_returns_empty(self, monkeypatch):
        """If every reply is from the account owner, return empty."""
        monkeypatch.setenv("THREADS_ACCESS_TOKEN", "tok123")

        media_resp = MagicMock()
        media_resp.raise_for_status = MagicMock()
        media_resp.json.return_value = {"data": [{"id": "post_1"}]}

        replies_resp = MagicMock()
        replies_resp.status_code = 200
        replies_resp.json.return_value = {
            "data": [
                {"id": "r1", "text": "First!", "username": "myaccount", "timestamp": "2026-03-10T12:00:00Z"},
            ]
        }

        def mock_get(url, **kwargs):
            if "/threads" in url and "/replies" not in url:
                return media_resp
            return replies_resp

        with patch("requests.get", side_effect=mock_get):
            result = asyncio.run(poll_threads_comments("anime", "myaccount"))

        assert result == []


class TestThreadsPollerHandlesPagination:
    """Poller must handle multiple posts with replies."""

    def test_collects_replies_across_multiple_posts(self, monkeypatch):
        monkeypatch.setenv("THREADS_ACCESS_TOKEN", "tok123")

        media_resp = MagicMock()
        media_resp.raise_for_status = MagicMock()
        media_resp.json.return_value = {
            "data": [{"id": "post_1"}, {"id": "post_2"}]
        }

        replies_post1 = MagicMock()
        replies_post1.status_code = 200
        replies_post1.json.return_value = {
            "data": [
                {"id": "r1", "text": "Nice outfit!", "username": "user_a", "timestamp": "2026-03-10T10:00:00Z"},
            ]
        }

        replies_post2 = MagicMock()
        replies_post2.status_code = 200
        replies_post2.json.return_value = {
            "data": [
                {"id": "r2", "text": "Where can I buy this?", "username": "user_b", "timestamp": "2026-03-10T11:00:00Z"},
            ]
        }

        call_count = {"replies": 0}

        def mock_get(url, **kwargs):
            if "/threads" in url and "/replies" not in url:
                return media_resp
            call_count["replies"] += 1
            return replies_post1 if call_count["replies"] == 1 else replies_post2

        with patch("requests.get", side_effect=mock_get):
            result = asyncio.run(poll_threads_comments("anime", "framedrift"))

        assert len(result) == 2
        assert result[0]["post_id"] == "post_1"
        assert result[1]["post_id"] == "post_2"
        # Question detection
        assert result[0]["is_question"] is False
        assert result[1]["is_question"] is True

    def test_no_posts_returns_empty(self, monkeypatch):
        """If the account has no recent posts, return empty."""
        monkeypatch.setenv("THREADS_ACCESS_TOKEN", "tok123")

        media_resp = MagicMock()
        media_resp.raise_for_status = MagicMock()
        media_resp.json.return_value = {"data": []}

        with patch("requests.get", return_value=media_resp):
            result = asyncio.run(poll_threads_comments("anime", "framedrift"))

        assert result == []


class TestThreadsPollerErrorHandling:
    """API errors must not crash the poller."""

    def test_returns_empty_on_api_error(self, monkeypatch):
        """Network/API errors should return empty list, not raise."""
        monkeypatch.setenv("THREADS_ACCESS_TOKEN", "tok123")

        import requests as _requests

        media_resp = MagicMock()
        media_resp.raise_for_status.side_effect = _requests.exceptions.HTTPError(
            "401 Unauthorized"
        )

        with patch("requests.get", return_value=media_resp):
            result = asyncio.run(poll_threads_comments("anime", "framedrift"))

        assert result == []

    def test_skips_posts_with_failed_replies(self, monkeypatch):
        """If replies fetch fails for one post, other posts still get processed."""
        monkeypatch.setenv("THREADS_ACCESS_TOKEN", "tok123")

        media_resp = MagicMock()
        media_resp.raise_for_status = MagicMock()
        media_resp.json.return_value = {
            "data": [{"id": "post_ok"}, {"id": "post_fail"}]
        }

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {
            "data": [
                {"id": "r1", "text": "Love it", "username": "fan", "timestamp": "2026-03-10T10:00:00Z"},
            ]
        }

        fail_resp = MagicMock()
        fail_resp.status_code = 403

        call_count = {"replies": 0}

        def mock_get(url, **kwargs):
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
        monkeypatch.delenv("THREADS_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)

        result = asyncio.run(poll_threads_comments("anime", "framedrift"))
        assert result == []

    def test_falls_back_to_meta_access_token(self, monkeypatch):
        """If THREADS_ACCESS_TOKEN is unset, fall back to META_ACCESS_TOKEN."""
        monkeypatch.delenv("THREADS_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("META_ACCESS_TOKEN", "meta_tok")

        media_resp = MagicMock()
        media_resp.raise_for_status = MagicMock()
        media_resp.json.return_value = {"data": [{"id": "post_1"}]}

        replies_resp = MagicMock()
        replies_resp.status_code = 200
        replies_resp.json.return_value = {
            "data": [
                {"id": "r1", "text": "Cool", "username": "fan", "timestamp": "2026-03-10T10:00:00Z"},
            ]
        }

        captured_params = {}

        def mock_get(url, **kwargs):
            captured_params["last_params"] = kwargs.get("params", {})
            if "/threads" in url and "/replies" not in url:
                return media_resp
            return replies_resp

        with patch("requests.get", side_effect=mock_get):
            result = asyncio.run(poll_threads_comments("anime", "framedrift"))

        assert len(result) == 1
        # Verify it used meta_tok
        assert captured_params["last_params"].get("access_token") == "meta_tok"
