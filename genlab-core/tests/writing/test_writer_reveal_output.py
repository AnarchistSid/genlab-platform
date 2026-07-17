"""Writer reveal-field output pin — Layer 3 S4b.

When the writer's question_reveal selector fires, the OUTPUT FORMAT
block includes a 7th required field ``reveal``. The parser normalizes
the reveal text (smart-quote normalization, length truncation).

When the variant DIDN'T fire, the OUTPUT FORMAT still requires only
6 fields — no reveal noise in the prompt (token cost).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_llm_mock(with_reveal: bool = False):
    client = MagicMock()
    if with_reveal:
        client.complete.return_value = (
            '{"hook": "test hook",'
            ' "instagram_caption": "test caption. #a #b #c",'
            ' "twitter_content": "test tweet",'
            ' "youtube_content": "test?",'
            ' "facebook_content": "test facebook post ending in question?",'
            ' "threads_content": "test threads post",'
            ' "reveal": "The answer is 42"}'
        )
    else:
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


class TestRevealPromptField:
    def test_reveal_field_in_prompt_when_question_reveal(self) -> None:
        from genlab_core.writing.video_content_writer import write_video_content

        video = _base_video(title="How did Curry hit this shot?")
        llm = _make_llm_mock(with_reveal=True)

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
        assert "reveal" in prompt.lower(), (
            "when question_reveal fires, prompt must request a `reveal` field"
        )
        # The specific format mentions "REQUIRED for question_reveal"
        assert "question_reveal" in prompt

    def test_reveal_field_absent_when_no_variant(self) -> None:
        from genlab_core.writing.video_content_writer import write_video_content

        video = _base_video(title="New DLC trailer")  # no variant fires
        llm = _make_llm_mock(with_reveal=False)

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
        # 6 required output keys — no reveal requirement
        assert "REQUIRED for question_reveal" not in prompt


class TestRevealParsing:
    def test_reveal_populated_in_content_when_variant_fires(self) -> None:
        from genlab_core.writing.video_content_writer import write_video_content

        video = _base_video(title="How did Curry hit this shot?")
        llm = _make_llm_mock(with_reveal=True)

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
            result = write_video_content(video, "sports", llm, existing_hooks=[])

        # Writer normalizes reveal text, but the substance should be there
        assert result.get("reveal"), "reveal field should populate on question_reveal"
        assert "answer" in result["reveal"].lower()

    def test_reveal_absent_when_no_variant(self) -> None:
        from genlab_core.writing.video_content_writer import write_video_content

        video = _base_video(title="New DLC trailer")
        llm = _make_llm_mock(with_reveal=False)

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
            result = write_video_content(video, "gaming", llm, existing_hooks=[])

        # No reveal field expected on non-question_reveal blueprints —
        # writer doesn't request it, LLM doesn't emit it. Parser doesn't
        # add it either.
        assert result.get("reveal", "") == ""

    def test_smart_quotes_normalized_in_reveal(self) -> None:
        from genlab_core.writing.video_content_writer import write_video_content

        video = _base_video(title="Why did this happen?")
        llm = MagicMock()
        llm.complete.return_value = (
            '{"hook": "test hook",'
            ' "instagram_caption": "test caption. #a #b #c",'
            ' "twitter_content": "test tweet",'
            ' "youtube_content": "test?",'
            ' "facebook_content": "test facebook post ending in question?",'
            ' "threads_content": "test threads post",'
            ' "reveal": "It’s about time"}'  # smart quote
        )

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
            result = write_video_content(video, "gaming", llm, existing_hooks=[])

        # Smart apostrophe should normalize to ASCII
        assert "’" not in result.get("reveal", "")
        assert "It's" in result.get("reveal", "")

    def test_long_reveal_truncated(self) -> None:
        from genlab_core.writing.video_content_writer import write_video_content

        video = _base_video(title="Why did this happen?")
        long_reveal = "A" * 120  # exceeds 80-char threshold
        llm = MagicMock()
        llm.complete.return_value = (
            '{"hook": "test hook",'
            ' "instagram_caption": "test caption. #a #b #c",'
            ' "twitter_content": "test tweet",'
            ' "youtube_content": "test?",'
            ' "facebook_content": "test facebook post ending in question?",'
            ' "threads_content": "test threads post",'
            f' "reveal": "{long_reveal}"}}'
        )

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
            result = write_video_content(video, "gaming", llm, existing_hooks=[])

        assert len(result.get("reveal", "")) <= 80, "reveal must be truncated to fit compositor"
