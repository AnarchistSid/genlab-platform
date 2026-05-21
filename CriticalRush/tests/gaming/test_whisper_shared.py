"""Test CriticalRush can use shared whisper_timing module.

Verifies _flatten_whisper_words handles the segment format produced by
CriticalRush's caption_generator.py (which uses 'probability' key
instead of 'confidence').
"""

from genlab_core.media.whisper_timing import _flatten_whisper_words


class TestFlattenCRSegments:
    """Verify _flatten_whisper_words handles CriticalRush segment format."""

    def test_cr_segment_format(self):
        """CriticalRush caption_generator returns segments with 'probability' key."""
        segments = [
            {
                "start": 0.0,
                "end": 1.5,
                "text": "clutch play",
                "words": [
                    {"word": "clutch", "start": 0.0, "end": 0.6, "probability": 0.95},
                    {"word": "play", "start": 0.7, "end": 1.2, "probability": 0.92},
                ],
            },
        ]
        result = _flatten_whisper_words(segments)
        assert len(result) == 2
        assert result[0]["word"] == "clutch"
        assert result[0]["confidence"] == 0.95  # mapped from probability
        assert result[1]["word"] == "play"
        assert result[1]["start"] == 0.7

    def test_empty_segments(self):
        assert _flatten_whisper_words([]) == []
