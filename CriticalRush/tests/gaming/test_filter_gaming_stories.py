"""Tests for FilterGamingStories stage."""

from niches.gaming.stages.filter_gaming_stories import FilterGamingStories


def _make_story(title, source="rss", score=0.5, summary=""):
    return {
        "title": title,
        "source": source,
        "source_url": "https://example.com",
        "score": score,
        "published_at": "2026-03-05T00:00:00Z",
        "summary": summary,
        "steam_app_id": None,
        "igdb_game_id": None,
        "developer": None,
    }


class TestSourcePassthrough:
    def test_steam_and_twitch_always_pass(self):
        """steam_spike and twitch_trending stories always pass."""
        stage = FilterGamingStories()
        stories = [
            _make_story("Random Title XYZ", source="steam_spike", score=0.5),
            _make_story("Another Random Thing", source="twitch_trending", score=0.7),
        ]
        context = {"stories": stories, "run_stats": {}}
        result = stage.execute(context)

        assert len(result["stories"]) == 2
        assert result["run_stats"]["filter"]["rejected"] == 0


class TestRSSFiltering:
    def test_rss_gaming_content_passes(self):
        """Title containing gaming keywords passes the filter."""
        stage = FilterGamingStories()
        stories = [
            _make_story("Elden Ring DLC gets major game update", score=0.8),
        ]
        context = {"stories": stories, "run_stats": {}}
        result = stage.execute(context)

        assert len(result["stories"]) == 1

    def test_rss_noise_rejected(self):
        """Non-gaming content like phone deals is rejected."""
        stage = FilterGamingStories()
        stories = [
            _make_story(
                "Best T-Mobile smartphone deals this week",
                score=0.9,
                summary="Save on the latest phone sale",
            ),
        ]
        context = {"stories": stories, "run_stats": {}}
        result = stage.execute(context)

        assert len(result["stories"]) == 0
        assert result["run_stats"]["filter"]["rejected"] == 1


class TestTopSelection:
    def test_top_5_selected(self):
        """When 8 stories pass, only top 5 by score are returned."""
        stage = FilterGamingStories()
        stories = [_make_story(f"Game {i} update news", score=i * 0.1) for i in range(1, 9)]
        context = {"stories": stories, "run_stats": {}}
        result = stage.execute(context)

        assert len(result["stories"]) == 5
        # Highest scores first
        scores = [s["score"] for s in result["stories"]]
        assert scores == sorted(scores, reverse=True)
        assert scores[0] == 0.8


class TestStats:
    def test_stats_written_to_context(self):
        """run_stats['filter'] has correct counts."""
        stage = FilterGamingStories()
        stories = [
            _make_story("New game release trailer", score=0.8),
            _make_story(
                "Best phone deal sale discount", score=0.9, summary="Save on electronics sale"
            ),
            _make_story("Steam patch update for FPS", source="steam_spike", score=0.5),
        ]
        context = {"stories": stories, "run_stats": {}}
        result = stage.execute(context)

        stats = result["run_stats"]["filter"]
        assert stats["input_count"] == 3
        assert stats["rejected"] == 1
        assert stats["selected"] == 2
        assert len(stats["rejected_titles"]) == 1
