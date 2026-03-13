"""Tests for engagement platform clients — YouTube, X/Twitter, Instagram, Facebook, Threads."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------


class TestYoutubeClient:
    def test_post_reply_calls_correct_api(self, monkeypatch):
        """post_youtube_reply posts a comment via the YouTube API."""
        monkeypatch.setenv("YOUTUBE_CLIENT_ID", "cid")
        monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "csec")
        monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", "rtok")

        mock_token_resp = MagicMock()
        mock_token_resp.json.return_value = {"access_token": "at123"}
        mock_token_resp.raise_for_status = MagicMock()

        mock_comment_resp = MagicMock()
        mock_comment_resp.raise_for_status = MagicMock()

        with patch(
            "genlab_core.engagement.platform_clients.youtube.requests.post",
            side_effect=[mock_token_resp, mock_comment_resp],
        ) as mock_post:
            from genlab_core.engagement.platform_clients.youtube import post_youtube_reply

            result = post_youtube_reply("vid_1", "parent_1", "Nice clip!")

        assert result is True
        # Second call is the comment insert
        comment_call = mock_post.call_args_list[1]
        assert "/comments" in comment_call.args[0]
        assert comment_call.kwargs["json"]["snippet"]["parentId"] == "parent_1"

    def test_post_reply_returns_false_on_error(self, monkeypatch):
        """post_youtube_reply returns False on failure instead of raising."""
        monkeypatch.setenv("YOUTUBE_CLIENT_ID", "cid")
        monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "csec")
        monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", "rtok")

        with patch(
            "genlab_core.engagement.platform_clients.youtube._get_access_token",
            side_effect=RuntimeError("auth fail"),
        ):
            from genlab_core.engagement.platform_clients.youtube import post_youtube_reply

            result = post_youtube_reply("vid_1", "parent_1", "text")

        assert result is False


# ---------------------------------------------------------------------------
# X/Twitter
# ---------------------------------------------------------------------------


class TestXTwitterClient:
    def test_post_reply_calls_create_tweet(self, monkeypatch):
        """post_x_reply calls tweepy create_tweet with correct params."""
        monkeypatch.setenv("X_API_KEY", "ak")
        monkeypatch.setenv("X_API_SECRET", "as")
        monkeypatch.setenv("X_ACCESS_TOKEN", "at")
        monkeypatch.setenv("X_ACCESS_SECRET", "ats")

        with patch("tweepy.Client") as MockClient:
            mock_instance = MockClient.return_value
            from genlab_core.engagement.platform_clients.x_twitter import post_x_reply

            post_x_reply("tweet_1", "Great point!")

        mock_instance.create_tweet.assert_called_once_with(
            text="Great point!", in_reply_to_tweet_id="tweet_1"
        )

    def test_like_does_not_raise_on_error(self, monkeypatch):
        """like_x_tweet catches exceptions gracefully."""
        monkeypatch.setenv("X_API_KEY", "ak")
        monkeypatch.setenv("X_API_SECRET", "as")
        monkeypatch.setenv("X_ACCESS_TOKEN", "at")
        monkeypatch.setenv("X_ACCESS_SECRET", "ats")

        with patch("tweepy.Client") as MockClient:
            MockClient.return_value.like.side_effect = RuntimeError("rate limited")
            from genlab_core.engagement.platform_clients.x_twitter import like_x_tweet

            # Should NOT raise
            like_x_tweet("tweet_1")


# ---------------------------------------------------------------------------
# Instagram
# ---------------------------------------------------------------------------


class TestInstagramClient:
    def test_post_reply_uses_graph_facebook_com(self, monkeypatch):
        """post_instagram_reply calls graph.facebook.com, not graph.instagram.com."""
        monkeypatch.setenv("META_ACCESS_TOKEN", "EAA_token")

        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            mock_post.return_value.raise_for_status = MagicMock()

            from genlab_core.engagement.platform_clients.instagram import post_instagram_reply

            post_instagram_reply("media_1", "comment_1", "Thanks!")

        call_url = mock_post.call_args[0][0]
        assert "graph.facebook.com" in call_url
        assert "graph.instagram.com" not in call_url
        assert "comment_1/replies" in call_url

    def test_like_does_not_raise_on_error(self, monkeypatch):
        """like_instagram_comment catches exceptions gracefully."""
        monkeypatch.setenv("META_ACCESS_TOKEN", "EAA_token")

        with patch("requests.post", side_effect=ConnectionError("timeout")):
            from genlab_core.engagement.platform_clients.instagram import like_instagram_comment

            # Should NOT raise
            like_instagram_comment("comment_1")


# ---------------------------------------------------------------------------
# Facebook
# ---------------------------------------------------------------------------


class TestFacebookClient:
    def test_post_reply_calls_correct_endpoint(self, monkeypatch):
        """post_facebook_reply calls /comments endpoint on graph.facebook.com."""
        monkeypatch.setenv("META_ACCESS_TOKEN", "EAA_token")

        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            mock_post.return_value.raise_for_status = MagicMock()

            from genlab_core.engagement.platform_clients.facebook import post_facebook_reply

            post_facebook_reply("comment_1", "Good question!")

        call_url = mock_post.call_args[0][0]
        assert "graph.facebook.com/v21.0/comment_1/comments" in call_url


# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------


class TestThreadsClient:
    def test_post_reply_calls_threads_endpoint(self, monkeypatch):
        """post_threads_reply calls graph.threads.net with correct token."""
        monkeypatch.setenv("THREADS_ACCESS_TOKEN", "threads_tok")

        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            mock_post.return_value.raise_for_status = MagicMock()

            from genlab_core.engagement.platform_clients.threads import post_threads_reply

            post_threads_reply("thread_1", "Agreed!")

        call_url = mock_post.call_args[0][0]
        assert "graph.threads.net/v1.0/thread_1/replies" in call_url

    def test_post_reply_returns_false_on_error(self, monkeypatch):
        """post_threads_reply returns False on failure."""
        monkeypatch.setenv("THREADS_ACCESS_TOKEN", "threads_tok")

        with patch("requests.post", side_effect=RuntimeError("boom")):
            from genlab_core.engagement.platform_clients.threads import post_threads_reply

            result = post_threads_reply("thread_1", "text")

        assert result is False

    def test_post_reply_returns_false_when_no_token(self, monkeypatch):
        """post_threads_reply returns False when no token is set."""
        monkeypatch.delenv("THREADS_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)

        from genlab_core.engagement.platform_clients.threads import post_threads_reply

        result = post_threads_reply("thread_1", "text")
        assert result is False


# ---------------------------------------------------------------------------
# Return-value consistency across all clients
# ---------------------------------------------------------------------------


class TestReturnValues:
    def test_instagram_returns_true_on_success(self, monkeypatch):
        """post_instagram_reply returns True on success."""
        monkeypatch.setenv("META_ACCESS_TOKEN", "EAA_token")

        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            mock_post.return_value.raise_for_status = MagicMock()

            from genlab_core.engagement.platform_clients.instagram import post_instagram_reply

            assert post_instagram_reply("media_1", "c_1", "hi") is True

    def test_instagram_returns_false_on_error(self, monkeypatch):
        """post_instagram_reply returns False on failure."""
        monkeypatch.setenv("META_ACCESS_TOKEN", "EAA_token")

        with patch("requests.post", side_effect=ConnectionError("timeout")):
            from genlab_core.engagement.platform_clients.instagram import post_instagram_reply

            assert post_instagram_reply("media_1", "c_1", "hi") is False

    def test_facebook_returns_true_on_success(self, monkeypatch):
        """post_facebook_reply returns True on success."""
        monkeypatch.setenv("META_ACCESS_TOKEN", "EAA_token")

        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            mock_post.return_value.raise_for_status = MagicMock()

            from genlab_core.engagement.platform_clients.facebook import post_facebook_reply

            assert post_facebook_reply("c_1", "hi") is True

    def test_x_returns_true_on_success(self, monkeypatch):
        """post_x_reply returns True on success."""
        monkeypatch.setenv("X_API_KEY", "ak")
        monkeypatch.setenv("X_API_SECRET", "as")
        monkeypatch.setenv("X_ACCESS_TOKEN", "at")
        monkeypatch.setenv("X_ACCESS_SECRET", "ats")

        with patch("tweepy.Client") as MockClient:
            from genlab_core.engagement.platform_clients.x_twitter import post_x_reply

            assert post_x_reply("tweet_1", "hi") is True

    def test_x_returns_false_on_error(self, monkeypatch):
        """post_x_reply returns False on failure."""
        monkeypatch.setenv("X_API_KEY", "ak")
        monkeypatch.setenv("X_API_SECRET", "as")
        monkeypatch.setenv("X_ACCESS_TOKEN", "at")
        monkeypatch.setenv("X_ACCESS_SECRET", "ats")

        with patch("tweepy.Client") as MockClient:
            MockClient.return_value.create_tweet.side_effect = RuntimeError("fail")
            from genlab_core.engagement.platform_clients.x_twitter import post_x_reply

            assert post_x_reply("tweet_1", "hi") is False
