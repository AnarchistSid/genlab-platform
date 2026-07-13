"""Pin: base_writing skips stories with thin context before invoking LLM.

Post-2026-07-13 audit follow-up (Improvement A). The recurring LLM
refusal preambles in prod (e.g. "I need the Story Summary to write a
hook for Moana. The...") all came from stories where the fetcher
provided empty ``summary`` and no substantive alternative context
field. The LLM correctly gave up, but its refusal text became the
hook via the fallback path.

Before this pin: the writer would burn LLM tokens on unwritable
stories and produce refusal-preamble hooks that the pre-render gate
(PR #784) then rejected at render time — cost + operator noise.

After this pin: unwritable stories are skipped BEFORE the LLM call.
No tokens burned, no rejected blueprints on the operator's Focus
Review queue.

Tests here pin:

  1. The helper's contract on thin-context stories → False
  2. The helper's contract on rich stories → True
  3. The threshold constant (40 chars) is what the writer body uses
  4. The writer body actually consults the helper before LLM
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestHasWritableContextContract:
    """The pure-function helper — no I/O, no LLM calls."""

    def test_empty_dict_returns_false(self):
        from genlab_core.strategies.base_writing import _has_writable_context

        assert _has_writable_context({}) is False

    def test_none_returns_false(self):
        from genlab_core.strategies.base_writing import _has_writable_context

        assert _has_writable_context(None) is False  # type: ignore[arg-type]

    def test_empty_summary_returns_false(self):
        from genlab_core.strategies.base_writing import _has_writable_context

        story = {"title": "Moana - the Heihei and Pua content", "summary": ""}
        assert _has_writable_context(story) is False

    def test_below_threshold_summary_returns_false(self):
        """39-char summary is 1 char below the 40-char floor — the
        boundary that separates thin from usable context."""
        from genlab_core.strategies.base_writing import _has_writable_context

        # Exactly 39 chars
        story = {"summary": "A" * 39}
        assert _has_writable_context(story) is False

    def test_at_threshold_returns_true(self):
        """Exactly 40 chars — the minimum viable summary."""
        from genlab_core.strategies.base_writing import _has_writable_context

        story = {"summary": "A" * 40}
        assert _has_writable_context(story) is True

    def test_rich_summary_returns_true(self):
        """A real film overview — the happy path."""
        from genlab_core.strategies.base_writing import _has_writable_context

        story = {
            "summary": (
                "A young Polynesian navigator sets sail on a wayfinding "
                "voyage to save her people. Directed by Ron Clements + "
                "John Musker."
            )
        }
        assert _has_writable_context(story) is True

    def test_description_snippet_fallback(self):
        """Some fetchers populate ``description_snippet`` instead of
        ``summary``. Either is enough."""
        from genlab_core.strategies.base_writing import _has_writable_context

        story = {
            "summary": "",
            "description_snippet": "Trending gameplay of the new patch — " * 3,
        }
        assert _has_writable_context(story) is True

    def test_description_fallback(self):
        """Reddit + niche-specific fetchers use ``description``."""
        from genlab_core.strategies.base_writing import _has_writable_context

        story = {
            "summary": "",
            "description_snippet": "",
            "description": "Long enough description that clears the char floor easily",
        }
        assert _has_writable_context(story) is True

    def test_all_empty_fields_returns_false(self):
        from genlab_core.strategies.base_writing import _has_writable_context

        story = {
            "title": "Grand Theft Auto V",  # title alone insufficient
            "summary": "",
            "description_snippet": "",
            "description": "",
        }
        assert _has_writable_context(story) is False

    def test_whitespace_only_summary_returns_false(self):
        """Trims whitespace before length check — a summary of just
        spaces / newlines is NOT usable context."""
        from genlab_core.strategies.base_writing import _has_writable_context

        story = {"summary": "   \n\n\t   " + " " * 50}
        assert _has_writable_context(story) is False


class TestHistoricalFailures:
    """Every one of the 30-day production refusal cases must be
    rejected as thin-context. If a future refactor loosens the
    threshold, THIS class fires."""

    @pytest.mark.parametrize(
        "title",
        [
            # 5 tmdb_trailer cases with empty overview
            "Moana - the Heihei and Pua content we're",
            "Moana - time to journey beyond the reef",
            "The Furious - Get tickets for The Furiou",
            "Enola Holmes 3 - Official Trailer",
            "Husbands in Action - Official Trailer",
            # 5 youtube_trending cases with empty description
            "I love this movie #millennial #nostalgia",
            "You came for the box, didn't you?#shorts",
        ],
    )
    def test_tmdb_and_youtube_empty_summary_stories_rejected(self, title):
        from genlab_core.strategies.base_writing import _has_writable_context

        # The real production failure shape: title present, summary empty
        story = {"title": title, "summary": ""}
        assert _has_writable_context(story) is False, (
            f"Historical refusal case must still be rejected: {title!r}"
        )


class TestWriterBodyWiresTheGate:
    """Source pin: the writer's ``write`` method body must actually
    call ``_has_writable_context`` and short-circuit on False. If a
    refactor drops the call, the historical failure class re-opens.
    """

    def test_writer_module_imports_the_helper(self):
        """The helper must be defined in the module — pin against
        accidental deletion during refactor."""
        import genlab_core.strategies.base_writing as mod

        assert hasattr(mod, "_has_writable_context")
        assert hasattr(mod, "_MIN_WRITABLE_CONTEXT_CHARS")
        assert mod._MIN_WRITABLE_CONTEXT_CHARS == 40

    def test_writer_body_references_the_helper(self):
        """Source pin: the ``write`` method body actually calls
        ``_has_writable_context``. If the call is removed the pin
        fires."""
        import re

        import genlab_core.strategies.base_writing as mod

        src = Path(mod.__file__).read_text()
        # Normalise whitespace so the pin survives cosmetic reformatting
        src = re.sub(r"\s+", " ", src)
        assert "_has_writable_context(story)" in src, (
            "The writer body must call _has_writable_context(story) "
            "before invoking the LLM. If this pin fires, someone "
            "removed the guard and the historical refusal-preamble "
            "class-of-bug is back on the table."
        )


class TestThinContextStorySkipped:
    """End-to-end: given a thin-context story, the writer marks it
    for skip and never calls the LLM."""

    def test_thin_story_marked_skip_without_llm_call(self, monkeypatch):
        from genlab_core.strategies.base_writing import BaseWritingStrategy

        # Concrete subclass with the abstract methods stubbed out
        class _StubWriter(BaseWritingStrategy):
            def _ensure_config(self):
                self._writing_config = {}
                self._writing_cfg = {}

            def _story_to_video_dict(self, story, clip_index=None):
                # Pass title through so the mocked LLM call can track
                # which story reached it.
                return {"title": story.get("title", "")}

            def prepare_whisper_words(self, clip_path, story):
                return []

            def _compose_frame(self, clip_path, story, context):
                return ""

            def _model_route_key(self):
                return "test"

        s = _StubWriter.__new__(_StubWriter)
        s._niche_id = "movies"
        s.niche_id = "movies"
        s._writing_config = {}
        s._writing_cfg = {}
        s._templates = {}

        # Environment shape the write() method inspects — force use_llm=True
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        # A thin-context Moana-shape story
        thin_story = {
            "story_id": "thin-1",
            "title": "Moana - the Heihei and Pua content",
            "summary": "",
            "_video_ok": True,
        }

        # A rich-context story that should NOT be skipped
        rich_story = {
            "story_id": "rich-1",
            "title": "Enola Holmes 3 breakdown",
            "summary": (
                "The third instalment of the Enola Holmes mystery series "
                "picks up with a fresh case and returning cast."
            ),
            "_video_ok": True,
        }

        context = {"stories": [thin_story, rich_story]}

        # Patch the LLM client construction + write_video_content so
        # the test doesn't hit the API. Track calls.
        calls_to_llm: list[str] = []

        def _fake_write(video, **kwargs):
            calls_to_llm.append(video.get("title", "unknown"))
            return {
                "hook": "Real hook with enough length",
                "instagram_caption": "cap",
                "facebook_content": "cap",
                "youtube_content": "cap",
                "twitter_content": "cap",
                "tiktok_content": "cap",
                "threads_content": "cap",
            }

        monkeypatch.setattr(
            "genlab_core.writing.video_content_writer.write_video_content",
            _fake_write,
        )

        # Fake the LLM client + model router so use_llm=True branch
        # is exercisable without live credentials.
        class _FakeLLM:
            pass

        monkeypatch.setattr(
            "genlab_core.writing.llm_client.AnthropicLLMClient",
            lambda **kwargs: _FakeLLM(),
        )
        monkeypatch.setattr(
            "genlab_core.cost.model_router.get_model_with_budget",
            lambda key: "claude-haiku-4-5-20251001",
        )

        # Run the writer
        s.execute(context)

        # The thin story must be marked skip AND not present in
        # calls_to_llm
        assert thin_story.get("_skip_llm") is True, (
            "Thin-context story must be marked _skip_llm — otherwise "
            "the LLM gets called on unwritable input"
        )
        assert "Moana - the Heihei and Pua content" not in calls_to_llm, (
            "LLM must not be invoked on thin-context stories"
        )
        # The rich story SHOULD have been written
        assert "Enola Holmes 3 breakdown" in calls_to_llm, (
            "Rich-context story must reach the LLM as normal"
        )
