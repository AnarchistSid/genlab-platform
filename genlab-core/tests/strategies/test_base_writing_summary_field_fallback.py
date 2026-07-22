"""Pin the 2026-07-22 movies summary-field fallback fix.

History: SpliceReel (movies) went dark from 2026-07-16 through
2026-07-22 (6 days) despite the pipeline running daily and producing
1-3 story candidates per run. Every archived blueprint in that
window carried an LLM refusal preamble as its hook — "I need to
flag a critical issue: the Story title is...", "I can't access the
Reddit link", "I need to stop here — the Summary field is empty".

Root cause was a shared-contract-N-implementers-silent-divergence:

  * ``_has_writable_context`` (base_writing.py:95) passes stories
    where ANY of ``summary`` / ``description_snippet`` /
    ``description`` clears the 40-char writable-context floor.
  * ``_story_to_video_dict`` (base_writing.py:174) — the boundary
    that converts pipeline stories into the writer-input dict —
    read ONLY ``story["summary"]``.
  * ``llm_hook_generator.py:400`` reads ONLY the writer-input
    dict's ``summary`` field and passes it verbatim into the
    Claude prompt as ``f"Story: {title}\\nSummary: {summary}"``.

Movies stories from ``TrendingVideoFetcher`` (YouTube) populate
``description_snippet`` (from the YouTube API description field)
and leave ``summary`` empty. The filter passed them. The writer
sent "Summary: " (empty). Claude refused. The refusal became the
hook. The refusal-hook got archived by the pre-render quality
gate — but only AFTER burning a pipeline slot AND depleting the
day's backlog.

Fix: ``_story_to_video_dict`` now falls back through the same
3-field chain the filter uses. Downstream writers (LLM, image,
caption) see a filled ``summary`` field.

These tests pin the contract so a future refactor cannot silently
regress the 3-field chain.
"""

from __future__ import annotations

from genlab_core.strategies.base_writing import (
    BaseWritingStrategy,
    _has_writable_context,
)


class _TestBaseWriting(BaseWritingStrategy):
    """Concrete subclass exposing the protected helper for testing."""

    def _load_config(self):
        pass

    def execute(self, context):  # pragma: no cover — not exercised
        return context


def _writer() -> _TestBaseWriting:
    """Instantiate a minimal BaseWritingStrategy for boundary tests."""
    from pathlib import Path

    return _TestBaseWriting(niche_id="movies", niche_root=Path("/tmp"))


class TestStoryToVideoDictSummaryFallback:
    """The video dict's `summary` must be non-empty when ANY of the
    3 fields the thin-context filter checks is populated."""

    def test_summary_present_wins(self) -> None:
        """When `summary` is populated, use it verbatim — no fallback."""
        w = _writer()
        video = w._story_to_video_dict(
            {
                "title": "Test",
                "summary": "This is a real summary that clears the 40-char floor easily.",
                "description_snippet": "shorter",
                "description": "also different",
            }
        )
        assert video["description_snippet"].startswith("This is a real summary")

    def test_description_snippet_fallback_when_summary_empty(self) -> None:
        """The exact class-of-bug: summary empty, description_snippet
        populated. Before the fix, video["description_snippet"] would be ""; after,
        it must carry description_snippet content."""
        w = _writer()
        video = w._story_to_video_dict(
            {
                "title": "Avengers: Doomsday",
                "summary": "",
                "description_snippet": (
                    "Doomsday arrives December 18th. Experience Avengers: "
                    "Doomsday on bigger, brighter, more immersive screens."
                ),
                "description": "",
            }
        )
        assert video["description_snippet"].startswith("Doomsday arrives"), (
            f"description_snippet fallback broken; got: {video['summary'][:100]!r}"
        )

    def test_description_fallback_when_summary_and_snippet_empty(self) -> None:
        """Rarest fallback path — Reddit stories sometimes only populate
        `description`. Same 3-field precedence must catch this."""
        w = _writer()
        video = w._story_to_video_dict(
            {
                "title": "Reddit test",
                "summary": "",
                "description_snippet": "",
                "description": "A meaningful description that easily clears 40 chars for the LLM.",
            }
        )
        assert video["description_snippet"].startswith("A meaningful description")

    def test_all_three_empty_yields_empty_summary(self) -> None:
        """When all 3 sources are empty, the fallback chain collapses to
        empty string — matching the filter's False verdict for skip_llm.

        Note: the video dict's OUTPUT key is `description_snippet` (which
        video_content_writer reads via `video.get('description_snippet')`),
        but its VALUE is derived from the source-of-truth chain
        summary → description_snippet → description on the input story.
        """
        w = _writer()
        video = w._story_to_video_dict(
            {
                "title": "Bare title only",
                "summary": "",
                "description_snippet": "",
                "description": "",
            }
        )
        assert video["description_snippet"] == ""

    def test_none_values_do_not_crash(self) -> None:
        """Story fetchers occasionally emit None (not ""). The `or`
        chain must handle that without raising."""
        w = _writer()
        video = w._story_to_video_dict(
            {
                "title": "None test",
                "summary": None,
                "description_snippet": None,
                "description": "Non-null description with enough chars for the writer.",
            }
        )
        assert video["description_snippet"].startswith("Non-null description")


class TestFilterAndWriterContractAligned:
    """Regression-style tests: for every story shape that
    `_has_writable_context` returns True, the writer input must have
    non-empty summary. Prevents silent divergence between the two."""

    def test_filter_passes_matches_writer_summary_populated(self) -> None:
        """Union over the 3-field chain: if filter passes, summary
        must not be empty in the writer's video dict."""
        w = _writer()
        cases = [
            {"title": "T", "summary": "A" * 50},  # summary path
            {"title": "T", "description_snippet": "B" * 50},  # snippet path
            {"title": "T", "description": "C" * 50},  # description path
        ]
        for story in cases:
            assert _has_writable_context(story), f"Filter should pass: {story}"
            video = w._story_to_video_dict(story)
            assert video["description_snippet"], (
                f"Contract violation: filter passed {list(story)!r} but "
                f"writer got empty summary. This is the exact class-of-bug "
                f"the movies backlog-starvation fix guards against."
            )

    def test_filter_rejects_matches_writer_output_may_be_short(self) -> None:
        """Symmetry: when the filter rejects (all 3 fields below 40 chars),
        the video dict's description_snippet may still carry the short
        fallback content — downstream `_skip_llm=True` handles the rest.
        This isn't strictly required but documents current behavior."""
        w = _writer()
        story = {"title": "T", "summary": "", "description_snippet": "short"}
        # short (5 chars) < 40 → filter False
        assert not _has_writable_context(story)
        video = w._story_to_video_dict(story)
        assert isinstance(video["description_snippet"], str)
