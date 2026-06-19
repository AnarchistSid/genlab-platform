"""Tests for genlab_core.pipeline.models.

Pin the three architectural contracts that fix today's (2026-06-19) silent-drop
bug class:

* ``StoryCandidate`` schema validation catches PR #359's KeyError 'score'
* ``merge_stories`` / ``replace_stories`` names catch PR #358's
  REPLACE-not-MERGE
* ``collect_emitted_sources`` catches PR #360's allowlist drift
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

# ─── StoryCandidate schema ────────────────────────────────────────────────────


class TestStoryCandidateSchema:
    def test_required_fields_only_round_trips(self):
        s = StoryCandidate(
            title="t",
            source="youtube_trending",
            source_url="https://example.com/x",
        )
        assert s.title == "t"
        assert s.source == "youtube_trending"
        assert s.source_url == "https://example.com/x"
        # Defaults populated — solves PR #359's KeyError 'score'
        assert s.score == 0.5
        assert s.summary == ""

    def test_score_must_be_in_range(self):
        with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError
            StoryCandidate(title="t", source="x", source_url="u", score=1.5)
        with pytest.raises(Exception):  # noqa: B017
            StoryCandidate(title="t", source="x", source_url="u", score=-0.1)

    def test_from_raw_tolerates_missing_optional_fields(self):
        """The PR #359 case: upstream dict lacked ``score``. Must NOT raise."""
        raw = {"title": "t", "source": "twitch_clips", "source_url": "https://x"}
        s = StoryCandidate.from_raw(raw)
        assert s.score == 0.5  # default kicked in

    def test_from_raw_preserves_unknown_fields_via_extra_allow(self):
        """Existing fetchers write scratch keys (``_trending_video``,
        ``clip_index``) — these must survive the model conversion."""
        raw = {
            "title": "t",
            "source": "twitch_clips",
            "source_url": "https://x",
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


class TestMergeStories:
    def test_merge_appends_to_existing(self):
        """The PR #358 case: a fetcher must extend, not overwrite."""
        ctx = {"stories": [{"title": "existing", "source": "rss", "source_url": "u"}]}
        merge_stories(ctx, [{"title": "new", "source": "twitch_clips", "source_url": "u2"}])
        assert len(ctx["stories"]) == 2
        assert ctx["stories"][0]["title"] == "existing"
        assert ctx["stories"][1]["title"] == "new"

    def test_merge_with_prepend_puts_new_first(self):
        """P1 phase 4: FetchTrendingVideos direct-fetch merges with prepend
        so trending videos take priority in downstream top-N selection."""
        ctx = {"stories": [{"title": "existing", "source": "rss", "source_url": "u"}]}
        merge_stories(
            ctx,
            [{"title": "trending", "source": "youtube_trending", "source_url": "u2"}],
            prepend=True,
        )
        assert len(ctx["stories"]) == 2
        assert ctx["stories"][0]["title"] == "trending"  # new came first
        assert ctx["stories"][1]["title"] == "existing"

    def test_merge_default_does_not_prepend(self):
        """Backward compatibility — default behavior must remain append."""
        ctx = {"stories": [{"title": "a", "source": "s", "source_url": "u"}]}
        # No prepend arg → default behavior
        merge_stories(ctx, [{"title": "b", "source": "s", "source_url": "u"}])
        assert ctx["stories"][0]["title"] == "a"
        assert ctx["stories"][1]["title"] == "b"

    def test_merge_validates_each_item(self):
        ctx: dict = {"stories": []}
        with pytest.raises(Exception):  # noqa: B017
            merge_stories(ctx, [{"source": "x", "source_url": "u"}])  # title missing

    def test_merge_accepts_typed_candidates_or_dicts(self):
        ctx: dict = {"stories": []}
        merge_stories(
            ctx,
            [
                StoryCandidate(title="typed", source="a", source_url="u"),
                {"title": "raw", "source": "b", "source_url": "u"},
            ],
        )
        assert [s["title"] for s in ctx["stories"]] == ["typed", "raw"]

    def test_merge_on_empty_context_initializes_stories(self):
        ctx: dict = {}
        merge_stories(ctx, [{"title": "t", "source": "s", "source_url": "u"}])
        assert ctx["stories"][0]["title"] == "t"

    def test_merge_defaults_score_for_unscored_upstream_items(self):
        """Pins PR #359's fix: upstream stories without ``score`` no longer
        crash downstream sort-by-score."""
        ctx: dict = {"stories": []}
        merge_stories(ctx, [{"title": "t", "source": "s", "source_url": "u"}])
        assert ctx["stories"][0]["score"] == 0.5


class TestReplaceStories:
    def test_replace_overwrites(self):
        """Filters intentionally narrow the pool."""
        ctx = {"stories": [{"title": "a", "source": "s", "source_url": "u"}] * 5}
        replace_stories(ctx, [{"title": "kept", "source": "s", "source_url": "u"}])
        assert len(ctx["stories"]) == 1
        assert ctx["stories"][0]["title"] == "kept"

    def test_replace_validates_each_item(self):
        ctx: dict = {"stories": []}
        with pytest.raises(Exception):  # noqa: B017
            replace_stories(ctx, [{"source": "x", "source_url": "u"}])

    def test_replace_with_empty_clears_pool(self):
        ctx = {"stories": [{"title": "a", "source": "s", "source_url": "u"}]}
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
        s = StoryCandidate(title="t", source="s", source_url="u")
        assert s.score == 0.5
