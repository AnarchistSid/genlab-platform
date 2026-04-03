"""Tests for Whisper-synced caption wiring in SportVisualRenderStrategy."""

from pathlib import Path
from unittest.mock import patch

import pytest
from cw_strategies.visual_render import SportVisualRenderStrategy


@pytest.fixture
def strategy():
    return SportVisualRenderStrategy()


class TestWhisperCaptionWiring:
    def test_get_whisper_config_loads_from_yaml(self, strategy):
        strategy._ensure_config()
        ws = strategy._get_whisper_config()
        assert isinstance(ws, dict)
        assert "enabled" in ws

    @patch("cw_strategies.visual_render.align_words")
    @patch("cw_strategies.visual_render.transcribe_words")
    @patch("cw_strategies.visual_render.extract_audio_track")
    @patch("cw_strategies.visual_render.has_meaningful_audio")
    def test_prepare_whisper_words_with_audio(
        self,
        mock_has_audio,
        mock_extract,
        mock_transcribe,
        mock_align,
        strategy,
    ):
        mock_has_audio.return_value = True
        mock_extract.return_value = Path("/tmp/audio.wav")
        mock_transcribe.return_value = [
            {"word": "goal", "start": 0.0, "end": 0.3, "confidence": 0.9},
        ]
        mock_align.return_value = [
            {"word": "GOAL", "start": 0.0, "end": 0.3, "confidence": 0.9},
        ]

        # Force config with whisper enabled
        strategy._visuals_config = {
            "animation": {
                "word_by_word": {
                    "whisper_sync": {"enabled": True, "model_size": "base"},
                },
            },
        }

        result = strategy.prepare_whisper_words(Path("/tmp/clip.mp4"), "GOAL")
        assert result is not None
        assert result[0]["word"] == "GOAL"

    @patch("cw_strategies.visual_render.has_meaningful_audio")
    def test_prepare_whisper_words_silent_clip_returns_none(
        self,
        mock_has_audio,
        strategy,
    ):
        mock_has_audio.return_value = False

        strategy._visuals_config = {
            "animation": {
                "word_by_word": {
                    "whisper_sync": {"enabled": True},
                },
            },
        }

        result = strategy.prepare_whisper_words(Path("/tmp/clip.mp4"), "GOAL")
        assert result is None
