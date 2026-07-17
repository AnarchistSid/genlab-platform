"""Writer question_reveal injection pin — Layer 3 S4a.

When `video_content_writer.write_video_content` receives a video whose
title is shaped as a question (Why/How/What/... + "?") in the 30-90s
range, the LLM system prompt MUST include a QUESTION-REVEAL MANDATE
section. When ineligible, absent.

## Three-way priority interaction

For a "How to X — Highlights Part 3?" title:
- Series detected → SERIES CONTEXT present
- Question_reveal selector short-circuits on series → MANDATE absent
- Watch_till_end selector short-circuits on series + wire-level guard →
  MANDATE absent

For a "How did Curry hit this shot?" title (question + not series + not compilation):
- Only QUESTION-REVEAL MANDATE fires

For a "Top 10 Elden Ring boss fights" title (compilation, not question):
- Only WATCH-TILL-END MANDATE fires

This test verifies the writer honors this three-way priority through
prompt inspection.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_llm_mock():
    client = MagicMock()
    client.complete.return_value = (
        '{"hook": "test hook",'
        ' "instagram_caption": "test caption. #a #b #c",'
        ' "twitter_content": "test tweet",'
        ' "youtube_content": "test?",'
        ' "facebook_content": "test facebook post ending in question?",'
        ' "threads_content": "test threads post"}'
    )
    return client


def _capture_system_prompt(call_args):
    if "system" in call_args.kwargs:
        return call_args.kwargs["system"]
    return call_args.args[0] if call_args.args else ""


def _base_video(**overrides):
    """Video dict with all required fields; override per test."""
    video = {
        "title": "test title",
        "channel_id": "UC_test",
        "channel_name": "TestChannel",
        "view_count": 100000,
        "view_velocity": 5000,
        "duration_seconds": 45,
        "description_snippet": "",
        "tags": [],
        "video_id": "vid_qr",
    }
    video.update(overrides)
    return video


class TestWriterQuestionRevealInjection:
    def test_eligible_video_triggers_mandate(self) -> None:
        from genlab_core.writing.video_content_writer import write_video_content

        video = _base_video(title="How did Curry hit this shot from 40 feet?")
        llm = _make_llm_mock()

        with (
            patch(
                "genlab_core.writing.llm_hook_generator.pick_hook_style",
                return_value=None,
            ),
            patch(
                "genlab_core.writing.content_type_hint.pick_content_type_hint",
                return_value=None,
            ),
        ):
            write_video_content(video, "sports", llm, existing_hooks=[])

        prompt = _capture_system_prompt(llm.complete.call_args)
        assert "QUESTION-REVEAL MANDATE" in prompt, (
            "writer prompt missing QUESTION-REVEAL MANDATE for question-titled video"
        )
        assert "SERIES CONTEXT" not in prompt
        assert "WATCH-TILL-END MANDATE" not in prompt

    def test_ineligible_video_no_mandate(self) -> None:
        from genlab_core.writing.video_content_writer import write_video_content

        video = _base_video(title="New Elden Ring DLC trailer")
        llm = _make_llm_mock()

        with (
            patch(
                "genlab_core.writing.llm_hook_generator.pick_hook_style",
                return_value=None,
            ),
            patch(
                "genlab_core.writing.content_type_hint.pick_content_type_hint",
                return_value=None,
            ),
        ):
            write_video_content(video, "gaming", llm, existing_hooks=[])

        prompt = _capture_system_prompt(llm.complete.call_args)
        assert "QUESTION-REVEAL MANDATE" not in prompt

    def test_series_priority_over_question_reveal(self) -> None:
        """Series wins — question_reveal selector short-circuits on series."""
        from genlab_core.writing.video_content_writer import write_video_content

        video = _base_video(title="How does this attack work Part 3?")
        llm = _make_llm_mock()

        with (
            patch(
                "genlab_core.writing.llm_hook_generator.pick_hook_style",
                return_value=None,
            ),
            patch(
                "genlab_core.writing.content_type_hint.pick_content_type_hint",
                return_value=None,
            ),
        ):
            write_video_content(video, "gaming", llm, existing_hooks=[])

        prompt = _capture_system_prompt(llm.complete.call_args)
        assert "SERIES CONTEXT" in prompt
        assert "QUESTION-REVEAL MANDATE" not in prompt

    def test_question_reveal_wins_over_watch_till_end(self) -> None:
        """Both selectors could match ("How are these clips ranked?").
        Question_reveal is MORE specific — must win. WATCH-TILL-END absent."""
        from genlab_core.writing.video_content_writer import write_video_content

        # Title matches BOTH: starts with "How", ends with "?", contains "clips"
        video = _base_video(title="How are these clips ranked?")
        llm = _make_llm_mock()

        with (
            patch(
                "genlab_core.writing.llm_hook_generator.pick_hook_style",
                return_value=None,
            ),
            patch(
                "genlab_core.writing.content_type_hint.pick_content_type_hint",
                return_value=None,
            ),
        ):
            write_video_content(video, "gaming", llm, existing_hooks=[])

        prompt = _capture_system_prompt(llm.complete.call_args)
        assert "QUESTION-REVEAL MANDATE" in prompt, (
            "question_reveal must win over watch_till_end (more specific selector)"
        )
        assert "WATCH-TILL-END MANDATE" not in prompt, (
            "watch_till_end wire-guard must short-circuit when question_reveal fired"
        )

    def test_question_reveal_selector_error_fails_open(self) -> None:
        from genlab_core.writing.video_content_writer import write_video_content

        video = _base_video(title="How did this happen?")
        llm = _make_llm_mock()

        with (
            patch(
                "genlab_core.writing.llm_hook_generator.pick_hook_style",
                return_value=None,
            ),
            patch(
                "genlab_core.writing.content_type_hint.pick_content_type_hint",
                return_value=None,
            ),
            patch(
                "genlab_core.writing.question_reveal_selector.is_question_reveal_eligible",
                side_effect=RuntimeError("simulated failure"),
            ),
        ):
            result = write_video_content(video, "gaming", llm, existing_hooks=[])
            assert result.get("hook"), (
                "writer returned empty hook when question_reveal selection failed"
            )
