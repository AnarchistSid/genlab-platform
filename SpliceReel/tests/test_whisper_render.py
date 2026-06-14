"""Pins the whisper-sync wiring on the movies strategy.

Note: ``whisper_sync.enabled`` is set to ``false`` across all 5
niches as of 2026-06-13 until the ``text_optimizer`` regression is
fixed (see CLAUDE.md and ``[[session-2026-06-13-render-audit-findings]]``).
The production config short-circuits ``prepare_whisper_words`` to
return ``None`` at the enabled-check.

These tests still matter — they pin the wiring that runs **when**
whisper is re-enabled, which is the design intent. To exercise the
post-gate code paths without flipping the config flag (and
re-introducing the regression), each test mocks
``_get_whisper_config`` to return ``enabled=True`` locally.
"""

from pathlib import Path
from unittest.mock import patch

from sr_strategies.visual_render import MovieVisualRenderStrategy

# Whisper config that simulates the post-regression-fix world where
# the feature is enabled. Mirrors the structure of the YAML block in
# ``SpliceReel/config/visuals.yaml`` minus the ab_test sub-dict (this
# function doesn't read it).
_ENABLED_WHISPER_CONFIG = {
    "enabled": True,
    "model_size": "base",
    "silence_threshold_db": -40,
    "min_confidence": 0.3,
}


class TestWhisperCaptionWiring:
    def setup_method(self):
        self.strategy = MovieVisualRenderStrategy()

    @patch.object(
        MovieVisualRenderStrategy,
        "_get_whisper_config",
        return_value=_ENABLED_WHISPER_CONFIG,
    )
    @patch("sr_strategies.visual_render.has_meaningful_audio", return_value=True)
    @patch("sr_strategies.visual_render.extract_audio_track")
    @patch("sr_strategies.visual_render.transcribe_words")
    @patch("sr_strategies.visual_render.align_words")
    def test_prepare_whisper_words_with_audio(
        self,
        mock_align,
        mock_transcribe,
        mock_extract,
        mock_has_audio,
        mock_whisper_config,
    ):
        mock_extract.return_value = Path("/tmp/audio.wav")
        mock_transcribe.return_value = [
            {"word": "epic", "start": 0.1, "end": 0.4, "confidence": 0.9}
        ]
        mock_align.return_value = [{"word": "EPIC", "start": 0.1, "end": 0.4, "confidence": 0.9}]
        result = self.strategy.prepare_whisper_words(
            clip_path=Path("/tmp/trailer.mp4"), caption_text="EPIC"
        )
        assert result is not None

    @patch.object(
        MovieVisualRenderStrategy,
        "_get_whisper_config",
        return_value=_ENABLED_WHISPER_CONFIG,
    )
    @patch("sr_strategies.visual_render.has_meaningful_audio", return_value=False)
    def test_silent_clip_returns_none(self, mock_has_audio, mock_whisper_config):
        """Pins that with whisper enabled, a silent clip returns
        None via the audio-detection gate (NOT via the enabled-check
        short-circuit, which would mask this code path)."""
        result = self.strategy.prepare_whisper_words(
            clip_path=Path("/tmp/silent.mp4"), caption_text="Coming soon"
        )
        assert result is None
