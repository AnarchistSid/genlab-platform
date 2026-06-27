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


class TestUpstreamFetcherSourcesTrusted:
    """2026-06-19 regression: PR #358 unlocked merging upstream-fetched stories
    into context["stories"], but FilterGamingStories' two-entry trust list
    (``steam_spike``/``twitch_trending`` only) silently dropped them via the
    keyword filter. Twitch clip titles / YouTube gaming-category clips are
    full of emoji + streamer slang with no English gaming keywords, so the
    keyword filter rejected legitimate gameplay clips and only chart-
    position commentary survived. Trust list now covers every gaming-by-
    construction fetcher."""

    def test_youtube_trending_clip_with_slang_title_passes(self):
        """YouTube category=20 (Gaming) trending clip with no English gaming
        keywords in its title is still gaming content — trust the source."""
        stage = FilterGamingStories()
        stories = [
            _make_story("MAX UMBRA, MIN VOLUME 😈 #shorts", source="youtube_trending", score=0.8),
        ]
        result = stage.execute({"stories": stories, "run_stats": {}})
        assert len(result["stories"]) == 1
        assert result["run_stats"]["filter"]["rejected"] == 0

    def test_twitch_clip_with_no_keyword_title_passes(self):
        """Twitch is a gaming-only platform — clip titles need no keyword."""
        stage = FilterGamingStories()
        stories = [
            _make_story("RAGED so hard I broke my chair", source="twitch_clips", score=0.7),
        ]
        result = stage.execute({"stories": stories, "run_stats": {}})
        assert len(result["stories"]) == 1

    def test_steam_trailer_source_passes(self):
        """Steam is a gaming storefront — trailers pass without keyword check."""
        stage = FilterGamingStories()
        stories = [_make_story("Official Reveal Cinematic", source="steam_trailer", score=0.6)]
        result = stage.execute({"stories": stories, "run_stats": {}})
        assert len(result["stories"]) == 1

    def test_reddit_subreddit_prefix_passes(self):
        """FetchRedditClips uses the ``reddit:<subreddit>`` source prefix
        (one source per subreddit). All gaming subreddits are vetted in
        sources.yaml, so anything that came back is gaming content."""
        stage = FilterGamingStories()
        stories = [
            _make_story("Big play just happened", source="reddit:gaming", score=0.6),
            _make_story("Massive clutch moment", source="reddit:LivestreamFail", score=0.7),
        ]
        result = stage.execute({"stories": stories, "run_stats": {}})
        assert len(result["stories"]) == 2

    def test_shared_pool_passes(self):
        """content_pool entries claimed by the gaming niche come in with
        source='shared_pool' — these are already niche-claimed, trust them."""
        stage = FilterGamingStories()
        stories = [_make_story("Cross-niche entry no keyword", source="shared_pool", score=0.6)]
        result = stage.execute({"stories": stories, "run_stats": {}})
        assert len(result["stories"]) == 1

    def test_legacy_rss_still_keyword_filtered(self):
        """The 'rss' source path is the legacy general-news path and is
        the only source still subject to the keyword filter — verify it
        keeps rejecting non-gaming noise."""
        stage = FilterGamingStories()
        stories = [
            _make_story(
                "Best T-Mobile smartphone deal this week",
                source="rss",
                score=0.9,
                summary="Save on the latest phone sale",
            ),
        ]
        result = stage.execute({"stories": stories, "run_stats": {}})
        assert len(result["stories"]) == 0


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


class TestFilterTopN:
    """Pin: filter_top_n is config-driven (default 5, gaming overrides to 7).

    Background — 2026-06-28: gaming was hitting daily 'zero_blueprints'
    alerts when the top-3 trending happened to be already-published this
    week. Funnel was: filter passes 5 → enrich loses 2 → render 3 →
    push_to_backlog dedup blocks all 3. Cap was hardcoded `[:5]`. PR
    raised it via niche_config.video_sourcing.filter_top_n (default 5
    for backward compat). Pin guards against regression to hardcoded value.
    """

    def test_default_cap_is_5_when_no_config(self):
        """Backward-compat: with no niche_config, cap stays at 5 — other
        niches consuming this stage are unaffected."""
        stage = FilterGamingStories()
        # 10 gaming-by-source stories, all pass _is_gaming_content
        stories = [
            _make_story(f"Title {i}", source="steam_spike", score=1.0 - i * 0.01)
            for i in range(10)
        ]
        result = stage.execute({"stories": stories, "run_stats": {}})
        assert len(result["stories"]) == 5, (
            "Default filter_top_n must remain 5 when niche_config is absent "
            "or doesn't set video_sourcing.filter_top_n; raising the default "
            "would silently affect other niches consuming this stage."
        )

    def test_config_override_raises_cap_to_7(self):
        """Gaming sets filter_top_n: 7 in niche.yaml; verify the stage reads it."""
        stage = FilterGamingStories()
        stories = [
            _make_story(f"Title {i}", source="steam_spike", score=1.0 - i * 0.01)
            for i in range(10)
        ]
        result = stage.execute(
            {
                "stories": stories,
                "run_stats": {},
                "niche_config": {"video_sourcing": {"filter_top_n": 7}},
            }
        )
        assert len(result["stories"]) == 7, (
            "When niche_config.video_sourcing.filter_top_n=7 is set, the "
            "filter must keep 7 top-scored survivors instead of 5. This is "
            "the durable fix for gaming's daily 'zero_blueprints' alerts."
        )

    def test_top_scored_kept_in_order(self):
        """Survivors must be the top-N by score, descending."""
        stage = FilterGamingStories()
        stories = [
            _make_story("low", source="steam_spike", score=0.1),
            _make_story("high", source="steam_spike", score=0.9),
            _make_story("mid", source="steam_spike", score=0.5),
        ]
        result = stage.execute(
            {
                "stories": stories,
                "run_stats": {},
                "niche_config": {"video_sourcing": {"filter_top_n": 2}},
            }
        )
        kept_titles = [s["title"] for s in result["stories"]]
        assert kept_titles == ["high", "mid"], (
            f"Expected top-2 by score [high, mid], got {kept_titles}"
        )
