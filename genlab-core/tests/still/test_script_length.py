"""Pins for the script-length target (ANIME-13 §7)."""

from __future__ import annotations

import pytest
from genlab_core.still import script_length as S


def test_the_target_is_arithmetic_not_a_constant():
    assert S.target_words(25, 177) == 74  # Inworld/Sarah
    assert S.target_words(25, 139) == 58  # ElevenLabs v3 / laura
    assert S.target_words(30, 139) == 70


def test_the_same_script_targets_differently_per_engine():
    """A 27% rate difference between engines is a different reel from the same
    words. A target computed against the wrong engine is a target for a
    different reel."""
    assert S.target_words(25, 177) != S.target_words(25, 139)


def test_tougen_27_words_is_rejected():
    beats = [
        "After dominating Kyoto and Nerima, Rasetsu Academy's reign just ended. "
        "The Oni bloodline finally found someone who could match them."
    ]
    with pytest.raises(S.ScriptTooShort) as e:
        S.enforce(beats, target_s=25, wpm=139)
    assert "script_too_short" in str(e.value)


def test_a_full_length_script_passes():
    beats = ["word " * 60]
    assert S.enforce(beats, target_s=25, wpm=139).ok


def test_the_floor_is_seventy_percent():
    c = S.check(["word " * 41], target_s=25, wpm=139)  # target 58, floor 41
    assert c.target == 58 and c.floor == 41 and c.ok
    assert not S.check(["word " * 40], target_s=25, wpm=139).ok


def test_delivery_marks_do_not_count_as_words():
    """The target is spoken words. A script marked up heavily must not be
    credited for its brackets and asterisks."""
    plain = S.check(["one two three four five"], target_s=25, wpm=139)
    marked = S.check(["{excited} one two *three* four -- five"], target_s=25, wpm=139)
    assert marked.words == plain.words + 1  # the " - " pause renders as a token


def test_the_prompt_states_the_number_the_gate_enforces():
    """A gate that rejects without the prompt stating the target turns a
    solvable instruction into a retry loop."""
    clause = S.prompt_clause(25, 139)
    assert "58 words" in clause and "41 words" in clause and "139" in clause


def test_zero_rate_is_an_error_not_a_division():
    with pytest.raises(ValueError):
        S.target_words(25, 0)
