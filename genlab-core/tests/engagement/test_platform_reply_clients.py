"""Tests for engagement platform reply clients."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from genlab_core.engagement.platform_clients import get_reply_client


class TestReplyClientRegistry:
    def test_youtube_client_exists(self):
        client = get_reply_client("youtube")
        assert client is not None
        assert hasattr(client, "post_reply")

    def test_instagram_client_exists(self):
        client = get_reply_client("instagram")
        assert client is not None
        assert hasattr(client, "post_reply")

    def test_twitter_client_exists(self):
        client = get_reply_client("twitter")
        assert client is not None
        assert hasattr(client, "post_reply")

    def test_facebook_client_exists(self):
        client = get_reply_client("facebook")
        assert client is not None
        assert hasattr(client, "post_reply")

    def test_threads_client_exists(self):
        client = get_reply_client("threads")
        assert client is not None
        assert hasattr(client, "post_reply")

    def test_x_alias_returns_twitter_client(self):
        """'x' should resolve to the same client class as 'twitter'."""
        client = get_reply_client("x")
        assert client is not None
        assert hasattr(client, "post_reply")
        assert type(client).__name__ == "TwitterReplyClient"

    def test_unknown_platform_returns_none(self):
        client = get_reply_client("tiktok")
        assert client is None

    def test_case_insensitive_lookup(self):
        client = get_reply_client("YouTube")
        assert client is not None
        assert type(client).__name__ == "YouTubeReplyClient"


class TestYouTubeReplyClient:
    @patch("genlab_core.engagement.platform_clients.youtube_reply.requests.post")
    @patch("genlab_core.engagement.platform_clients.youtube_reply.resolve_youtube_credentials")
    def test_post_reply_success(self, mock_creds, mock_post):
        mock_creds.return_value = {
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "rtoken",
        }
        # Mock token refresh
        token_resp = MagicMock()
        token_resp.status_code = 200
        token_resp.json.return_value = {"access_token": "fresh_token"}
        token_resp.raise_for_status = MagicMock()

        # Mock comment insert
        insert_resp = MagicMock()
        insert_resp.status_code = 200
        insert_resp.json.return_value = {"id": "reply123"}
        insert_resp.raise_for_status = MagicMock()

        mock_post.side_effect = [token_resp, insert_resp]

        client = get_reply_client("youtube")
        result = client.post_reply("comment123", "Great video!", "gaming")
        assert result is True

    @patch("genlab_core.engagement.platform_clients.youtube_reply.resolve_youtube_credentials")
    def test_post_reply_no_credentials(self, mock_creds):
        mock_creds.return_value = {"client_id": "", "client_secret": "", "refresh_token": ""}
        client = get_reply_client("youtube")
        result = client.post_reply("c1", "reply", "gaming")
        assert result is False


class TestInstagramReplyClient:
    @patch("genlab_core.engagement.platform_clients.instagram_reply.requests.post")
    @patch("genlab_core.engagement.platform_clients.instagram_reply.resolve_meta_credentials")
    def test_post_reply_success(self, mock_creds, mock_post):
        mock_creds.return_value = {
            "ig_access_token": "eaa_token",
            "ig_user_id": "12345",
            "fb_access_token": "",
            "fb_page_id": "",
        }
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"id": "reply456"}
        resp.raise_for_status = MagicMock()
        mock_post.return_value = resp

        client = get_reply_client("instagram")
        result = client.post_reply("comment456", "Thanks!", "ai_creators")
        assert result is True
        # Must use graph.facebook.com (never graph.instagram.com)
        call_url = mock_post.call_args[0][0]
        assert "graph.facebook.com" in call_url
        assert "graph.instagram.com" not in call_url

    @patch("genlab_core.engagement.platform_clients.instagram_reply.resolve_meta_credentials")
    def test_post_reply_no_token(self, mock_creds):
        mock_creds.return_value = {
            "ig_access_token": "",
            "ig_user_id": "",
            "fb_access_token": "",
            "fb_page_id": "",
        }
        client = get_reply_client("instagram")
        assert client.post_reply("c1", "reply", "gaming") is False


class TestFacebookReplyClient:
    @patch("genlab_core.engagement.platform_clients.facebook_reply.requests.post")
    @patch("genlab_core.engagement.platform_clients.facebook_reply.resolve_meta_credentials")
    def test_post_reply_success(self, mock_creds, mock_post):
        mock_creds.return_value = {
            "ig_access_token": "",
            "ig_user_id": "",
            "fb_access_token": "fb_token",
            "fb_page_id": "page123",
        }
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"id": "reply789"}
        resp.raise_for_status = MagicMock()
        mock_post.return_value = resp

        client = get_reply_client("facebook")
        result = client.post_reply("comment789", "Great point!", "movies")
        assert result is True
        call_url = mock_post.call_args[0][0]
        assert "graph.facebook.com" in call_url

    @patch("genlab_core.engagement.platform_clients.facebook_reply.resolve_meta_credentials")
    def test_post_reply_no_token(self, mock_creds):
        mock_creds.return_value = {
            "ig_access_token": "",
            "ig_user_id": "",
            "fb_access_token": "",
            "fb_page_id": "",
        }
        client = get_reply_client("facebook")
        assert client.post_reply("c1", "reply", "gaming") is False


class TestTwitterReplyClient:
    @patch("genlab_core.engagement.platform_clients.twitter_reply.resolve_twitter_credentials")
    def test_post_reply_no_credentials(self, mock_creds):
        mock_creds.return_value = {
            "api_key": "",
            "api_secret": "",
            "access_token": "",
            "access_secret": "",
        }
        client = get_reply_client("twitter")
        assert client.post_reply("t1", "reply", "gaming") is False

    @patch("genlab_core.engagement.platform_clients.twitter_reply.tweepy")
    @patch("genlab_core.engagement.platform_clients.twitter_reply.resolve_twitter_credentials")
    def test_post_reply_success(self, mock_creds, mock_tweepy):
        mock_creds.return_value = {
            "api_key": "k",
            "api_secret": "s",
            "access_token": "at",
            "access_secret": "as",
        }
        mock_client = MagicMock()
        mock_tweepy.Client.return_value = mock_client
        mock_client.create_tweet.return_value = MagicMock(data={"id": "12345"})

        client = get_reply_client("twitter")
        result = client.post_reply("tweet123", "Thanks!", "gaming")
        assert result is True
        mock_client.create_tweet.assert_called_once_with(
            text="Thanks!",
            in_reply_to_tweet_id="tweet123",
        )


class TestThreadsReplyClient:
    @patch("genlab_core.engagement.platform_clients.threads_reply.resolve_threads_credentials")
    def test_post_reply_no_credentials(self, mock_creds):
        mock_creds.return_value = ("", "")
        client = get_reply_client("threads")
        assert client.post_reply("t1", "reply", "gaming") is False

    @patch("genlab_core.engagement.platform_clients.threads_reply.requests.post")
    @patch("genlab_core.engagement.platform_clients.threads_reply.resolve_threads_credentials")
    def test_post_reply_success(self, mock_creds, mock_post):
        mock_creds.return_value = ("threads_token", "user123")
        # Threads reply is a two-step process: create container, then publish
        create_resp = MagicMock()
        create_resp.status_code = 200
        create_resp.json.return_value = {"id": "container_id"}
        create_resp.raise_for_status = MagicMock()

        publish_resp = MagicMock()
        publish_resp.status_code = 200
        publish_resp.json.return_value = {"id": "published_id"}
        publish_resp.raise_for_status = MagicMock()

        mock_post.side_effect = [create_resp, publish_resp]

        client = get_reply_client("threads")
        result = client.post_reply("thread_post_123", "Nice!", "anime")
        assert result is True
