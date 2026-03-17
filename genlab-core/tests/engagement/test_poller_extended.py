"""Extended poller tests -- YouTube params, Twitter empty response, and polling constants."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from genlab_core.engagement.poller import (
    TWITTER_POLL_INTERVAL,
    YOUTUBE_POLL_INTERVAL,
    poll_twitter_mentions,
    poll_youtube_comments,
)


def _make_yt_mocks(comment_items=None):
    """Build mock responses for the 2-step YouTube poller (playlist + comments).

    Tests use YOUTUBE_API_KEY (Data API key) as the auth method.
    No OAuth token mocking needed.
    """
    playlist_resp = MagicMock()
    playlist_resp.raise_for_status = MagicMock()
    playlist_resp.json.return_value = {
        "items": [{"contentDetails": {"videoId": "vid_1"}}]
    }

    comments_resp = MagicMock()
    comments_resp.status_code = 200
    comments_resp.raise_for_status = MagicMock()
    comments_resp.json.return_value = {"items": comment_items or []}

    def mock_get(url, **kwargs):
        if "playlistItems" in url:
            return playlist_resp
        return comments_resp

    return mock_get, playlist_resp, comments_resp


def _set_api_key(monkeypatch):
    """Set YOUTUBE_API_KEY and clear OAuth vars so poller uses API key path."""
    monkeypatch.setenv("YOUTUBE_API_KEY", "test_api_key")
    monkeypatch.delenv("YOUTUBE_CLIENT_ID", raising=False)
    monkeypatch.delenv("YOUTUBE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("YOUTUBE_REFRESH_TOKEN", raising=False)


class TestPollingConstants:
    def test_youtube_poll_interval_is_30_minutes(self):
        assert YOUTUBE_POLL_INTERVAL == 1800  # Reduced from 300 to save quota

    def test_twitter_poll_interval_is_15_minutes(self):
        assert TWITTER_POLL_INTERVAL == 900

    def test_youtube_polls_less_frequently_than_twitter(self):
        # YouTube polls less often to conserve 10K daily quota
        assert YOUTUBE_POLL_INTERVAL > TWITTER_POLL_INTERVAL


class TestYouTubePollerParams:
    def test_playlist_requests_10_recent_videos(self, monkeypatch):
        """Playlist call should request maxResults=10 for recent uploads."""
        _set_api_key(monkeypatch)

        mock_get_fn, _, _ = _make_yt_mocks()
        get_calls = []

        def tracking_get(url, **kwargs):
            get_calls.append((url, kwargs))
            return mock_get_fn(url, **kwargs)

        with patch("requests.get", side_effect=tracking_get):
            asyncio.run(poll_youtube_comments("gaming", "UC_test"))

        # First GET = playlistItems with maxResults=10
        playlist_call = get_calls[0]
        params = playlist_call[1].get("params", {})
        assert params["maxResults"] == 10
        assert "playlistItems" in playlist_call[0]
        assert params["key"] == "test_api_key"

    def test_comment_threads_use_video_id(self, monkeypatch):
        """commentThreads calls should use videoId, not allThreadsRelatedToChannelId."""
        _set_api_key(monkeypatch)

        mock_get_fn, _, _ = _make_yt_mocks()
        get_calls = []

        def tracking_get(url, **kwargs):
            get_calls.append((url, kwargs))
            return mock_get_fn(url, **kwargs)

        with patch("requests.get", side_effect=tracking_get):
            asyncio.run(poll_youtube_comments("gaming", "UC_test"))

        # Second GET = commentThreads with videoId
        comment_call = get_calls[1]
        params = comment_call[1].get("params", {})
        assert params["videoId"] == "vid_1"
        assert "allThreadsRelatedToChannelId" not in params
        assert params["order"] == "time"

    def test_returns_empty_on_quota_exceeded(self, monkeypatch):
        """HTTP 403 quota exceeded returns empty list, doesn't crash."""
        _set_api_key(monkeypatch)

        import requests as _requests
        quota_resp = MagicMock()
        quota_resp.status_code = 403
        quota_resp.raise_for_status.side_effect = _requests.exceptions.HTTPError(
            "403 Client Error: Forbidden (quotaExceeded)"
        )

        with patch("requests.get", return_value=quota_resp):
            result = asyncio.run(poll_youtube_comments("gaming", "UC_test"))

        assert result == []

    def test_is_question_detection(self, monkeypatch):
        """Comments with '?' should have is_question=True."""
        _set_api_key(monkeypatch)

        comment_items = [
            {
                "id": "c1",
                "snippet": {
                    "topLevelComment": {
                        "snippet": {
                            "videoId": "v1",
                            "authorChannelId": {"value": "a1"},
                            "authorDisplayName": "User",
                            "textOriginal": "What settings do you use?",
                        }
                    }
                },
            },
            {
                "id": "c2",
                "snippet": {
                    "topLevelComment": {
                        "snippet": {
                            "videoId": "v1",
                            "authorChannelId": {"value": "a2"},
                            "authorDisplayName": "Fan",
                            "textOriginal": "Sick clip bro",
                        }
                    }
                },
            },
        ]

        mock_get_fn, _, _ = _make_yt_mocks(comment_items)

        with patch("requests.get", side_effect=mock_get_fn):
            result = asyncio.run(poll_youtube_comments("gaming", "UC_test"))

        assert result[0]["is_question"] is True
        assert result[1]["is_question"] is False


    def test_self_comments_filtered_out(self, monkeypatch):
        """Comments from the channel owner should be excluded."""
        _set_api_key(monkeypatch)

        comment_items = [
            {
                "id": "c_self",
                "snippet": {
                    "topLevelComment": {
                        "snippet": {
                            "videoId": "v1",
                            "authorChannelId": {"value": "UC_MYCHANNEL"},
                            "authorDisplayName": "MyChannel",
                            "textOriginal": "What would you build?",
                        }
                    }
                },
            },
            {
                "id": "c_viewer",
                "snippet": {
                    "topLevelComment": {
                        "snippet": {
                            "videoId": "v1",
                            "authorChannelId": {"value": "UC_VIEWER"},
                            "authorDisplayName": "Viewer",
                            "textOriginal": "Great video!",
                        }
                    }
                },
            },
        ]

        mock_get_fn, _, _ = _make_yt_mocks(comment_items)

        with patch("requests.get", side_effect=mock_get_fn):
            result = asyncio.run(poll_youtube_comments("gaming", "UC_MYCHANNEL"))

        assert len(result) == 1
        assert result[0]["comment_id"] == "c_viewer"

    def test_uploads_playlist_id_derivation(self, monkeypatch):
        """Uploads playlist ID should be UC→UU prefix swap."""
        _set_api_key(monkeypatch)

        mock_get_fn, _, _ = _make_yt_mocks()
        get_calls = []

        def tracking_get(url, **kwargs):
            get_calls.append((url, kwargs))
            return mock_get_fn(url, **kwargs)

        with patch("requests.get", side_effect=tracking_get):
            asyncio.run(poll_youtube_comments("gaming", "UC3GCipF_BTgjaxu"))

        playlist_params = get_calls[0][1].get("params", {})
        assert playlist_params["playlistId"] == "UU3GCipF_BTgjaxu"


class TestTwitterPollerEdgeCases:
    def test_empty_response_data_returns_empty(self, monkeypatch):
        """When Twitter returns no mentions, result should be empty."""
        monkeypatch.setenv("X_API_KEY", "ak")
        monkeypatch.setenv("X_API_SECRET", "as")
        monkeypatch.setenv("X_ACCESS_TOKEN", "at")
        monkeypatch.setenv("X_ACCESS_SECRET", "ats")

        mock_response = MagicMock()
        mock_response.data = None
        mock_response.includes = {}

        with patch("tweepy.Client") as MockClient:
            MockClient.return_value.get_users_mentions.return_value = mock_response
            result = asyncio.run(poll_twitter_mentions("gaming", "12345"))

        assert result == []

    def test_question_detection_in_tweets(self, monkeypatch):
        """Tweets with '?' should have is_question=True."""
        monkeypatch.setenv("X_API_KEY", "ak")
        monkeypatch.setenv("X_API_SECRET", "as")
        monkeypatch.setenv("X_ACCESS_TOKEN", "at")
        monkeypatch.setenv("X_ACCESS_SECRET", "ats")

        mock_tweet_q = MagicMock()
        mock_tweet_q.id = "t1"
        mock_tweet_q.author_id = "uid_1"
        mock_tweet_q.text = "What game is this?"

        mock_tweet_s = MagicMock()
        mock_tweet_s.id = "t2"
        mock_tweet_s.author_id = "uid_2"
        mock_tweet_s.text = "Fire clip"

        mock_user1 = MagicMock()
        mock_user1.id = "uid_1"
        mock_user1.username = "asker"

        mock_user2 = MagicMock()
        mock_user2.id = "uid_2"
        mock_user2.username = "fan"

        mock_response = MagicMock()
        mock_response.data = [mock_tweet_q, mock_tweet_s]
        mock_response.includes = {"users": [mock_user1, mock_user2]}

        with patch("tweepy.Client") as MockClient:
            MockClient.return_value.get_users_mentions.return_value = mock_response
            result = asyncio.run(poll_twitter_mentions("gaming", "12345"))

        assert len(result) == 2
        assert result[0]["is_question"] is True
        assert result[1]["is_question"] is False

    def test_author_missing_from_includes(self, monkeypatch):
        """If a tweet author isn't in includes.users, author_name should be empty."""
        monkeypatch.setenv("X_API_KEY", "ak")
        monkeypatch.setenv("X_API_SECRET", "as")
        monkeypatch.setenv("X_ACCESS_TOKEN", "at")
        monkeypatch.setenv("X_ACCESS_SECRET", "ats")

        mock_tweet = MagicMock()
        mock_tweet.id = "t1"
        mock_tweet.author_id = "uid_missing"
        mock_tweet.text = "Hey nice clip"

        mock_response = MagicMock()
        mock_response.data = [mock_tweet]
        mock_response.includes = {"users": []}  # No users in includes

        with patch("tweepy.Client") as MockClient:
            MockClient.return_value.get_users_mentions.return_value = mock_response
            result = asyncio.run(poll_twitter_mentions("gaming", "12345"))

        assert len(result) == 1
        assert result[0]["author_name"] == ""

    def test_self_mentions_filtered_out(self, monkeypatch):
        """Mentions from the account's own user_id should be excluded."""
        monkeypatch.setenv("X_API_KEY", "ak")
        monkeypatch.setenv("X_API_SECRET", "as")
        monkeypatch.setenv("X_ACCESS_TOKEN", "at")
        monkeypatch.setenv("X_ACCESS_SECRET", "ats")

        mock_self = MagicMock()
        mock_self.id = "t_self"
        mock_self.author_id = "12345"
        mock_self.text = "Check out our new video!"

        mock_other = MagicMock()
        mock_other.id = "t_other"
        mock_other.author_id = "99999"
        mock_other.text = "Love your content"

        mock_response = MagicMock()
        mock_response.data = [mock_self, mock_other]
        mock_response.includes = {"users": []}

        with patch("tweepy.Client") as MockClient:
            MockClient.return_value.get_users_mentions.return_value = mock_response
            result = asyncio.run(poll_twitter_mentions("gaming", "12345"))

        assert len(result) == 1
        assert result[0]["comment_id"] == "t_other"

    def test_twitter_requests_max_50_results(self, monkeypatch):
        """Twitter poll should request max_results=50."""
        monkeypatch.setenv("X_API_KEY", "ak")
        monkeypatch.setenv("X_API_SECRET", "as")
        monkeypatch.setenv("X_ACCESS_TOKEN", "at")
        monkeypatch.setenv("X_ACCESS_SECRET", "ats")

        mock_response = MagicMock()
        mock_response.data = None
        mock_response.includes = {}

        with patch("tweepy.Client") as MockClient:
            mock_client = MockClient.return_value
            mock_client.get_users_mentions.return_value = mock_response
            asyncio.run(poll_twitter_mentions("gaming", "12345"))

        call_kwargs = mock_client.get_users_mentions.call_args
        assert call_kwargs.kwargs.get("max_results") == 50

    def test_twitter_mentions_include_expansions(self, monkeypatch):
        """Twitter poll should request author_id expansion."""
        monkeypatch.setenv("X_API_KEY", "ak")
        monkeypatch.setenv("X_API_SECRET", "as")
        monkeypatch.setenv("X_ACCESS_TOKEN", "at")
        monkeypatch.setenv("X_ACCESS_SECRET", "ats")

        mock_response = MagicMock()
        mock_response.data = None
        mock_response.includes = {}

        with patch("tweepy.Client") as MockClient:
            mock_client = MockClient.return_value
            mock_client.get_users_mentions.return_value = mock_response
            asyncio.run(poll_twitter_mentions("gaming", "12345"))

        call_kwargs = mock_client.get_users_mentions.call_args
        assert "author_id" in call_kwargs.kwargs.get("expansions", [])
