"""Pins for delivery marks and script-timed captions (ANIME-13 §4)."""

from __future__ import annotations

import pytest

from genlab_core.still import align as A
from genlab_core.still import delivery as D
from genlab_core.still.audio import Word as AsrWord


class TestDeliveryMarks:
    def test_one_source_renders_two_ways(self):
        src = "{excited} Satoko has everything except *time* -- her illness gives her months."
        assert D.to_speech(src) == (
            "[excited] Satoko has everything except TIME... her illness gives her months."
        )
        assert D.to_caption(src) == (
            "Satoko has everything except time - her illness gives her months."
        )

    def test_inworld_gets_no_tags_because_it_has_no_tag_vocabulary(self):
        src = "{excited} a *deadly* assassin"
        assert D.to_speech(src, engine="inworld") == "a deadly assassin"
        assert "[excited]" in D.to_speech(src, engine="elevenlabs")

    def test_an_unknown_emotion_is_refused_not_passed_through(self):
        """v3 SPEAKS an unrecognised bracket. A silent pass-through would put
        the word 'dramatic' in the middle of the narration."""
        with pytest.raises(D.DeliveryError):
            D.to_speech("{dramatic} the fight begins")

    def test_unbalanced_stress_is_refused(self):
        with pytest.raises(D.DeliveryError):
            D.to_caption("a *deadly assassin")

    def test_caption_words_are_the_written_words(self):
        assert D.words("{sad} she has *mere months* -- to live") == [
            "she",
            "has",
            "mere",
            "months",
            "-",
            "to",
            "live",
        ]

    def test_coverage_reports_a_flat_script_as_flat(self):
        assert D.coverage(["plain one.", "plain two."]) == 0.0
        assert D.coverage(["{excited} one.", "plain two."]) == 0.5


class TestScriptTimedCaptions:
    def _asr(self, pairs):
        return [AsrWord(text=t, start_s=a, end_s=b) for t, a, b in pairs]

    def test_the_screen_shows_what_we_wrote_not_what_was_heard(self):
        """The 'nareema' case. faster-whisper misheard Nerima; fix_names needed
        three edits and allows two; the wrong spelling reached the frame."""
        script = "After dominating Kyoto and Nerima".split()
        asr = self._asr(
            [
                ("After", 0.0, 0.3),
                ("dominating", 0.3, 0.9),
                ("kyoto", 0.9, 1.3),
                ("and", 1.3, 1.5),
                ("nareema", 1.5, 2.1),
            ]
        )
        out = A.align_script(script, asr)
        assert [w.t for w in out] == script
        assert out[-1].t == "Nerima"
        assert out[-1].a == pytest.approx(1.5) and out[-1].b == pytest.approx(2.1)

    def test_one_asr_token_covering_two_script_words_splits_its_span(self):
        """'twenty twenty-six' came back as '9th, 2026'. Both script words must
        share that token's duration rather than both claiming all of it."""
        script = ["premieres", "twenty", "twenty-six"]
        asr = self._asr([("premieres", 0.0, 0.6), ("2026", 0.6, 1.4)])
        out = A.align_script(script, asr)
        assert [w.t for w in out] == script
        assert out[1].b <= out[2].a + 1e-6
        assert out[2].b == pytest.approx(1.4, abs=0.05)

    def test_timings_stay_monotonic(self):
        script = "one two three four five six".split()
        asr = self._asr([("one", 0.0, 0.2), ("XX", 0.2, 0.9), ("six", 0.9, 1.2)])
        out = A.align_script(script, asr)
        for prev, nxt in zip(out, out[1:], strict=False):
            assert prev.b <= nxt.a + 1e-6, [(w.t, w.a, w.b) for w in out]

    def test_no_asr_is_an_error_not_a_guess(self):
        with pytest.raises(ValueError):
            A.align_script(["a", "b"], [])

    def test_every_script_word_survives(self):
        script = "Firefly Wedding premieres October ninth twenty twenty-six".split()
        asr = self._asr(
            [
                ("firefly", 0.0, 0.4),
                ("wedding", 0.4, 0.8),
                ("premieres", 0.8, 1.3),
                ("october", 1.3, 1.8),
                ("9th", 1.8, 2.1),
                ("2026", 2.1, 2.7),
            ]
        )
        out = A.align_script(script, asr)
        assert [w.t for w in out] == script
