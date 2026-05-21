"""Tests for Reddit video post fetcher."""

from unittest.mock import MagicMock, patch

from genlab_core.intel.reddit_fetcher import _get_access_token, fetch_reddit_videos


class TestAccessToken:
    @patch("genlab_core.intel.reddit_fetcher.requests.post")
    def test_returns_token_on_success(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"access_token": "tok123"},
        )
        mock_post.return_value.raise_for_status = MagicMock()
        assert _get_access_token("cid", "csecret") == "tok123"

    @patch("genlab_core.intel.reddit_fetcher.requests.post")
    def test_returns_none_on_failure(self, mock_post):
        mock_post.side_effect = Exception("network error")
        assert _get_access_token("cid", "csecret") is None


class TestFetchRedditVideos:
    def test_returns_empty_when_no_credentials(self):
        with patch.dict("os.environ", {}, clear=True):
            result = fetch_reddit_videos([{"subreddit": "r/nba", "min_score": 100}])
            assert result == []

    @patch("genlab_core.intel.reddit_fetcher._get_access_token")
    @patch("genlab_core.intel.reddit_fetcher.requests.get")
    def test_filters_by_min_score(self, mock_get, mock_token):
        mock_token.return_value = "tok"
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "data": {
                    "children": [
                        {
                            "data": {
                                "title": "Low",
                                "score": 50,
                                "is_video": True,
                                "media": {"reddit_video": {"fallback_url": "http://v.redd.it/a"}},
                                "permalink": "/r/nba/1",
                                "url": "http://v.redd.it/a",
                            }
                        },
                        {
                            "data": {
                                "title": "High",
                                "score": 5000,
                                "is_video": True,
                                "media": {"reddit_video": {"fallback_url": "http://v.redd.it/b"}},
                                "permalink": "/r/nba/2",
                                "url": "http://v.redd.it/b",
                            }
                        },
                    ]
                }
            },
        )
        with patch.dict("os.environ", {"REDDIT_CLIENT_ID": "x", "REDDIT_CLIENT_SECRET": "y"}):
            result = fetch_reddit_videos([{"subreddit": "r/nba", "min_score": 1000}])
        assert len(result) == 1
        assert result[0]["title"] == "High"

    @patch("genlab_core.intel.reddit_fetcher._get_access_token")
    @patch("genlab_core.intel.reddit_fetcher.requests.get")
    def test_filters_video_only(self, mock_get, mock_token):
        mock_token.return_value = "tok"
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "data": {
                    "children": [
                        {
                            "data": {
                                "title": "Text",
                                "score": 9999,
                                "is_video": False,
                                "permalink": "/r/nba/1",
                                "url": "http://self.reddit",
                            }
                        },
                        {
                            "data": {
                                "title": "Video",
                                "score": 9999,
                                "is_video": True,
                                "media": {"reddit_video": {"fallback_url": "http://v.redd.it/c"}},
                                "permalink": "/r/nba/2",
                                "url": "http://v.redd.it/c",
                            }
                        },
                    ]
                }
            },
        )
        with patch.dict("os.environ", {"REDDIT_CLIENT_ID": "x", "REDDIT_CLIENT_SECRET": "y"}):
            result = fetch_reddit_videos(
                [{"subreddit": "r/nba", "min_score": 100, "content_type": "video"}]
            )
        assert len(result) == 1
        assert result[0]["is_video"] is True
