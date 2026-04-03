"""Tests for concurrent fetch in fetch_gaming_stories.py."""

import time
from unittest.mock import MagicMock, patch


class TestConcurrentFetch:
    """Verify Steam + Twitch run concurrently, RSS runs after."""

    @patch("niches.gaming.stages.fetch_gaming_stories.DedupEngine")
    @patch("niches.gaming.stages.fetch_gaming_stories.RSSFeedAggregator")
    @patch("niches.gaming.stages.fetch_gaming_stories.TwitchTrendingFetcher")
    @patch("niches.gaming.stages.fetch_gaming_stories.SteamSpikeFetcher")
    def test_all_three_sources_called(
        self, MockSteam, MockTwitch, MockRSS, MockDedup,
    ):
        """All three sources are called and their results merged."""
        from niches.gaming.stages.fetch_gaming_stories import FetchGamingStories

        MockSteam.return_value.fetch.return_value = [
            {"title": "Steam Game", "source": "steam_spike", "source_url": "http://s", "score": 0.8},
        ]
        MockTwitch.return_value.fetch.return_value = [
            {"title": "Twitch Game", "source": "twitch_trending", "source_url": "http://t", "score": 0.7},
        ]
        MockRSS.return_value.fetch.return_value = [
            {"title": "RSS Story", "source": "rss", "source_url": "http://r", "score": 0.5},
        ]

        # DedupEngine pass-through
        dedup_result = MagicMock()
        dedup_result.unique = [
            {"title": "Steam Game", "score": 0.8},
            {"title": "Twitch Game", "score": 0.7},
            {"title": "RSS Story", "score": 0.5},
        ]
        dedup_result.pass1_removed = 0
        dedup_result.pass2_removed = 0
        dedup_result.pass3_removed = 0
        MockDedup.return_value.run.return_value = dedup_result

        stage = FetchGamingStories()
        context = stage.execute({})

        assert len(context["stories"]) == 3
        MockSteam.return_value.fetch.assert_called_once()
        MockTwitch.return_value.fetch.assert_called_once()
        MockRSS.return_value.fetch.assert_called_once()

    @patch("niches.gaming.stages.fetch_gaming_stories.DedupEngine")
    @patch("niches.gaming.stages.fetch_gaming_stories.RSSFeedAggregator")
    @patch("niches.gaming.stages.fetch_gaming_stories.TwitchTrendingFetcher")
    @patch("niches.gaming.stages.fetch_gaming_stories.SteamSpikeFetcher")
    def test_one_source_failure_does_not_block_others(
        self, MockSteam, MockTwitch, MockRSS, MockDedup,
    ):
        """If Steam fails, Twitch + RSS still return results."""
        from niches.gaming.stages.fetch_gaming_stories import FetchGamingStories

        MockSteam.return_value.fetch.side_effect = RuntimeError("Steam API down")
        MockTwitch.return_value.fetch.return_value = [
            {"title": "Twitch Game", "source": "twitch_trending", "source_url": "http://t", "score": 0.7},
        ]
        MockRSS.return_value.fetch.return_value = [
            {"title": "RSS Story", "source": "rss", "source_url": "http://r", "score": 0.5},
        ]

        dedup_result = MagicMock()
        dedup_result.unique = [
            {"title": "Twitch Game", "score": 0.7},
            {"title": "RSS Story", "score": 0.5},
        ]
        dedup_result.pass1_removed = 0
        dedup_result.pass2_removed = 0
        dedup_result.pass3_removed = 0
        MockDedup.return_value.run.return_value = dedup_result

        stage = FetchGamingStories()
        context = stage.execute({})

        # Steam failed but Twitch + RSS results are present
        assert len(context["stories"]) == 2

    @patch("niches.gaming.stages.fetch_gaming_stories.DedupEngine")
    @patch("niches.gaming.stages.fetch_gaming_stories.RSSFeedAggregator")
    @patch("niches.gaming.stages.fetch_gaming_stories.TwitchTrendingFetcher")
    @patch("niches.gaming.stages.fetch_gaming_stories.SteamSpikeFetcher")
    def test_steam_twitch_run_concurrently(
        self, MockSteam, MockTwitch, MockRSS, MockDedup,
    ):
        """Steam and Twitch start within overlapping time windows."""
        call_times = {}

        def slow_steam():
            call_times["steam_start"] = time.monotonic()
            time.sleep(0.3)
            call_times["steam_end"] = time.monotonic()
            return [{"title": "S", "source": "steam_spike", "source_url": "http://s", "score": 0.5}]

        def slow_twitch():
            call_times["twitch_start"] = time.monotonic()
            time.sleep(0.3)
            call_times["twitch_end"] = time.monotonic()
            return [{"title": "T", "source": "twitch_trending", "source_url": "http://t", "score": 0.5}]

        MockSteam.return_value.fetch.side_effect = slow_steam
        MockTwitch.return_value.fetch.side_effect = slow_twitch
        MockRSS.return_value.fetch.return_value = []

        dedup_result = MagicMock()
        dedup_result.unique = [{"title": "S", "score": 0.5}, {"title": "T", "score": 0.5}]
        dedup_result.pass1_removed = 0
        dedup_result.pass2_removed = 0
        dedup_result.pass3_removed = 0
        MockDedup.return_value.run.return_value = dedup_result

        from niches.gaming.stages.fetch_gaming_stories import FetchGamingStories

        start = time.monotonic()
        stage = FetchGamingStories()
        stage.execute({})
        total = time.monotonic() - start

        # If sequential, would take >= 0.6s. Concurrent should be ~0.3s.
        assert total < 0.55, f"Took {total:.2f}s — fetches appear sequential"
        # Both should have started before either finished
        assert call_times["steam_start"] < call_times["twitch_end"]
        assert call_times["twitch_start"] < call_times["steam_end"]
