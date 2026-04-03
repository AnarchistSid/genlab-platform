"""Tests for FrameDrift Whisper-synced captions with TTS fallback."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fd_strategies.visual_render import AnimeVisualRenderStrategy


@pytest.fixture
def whisper_strategy(tmp_path):
    """Strategy with whisper_sync enabled in visuals.yaml."""
    import yaml

    config_dir = tmp_path / "config"
    config_dir.mkdir()

    # sources.yaml (required by _ensure_config)
    (config_dir / "sources.yaml").write_text(yaml.dump({
        "media": {"pexels": {"anime_queries": ["anime aesthetic lifestyle urban"]}},
    }))

    # visuals.yaml with whisper_sync enabled
    (config_dir / "visuals.yaml").write_text(yaml.dump({
        "animation": {
            "word_by_word": {
                "whisper_sync": {
                    "enabled": True,
                    "model_size": "base",
                    "silence_threshold_db": -40,
                    "min_confidence": 0.3,
                },
            },
        },
    }))

    with patch("fd_strategies.visual_render.NICHE_ROOT", tmp_path):
        s = AnimeVisualRenderStrategy()
        s._ensure_config()
        yield s


class TestWhisperCaptionWiring:
    @patch("fd_strategies.visual_render.has_meaningful_audio", return_value=True)
    @patch("fd_strategies.visual_render.extract_audio_track")
    @patch("fd_strategies.visual_render.transcribe_words")
    @patch("fd_strategies.visual_render.align_words")
    def test_path_a_audio_clip(
        self, mock_align, mock_transcribe, mock_extract, mock_has_audio, whisper_strategy,
    ):
        mock_extract.return_value = Path("/tmp/audio.wav")
        mock_transcribe.return_value = [
            {"word": "sakura", "start": 0.1, "end": 0.5, "confidence": 0.9},
        ]
        mock_align.return_value = [
            {"word": "Sakura", "start": 0.1, "end": 0.5, "confidence": 0.9},
        ]
        result = whisper_strategy.prepare_whisper_words(
            clip_path=Path("/tmp/anime.mp4"),
            caption_text="Sakura",
        )
        assert result is not None
        mock_extract.assert_called_once()
        mock_transcribe.assert_called_once()

    @patch("fd_strategies.visual_render.has_meaningful_audio", return_value=False)
    @patch("fd_strategies.visual_render.build_tts_cascade")
    @patch("fd_strategies.visual_render.transcribe_words")
    @patch("fd_strategies.visual_render.align_words")
    def test_path_b_silent_generates_tts(
        self, mock_align, mock_transcribe, mock_tts_cls, mock_has_audio, whisper_strategy,
    ):
        mock_tts = MagicMock()
        mock_tts.synthesize.return_value = MagicMock(success=True, output_path="/tmp/tts.wav")
        mock_tts_cls.return_value = mock_tts
        mock_transcribe.return_value = [
            {"word": "Beautiful", "start": 0.0, "end": 0.4, "confidence": 0.9},
        ]
        mock_align.return_value = [
            {"word": "Beautiful", "start": 0.0, "end": 0.4, "confidence": 0.9},
        ]
        result = whisper_strategy.prepare_whisper_words(
            clip_path=Path("/tmp/silent.mp4"),
            caption_text="Beautiful",
        )
        assert result is not None
        mock_tts.synthesize.assert_called_once()

    @patch("fd_strategies.visual_render.has_meaningful_audio", return_value=False)
    @patch("fd_strategies.visual_render.build_tts_cascade")
    def test_path_b_tts_failure_returns_none(
        self, mock_tts_cls, mock_has_audio, whisper_strategy,
    ):
        mock_tts = MagicMock()
        mock_tts.synthesize.return_value = MagicMock(success=False)
        mock_tts_cls.return_value = mock_tts
        result = whisper_strategy.prepare_whisper_words(
            clip_path=Path("/tmp/silent.mp4"),
            caption_text="Fallback",
        )
        assert result is None
