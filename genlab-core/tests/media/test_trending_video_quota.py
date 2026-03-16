"""Tests for Sprint 64 YouTube quota optimization in trending_video_fetcher."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, mock_open

import pytest

from genlab_core.media.trending_video_fetcher import (
    QUOTA_TRACKER,
    TrendingVideoFetcher,
    FetchTrendingVideos,
    reset_quota_tracker,
)


@pytest.fixture(autouse=True)
def _reset_quota():
    """Reset quota tracker before each test."""
    reset_quota_tracker()
    yield


class TestRSSFetch:
    """Tests for YouTube RSS feed parsing (0 quota)."""

    RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
          xmlns:media="http://search.yahoo.com/mrss/"
          xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <yt:videoId>abc123</yt:videoId>
        <title>Test Video Title</title>
        <published>2026-03-15T10:00:00+00:00</published>
        <author><name>Test Channel</name></author>
        <media:group>
          <media:description>Some description text</media:description>
        </media:group>
      </entry>
      <entry>
        <yt:videoId>def456</yt:videoId>
        <title>Another Video</title>
        <published>2026-03-14T08:00:00+00:00</published>
        <author><name>Test Channel</name></author>
      </entry>
    </feed>"""

    def test_parses_video_ids_from_rss(self):
        fetcher = TrendingVideoFetcher(api_key="test")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = self.RSS_XML
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            items = fetcher._fetch_channel_rss("https://www.youtube.com/feeds/videos.xml?channel_id=UCtest")

        assert len(items) == 2
        assert items[0]["video_id"] == "abc123"
        assert items[0]["title"] == "Test Video Title"
        assert items[0]["channel_name"] == "Test Channel"
        assert items[0]["source"] == "youtube_rss"

    def test_rss_returns_empty_on_network_error(self):
        fetcher = TrendingVideoFetcher(api_key="test")
        with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
            items = fetcher._fetch_channel_rss("https://bad-url")
        assert items == []

    def test_rss_costs_zero_quota(self):
        fetcher = TrendingVideoFetcher(api_key="test")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = self.RSS_XML
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            fetcher._fetch_channel_rss("https://www.youtube.com/feeds/videos.xml?channel_id=UCtest")

        assert QUOTA_TRACKER["units_used"] == 0
        assert QUOTA_TRACKER["rss_fetches"] == 1


class TestPlaylistItemsFetch:
    """Tests for playlistItems.list fallback (1 unit)."""

    def test_converts_uc_to_uu_prefix(self):
        fetcher = TrendingVideoFetcher(api_key="test")
        with patch.object(fetcher, "_session") as mock_session:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"items": []}
            mock_resp.raise_for_status = MagicMock()
            mock_session.get.return_value = mock_resp

            fetcher._fetch_playlist_items("UCtest123")

        call_kwargs = mock_session.get.call_args
        assert call_kwargs[1]["params"]["playlistId"] == "UUtest123"

    def test_playlist_costs_one_unit(self):
        fetcher = TrendingVideoFetcher(api_key="test")
        with patch.object(fetcher, "_session") as mock_session:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"items": []}
            mock_resp.raise_for_status = MagicMock()
            mock_session.get.return_value = mock_resp

            fetcher._fetch_playlist_items("UCtest123")

        assert QUOTA_TRACKER["units_used"] == 1

    def test_returns_empty_on_error(self):
        fetcher = TrendingVideoFetcher(api_key="test")
        with patch.object(fetcher, "_session") as mock_session:
            mock_session.get.side_effect = Exception("API error")

            items = fetcher._fetch_playlist_items("UCtest123")

        assert items == []


class TestChannelIdExtraction:
    """Tests for extracting channel_id from RSS URLs."""

    def test_extracts_from_standard_url(self):
        url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCWJ2lWNubArHWmf3FIHbfcQ"
        assert TrendingVideoFetcher._extract_channel_id(url) == "UCWJ2lWNubArHWmf3FIHbfcQ"

    def test_returns_none_for_bad_url(self):
        assert TrendingVideoFetcher._extract_channel_id("https://example.com") is None
        assert TrendingVideoFetcher._extract_channel_id("not a url") is None


class TestFetchFromChannels:
    """Tests for the orchestrated RSS → playlist flow."""

    RSS_XML_EMPTY = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
          xmlns="http://www.w3.org/2005/Atom">
    </feed>"""

    RSS_XML_ONE = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
          xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <yt:videoId>vid001</yt:videoId>
        <title>Solo Video</title>
        <published>2026-03-15T10:00:00+00:00</published>
      </entry>
    </feed>"""

    def test_falls_back_to_playlist_when_rss_has_few_items(self):
        fetcher = TrendingVideoFetcher(api_key="test")
        channels = [
            {"url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCtest", "name": "Test"},
        ]

        with patch("urllib.request.urlopen") as mock_urlopen, \
             patch.object(fetcher, "_fetch_playlist_items") as mock_playlist, \
             patch.object(fetcher, "_fetch_video_details", return_value=[]):
            # RSS returns only 1 item (< 3 threshold)
            mock_resp = MagicMock()
            mock_resp.read.return_value = self.RSS_XML_ONE
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp
            mock_playlist.return_value = []

            published_after = datetime.now(timezone.utc) - timedelta(hours=48)
            fetcher._fetch_from_channels(channels, "sports", published_after)

        mock_playlist.assert_called_once_with("UCtest")


class TestFetchTrendingQuota:
    """Tests for quota consumption in fetch_trending."""

    def test_no_search_list_by_default(self):
        """Verify search.list is NOT called when allow_keyword_search=False."""
        fetcher = TrendingVideoFetcher(api_key="test")
        with patch.object(fetcher, "_fetch_most_popular", return_value=[]), \
             patch.object(fetcher, "_fetch_from_channels", return_value=[]), \
             patch.object(fetcher, "_search_recent") as mock_search:

            fetcher.fetch_trending(
                "sports",
                subscribed_channels=[{"url": "https://yt.com?channel_id=UCtest"}],
                allow_keyword_search=False,
            )

        mock_search.assert_not_called()

    def test_search_called_when_explicitly_enabled_and_few_results(self):
        """search.list is called only when allow_keyword_search=True AND < 3 candidates."""
        fetcher = TrendingVideoFetcher(api_key="test")
        with patch.object(fetcher, "_fetch_most_popular", return_value=[]), \
             patch.object(fetcher, "_fetch_from_channels", return_value=[]), \
             patch.object(fetcher, "_search_recent", return_value=[]) as mock_search, \
             patch.object(fetcher, "_fetch_video_details", return_value=[]):

            fetcher.fetch_trending(
                "sports",
                subscribed_channels=[],
                allow_keyword_search=True,
            )

        assert mock_search.call_count >= 1


class TestQuotaTracker:
    """Tests for the quota tracking counter."""

    def test_reset_clears_counters(self):
        QUOTA_TRACKER["units_used"] = 999
        QUOTA_TRACKER["rss_fetches"] = 50
        reset_quota_tracker()
        assert QUOTA_TRACKER["units_used"] == 0
        assert QUOTA_TRACKER["rss_fetches"] == 0

    def test_most_popular_increments_by_one(self):
        fetcher = TrendingVideoFetcher(api_key="test")
        with patch.object(fetcher, "_session") as mock_session:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"items": []}
            mock_resp.raise_for_status = MagicMock()
            mock_session.get.return_value = mock_resp

            published_after = datetime.now(timezone.utc) - timedelta(hours=48)
            fetcher._fetch_most_popular("20", published_after)

        assert QUOTA_TRACKER["units_used"] == 1

    def test_video_details_increments_by_batch_count(self):
        fetcher = TrendingVideoFetcher(api_key="test")
        with patch.object(fetcher, "_session") as mock_session:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"items": []}
            mock_resp.raise_for_status = MagicMock()
            mock_session.get.return_value = mock_resp

            # 60 IDs = 2 batches
            ids = [f"vid{i}" for i in range(60)]
            fetcher._fetch_video_details(ids)

        assert QUOTA_TRACKER["units_used"] == 2
