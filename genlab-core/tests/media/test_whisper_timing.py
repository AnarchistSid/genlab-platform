"""Tests for genlab_core.media.whisper_timing.

Covers transcribe_words() (faster-whisper wrapper), align_words()
(two-pointer text alignment with interpolation), and
_flatten_whisper_words() (CR segment format conversion).
All tests are mock-based -- no real faster-whisper model required.
"""

from unittest.mock import MagicMock, patch

from genlab_core.media.whisper_timing import (
    _flatten_whisper_words,
    align_words,
    transcribe_words,
)

# ── Helper to build mock Word objects ────────────────────────────────────────


def _mock_word(word: str, start: float, end: float, probability: float) -> MagicMock:
    """Build a MagicMock mimicking a faster-whisper Word namedtuple."""
    w = MagicMock()
    w.word = word
    w.start = start
    w.end = end
    w.probability = probability
    return w


def _mock_segment(words: list[MagicMock]) -> MagicMock:
    """Build a MagicMock mimicking a faster-whisper Segment."""
    seg = MagicMock()
    seg.words = words
    return seg


# ── transcribe_words ─────────────────────────────────────────────────────────


class TestTranscribeWords:
    @patch("genlab_core.media.whisper_timing.get_model")
    @patch("genlab_core.media.whisper_timing._FASTER_WHISPER_AVAILABLE", True)
    def test_returns_word_dicts(self, mock_get_model, tmp_path):
        """3 words should come back as list of dicts with correct keys."""
        audio = tmp_path / "clip.wav"
        audio.touch()

        w1 = _mock_word("Hello", 0.0, 0.3, 0.95)
        w2 = _mock_word("world", 0.4, 0.7, 0.88)
        w3 = _mock_word("today", 0.8, 1.1, 0.91)
        seg = _mock_segment([w1, w2, w3])

        info_mock = MagicMock()
        mock_get_model.return_value.transcribe.return_value = (iter([seg]), info_mock)

        result = transcribe_words(audio)

        assert result is not None
        assert len(result) == 3
        for d in result:
            assert set(d.keys()) == {"word", "start", "end", "confidence"}
        assert result[0]["word"] == "Hello"
        assert result[0]["start"] == 0.0
        assert result[0]["end"] == 0.3
        assert result[0]["confidence"] == 0.95

    @patch("genlab_core.media.whisper_timing.get_model")
    @patch("genlab_core.media.whisper_timing._FASTER_WHISPER_AVAILABLE", True)
    def test_filters_low_confidence(self, mock_get_model, tmp_path):
        """Words with probability below min_confidence should be excluded."""
        audio = tmp_path / "clip.wav"
        audio.touch()

        w1 = _mock_word("good", 0.0, 0.3, 0.85)
        w2 = _mock_word("um", 0.4, 0.5, 0.15)  # below default 0.3
        w3 = _mock_word("day", 0.6, 0.9, 0.90)
        seg = _mock_segment([w1, w2, w3])

        info_mock = MagicMock()
        mock_get_model.return_value.transcribe.return_value = (iter([seg]), info_mock)

        result = transcribe_words(audio, min_confidence=0.3)

        assert result is not None
        assert len(result) == 2
        assert [d["word"] for d in result] == ["good", "day"]

    @patch("genlab_core.media.whisper_timing._FASTER_WHISPER_AVAILABLE", False)
    def test_returns_none_when_not_installed(self, tmp_path):
        """When faster-whisper is not installed, should return None."""
        audio = tmp_path / "clip.wav"
        audio.touch()
        result = transcribe_words(audio)
        assert result is None

    @patch("genlab_core.media.whisper_timing.get_model")
    @patch("genlab_core.media.whisper_timing._FASTER_WHISPER_AVAILABLE", True)
    def test_transcription_error_returns_none(self, mock_get_model, tmp_path):
        """RuntimeError during transcription should return None, not raise."""
        audio = tmp_path / "clip.wav"
        audio.touch()

        mock_get_model.return_value.transcribe.side_effect = RuntimeError("CUDA OOM")

        result = transcribe_words(audio)
        assert result is None


# ── align_words ──────────────────────────────────────────────────────────────


class TestAlignWords:
    def test_perfect_match(self):
        """When all words match exactly, all get Whisper timestamps."""
        text = "Hello world good day"
        whisper_words = [
            {"word": "Hello", "start": 0.0, "end": 0.3, "confidence": 0.95},
            {"word": "world", "start": 0.4, "end": 0.7, "confidence": 0.90},
            {"word": "good", "start": 0.8, "end": 1.0, "confidence": 0.88},
            {"word": "day", "start": 1.1, "end": 1.4, "confidence": 0.92},
        ]

        result = align_words(text, whisper_words)

        assert result is not None
        assert len(result) == 4
        # Authored text preserved
        assert result[0]["word"] == "Hello"
        assert result[0]["start"] == 0.0
        assert result[0]["end"] == 0.3
        assert result[3]["word"] == "day"
        assert result[3]["start"] == 1.1

    def test_case_insensitive_match(self):
        """Uppercase authored text should match lowercase Whisper output."""
        text = "THE quick FOX"
        whisper_words = [
            {"word": "the", "start": 0.0, "end": 0.2, "confidence": 0.90},
            {"word": "quick", "start": 0.3, "end": 0.5, "confidence": 0.85},
            {"word": "fox", "start": 0.6, "end": 0.9, "confidence": 0.88},
        ]

        result = align_words(text, whisper_words)

        assert result is not None
        assert len(result) == 3
        # Authored text preserved (uppercase)
        assert result[0]["word"] == "THE"
        assert result[0]["start"] == 0.0
        assert result[2]["word"] == "FOX"
        assert result[2]["start"] == 0.6

    def test_unmatched_words_get_interpolated(self):
        """A word missing from Whisper output should get interpolated timestamps."""
        text = "AI will change everything"
        whisper_words = [
            {"word": "AI", "start": 0.0, "end": 0.3, "confidence": 0.92},
            # "will" is missing from Whisper
            {"word": "change", "start": 0.6, "end": 0.9, "confidence": 0.88},
            {"word": "everything", "start": 1.0, "end": 1.5, "confidence": 0.90},
        ]

        result = align_words(text, whisper_words)

        assert result is not None
        assert len(result) == 4
        assert result[0]["word"] == "AI"
        assert result[0]["start"] == 0.0
        # "will" should have interpolated timestamps between AI.end and change.start
        assert result[1]["word"] == "will"
        assert result[1]["start"] is not None
        assert result[1]["end"] is not None
        assert result[1]["start"] >= 0.3   # after AI ends
        assert result[1]["end"] <= 0.6     # before change starts
        assert result[2]["word"] == "change"
        assert result[2]["start"] == 0.6

    def test_catastrophic_mismatch_returns_none(self):
        """Completely different text should exceed mismatch threshold and return None."""
        text = "apple banana cherry date elderberry fig grape"
        whisper_words = [
            {"word": "one", "start": 0.0, "end": 0.2, "confidence": 0.80},
            {"word": "two", "start": 0.3, "end": 0.5, "confidence": 0.75},
            {"word": "three", "start": 0.6, "end": 0.9, "confidence": 0.70},
        ]

        result = align_words(text, whisper_words, mismatch_threshold=0.30)
        assert result is None


# ── _flatten_whisper_words ───────────────────────────────────────────────────


class TestFlattenWhisperWords:
    def test_flattens_multiple_segments(self):
        """Two CR-format segments should produce a flat list with 'confidence' key."""
        segments = [
            {
                "start": 0.0,
                "end": 1.0,
                "text": "Hello world",
                "words": [
                    {"word": "Hello", "start": 0.0, "end": 0.3, "probability": 0.95},
                    {"word": "world", "start": 0.4, "end": 0.7, "probability": 0.88},
                ],
            },
            {
                "start": 1.0,
                "end": 2.0,
                "text": "good day",
                "words": [
                    {"word": "good", "start": 1.0, "end": 1.3, "probability": 0.91},
                    {"word": "day", "start": 1.4, "end": 1.8, "probability": 0.87},
                ],
            },
        ]

        result = _flatten_whisper_words(segments)

        assert len(result) == 4
        # All entries should have "confidence" (not "probability")
        for d in result:
            assert "confidence" in d
            assert "probability" not in d
            assert set(d.keys()) == {"word", "start", "end", "confidence"}

        assert result[0]["word"] == "Hello"
        assert result[0]["confidence"] == 0.95
        assert result[3]["word"] == "day"
        assert result[3]["confidence"] == 0.87
