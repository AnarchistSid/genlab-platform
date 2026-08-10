"""Tests for genlab_core.pipeline.models.

Pin the three architectural contracts that fix today's (2026-06-19) silent-drop
bug class:

* ``StoryCandidate`` schema validation catches PR #359's KeyError 'score'
* ``merge_stories`` / ``replace_stories`` names catch PR #358's
  REPLACE-not-MERGE
* ``collect_emitted_sources`` catches PR #360's allowlist drift

Plus the 2026-08-10 Option C video-invariant contract (see
TestVideoInvariantContract + TestGamingFetcherShapesArchived below).
"""

from __future__ import annotations

import pytest
from genlab_core.pipeline.models import (
    FetcherStage,
    StoryCandidate,
    collect_emitted_sources,
    merge_stories,
    replace_stories,
)
from pydantic import ValidationError

# ─── StoryCandidate schema ────────────────────────────────────────────────────


class TestStoryCandidateSchema:
    def test_required_fields_only_round_trips(self):
        s = StoryCandidate(
            title="t",
            source="youtube_trending",
            source_url="https://example.com/x",
            video_id="abc123",  # 2026-08-10: video-invariant contract
        )
        assert s.title == "t"
        assert s.source == "youtube_trending"
        assert s.source_url == "https://example.com/x"
        # Defaults populated — solves PR #359's KeyError 'score'
        assert s.score == 0.5
        assert s.summary == ""

    def test_score_must_be_in_range(self):
        with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError
            StoryCandidate(title="t", source="x", source_url="u", video_id="v", score=1.5)
        with pytest.raises(Exception):  # noqa: B017
            StoryCandidate(title="t", source="x", source_url="u", video_id="v", score=-0.1)

    def test_from_raw_tolerates_missing_optional_fields(self):
        """The PR #359 case: upstream dict lacked ``score``. Must NOT raise."""
        raw = {
            "title": "t",
            "source": "twitch_clips",
            "source_url": "https://x",
            "video_id": "clip123",
        }
        s = StoryCandidate.from_raw(raw)
        assert s.score == 0.5  # default kicked in

    def test_from_raw_preserves_unknown_fields_via_extra_allow(self):
        """Existing fetchers write scratch keys (``_trending_video``,
        ``clip_index``) — these must survive the model conversion."""
        raw = {
            "title": "t",
            "source": "twitch_clips",
            "source_url": "https://x",
            "video_id": "v",
            "_trending_video": "https://video.url",
            "weird_legacy_field": 42,
        }
        s = StoryCandidate.from_raw(raw)
        dumped = s.model_dump()
        assert dumped["_trending_video"] == "https://video.url"
        assert dumped["weird_legacy_field"] == 42

    def test_required_fields_actually_required(self):
        """A fetcher emitting an incomplete shape must fail loudly at the
        merge boundary, not silently propagate."""
        with pytest.raises(Exception):  # noqa: B017
            StoryCandidate.from_raw({"source": "x", "source_url": "u"})  # no title


# ─── merge_stories / replace_stories ──────────────────────────────────────────


def _valid_story(title="t", source="youtube_trending", video_id="vid1", **extra):
    """Helper — produce a video-invariant-compliant story dict for tests
    that aren't testing the invariant itself."""
    return {
        "title": title,
        "source": source,
        "source_url": f"https://example.com/{video_id}",
        "video_id": video_id,
        **extra,
    }


class TestMergeStories:
    def test_merge_appends_to_existing(self):
        """The PR #358 case: a fetcher must extend, not overwrite."""
        ctx = {"stories": [_valid_story(title="existing", source="rss", video_id="e1")]}
        merge_stories(ctx, [_valid_story(title="new", source="twitch_clips", video_id="n1")])
        assert len(ctx["stories"]) == 2
        assert ctx["stories"][0]["title"] == "existing"
        assert ctx["stories"][1]["title"] == "new"

    def test_merge_with_prepend_puts_new_first(self):
        """P1 phase 4: FetchTrendingVideos direct-fetch merges with prepend
        so trending videos take priority in downstream top-N selection."""
        ctx = {"stories": [_valid_story(title="existing", source="rss", video_id="e")]}
        merge_stories(
            ctx,
            [_valid_story(title="trending", source="youtube_trending", video_id="t")],
            prepend=True,
        )
        assert len(ctx["stories"]) == 2
        assert ctx["stories"][0]["title"] == "trending"  # new came first
        assert ctx["stories"][1]["title"] == "existing"

    def test_merge_default_does_not_prepend(self):
        """Backward compatibility — default behavior must remain append."""
        ctx = {"stories": [_valid_story(title="a", source="s", video_id="a1")]}
        merge_stories(ctx, [_valid_story(title="b", source="s", video_id="b1")])
        assert ctx["stories"][0]["title"] == "a"
        assert ctx["stories"][1]["title"] == "b"

    def test_merge_accepts_typed_candidates_or_dicts(self):
        ctx: dict = {"stories": []}
        merge_stories(
            ctx,
            [
                StoryCandidate(title="typed", source="a", source_url="u", video_id="v1"),
                _valid_story(title="raw", source="b", video_id="v2"),
            ],
        )
        assert [s["title"] for s in ctx["stories"]] == ["typed", "raw"]

    def test_merge_on_empty_context_initializes_stories(self):
        ctx: dict = {}
        merge_stories(ctx, [_valid_story()])
        assert ctx["stories"][0]["title"] == "t"

    def test_merge_defaults_score_for_unscored_upstream_items(self):
        """Pins PR #359's fix: upstream stories without ``score`` no longer
        crash downstream sort-by-score."""
        ctx: dict = {"stories": []}
        merge_stories(ctx, [_valid_story()])
        assert ctx["stories"][0]["score"] == 0.5


class TestReplaceStories:
    def test_replace_overwrites(self):
        """Filters intentionally narrow the pool."""
        ctx = {"stories": [_valid_story(video_id=f"v{i}") for i in range(5)]}
        replace_stories(ctx, [_valid_story(title="kept", video_id="k")])
        assert len(ctx["stories"]) == 1
        assert ctx["stories"][0]["title"] == "kept"

    def test_replace_with_empty_clears_pool(self):
        ctx = {"stories": [_valid_story()]}
        replace_stories(ctx, [])
        assert ctx["stories"] == []


# ─── FetcherStage + collect_emitted_sources ──────────────────────────────────


class TestProducerRegistry:
    def test_collect_aggregates_all_fetcher_sources(self):
        """The PR #360 case: filter trust list must be derivable from
        producers, not maintained by hand."""

        class FetchA(FetcherStage):
            EMITTED_SOURCES = frozenset({"source_a", "source_a2"})

        class FetchB(FetcherStage):
            EMITTED_SOURCES = frozenset({"source_b"})

        result = collect_emitted_sources([FetchA, FetchB])
        assert result == frozenset({"source_a", "source_a2", "source_b"})

    def test_collect_skips_non_fetcher_classes_silently(self):
        """Pipeline stage list contains many non-fetcher classes (filters,
        scorers, renderers). They should be silently skipped."""

        class FetchOnly(FetcherStage):
            EMITTED_SOURCES = frozenset({"only_source"})

        class NotAFetcher:
            pass

        result = collect_emitted_sources([FetchOnly, NotAFetcher, str])
        assert result == frozenset({"only_source"})

    def test_collect_returns_empty_for_fetcher_without_declared_sources(self):
        """A fetcher subclass that forgets EMITTED_SOURCES gets the base
        empty frozenset — and surfaces as a coverage gap in the contract test."""

        class FetchEmpty(FetcherStage):
            pass  # forgot to declare

        assert collect_emitted_sources([FetchEmpty]) == frozenset()

    def test_collect_immutable_return_type(self):
        """frozenset return prevents the consumer from accidentally mutating
        the shared trust list."""

        class FetchA(FetcherStage):
            EMITTED_SOURCES = frozenset({"a"})

        result = collect_emitted_sources([FetchA])
        with pytest.raises(AttributeError):
            result.add("b")  # type: ignore[attr-defined]


# ─── architectural pins ──────────────────────────────────────────────────────


class TestArchitecturalContracts:
    """Tests that the names themselves communicate the contract — these are
    documentation tests, not behavioral. They fail if a future refactor
    breaks the contract."""

    def test_merge_and_replace_are_distinct_functions(self):
        """The fix for PR #358's bug class depends on these being two
        functions with different names. If they're ever consolidated into a
        single ``set_stories(mode='merge' | 'replace')``, this test fails to
        force a design conversation."""
        assert merge_stories is not replace_stories
        assert merge_stories.__name__ == "merge_stories"
        assert replace_stories.__name__ == "replace_stories"

    def test_story_candidate_score_default_is_0_5(self):
        """Pin the default. If it ever changes, downstream sort orderings
        change too — needs explicit migration."""
        s = StoryCandidate(title="t", source="s", source_url="u", video_id="v")
        assert s.score == 0.5


# ─── Option C video-invariant contract (2026-08-10) ───────────────────────────


class TestVideoInvariantContract:
    """Pins the video-invariant contract — the fix for the
    ``fetcher-schema-drift-from-downstream-contract`` class-of-bug.

    Origin: 30-day gaming audit surfaced 42 blueprints across only 19
    distinct titles (LoL x10, Fortnite x8, ...) because every gaming
    row had empty ``video_id`` — silently no-op'ing every downstream
    dedup gate that keys on video_id.
    """

    def test_video_bearing_story_passes(self):
        """Shape A: fetcher that populated video_id is legal."""
        s = StoryCandidate(
            title="Real trending video",
            source="youtube_trending",
            source_url="https://youtube.com/watch?v=abc",
            video_id="abc",
        )
        assert s.video_id == "abc"
        assert s.bypass_video_id_dedup is False

    def test_signal_story_with_bypass_passes(self):
        """Shape B: fetcher that explicitly declared bypass is legal."""
        s = StoryCandidate(
            title="Trending game (no clip yet)",
            source="steam_spike",
            source_url="https://store.steampowered.com/app/570",
            bypass_video_id_dedup=True,
            bypass_reason="steam_spike:signal_not_video",
        )
        assert s.bypass_video_id_dedup is True
        assert s.bypass_reason == "steam_spike:signal_not_video"

    def test_no_video_no_bypass_raises(self):
        """The exact shape the gaming local fetchers used to emit:
        video_id empty, no bypass declared. Must raise so merge_stories
        can log + drop it."""
        with pytest.raises(ValidationError) as ei:
            StoryCandidate(
                title="Fortnite",
                source="twitch_trending",
                source_url="https://www.twitch.tv/lacy",
                # video_id absent; no bypass_video_id_dedup — the bug shape
            )
        assert "lacks video_id" in str(ei.value)

    def test_bypass_without_reason_raises(self):
        """Bypass with no reason is worse than no bypass — hides the
        architectural gap without an audit trail."""
        with pytest.raises(ValidationError) as ei:
            StoryCandidate(
                title="Anon bypass",
                source="mystery_fetcher",
                source_url="https://x",
                bypass_video_id_dedup=True,
                # bypass_reason left empty
            )
        assert "bypass_reason is empty" in str(ei.value)

    def test_bypass_reason_whitespace_only_raises(self):
        """Whitespace-only reason string doesn't count as a reason."""
        with pytest.raises(ValidationError):
            StoryCandidate(
                title="t",
                source="x",
                source_url="u",
                bypass_video_id_dedup=True,
                bypass_reason="   ",
            )

    def test_video_id_takes_precedence_over_missing_bypass(self):
        """A fetcher that populated video_id doesn't need bypass — the
        two shapes aren't mutually exclusive but video_id alone suffices."""
        s = StoryCandidate(
            title="t",
            source="youtube_trending",
            source_url="u",
            video_id="v",
            # No bypass fields — Shape A only
        )
        assert s.video_id == "v"

    def test_channel_id_still_optional_in_phase_1(self):
        """Sports ships 36/40 blueprints with NULL source_channel_id today.
        Phase 1 enforcement must not regress those niches — channel_id is
        strongly encouraged but not required until per-fetcher retrofits
        happen in Phase 2."""
        s = StoryCandidate(
            title="Sports moment",
            source="scorebat",
            source_url="https://scorebat/x",
            video_id="v",
            # channel_id intentionally absent
        )
        assert s.channel_id is None


class TestMergeStoriesFailOpen:
    """merge_stories must survive a bad story in the batch — drop the bad
    one with a WARN log, keep the rest. Fail-open with visible signal
    (rule #19)."""

    def test_bad_story_dropped_from_batch(self, caplog):
        """A batch with a video-invariant violation loses the bad story
        but keeps the good ones. Rest of pipeline continues."""
        import logging

        ctx: dict = {"stories": []}
        with caplog.at_level(logging.WARNING):
            merge_stories(
                ctx,
                [
                    _valid_story(title="good1", video_id="v1"),
                    {  # BAD: no video_id, no bypass
                        "title": "bad",
                        "source": "broken_fetcher",
                        "source_url": "https://x",
                    },
                    _valid_story(title="good2", video_id="v2"),
                ],
            )

        # 2 of 3 stories kept — bad one silently dropped
        assert len(ctx["stories"]) == 2
        assert [s["title"] for s in ctx["stories"]] == ["good1", "good2"]

        # WARN log fired identifying the source + reason
        warn_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("DROPPED story" in m and "broken_fetcher" in m for m in warn_msgs)
        assert any("dropped 1 of 3" in m for m in warn_msgs)

    def test_bypass_declaration_passes_with_info_log(self, caplog):
        """A story explicitly declaring bypass is kept, and INFO logged
        with the bypass reason for operator auditability."""
        import logging

        ctx: dict = {"stories": [], "niche_id": "gaming"}
        with caplog.at_level(logging.INFO):
            merge_stories(
                ctx,
                [
                    {
                        "title": "LoL",
                        "source": "twitch_trending",
                        "source_url": "https://twitch.tv/directory/game/League",
                        "bypass_video_id_dedup": True,
                        "bypass_reason": "twitch_trending:live_channel_not_clip",
                    }
                ],
            )
        assert len(ctx["stories"]) == 1
        info_msgs = [r.message for r in caplog.records]
        assert any(
            "bypass" in m and "twitch_trending:live_channel_not_clip" in m for m in info_msgs
        )

    def test_all_bad_stories_produces_empty_pool_not_crash(self, caplog):
        """Batch of only violations: pool stays empty, no crash, WARN log
        summary shows 3-of-3 dropped."""
        import logging

        ctx: dict = {"stories": []}
        with caplog.at_level(logging.WARNING):
            merge_stories(
                ctx,
                [
                    {"title": "b1", "source": "x", "source_url": "u"},
                    {"title": "b2", "source": "y", "source_url": "u"},
                    {"title": "b3", "source": "z", "source_url": "u"},
                ],
            )
        assert ctx["stories"] == []
        summary = [r.message for r in caplog.records if "3 of 3" in r.message]
        assert summary, "expected dropped-summary WARN with '3 of 3'"

    def test_missing_title_still_dropped(self):
        """Non-video-invariant validation (e.g. missing title) also
        drops the story rather than raising."""
        ctx: dict = {"stories": []}
        merge_stories(ctx, [{"source": "x", "source_url": "u", "video_id": "v"}])
        assert ctx["stories"] == []  # dropped, not raised


class TestReplaceStoriesFailOpen:
    """replace_stories mirrors merge_stories fail-open — a filter that
    accidentally strips video_id doesn't crash the pipeline; the story
    drops with a WARN log."""

    def test_filter_stripping_video_id_gets_dropped(self, caplog):
        import logging

        ctx: dict = {"stories": []}
        with caplog.at_level(logging.WARNING):
            replace_stories(
                ctx,
                [
                    _valid_story(video_id="ok"),
                    {"title": "stripped", "source": "filter", "source_url": "u"},
                ],
            )
        assert len(ctx["stories"]) == 1
        assert ctx["stories"][0]["title"] == "t"
        assert any("DROPPED" in r.message for r in caplog.records)


class TestGamingFetcherShapesRetrofit:
    """Regression pin: the 3 gaming local fetchers (Steam spike, Twitch
    trending, RSS aggregator) declare bypass at the emit site. If a
    future refactor removes those declarations, these tests fail —
    surfacing the class-of-bug before it re-ships."""

    def test_steam_spike_emit_shape_with_bypass_passes(self):
        """Mirrors SteamSpikeFetcher.fetch() emit dict shape."""
        raw = {
            "story_id": "abc123",
            "title": "Palworld",
            "source": "steam_spike",
            "source_url": "https://store.steampowered.com/app/1623730",
            "score": 0.75,
            "published_at": "2026-08-10T12:00:00+00:00",
            "summary": "Currently 500,000 players (baseline ~250,000)",
            "steam_app_id": 1623730,
            "bypass_video_id_dedup": True,
            "bypass_reason": "steam_spike:signal_not_video",
        }
        s = StoryCandidate.from_raw(raw)
        assert s.bypass_video_id_dedup is True
        assert s.bypass_reason == "steam_spike:signal_not_video"

    def test_twitch_trending_emit_shape_with_bypass_passes(self):
        """Mirrors TwitchTrendingFetcher.fetch() emit dict shape."""
        raw = {
            "story_id": "xyz",
            "title": "League of Legends",
            "source": "twitch_trending",
            "source_url": "https://www.twitch.tv/sol1xd",
            "video_url": "https://www.twitch.tv/sol1xd",
            "source_channel_title": "sol1xd",
            "score": 1.0,
            "published_at": "2026-08-10T12:00:00+00:00",
            "summary": "Live: LoL — Twitch trending rank #1",
            "igdb_game_id": "21779",
            "bypass_video_id_dedup": True,
            "bypass_reason": "twitch_trending:live_channel_not_clip",
        }
        s = StoryCandidate.from_raw(raw)
        assert s.bypass_video_id_dedup is True

    def test_rss_aggregator_emit_shape_with_bypass_passes(self):
        """Mirrors RSSFeedAggregator.fetch() emit dict shape."""
        raw = {
            "story_id": "rss1",
            "title": "IGN: New Elden Ring DLC teased",
            "source": "rss",
            "source_url": "https://ign.com/articles/elden-ring-dlc",
            "score": 0.8,
            "published_at": "2026-08-10T12:00:00+00:00",
            "summary": "FromSoftware hinted at ...",
            "bypass_video_id_dedup": True,
            "bypass_reason": "rss:text_only_no_video",
        }
        s = StoryCandidate.from_raw(raw)
        assert s.bypass_video_id_dedup is True

    def test_regression_gaming_shape_WITHOUT_bypass_fails(self):
        """Pin the original bug shape — a story like today's gaming
        blueprints (video_id empty, bypass NOT declared) MUST fail
        validation. If this test ever passes, the contract regressed."""
        historical_gaming_shape = {
            "story_id": "old_shape",
            "title": "Fortnite",
            "source": "twitch_trending",
            "source_url": "https://www.twitch.tv/lacy",
            "score": 0.9,
            "summary": "Twitch trending rank #1",
            # Deliberately no video_id and no bypass_video_id_dedup —
            # this is the 42-blueprints-19-titles bug shape verbatim.
        }
        with pytest.raises(ValidationError):
            StoryCandidate.from_raw(historical_gaming_shape)
