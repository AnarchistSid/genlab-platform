"""Writer series injection pin — Layer 3 S2.

When `video_content_writer.write_video_content` receives a video whose
title indicates a series (e.g. "Elden Ring Part 3"), the LLM system
prompt MUST include a SERIES CONTEXT section. When the title is
standalone, no SERIES CONTEXT section should appear.

This pin catches regressions where series detection wire is removed or
short-circuited without the corresponding audit trail.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_llm_mock():
    """Return an llm_client mock whose .complete() returns valid JSON."""
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
    """Extract the system prompt from an llm_client.complete() call."""
    # complete(system=..., user=..., max_tokens=..., temperature=...)
    if "system" in call_args.kwargs:
        return call_args.kwargs["system"]
    # positional
    return call_args.args[0] if call_args.args else ""


class TestWriterSeriesInjection:
    def test_series_title_triggers_series_context_section(self) -> None:
        from genlab_core.writing.video_content_writer import write_video_content

        video = {
            "title": "Elden Ring Playthrough Part 3 of 5",
            "channel_id": "UC_test",
            "channel_name": "TestChannel",
            "view_count": 100000,
            "view_velocity": 5000,
            "description_snippet": "part 3 of the ring",
            "tags": [],
            "video_id": "vid_1",
        }
        llm = _make_llm_mock()

        # Patch external bandit + strategist hint injectors to isolate the
        # series-injection behavior from those other prompt sections.
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
        assert "SERIES CONTEXT" in prompt, (
            "writer prompt missing SERIES CONTEXT for a series-titled video"
        )
        assert "Part 3 of 5" in prompt, "writer prompt SERIES CONTEXT missing the part indicator"

    def test_standalone_video_no_series_context(self) -> None:
        from genlab_core.writing.video_content_writer import write_video_content

        video = {
            "title": "Just a random gameplay clip",
            "channel_id": "UC_test",
            "channel_name": "TestChannel",
            "view_count": 100000,
            "view_velocity": 5000,
            "description_snippet": "no series here",
            "tags": [],
            "video_id": "vid_2",
        }
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
        assert "SERIES CONTEXT" not in prompt, (
            "writer prompt should NOT include SERIES CONTEXT for standalone video"
        )

    def test_series_detection_error_fails_open(self) -> None:
        """If detect_series raises, writer must still complete normally."""
        from genlab_core.writing.video_content_writer import write_video_content

        video = {
            "title": "Elden Ring Part 3",
            "channel_id": "UC_test",
            "channel_name": "TestChannel",
            "view_count": 100000,
            "view_velocity": 5000,
            "description_snippet": "",
            "tags": [],
            "video_id": "vid_3",
        }
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
                "genlab_core.writing.series_detector.detect_series",
                side_effect=RuntimeError("simulated failure"),
            ),
        ):
            # Should NOT raise — writer must remain functional even if the
            # series-detection module explodes. Writer normalizes hook case
            # so we just check for non-empty return.
            result = write_video_content(video, "gaming", llm, existing_hooks=[])
            assert result.get("hook"), "writer returned empty hook when series detection failed"
