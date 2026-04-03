from pathlib import Path
from unittest.mock import patch

from sr_strategies.visual_render import MovieVisualRenderStrategy


class TestWhisperCaptionWiring:
    def setup_method(self):
        self.strategy = MovieVisualRenderStrategy()

    @patch("sr_strategies.visual_render.has_meaningful_audio", return_value=True)
    @patch("sr_strategies.visual_render.extract_audio_track")
    @patch("sr_strategies.visual_render.transcribe_words")
    @patch("sr_strategies.visual_render.align_words")
    def test_prepare_whisper_words_with_audio(self, mock_align, mock_transcribe, mock_extract, mock_has_audio):
        mock_extract.return_value = Path("/tmp/audio.wav")
        mock_transcribe.return_value = [{"word": "epic", "start": 0.1, "end": 0.4, "confidence": 0.9}]
        mock_align.return_value = [{"word": "EPIC", "start": 0.1, "end": 0.4, "confidence": 0.9}]
        result = self.strategy.prepare_whisper_words(clip_path=Path("/tmp/trailer.mp4"), caption_text="EPIC")
        assert result is not None

    @patch("sr_strategies.visual_render.has_meaningful_audio", return_value=False)
    def test_silent_clip_returns_none(self, mock_has_audio):
        result = self.strategy.prepare_whisper_words(clip_path=Path("/tmp/silent.mp4"), caption_text="Coming soon")
        assert result is None
