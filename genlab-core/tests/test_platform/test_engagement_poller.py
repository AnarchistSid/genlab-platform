"""Tests for engagement_poller — poll_youtube_comments and poll_twitter_mentions."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# poll_youtube_comments
# ---------------------------------------------------------------------------


class TestPollYoutubeComments:
    """Verify YouTube comment polling via Data API v3."""

    def test_returns_correct_dict_keys_on_success(self, monkeypatch):
        """poll_youtube_comments returns dicts with InboundComment-compatible keys."""
        monkeypatch.setenv("YOUTUBE_API_KEY", "test_api_key")
        monkeypatch.delenv("YOUTUBE_CLIENT_ID", raising=False)
        monkeypatch.delenv("YOUTUBE_CLIENT_SECRET", raising=False)
        monkeypatch.delenv("YOUTUBE_REFRESH_TOKEN", raising=False)

        fake_playlist_resp = MagicMock()
        fake_playlist_resp.raise_for_status = MagicMock()
        fake_playlist_resp.json.return_value = {
            "items": [{"contentDetails": {"videoId": "vid_1"}}]
        }

        fake_comments_resp = MagicMock()
        fake_comments_resp.status_code = 200
        fake_comments_resp.raise_for_status = MagicMock()
        fake_comments_resp.json.return_value = {
            "items": [
                {
                    "id": "comment_1",
                    "snippet": {
                        "topLevelComment": {
                            "snippet": {
                                "videoId": "vid_1",
                                "authorChannelId": {"value": "auth_1"},
                                "authorDisplayName": "Alice",
                                "textOriginal": "Great video!",
                            }
                        }
                    },
                },
                {
                    "id": "comment_2",
                    "snippet": {
                        "topLevelComment": {
                            "snippet": {
                                "videoId": "vid_1",
                                "authorChannelId": {"value": "auth_2"},
                                "authorDisplayName": "Bob",
                                "textOriginal": "What game is this?",
                            }
                        }
                    },
                },
            ]
        }

        def mock_get(url, **kwargs):
            if "playlistItems" in url:
                return fake_playlist_resp
            return fake_comments_resp

        with patch("requests.get", side_effect=mock_get):
            from genlab_core.engagement.poller import poll_youtube_comments

            result = asyncio.run(poll_youtube_comments("gaming", "UC_test"))

        assert len(result) == 2
        expected_keys = {"platform", "post_id", "comment_id", "author_id", "author_name", "text", "is_question"}
        for item in result:
            assert expected_keys.issubset(item.keys())
        assert result[0]["platform"] == "youtube"
        assert result[0]["comment_id"] == "comment_1"
        assert result[0]["author_name"] == "Alice"
        assert result[1]["is_question"] is True

    def test_returns_empty_on_api_error(self, monkeypatch):
        """poll_youtube_comments returns [] when the API call fails."""
        monkeypatch.setenv("YOUTUBE_CLIENT_ID", "cid")
        monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "csec")
        monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", "rtok")

        with patch("requests.post", side_effect=ConnectionError("network down")):
            from genlab_core.engagement.poller import poll_youtube_comments

            result = asyncio.run(poll_youtube_comments("gaming", "UC_test"))

        assert result == []

    def test_returns_empty_when_credentials_missing(self, monkeypatch):
        """poll_youtube_comments returns [] if env vars are not set."""
        monkeypatch.delenv("YOUTUBE_CLIENT_ID", raising=False)
        monkeypatch.delenv("YOUTUBE_CLIENT_SECRET", raising=False)
        monkeypatch.delenv("YOUTUBE_REFRESH_TOKEN", raising=False)

        from genlab_core.engagement.poller import poll_youtube_comments

        result = asyncio.run(poll_youtube_comments("gaming", "UC_test"))
        assert result == []


# ---------------------------------------------------------------------------
# poll_twitter_mentions
# ---------------------------------------------------------------------------


class TestPollTwitterMentions:
    """Verify X/Twitter mention polling via tweepy."""

    def test_returns_correct_dict_keys_on_success(self, monkeypatch):
        """poll_twitter_mentions returns dicts with InboundComment-compatible keys."""
        monkeypatch.setenv("X_API_KEY", "ak")
        monkeypatch.setenv("X_API_SECRET", "as")
        monkeypatch.setenv("X_ACCESS_TOKEN", "at")
        monkeypatch.setenv("X_ACCESS_SECRET", "ats")

        mock_tweet = MagicMock()
        mock_tweet.id = "tweet_1"
        mock_tweet.author_id = "uid_1"
        mock_tweet.text = "Hey @brand, nice clip?"

        mock_user = MagicMock()
        mock_user.id = "uid_1"
        mock_user.username = "tweeter"

        mock_response = MagicMock()
        mock_response.data = [mock_tweet]
        mock_response.includes = {"users": [mock_user]}

        with patch("tweepy.Client") as MockClient:
            MockClient.return_value.get_users_mentions.return_value = mock_response

            from genlab_core.engagement.poller import poll_twitter_mentions

            result = asyncio.run(poll_twitter_mentions("gaming", "12345"))

        assert len(result) == 1
        expected_keys = {"platform", "post_id", "comment_id", "author_id", "author_name", "text", "is_question"}
        assert expected_keys.issubset(result[0].keys())
        assert result[0]["platform"] == "twitter"
        assert result[0]["author_name"] == "tweeter"
        assert result[0]["is_question"] is True

    def test_returns_empty_on_error(self, monkeypatch):
        """poll_twitter_mentions returns [] when tweepy raises."""
        monkeypatch.setenv("X_API_KEY", "ak")
        monkeypatch.setenv("X_API_SECRET", "as")
        monkeypatch.setenv("X_ACCESS_TOKEN", "at")
        monkeypatch.setenv("X_ACCESS_SECRET", "ats")

        with patch("tweepy.Client") as MockClient:
            MockClient.return_value.get_users_mentions.side_effect = RuntimeError("api fail")

            from genlab_core.engagement.poller import poll_twitter_mentions

            result = asyncio.run(poll_twitter_mentions("gaming", "12345"))

        assert result == []

    def test_returns_empty_when_credentials_missing(self, monkeypatch):
        """poll_twitter_mentions returns [] if env vars are not set."""
        monkeypatch.delenv("X_API_KEY", raising=False)
        monkeypatch.delenv("X_API_SECRET", raising=False)
        monkeypatch.delenv("X_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("X_ACCESS_SECRET", raising=False)

        from genlab_core.engagement.poller import poll_twitter_mentions

        result = asyncio.run(poll_twitter_mentions("gaming", "12345"))
        assert result == []
