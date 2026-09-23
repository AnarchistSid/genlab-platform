"""Pins for the length floor (ANIME-15 §6).

Named in four packets and never wired. TOUGEN ANKI's pipeline script is 20
spoken words against a 60-word target: it passes every other rule, produces a
correct reel, and that reel runs 9 s against a 15 s floor. Nothing downstream
can fix it — stretching 20 words means holding shots past what the pacing
allows.
"""
from __future__ import annotations

import pytest
from genlab_core.writing.narration_validator import (
    validate_narration_script,
    word_cap,
    word_floor,
)
from genlab_core.writing.video_content_writer import _build_narration_hint


def _words(n: int) -> str:
    return " ".join(["word"] * n)


class TestTheFloorFires:
    def test_the_tougen_script_is_rejected(self):
        script = ("After dominating Kyoto and Nerima, Rasetsu Academy's reign just "
                  "ended. The Oni bloodline finally found someone who could match them.")
        assert validate_narration_script(script, 35, 177, 2.0, 0.05, 0.70) == (
            False, "script_too_short")

    def test_off_by_default_so_no_niche_changes_without_opting_in(self):
        script = "Two short sentences. That is all there is to say about it here."
        assert validate_narration_script(script, 35, 177, 2.0, 0.05)[0] is True
        assert validate_narration_script(script, 35, 177, 2.0, 0.05, 0.0)[0] is True

    def test_it_is_reported_before_too_long_can_apply(self):
        """A script is only ever one of the two, and which one is reported
        decides which correction the retry carries."""
        assert validate_narration_script(_words(5), 35, 177, 2.0, 0.05, 0.70)[1] in (
            "script_generation_failed", "script_too_short")


class TestOneContractOneImplementer:
    """NARR-11's defect, for the fourth time: the prompt and the validator
    each computing the same bound their own way and drifting apart."""

    @pytest.mark.parametrize("target", [20, 26, 35, 45, 60])
    @pytest.mark.parametrize("wpm", [139, 150, 177])
    def test_the_stated_floor_and_cap_are_both_accepted(self, target, wpm):
        cap = word_cap(target, wpm, 2.0, 0.05)
        floor = word_floor(target, wpm, 2.0, 0.05, 0.70)
        assert validate_narration_script(
            _words(floor), target, wpm, 2.0, 0.05, 0.70) == (True, ""), (
            f"a script written to the STATED minimum of {floor} was rejected")
        assert validate_narration_script(
            _words(cap), target, wpm, 2.0, 0.05, 0.70) == (True, "")

    @pytest.mark.parametrize("target", [26, 35, 45])
    def test_one_word_outside_each_bound_is_rejected(self, target):
        cap = word_cap(target, 177, 2.0, 0.05)
        floor = word_floor(target, 177, 2.0, 0.05, 0.70)
        assert validate_narration_script(
            _words(floor - 1), target, 177, 2.0, 0.05, 0.70)[1] == "script_too_short"
        assert validate_narration_script(
            _words(cap + 1), target, 177, 2.0, 0.05, 0.70)[1] == "script_too_long"

    def test_the_prompt_states_the_number_the_validator_enforces(self):
        """A gate that rejects a target the prompt never stated is a retry
        loop, not a gate."""
        hint = _build_narration_hint(35, 177, 0.05, 0.70)
        floor = word_floor(35, 177, 2.0, 0.05, 0.70)
        cap = word_cap(35, 177, 2.0, 0.05)
        assert f"MINIMUM: {floor} words" in hint
        assert f"HARD word cap: {cap} words" in hint

    def test_no_minimum_line_when_the_floor_is_off(self):
        assert "MINIMUM" not in _build_narration_hint(35, 177, 0.05)


class TestTheAsymmetry:
    def test_short_keeps_the_script_where_long_degrades(self):
        """Source-level pin. An oversized VO is truncated mid-sentence by the
        mix and silence is better; a short VO still produces a correct reel,
        so degrading it to no narration trades a short reel for a worse one."""
        from pathlib import Path

        import genlab_core.strategies.base_writing as BW

        src = Path(BW.__file__).read_text()
        body = src[src.index("_validate_narration_with_retry"):]
        assert 'if reason2 == "script_too_short":' in body
        assert "a short script beats no narration" in body
