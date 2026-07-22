"""Pin the 2026-07-22 S7 phase F storytime caption-text dispatch.

Layer 3 S7 phase E (2026-07-22) added compose_storytime — the rendered
video's audio is TTS narration, not source. If RenderWhisperCaptions
were left unchanged, it would try to align caption text against the
NARRATION audio using the HOOK text as the reference — a totally
scrambled overlay.

Phase F fix: when variant_type == "storytime", read caption_text from
variant_payload.narration_text so whisper aligns against the actual
said content. Other variants (single_clip, series_part, watch_till_end,
question_reveal, split_screen) keep the legacy hook/title lookup path
untouched.

These pins lock the dispatch so a future refactor of the priority chain
can't silently regress the storytime path.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.pipeline.stages.render_whisper_captions import RenderWhisperCaptions


class TestCaptionTextDispatchForStorytime:
    def _run_stage_capture_caption(self, story: dict) -> str:
        """Execute the stage with a mocked _render_captions that captures
        caption_text so we can assert the dispatch pick."""
        stage = RenderWhisperCaptions()

        captured: dict[str, str] = {}

        def _fake_render_captions(
            *, video_path, caption_text, ws_config, item_key, config, force_wpm, audio_path
        ):
            captured["caption_text"] = caption_text
            return None  # skip the actual render step

        # Config with whisper_sync enabled so stage doesn't early-return
        cfg = {"animation": {"word_by_word": {"whisper_sync": {"enabled": True}}}}

        # Patch Path.exists to True so the stage doesn't skip on file check
        with patch(
            "genlab_core.pipeline.stages.render_whisper_captions.Path"
        ) as MockPath:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            MockPath.return_value = mock_path_instance
            with patch.object(stage, "_render_captions", side_effect=_fake_render_captions):
                stage.execute({
                    "stories": [story],
                    "niche_config": cfg,
                })
        return captured.get("caption_text", "")

    def test_storytime_uses_narration_text_not_hook(self) -> None:
        """The critical S7-phase-F contract: storytime blueprints have
        their whisper caption text read from variant_payload.narration_text
        (NOT hook_text) so whisper alignment is against the TTS-generated
        audio's actual content."""
        story = {
            "variant_type": "storytime",
            "variant_payload": {
                "narration_text": "This is the narrative content that whisper should align against.",
            },
            "hook": "Different hook text that must not win",
            "media": {
                "rendered_path": "/fake/path.mp4",
                "hook_text": "Yet another hook text",
            },
        }
        got = self._run_stage_capture_caption(story)
        assert got.startswith("This is the narrative content"), (
            f"storytime dispatch broken — caption_text should be narration; got: {got!r}"
        )

    def test_non_storytime_still_uses_hook(self) -> None:
        """Regression pin — the storytime special case MUST NOT bleed
        into other variants. single_clip / series_part / etc. keep the
        legacy hook lookup."""
        story = {
            "variant_type": "single_clip",
            "variant_payload": {"narration_text": "Ignored on single_clip"},
            "hook": "The real hook we want",
            "media": {"rendered_path": "/fake/path.mp4"},
        }
        got = self._run_stage_capture_caption(story)
        assert got == "The real hook we want", (
            f"single_clip dispatch regressed — got: {got!r}"
        )

    def test_storytime_falls_back_to_hook_when_narration_empty(self) -> None:
        """Edge case: variant_type=storytime but narration_text empty. Rather
        than skip the blueprint entirely, fall back to the standard hook
        lookup so the blueprint still gets word-timed overlays (they'll
        be aligned against the hook, which is imperfect but non-crashing)."""
        story = {
            "variant_type": "storytime",
            "variant_payload": {"narration_text": ""},
            "hook": "Fallback hook when narration missing",
            "media": {"rendered_path": "/fake/path.mp4"},
        }
        got = self._run_stage_capture_caption(story)
        assert got == "Fallback hook when narration missing"

    def test_storytime_prefers_narration_over_hook_text(self) -> None:
        """When variant is storytime AND all four fields present (narration,
        hook_text, hook, title), narration_text MUST win."""
        story = {
            "variant_type": "storytime",
            "variant_payload": {"narration_text": "narration wins over all"},
            "hook": "hook_field",
            "title": "title_field",
            "media": {"rendered_path": "/fake/path.mp4", "hook_text": "hook_text_field"},
        }
        got = self._run_stage_capture_caption(story)
        assert "narration wins" in got

    def test_no_variant_type_falls_through_to_hook(self) -> None:
        """Legacy blueprints with no variant_type field (pre-Layer-3) must
        continue to work — caption_text stays hook-derived."""
        story = {
            "hook": "Legacy blueprint hook",
            "media": {"rendered_path": "/fake/path.mp4"},
        }
        got = self._run_stage_capture_caption(story)
        assert got == "Legacy blueprint hook"
