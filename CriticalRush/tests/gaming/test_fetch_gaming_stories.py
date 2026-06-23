"""Tests for FetchGamingStories — all external calls mocked."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch


def _now_utc():
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Steam spike tests
# ---------------------------------------------------------------------------


class TestSteamSpike:
    @patch("niches.gaming.stages.fetch_gaming_stories.requests.get")
    def test_steam_spike_returns_stories(self, mock_get, tmp_path, monkeypatch):
        """Mock Steam APIs, verify stories are populated."""
        # Mock featuredcategories response
        featured_resp = MagicMock()
        featured_resp.raise_for_status = MagicMock()
        featured_resp.json.return_value = {
            "top_sellers": {
                "items": [
                    {"id": 1245620, "name": "Elden Ring"},
                    {"id": 730, "name": "Counter-Strike 2"},
                ]
            }
        }

        # Mock player count responses — both first-time (no baseline)
        players_resp_1 = MagicMock()
        players_resp_1.raise_for_status = MagicMock()
        players_resp_1.json.return_value = {"response": {"player_count": 50000}}

        players_resp_2 = MagicMock()
        players_resp_2.raise_for_status = MagicMock()
        players_resp_2.json.return_value = {"response": {"player_count": 100000}}

        mock_get.side_effect = [featured_resp, players_resp_1, players_resp_2]

        from niches.gaming.stages.fetch_gaming_stories import SteamSpikeFetcher

        # Patch PROJECT_ROOT to use tmp_path for baseline file
        monkeypatch.setattr("niches.gaming.stages.fetch_gaming_stories.PROJECT_ROOT", tmp_path)

        fetcher = SteamSpikeFetcher({"spike_threshold_multiplier": 1.5, "max_stories": 5})
        fetcher._baseline_path = tmp_path / ".tmp" / "steam_baseline.json"
        stories = fetcher.fetch()

        # First-time games get score 0.5
        assert len(stories) == 2
        assert stories[0]["title"] == "Elden Ring"
        assert stories[0]["score"] == 0.5
        assert stories[0]["steam_app_id"] == "1245620"
        assert stories[0]["source"] == "steam_spike"


# ---------------------------------------------------------------------------
# Twitch skip test
# ---------------------------------------------------------------------------


class TestTwitchSkip:
    def test_twitch_skipped_without_credentials(self, monkeypatch):
        """When TWITCH_CLIENT_ID is unset, fetch returns empty list."""
        monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
        monkeypatch.delenv("TWITCH_CLIENT_SECRET", raising=False)

        from niches.gaming.stages.fetch_gaming_stories import TwitchTrendingFetcher

        fetcher = TwitchTrendingFetcher()
        stories = fetcher.fetch()
        # Without Twitch creds, Twitch clips are skipped but IGDB/other sources may return results.
        # Verify no story has source_type == "twitch_clip"
        twitch_stories = [s for s in stories if s.get("source_type") == "twitch_clip"]
        assert twitch_stories == [], (
            f"Expected no Twitch clips without credentials, got {len(twitch_stories)}"
        )


# ---------------------------------------------------------------------------
# RSS tests
# ---------------------------------------------------------------------------


class TestRSSFiltering:
    def test_rss_filters_old_articles(self):
        """Articles older than 48 hours are excluded."""
        from niches.gaming.stages.fetch_gaming_stories import RSSFeedAggregator

        # Create a mock feed with one old entry
        old_time = _now_utc() - timedelta(hours=72)
        old_parsed = old_time.timetuple()

        with patch("niches.gaming.stages.fetch_gaming_stories.feedparser.parse") as mock_parse:
            entry = MagicMock()
            entry.title = "Old News"
            entry.link = "https://example.com/old"
            entry.summary = "Ancient article"
            entry.published_parsed = old_parsed

            mock_parse.return_value = MagicMock(entries=[entry])

            agg = RSSFeedAggregator(
                [{"name": "Test", "url": "https://test.com/rss", "weight": 1.0}]
            )
            stories = agg.fetch(trending_titles=[])
            assert len(stories) == 0


class TestCrossSourceBoost:
    def test_cross_source_mention_boost(self):
        """A game appearing in trending titles gets +0.2 score boost in RSS."""
        from niches.gaming.stages.fetch_gaming_stories import RSSFeedAggregator

        recent_time = _now_utc() - timedelta(hours=1)
        recent_parsed = recent_time.timetuple()

        with patch("niches.gaming.stages.fetch_gaming_stories.feedparser.parse") as mock_parse:
            entry = MagicMock()
            entry.title = "Elden Ring DLC breaks records"
            entry.link = "https://example.com/elden"
            entry.summary = "New DLC"
            entry.published_parsed = recent_parsed

            mock_parse.return_value = MagicMock(entries=[entry])

            agg = RSSFeedAggregator([{"name": "IGN", "url": "https://ign.com/rss", "weight": 0.7}])

            # Without trending
            stories_no_boost = agg.fetch(trending_titles=[])
            # With trending
            stories_boosted = agg.fetch(trending_titles=["Elden Ring"])

            assert len(stories_no_boost) == 1
            assert len(stories_boosted) == 1
            assert stories_boosted[0]["score"] > stories_no_boost[0]["score"]


# ---------------------------------------------------------------------------
# Deduplication test
# ---------------------------------------------------------------------------


class TestDeduplication:
    def test_deduplication_keeps_highest_score(self, monkeypatch):
        """Two stories with same title from different sources — highest wins."""
        from niches.gaming.stages.fetch_gaming_stories import FetchGamingStories

        steam_stories = [
            {
                "title": "Elden Ring",
                "source": "steam_spike",
                "source_url": "https://store.steampowered.com/app/1245620",
                "score": 0.8,
                "published_at": _now_utc().isoformat(),
                "summary": "Spike",
                "steam_app_id": "1245620",
                "igdb_game_id": None,
                "developer": None,
            }
        ]
        rss_stories = [
            {
                "title": "Elden Ring",
                "source": "rss",
                "source_url": "https://ign.com/elden-ring",
                "score": 0.5,
                "published_at": _now_utc().isoformat(),
                "summary": "Article",
                "steam_app_id": None,
                "igdb_game_id": None,
                "developer": None,
            }
        ]

        with (
            patch.object(FetchGamingStories, "_load_sources_config", return_value={}),
            patch("niches.gaming.stages.fetch_gaming_stories.SteamSpikeFetcher") as MockSteam,
            patch("niches.gaming.stages.fetch_gaming_stories.TwitchTrendingFetcher") as MockTwitch,
            patch("niches.gaming.stages.fetch_gaming_stories.RSSFeedAggregator") as MockRSS,
        ):
            MockSteam.return_value.fetch.return_value = steam_stories
            MockTwitch.return_value.fetch.return_value = []
            MockRSS.return_value.fetch.return_value = rss_stories

            stage = FetchGamingStories()
            context = {"niche_config": {}, "run_stats": {}, "feature_flags": {}}
            result = stage.execute(context)

            stories = result["stories"]
            elden_stories = [s for s in stories if "elden" in s["title"].lower()]
            assert len(elden_stories) == 1
            assert elden_stories[0]["score"] == 0.8
            assert elden_stories[0]["source"] == "steam_spike"


# ---------------------------------------------------------------------------
# Twitch non-game category filter (2026-06-18 outage fix)
# ---------------------------------------------------------------------------


class TestTwitchNonGameFilter:
    """The Twitch helix top-games endpoint returns non-game categories
    like 'Just Chatting' and 'IRL' mixed with real games. The fetcher
    must filter these out — see the 2026-06-18 outage root-cause.
    """

    def _mock_helix_response(self, games):
        """Build a mocked Twitch helix response with given game dicts."""
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"data": games}
        return resp

    @patch("niches.gaming.stages.fetch_gaming_stories.requests.get")
    def test_skips_entries_with_empty_igdb_id(self, mock_get, monkeypatch):
        """The canonical "not a real game" signal from Twitch is
        ``igdb_id == ""``. Drop any such entry."""
        # Patch the already-instantiated pydantic settings singleton —
        # TwitchTrendingFetcher.__init__ reads settings.twitch_client_id
        # at instance-construction time, so env-var monkeypatching alone
        # doesn't propagate (env was sampled at module-import time, and
        # CI's clean env never had the var to begin with).
        from genlab_core.settings import settings

        monkeypatch.setattr(settings, "twitch_client_id", "test-id")
        monkeypatch.setattr(settings, "twitch_client_secret", "test-secret")

        mock_get.return_value = self._mock_helix_response(
            [
                {"id": "1", "name": "Real Game", "igdb_id": "100", "box_art_url": ""},
                {"id": "2", "name": "Just Chatting", "igdb_id": "", "box_art_url": ""},
                {"id": "3", "name": "Another Game", "igdb_id": "200", "box_art_url": ""},
                {"id": "4", "name": "IRL", "igdb_id": "", "box_art_url": ""},
            ]
        )

        from niches.gaming.stages.fetch_gaming_stories import TwitchTrendingFetcher

        with patch("niches.gaming.tools._twitch_auth.TwitchTokenManager") as MockToken:
            MockToken.return_value.get_token.return_value = "fake-token"
            fetcher = TwitchTrendingFetcher()
            stories = fetcher.fetch()

        titles = [s["title"] for s in stories]
        assert "Real Game" in titles
        assert "Another Game" in titles
        assert "Just Chatting" not in titles
        assert "IRL" not in titles

    @patch("niches.gaming.stages.fetch_gaming_stories.requests.get")
    def test_skips_hardcoded_non_game_category_ids(self, mock_get, monkeypatch):
        """Belt-and-suspenders: even if Twitch populates igdb_id for
        a non-game category, the hardcoded ID list catches it."""
        # Patch the already-instantiated pydantic settings singleton —
        # TwitchTrendingFetcher.__init__ reads settings.twitch_client_id
        # at instance-construction time, so env-var monkeypatching alone
        # doesn't propagate (env was sampled at module-import time, and
        # CI's clean env never had the var to begin with).
        from genlab_core.settings import settings

        monkeypatch.setattr(settings, "twitch_client_id", "test-id")
        monkeypatch.setattr(settings, "twitch_client_secret", "test-secret")

        mock_get.return_value = self._mock_helix_response(
            [
                {
                    "id": "509658",  # Just Chatting ID in non-game list
                    "name": "Just Chatting (with bogus igdb)",
                    "igdb_id": "999",  # Twitch could in theory populate this
                    "box_art_url": "",
                },
                {"id": "9999", "name": "Real Game", "igdb_id": "100", "box_art_url": ""},
            ]
        )

        from niches.gaming.stages.fetch_gaming_stories import TwitchTrendingFetcher

        with patch("niches.gaming.tools._twitch_auth.TwitchTokenManager") as MockToken:
            MockToken.return_value.get_token.return_value = "fake-token"
            fetcher = TwitchTrendingFetcher()
            stories = fetcher.fetch()

        titles = [s["title"] for s in stories]
        assert any("Real Game" in t for t in titles)
        assert not any("Just Chatting" in t for t in titles)

    @patch("niches.gaming.stages.fetch_gaming_stories.requests.get")
    def test_returns_at_most_5_real_games(self, mock_get, monkeypatch):
        """Even with 20 real-game entries, we cap at 5 (top-N by Twitch chart rank)."""
        # Patch the already-instantiated pydantic settings singleton —
        # TwitchTrendingFetcher.__init__ reads settings.twitch_client_id
        # at instance-construction time, so env-var monkeypatching alone
        # doesn't propagate (env was sampled at module-import time, and
        # CI's clean env never had the var to begin with).
        from genlab_core.settings import settings

        monkeypatch.setattr(settings, "twitch_client_id", "test-id")
        monkeypatch.setattr(settings, "twitch_client_secret", "test-secret")

        many_games = [
            {"id": str(i), "name": f"Game {i}", "igdb_id": str(100 + i), "box_art_url": ""}
            for i in range(20)
        ]
        mock_get.return_value = self._mock_helix_response(many_games)

        from niches.gaming.stages.fetch_gaming_stories import TwitchTrendingFetcher

        with patch("niches.gaming.tools._twitch_auth.TwitchTokenManager") as MockToken:
            MockToken.return_value.get_token.return_value = "fake-token"
            fetcher = TwitchTrendingFetcher()
            stories = fetcher.fetch()

        assert len(stories) == 5
        # All returned stories must have proper rank ordering (highest score first)
        scores = [s["score"] for s in stories]
        assert scores == sorted(scores, reverse=True)

    @patch("niches.gaming.stages.fetch_gaming_stories.requests.get")
    def test_returns_empty_when_all_entries_are_non_games(self, mock_get, monkeypatch):
        """Edge case: Twitch chart is entirely non-game categories. Should
        return 0 stories cleanly, letting Steam + RSS provide content."""
        # Patch the already-instantiated pydantic settings singleton —
        # TwitchTrendingFetcher.__init__ reads settings.twitch_client_id
        # at instance-construction time, so env-var monkeypatching alone
        # doesn't propagate (env was sampled at module-import time, and
        # CI's clean env never had the var to begin with).
        from genlab_core.settings import settings

        monkeypatch.setattr(settings, "twitch_client_id", "test-id")
        monkeypatch.setattr(settings, "twitch_client_secret", "test-secret")

        mock_get.return_value = self._mock_helix_response(
            [
                {"id": "1", "name": "Just Chatting", "igdb_id": "", "box_art_url": ""},
                {"id": "2", "name": "IRL", "igdb_id": "", "box_art_url": ""},
            ]
        )

        from niches.gaming.stages.fetch_gaming_stories import TwitchTrendingFetcher

        with patch("niches.gaming.tools._twitch_auth.TwitchTokenManager") as MockToken:
            MockToken.return_value.get_token.return_value = "fake-token"
            fetcher = TwitchTrendingFetcher()
            stories = fetcher.fetch()

        assert stories == []


# ---------------------------------------------------------------------------
# Source merge — prevents 2026-06-19 regression
# ---------------------------------------------------------------------------


class TestFetchGamingStoriesMergesUpstream:
    """FetchGamingStories must MERGE its locally-fetched stories with any
    upstream-populated context['stories'] (from FetchTrendingVideos,
    FetchTwitchClips, FetchRedditClips), not REPLACE them.

    Regression context (2026-06-19): line 507 used to do
    ``context['stories'] = final``, silently dropping ~45 real video
    sources per run and leaving only Twitch chart commentary as
    blueprint candidates. Operator observed this as "CR is producing
    useless content".
    """

    def test_upstream_stories_preserved_in_output(self):
        """20 upstream content_pool stories + 1 Twitch trending story =
        21 stories total in output (not just 1)."""
        from niches.gaming.stages.fetch_gaming_stories import FetchGamingStories

        # Simulate FetchTrendingVideos having populated 20 stories.
        # Use distinct titles per story (real game titles) so the dedup
        # engine doesn't collapse them — Jaccard threshold 0.80 would
        # treat near-identical "Real gameplay clip 1/2/3" strings as
        # duplicates.
        distinct_game_titles = [
            "Elden Ring",
            "Cyberpunk 2077",
            "Hollow Knight",
            "Hades II",
            "Stardew Valley",
            "Factorio",
            "Terraria",
            "Minecraft Dungeons",
            "Sekiro",
            "Dark Souls Remastered",
            "Returnal",
            "Death Stranding",
            "Subnautica",
            "Among Us",
            "Hollow Realm",
            "Phasmophobia",
            "Lethal Company",
            "Helldivers 2",
            "Palworld",
            "Baldur's Gate 3",
        ]
        upstream_stories = [
            {
                "title": f"{title} viral moment",
                "source": "content_pool",
                "source_url": f"https://youtube.com/watch?v=clip{i:02d}",
                "score": 0.5 + (i * 0.01),
                "published_at": _now_utc().isoformat(),
                "summary": "Real content",
                "steam_app_id": None,
                "igdb_game_id": None,
                "developer": None,
            }
            for i, title in enumerate(distinct_game_titles)
        ]

        # FetchGamingStories' own Twitch fetcher returns 1 chart story
        twitch_stories = [
            {
                "title": "Overwatch",
                "source": "twitch_trending",
                "source_url": "https://twitch.tv/directory/game/Overwatch",
                "score": 1.0,
                "published_at": _now_utc().isoformat(),
                "summary": "Twitch trending rank #1",
                "steam_app_id": None,
                "igdb_game_id": "115",
                "developer": None,
            }
        ]

        with (
            patch.object(FetchGamingStories, "_load_sources_config", return_value={}),
            patch("niches.gaming.stages.fetch_gaming_stories.SteamSpikeFetcher") as MockSteam,
            patch("niches.gaming.stages.fetch_gaming_stories.TwitchTrendingFetcher") as MockTwitch,
            patch("niches.gaming.stages.fetch_gaming_stories.RSSFeedAggregator") as MockRSS,
        ):
            MockSteam.return_value.fetch.return_value = []
            MockTwitch.return_value.fetch.return_value = twitch_stories
            MockRSS.return_value.fetch.return_value = []

            stage = FetchGamingStories()
            context = {
                "niche_config": {"max_stories_per_run": 50},
                "run_stats": {},
                "feature_flags": {},
                # Critical: upstream FetchTrendingVideos populated this
                "stories": upstream_stories,
            }
            result = stage.execute(context)

            stories = result["stories"]
            content_pool_count = sum(1 for s in stories if s["source"] == "content_pool")
            twitch_count = sum(1 for s in stories if s["source"] == "twitch_trending")

            assert content_pool_count == 20, (
                f"Expected 20 upstream content_pool stories preserved, "
                f"got {content_pool_count} — FetchGamingStories is REPLACING "
                f"context['stories'] instead of merging"
            )
            assert twitch_count == 1, "Expected the local Twitch story to also be present"
            assert len(stories) == 21, (
                f"Expected 21 total (20 upstream + 1 Twitch), got {len(stories)}"
            )

    def test_empty_upstream_works_as_before(self):
        """Backwards-compat: when no upstream stories exist, behavior is
        identical to the pre-fix (steam + twitch + rss only)."""
        from niches.gaming.stages.fetch_gaming_stories import FetchGamingStories

        steam = [
            {
                "title": "Elden Ring",
                "source": "steam_spike",
                "source_url": "https://store.steampowered.com/app/1245620",
                "score": 0.7,
                "published_at": _now_utc().isoformat(),
                "summary": "Spike",
                "steam_app_id": "1245620",
                "igdb_game_id": None,
                "developer": None,
            }
        ]

        with (
            patch.object(FetchGamingStories, "_load_sources_config", return_value={}),
            patch("niches.gaming.stages.fetch_gaming_stories.SteamSpikeFetcher") as MockSteam,
            patch("niches.gaming.stages.fetch_gaming_stories.TwitchTrendingFetcher") as MockTwitch,
            patch("niches.gaming.stages.fetch_gaming_stories.RSSFeedAggregator") as MockRSS,
        ):
            MockSteam.return_value.fetch.return_value = steam
            MockTwitch.return_value.fetch.return_value = []
            MockRSS.return_value.fetch.return_value = []

            stage = FetchGamingStories()
            # No 'stories' key in context — upstream didn't populate
            context = {"niche_config": {}, "run_stats": {}, "feature_flags": {}}
            result = stage.execute(context)

            assert len(result["stories"]) == 1
            assert result["stories"][0]["source"] == "steam_spike"

    def test_dedup_between_upstream_and_local(self):
        """If an upstream story has the same URL as a locally-fetched
        story, dedup engine keeps one (not both)."""
        from niches.gaming.stages.fetch_gaming_stories import FetchGamingStories

        same_url = "https://twitch.tv/directory/game/Overwatch"
        upstream = [
            {
                "title": "Overwatch (from content_pool)",
                "source": "content_pool",
                "source_url": same_url,
                "score": 0.4,
                "published_at": _now_utc().isoformat(),
                "summary": "Upstream",
                "steam_app_id": None,
                "igdb_game_id": None,
                "developer": None,
            }
        ]
        twitch = [
            {
                "title": "Overwatch",
                "source": "twitch_trending",
                "source_url": same_url,
                "score": 1.0,
                "published_at": _now_utc().isoformat(),
                "summary": "Twitch trending #1",
                "steam_app_id": None,
                "igdb_game_id": "115",
                "developer": None,
            }
        ]

        with (
            patch.object(FetchGamingStories, "_load_sources_config", return_value={}),
            patch("niches.gaming.stages.fetch_gaming_stories.SteamSpikeFetcher") as MockSteam,
            patch("niches.gaming.stages.fetch_gaming_stories.TwitchTrendingFetcher") as MockTwitch,
            patch("niches.gaming.stages.fetch_gaming_stories.RSSFeedAggregator") as MockRSS,
        ):
            MockSteam.return_value.fetch.return_value = []
            MockTwitch.return_value.fetch.return_value = twitch
            MockRSS.return_value.fetch.return_value = []

            stage = FetchGamingStories()
            context = {
                "niche_config": {},
                "run_stats": {},
                "feature_flags": {},
                "stories": upstream,
            }
            result = stage.execute(context)

            # Dedup keeps one — the higher-scoring local Twitch story should
            # win (score 1.0 > 0.4)
            overwatch = [s for s in result["stories"] if s.get("source_url") == same_url]
            assert len(overwatch) == 1, f"Expected dedup to one row, got {len(overwatch)}"


# ---------------------------------------------------------------------------
# Schema-tolerance for upstream stories — prevents 2026-06-19 hotfix regression
# ---------------------------------------------------------------------------


class TestFetchGamingStoriesUpstreamSchemaTolerance:
    """Upstream fetchers (FetchTrendingVideos, FetchTwitchClips) use a
    video-centric schema that may omit ``score`` and other fields. The
    merge code must normalize these before passing to dedup + sort
    (which previously raised KeyError, failing the stage).
    """

    def test_upstream_stories_without_score_dont_crash(self):
        """Real prod regression (2026-06-19 11:02 UTC run): upstream
        stories from FetchTrendingVideos didn't have a 'score' field.
        The sort raised KeyError, retry-then-fail, 0 blueprints."""
        from niches.gaming.stages.fetch_gaming_stories import FetchGamingStories

        # Mimics actual FetchTrendingVideos output: title + source_url + a
        # video-centric ``views`` field BUT NO ``score``.
        scoreless_upstream = [
            {
                "title": "Elden Ring boss fight viral moment",
                "source": "content_pool",
                "source_url": "https://youtube.com/watch?v=abc123",
                "video_id": "abc123",
                "views": 50000,
                # Note: no "score", no "summary", no "steam_app_id" etc.
            }
        ]

        with (
            patch.object(FetchGamingStories, "_load_sources_config", return_value={}),
            patch("niches.gaming.stages.fetch_gaming_stories.SteamSpikeFetcher") as MockSteam,
            patch("niches.gaming.stages.fetch_gaming_stories.TwitchTrendingFetcher") as MockTwitch,
            patch("niches.gaming.stages.fetch_gaming_stories.RSSFeedAggregator") as MockRSS,
        ):
            MockSteam.return_value.fetch.return_value = []
            MockTwitch.return_value.fetch.return_value = []
            MockRSS.return_value.fetch.return_value = []

            stage = FetchGamingStories()
            context = {
                "niche_config": {},
                "run_stats": {},
                "feature_flags": {},
                "stories": scoreless_upstream,
            }
            # Must NOT raise KeyError on 'score'
            result = stage.execute(context)

            # The story should survive (with a default score)
            assert len(result["stories"]) == 1
            assert result["stories"][0]["title"] == "Elden Ring boss fight viral moment"
            assert "score" in result["stories"][0]  # normalize injected default

    def test_mixed_with_and_without_score_sorts_cleanly(self):
        """Upstream scoreless + local scored stories sort without crashing.
        Higher score wins; missing-score defaults to a low value so local
        stories still rank by their real score."""
        from niches.gaming.stages.fetch_gaming_stories import FetchGamingStories

        scoreless_upstream = [
            {
                "title": "Hollow Knight speedrun WR",
                "source": "content_pool",
                "source_url": "https://youtube.com/watch?v=def456",
            }
        ]
        scored_twitch = [
            {
                "title": "Valorant",
                "source": "twitch_trending",
                "source_url": "https://twitch.tv/directory/game/Valorant",
                "score": 1.0,
                "published_at": _now_utc().isoformat(),
                "summary": "Twitch trending #1",
                "steam_app_id": None,
                "igdb_game_id": "200",
                "developer": None,
            }
        ]

        with (
            patch.object(FetchGamingStories, "_load_sources_config", return_value={}),
            patch("niches.gaming.stages.fetch_gaming_stories.SteamSpikeFetcher") as MockSteam,
            patch("niches.gaming.stages.fetch_gaming_stories.TwitchTrendingFetcher") as MockTwitch,
            patch("niches.gaming.stages.fetch_gaming_stories.RSSFeedAggregator") as MockRSS,
        ):
            MockSteam.return_value.fetch.return_value = []
            MockTwitch.return_value.fetch.return_value = scored_twitch
            MockRSS.return_value.fetch.return_value = []

            stage = FetchGamingStories()
            context = {
                "niche_config": {},
                "run_stats": {},
                "feature_flags": {},
                "stories": scoreless_upstream,
            }
            result = stage.execute(context)

            assert len(result["stories"]) == 2
            # Twitch (score 1.0) > upstream default (0.5)
            assert result["stories"][0]["title"] == "Valorant"


# ---------------------------------------------------------------------------
# PR #506 (2026-06-24) — all 3 sub-fetchers must emit `story_id` explicitly.
#
# Without explicit story_id, StoryCandidate.model_dump() fills the field with
# its default None, and downstream `story.get("story_id", "")[:N]` slicing
# crashes (the 2026-06-23 VideoGate outage shape that PR #499 + PR #502
# defensively patched at consumers).
#
# These pins make the bug class architecturally impossible at the source.
# ---------------------------------------------------------------------------


class TestStoryIdAlwaysPresent:
    """Every dict produced by a gaming sub-fetcher must carry a real
    ``story_id`` — not absent, not None, not empty.
    """

    @patch("niches.gaming.stages.fetch_gaming_stories.requests.get")
    def test_steam_spike_emits_story_id(self, mock_get, tmp_path, monkeypatch):
        featured_resp = MagicMock()
        featured_resp.raise_for_status = MagicMock()
        featured_resp.json.return_value = {
            "top_sellers": {"items": [{"id": 1245620, "name": "Elden Ring"}]}
        }
        players_resp = MagicMock()
        players_resp.raise_for_status = MagicMock()
        players_resp.json.return_value = {"response": {"player_count": 50000}}
        mock_get.side_effect = [featured_resp, players_resp]

        monkeypatch.setattr("niches.gaming.stages.fetch_gaming_stories.PROJECT_ROOT", tmp_path)
        from niches.gaming.stages.fetch_gaming_stories import SteamSpikeFetcher

        fetcher = SteamSpikeFetcher({"spike_threshold_multiplier": 1.5, "max_stories": 5})
        fetcher._baseline_path = tmp_path / ".tmp" / "steam_baseline.json"
        stories = fetcher.fetch()

        assert stories, "Steam spike must produce at least 1 story for this test"
        for s in stories:
            assert "story_id" in s, "story_id key must be PRESENT"
            assert s["story_id"], "story_id must be truthy (not None, not '')"
            assert isinstance(s["story_id"], str)

    @patch("niches.gaming.tools._twitch_auth.TwitchTokenManager")
    @patch("niches.gaming.stages.fetch_gaming_stories.requests.get")
    def test_twitch_trending_emits_story_id(self, mock_get, mock_token_mgr_cls):
        # TwitchTrendingFetcher pulls credentials from
        # ``genlab_core.settings.settings`` (Pydantic env-cached, loaded at
        # module import). A bare ``monkeypatch.setenv`` from the test won't
        # reach the cached attrs — CI hits "TWITCH_CLIENT_ID not set" and
        # the fetcher exits early returning []. Fix: construct the fetcher
        # then inject credentials directly + mock the token manager so the
        # network token call is bypassed.
        token_inst = MagicMock()
        token_inst.get_token.return_value = "fake_token"
        mock_token_mgr_cls.return_value = token_inst

        # Top games response (GET)
        top_resp = MagicMock()
        top_resp.raise_for_status = MagicMock()
        top_resp.json.return_value = {
            "data": [
                {"id": "1", "name": "Valorant", "igdb_id": "200", "box_art_url": ""},
            ]
        }
        mock_get.return_value = top_resp

        from niches.gaming.stages.fetch_gaming_stories import TwitchTrendingFetcher

        fetcher = TwitchTrendingFetcher()
        fetcher._client_id = "test_id"
        fetcher._client_secret = "test_secret"

        stories = fetcher.fetch()

        assert stories
        for s in stories:
            assert "story_id" in s
            assert s["story_id"]
            assert isinstance(s["story_id"], str)

    def test_rss_emits_story_id(self, monkeypatch):
        # Construct a fake feedparser result with a single entry
        fake_entry = MagicMock()
        fake_entry.title = "Big gaming news"
        fake_entry.link = "https://example.com/news/1"
        fake_entry.summary = "summary text"
        # published 1 hour ago — passes the 48h freshness gate
        recent = _now_utc() - timedelta(hours=1)
        fake_entry.published_parsed = recent.timetuple()

        fake_parsed = MagicMock()
        fake_parsed.entries = [fake_entry]

        monkeypatch.setattr(
            "niches.gaming.stages.fetch_gaming_stories.feedparser.parse",
            lambda url: fake_parsed,
        )

        from niches.gaming.stages.fetch_gaming_stories import RSSFeedAggregator

        fetcher = RSSFeedAggregator(
            [{"name": "TestFeed", "url": "https://example.com/feed.xml", "weight": 0.5}]
        )
        stories = fetcher.fetch(trending_titles=[])

        assert stories
        for s in stories:
            assert "story_id" in s
            assert s["story_id"]
            assert isinstance(s["story_id"], str)

    def test_story_id_stable_across_runs_for_same_url(self):
        """Same URL + same published_at → same story_id (sha256-deterministic).

        Pins that the chosen helper (`generate_story_id`) IS deterministic —
        a non-deterministic helper would defeat the dedup logic downstream
        that hashes (story_id) for cross-run uniqueness.
        """
        from genlab_core.cache.stable_ids import generate_story_id

        a = generate_story_id("https://example.com/x", "2026-06-24T00:00:00+00:00")
        b = generate_story_id("https://example.com/x", "2026-06-24T00:00:00+00:00")
        assert a == b


class TestSourcePinsExplicitStoryId:
    """Source-level negative pins — the OLD unsafe shapes (no story_id key)
    must NOT come back. PR #499 + #502's defensive consumer-side coercion
    only works AS A SAFETY NET; the architectural fix is producer-side."""

    def test_source_has_explicit_story_id_in_steam_spike_append(self):
        from pathlib import Path

        import niches.gaming.stages.fetch_gaming_stories as mod

        src = Path(mod.__file__).read_text()
        # Pin the new shape — each sub-fetcher's append must include
        # ``"story_id": generate_story_id(...)`` as the first key.
        assert src.count('"story_id": generate_story_id(') >= 3, (
            "Expected 3 sub-fetchers (Steam/Twitch/RSS) each emitting a "
            "``story_id`` via ``generate_story_id`` — see PR #506 docstring."
        )
        # Negative pin: the OLD unsafe shape (story dict without story_id)
        # would have ``"title": name, "source": "steam_spike"`` directly
        # without story_id above it. The presence of all 3 ``"story_id":
        # generate_story_id(`` strings prevents that.
