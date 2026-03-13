"""Tests for genlab_core.pipeline.stages.render_whisper_captions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.pipeline.stages.render_whisper_captions import RenderWhisperCaptions


class TestRenderWhisperCaptions:
    def setup_method(self):
        self.stage = RenderWhisperCaptions()

    def test_skips_when_disabled(self):
        """Stage returns immediately when whisper_sync.enabled is false."""
        context = {"stories": [], "config": {}}
        result = self.stage.execute(context)
        # When disabled, no stats are produced (early return)
        assert "whisper_captions" not in result.get("run_stats", {})

    def test_skips_stories_without_rendered_path(self):
        """Stories without media.rendered_path are counted as skipped."""
        context = {
            "stories": [{"title": "test", "media": {}}],
            "config": {"animation": {"word_by_word": {"whisper_sync": {"enabled": True}}}},
        }
        result = self.stage.execute(context)
        assert result["run_stats"]["whisper_captions"]["skipped"] == 1

    def test_get_whisper_config_extracts_config(self):
        """Config extraction from animation.word_by_word.whisper_sync."""
        config = {
            "animation": {
                "word_by_word": {
                    "whisper_sync": {
                        "enabled": True,
                        "model_size": "small",
                    }
                }
            }
        }
        ws = self.stage._get_whisper_config(config)
        assert ws["enabled"] is True
        assert ws["model_size"] == "small"

    def test_get_whisper_config_returns_disabled_when_missing(self):
        """Missing config returns disabled default."""
        ws = self.stage._get_whisper_config({})
        assert ws["enabled"] is False

    @patch.object(RenderWhisperCaptions, "_get_animator_class")
    @patch.object(RenderWhisperCaptions, "_get_whisper_words")
    @patch(
        "genlab_core.pipeline.stages.render_whisper_captions.get_ffmpeg_binary",
        return_value="ffmpeg",
    )
    @patch("subprocess.run")
    def test_render_captions_calls_animator(
        self,
        mock_run,
        mock_ffmpeg,
        mock_whisper,
        mock_animator_cls,
    ):
        """When whisper words are available, animator receives them."""
        # Setup mock animator instance
        mock_animator = MagicMock()
        mock_animator.build_animated_filters.return_value = (
            "drawtext=text='test'",
            2.0,
            100,
        )
        # _get_animator_class returns a class; calling it returns the instance
        mock_animator_cls.return_value = MagicMock(return_value=mock_animator)

        whisper_words = [
            {"word": "test", "start": 0.1, "end": 0.5, "confidence": 0.9},
        ]
        mock_whisper.return_value = whisper_words

        mock_run.return_value = MagicMock(returncode=0)

        # Patch Path.exists so the output file "exists" after FFmpeg
        with patch("pathlib.Path.exists", return_value=True):
            result = self.stage._render_captions(
                video_path=Path("/tmp/video.mp4"),
                caption_text="test",
                ws_config={"enabled": True, "model_size": "base"},
                item_key="story_0",
                config={},
            )

        # Verify animator was called with whisper_words
        mock_animator.build_animated_filters.assert_called_once()
        call_kwargs = mock_animator.build_animated_filters.call_args
        assert call_kwargs.kwargs.get("whisper_words") == whisper_words
