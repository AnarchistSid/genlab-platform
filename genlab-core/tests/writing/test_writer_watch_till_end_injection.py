"""Writer watch_till_end injection pin — Layer 3 S3.

When `video_content_writer.write_video_content` receives a video that
matches watch_till_end eligibility (compilation-type title, 30-90s
duration, not a series), the LLM system prompt MUST include a
WATCH-TILL-END MANDATE section. When ineligible, no MANDATE section.

## Priority interaction with series_part

Both SERIES CONTEXT and WATCH-TILL-END MANDATE could theoretically be
injected together (they aren't mutually exclusive in the prompt), but
the watch_till_end SELECTOR short-circuits on series priority. So in
practice: a "Highlights Part 3" title produces SERIES CONTEXT only,
not both — because is_watch_till_end_eligible returns False when
detect_series fires.

This test file verifies the writer honors that priority through the
prompt inspection, not just through the selector's unit tests.
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


class TestWriterWatchTillEndInjection:
    def test_eligible_video_triggers_mandate(self) -> None:
        from genlab_core.writing.video_content_writer import write_video_content

        video = {
            "title": "Top 10 Elden Ring boss fights",
            "channel_id": "UC_test",
            "channel_name": "TestChannel",
            "view_count": 100000,
            "view_velocity": 5000,
            "duration_seconds": 55,
            "description_snippet": "compilation of the best boss fights",
            "tags": [],
            "video_id": "vid_wte1",
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
        assert "WATCH-TILL-END MANDATE" in prompt, (
            "writer prompt missing WATCH-TILL-END MANDATE for eligible video"
        )
        # Sanity check: series wasn't detected here so SERIES CONTEXT should be absent
        assert "SERIES CONTEXT" not in prompt

    def test_ineligible_video_no_mandate(self) -> None:
        from genlab_core.writing.video_content_writer import write_video_content

        video = {
            # 2026-08-12: "trailer" is now in the widened compilation
            # vocabulary. Use a title matching neither old nor new vocab
            # so the negative-case test is stable across widenings.
            "title": "Deep dive into React hooks internals",
            "channel_id": "UC_test",
            "channel_name": "TestChannel",
            "view_count": 100000,
            "view_velocity": 5000,
            "duration_seconds": 55,
            "description_snippet": "",
            "tags": [],
            "video_id": "vid_wte2",
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
        assert "WATCH-TILL-END MANDATE" not in prompt, (
            "writer prompt should NOT include WATCH-TILL-END MANDATE for ineligible video"
        )

    def test_series_priority_over_watch_till_end(self) -> None:
        """A "Highlights Part 3" title matches both. Series wins in the
        selector — WATCH-TILL-END MANDATE must be absent. But SERIES
        CONTEXT should be present."""
        from genlab_core.writing.video_content_writer import write_video_content

        video = {
            "title": "NBA Highlights Part 3",
            "channel_id": "UC_test",
            "channel_name": "TestChannel",
            "view_count": 100000,
            "view_velocity": 5000,
            "duration_seconds": 55,
            "description_snippet": "",
            "tags": [],
            "video_id": "vid_wte3",
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
        assert "SERIES CONTEXT" in prompt, "series should be detected"
        assert "WATCH-TILL-END MANDATE" not in prompt, (
            "series must take priority — watch_till_end selector short-circuits"
        )

    def test_watch_till_end_selector_error_fails_open(self) -> None:
        """If watch_till_end selector explodes, writer must still work."""
        from genlab_core.writing.video_content_writer import write_video_content

        video = {
            "title": "Top 10 plays",
            "channel_id": "UC_test",
            "channel_name": "TestChannel",
            "view_count": 100000,
            "view_velocity": 5000,
            "duration_seconds": 45,
            "description_snippet": "",
            "tags": [],
            "video_id": "vid_wte4",
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
                "genlab_core.writing.watch_till_end_selector.is_watch_till_end_eligible",
                side_effect=RuntimeError("simulated failure"),
            ),
        ):
            result = write_video_content(video, "gaming", llm, existing_hooks=[])
            assert result.get("hook"), (
                "writer returned empty hook when watch_till_end selection failed"
            )
