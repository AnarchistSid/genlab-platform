"""NARR-06 pin: narration_script propagation from writer result → content dict.

Root cause of 2-day recurrence (NARR-04 manual + NARR-05 02:30 fire):
base_writing._write_story_llm's result→content propagator (lines
~514-518) copied hook + instagram_caption + written + written_by,
but NEVER copied narration_script. LLM was correctly emitting the
field (verified via NARR-06 standalone reproduction — response JSON
contained 350+ chars of legit on-topic commentary), but the writer
dropped it before GenerateAudio could read it.

Pin here so the propagation cannot regress. Test the specific
break point AND the prompt-block presence gate.
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock

from genlab_core.strategies import base_writing
from genlab_core.writing import video_content_writer


class TestNarrationPropagation:
    """The break point that caused the NARR-01 → NARR-05 regression."""

    def test_narration_script_propagation_line_present(self):
        """Structural pin: _write_story_llm MUST land the writer's
        narration_script into the content dict.

        NARR-11 (2026-08-20) loosened the *shape* of this assertion, not its
        intent. The original pinned the literal
        ``content["narration_script"] = result.get("narration_script"...)``.
        The script now passes through validate-and-retry before landing, so
        the right-hand side is a validated local — pinning the old RHS would
        forbid ever validating the script, which is the opposite of what this
        file is protecting.

        What still must hold, and is what actually regressed twice: the
        writer's narration_script reaches ``content``.
        """
        src = inspect.getsource(base_writing.BaseWritingStrategy._write_story_llm)
        assert 'content["narration_script"] =' in src, (
            "narration_script propagation missing from _write_story_llm — "
            "the exact NARR-01→NARR-05 regression will recur"
        )
        assert 'result.get("narration_script"' in src, (
            "the writer's narration_script is never read — the value landing "
            "in content must originate from the LLM result"
        )

    def test_propagator_sits_between_content_seed_and_return(self):
        """The line must be inside _write_story_llm (not somewhere
        unreachable). Cheap sanity — assign happens in the same
        function that seeds `content = story.setdefault('content', {})`."""
        src = inspect.getsource(base_writing.BaseWritingStrategy._write_story_llm)
        content_seed_idx = src.find('content = story.setdefault("content", {})')
        prop_idx = src.find('content["narration_script"] =')
        assert content_seed_idx >= 0
        assert prop_idx > content_seed_idx, (
            "narration_script propagation must appear AFTER content = story.setdefault"
        )


class TestPromptBlockConditional:
    """The other missing test from NARR-01: prompt block present when
    the caller asks for narration, absent when not.

    Tests write_video_content's `narration_target_seconds` kwarg —
    None means byte-identical legacy prompt (non-canary niches),
    non-None means the narration block is included."""

    def _make_stub_llm(self, response_json='{}'):
        """LLM stub that records the prompt it received AND returns
        a fixed JSON so write_video_content doesn't blow up."""
        client = MagicMock()
        captured = {}
        def _complete(system, user, max_tokens=1024, temperature=0.7):
            captured["system"] = system
            captured["user"] = user
            return response_json
        client.complete.side_effect = _complete
        return client, captured

    def _minimal_video(self):
        return {
            "video_id": "test123",
            "title": "Test title for narration prompt gate",
            "channel_name": "Test Channel",
            "view_count": 1000,
            "view_velocity": 100,
            "age_hours": 10,
            "description_snippet": "Test description that provides context.",
            "tags": ["test", "narration"],
            "duration_seconds": 30.0,
        }

    def test_narration_block_absent_when_target_seconds_none(self):
        """Non-canary path: writer called with narration_target_seconds=None
        must produce a prompt WITHOUT the narration instruction block.
        Byte-identical to pre-NARR-01 output for the 4 non-canary niches."""
        client, captured = self._make_stub_llm(
            '{"hook":"h","instagram_caption":"c","twitter_content":"t",'
            '"youtube_content":"y","facebook_content":"f","threads_content":"th"}'
        )
        video_content_writer.write_video_content(
            video=self._minimal_video(),
            niche_id="gaming",
            llm_client=client,
            narration_target_seconds=None,
        )
        combined = captured["system"] + captured["user"]
        assert "narration_script" not in combined, (
            "prompt must not mention narration_script when "
            "narration_target_seconds is None"
        )
        assert "NARRATION SCRIPT" not in combined, (
            "prompt must not include the NARRATION SCRIPT block when "
            "narration_target_seconds is None"
        )

    def test_narration_block_present_when_target_seconds_set(self):
        """Canary path: writer called with narration_target_seconds=30
        must include the narration block in the assembled prompt so
        the LLM knows to emit narration_script."""
        client, captured = self._make_stub_llm(
            '{"hook":"h","instagram_caption":"c","twitter_content":"t",'
            '"youtube_content":"y","facebook_content":"f","threads_content":"th",'
            '"narration_script":"test narration"}'
        )
        video_content_writer.write_video_content(
            video=self._minimal_video(),
            niche_id="ai_creators",
            llm_client=client,
            narration_target_seconds=30.0,
        )
        combined = captured["system"] + captured["user"]
        # H1 test: the actual block markers from _build_narration_hint
        # + the OUTPUT FORMAT REQUIRED-field mention
        assert "NARRATION SCRIPT" in combined, (
            "prompt must include NARRATION SCRIPT block when target_seconds set"
        )
        assert "narration_script" in combined, (
            "prompt must name the narration_script output field"
        )
        assert "HARD word cap" in combined, (
            "prompt must include the word-cap directive"
        )


class TestReturnedResultShape:
    """Downstream contract: write_video_content's return dict includes
    narration_script when the LLM emits it."""

    def test_narration_script_key_returned_when_present(self):
        client = MagicMock()
        client.complete.return_value = (
            '{"hook":"h","instagram_caption":"c","twitter_content":"t",'
            '"youtube_content":"y","facebook_content":"f","threads_content":"th",'
            '"narration_script":"real commentary here"}'
        )
        video = {
            "video_id": "test123",
            "title": "Test",
            "channel_name": "Ch",
            "view_count": 100,
            "view_velocity": 10,
            "age_hours": 1,
            "description_snippet": "Test description providing enough context.",
            "duration_seconds": 30.0,
        }
        result = video_content_writer.write_video_content(
            video=video,
            niche_id="ai_creators",
            llm_client=client,
            narration_target_seconds=30.0,
        )
        assert "narration_script" in result
        # Sentence-case may have munged it slightly but the substring
        # of the LLM's content must still be there
        assert "commentary" in result["narration_script"].lower()
