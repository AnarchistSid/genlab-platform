"""Tests for genlab_core.pipeline.stages.render_whisper_captions."""

from __future__ import annotations

import random
from pathlib import Path
from unittest.mock import MagicMock, patch


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


class TestABTestAssignment:
    """Test A/B test assignment logic for caption mode."""

    def setup_method(self):
        self.stage = RenderWhisperCaptions()

    def _make_context(self, ab_enabled=False, wpm_control_pct=0.30, stories=None):
        """Build context with whisper enabled and optional A/B config."""
        ws_config = {
            "enabled": True,
            "model_size": "base",
            "ab_test": {
                "enabled": ab_enabled,
                "wpm_control_pct": wpm_control_pct,
            },
        }
        return {
            "stories": stories or [],
            "config": {"animation": {"word_by_word": {"whisper_sync": ws_config}}},
        }

    def _story_with_render(self, tmp_path, idx=0):
        """Create a story with a fake rendered video file."""
        video = tmp_path / f"video_{idx}.mp4"
        video.write_bytes(b"fake")
        return {
            "title": f"Test story {idx}",
            "hook": f"Hook text {idx}",
            "media": {"rendered_path": str(video)},
        }

    def test_ab_disabled_tags_synced_no_ab(self, tmp_path):
        """When A/B test is disabled, stories get caption_mode=synced_no_ab."""
        story = self._story_with_render(tmp_path)
        context = self._make_context(ab_enabled=False, stories=[story])

        with patch.object(self.stage, "_render_captions", return_value=None):
            result = self.stage.execute(context)

        assert result["stories"][0]["metadata"]["caption_mode"] == "synced_no_ab"
        assert result["run_stats"]["whisper_captions"]["ab_synced"] == 0
        assert result["run_stats"]["whisper_captions"]["ab_wpm_control"] == 0

    def test_ab_enabled_100pct_control_forces_wpm(self, tmp_path):
        """With wpm_control_pct=1.0, all stories get wpm_control."""
        story = self._story_with_render(tmp_path)
        context = self._make_context(
            ab_enabled=True, wpm_control_pct=1.0, stories=[story]
        )

        with patch.object(self.stage, "_render_captions", return_value=None) as mock_render:
            result = self.stage.execute(context)

        assert result["stories"][0]["metadata"]["caption_mode"] == "wpm_control"
        assert result["run_stats"]["whisper_captions"]["ab_wpm_control"] == 1
        # force_wpm=True should be passed
        mock_render.assert_called_once()
        assert mock_render.call_args.kwargs.get("force_wpm") is True

    def test_ab_enabled_0pct_control_forces_synced(self, tmp_path):
        """With wpm_control_pct=0.0, all stories get synced."""
        story = self._story_with_render(tmp_path)
        context = self._make_context(
            ab_enabled=True, wpm_control_pct=0.0, stories=[story]
        )

        with patch.object(self.stage, "_render_captions", return_value=None) as mock_render:
            result = self.stage.execute(context)

        assert result["stories"][0]["metadata"]["caption_mode"] == "synced"
        assert result["run_stats"]["whisper_captions"]["ab_synced"] == 1
        mock_render.assert_called_once()
        assert mock_render.call_args.kwargs.get("force_wpm") is False

    def test_ab_deterministic_with_seed(self, tmp_path):
        """With a fixed seed, assignment is deterministic and splits correctly."""
        stories = [self._story_with_render(tmp_path, idx=i) for i in range(20)]
        context = self._make_context(
            ab_enabled=True, wpm_control_pct=0.30, stories=stories
        )

        random.seed(42)
        with patch.object(self.stage, "_render_captions", return_value=None):
            result = self.stage.execute(context)

        modes = [s["metadata"]["caption_mode"] for s in result["stories"]]
        wpm_count = modes.count("wpm_control")
        synced_count = modes.count("synced")
        assert wpm_count + synced_count == 20
        # With 30% control, ~6 should be wpm. Allow 2-12 for random variation.
        assert 2 <= wpm_count <= 12
        assert result["run_stats"]["whisper_captions"]["ab_wpm_control"] == wpm_count
        assert result["run_stats"]["whisper_captions"]["ab_synced"] == synced_count

    @patch.object(RenderWhisperCaptions, "_get_animator_class")
    @patch.object(RenderWhisperCaptions, "_get_whisper_words")
    @patch(
        "genlab_core.pipeline.stages.render_whisper_captions.get_ffmpeg_binary",
        return_value="ffmpeg",
    )
    @patch("subprocess.run")
    def test_force_wpm_skips_whisper_call(
        self, mock_run, mock_ffmpeg, mock_whisper, mock_animator_cls
    ):
        """When force_wpm=True, _get_whisper_words is NOT called."""
        mock_animator = MagicMock()
        mock_animator.build_animated_filters.return_value = ("drawtext=text='x'", 1.0, 50)
        mock_animator_cls.return_value = MagicMock(return_value=mock_animator)
        mock_run.return_value = MagicMock(returncode=0)

        with patch("pathlib.Path.exists", return_value=True):
            self.stage._render_captions(
                video_path=Path("/tmp/v.mp4"),
                caption_text="test",
                ws_config={"enabled": True},
                item_key="s0",
                config={},
                force_wpm=True,
            )

        mock_whisper.assert_not_called()
        # Animator should receive whisper_words=None (WPM fallback)
        call_kwargs = mock_animator.build_animated_filters.call_args
        assert call_kwargs.kwargs.get("whisper_words") is None
