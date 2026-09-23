"""Pins for the script-length target (ANIME-13 §7)."""

from __future__ import annotations

import pytest
from genlab_core.still import script_length as S


def test_the_target_comes_from_the_validator_not_local_arithmetic():
    """target_s * wpm / 60 ignores the 2 s music-bed tail and the fit margin.
    At 38 s / 139 wpm it said 88 words while the validator's cap was 79, so
    this module reported a shortfall the validator called too LONG."""
    from genlab_core.writing.narration_validator import word_cap, word_floor

    for target, wpm in ((25, 177), (25, 139), (38, 139), (45, 177)):
        assert S.target_words(target, wpm) == word_cap(target, wpm, 2.0, 0.05)
        assert S.check([""], target_s=target, wpm=wpm).floor == word_floor(
            target, wpm, 2.0, 0.05, S.MIN_FRACTION
        )


def test_the_same_script_targets_differently_per_engine():
    """A 27% rate difference between engines is a different reel from the same
    words. A target computed against the wrong engine is a target for a
    different reel."""
    assert S.target_words(25, 177) != S.target_words(25, 139)


def test_a_script_at_the_target_is_accepted_by_the_validator_too():
    """The round trip the drift broke: what this module calls "on target" must
    be what the validator calls valid."""
    from genlab_core.writing.narration_validator import validate_narration_script

    for target, wpm in ((26, 139), (38, 139), (38, 177), (45, 177)):
        n = S.target_words(target, wpm)
        assert validate_narration_script(
            " ".join(["word"] * n), target, wpm, 2.0, 0.05, S.MIN_FRACTION
        ) == (True, ""), f"{n} words at {target}s/{wpm}wpm"


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
    """Numbers moved when target_words started deducting the 2 s tail and the
    fit margin: at 25 s / 139 wpm the cap is 50 words, not 58."""
    floor = S.check([""], target_s=25, wpm=139).floor
    assert S.target_words(25, 139) == 50 and floor == 36
    assert S.check(["word " * floor], target_s=25, wpm=139).ok
    assert not S.check(["word " * (floor - 1)], target_s=25, wpm=139).ok


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
    assert f"{S.target_words(25, 139)} words" in clause
    assert f"{S.check([''], target_s=25, wpm=139).floor} words" in clause
    assert "139" in clause


def test_zero_rate_is_an_error_not_a_division():
    with pytest.raises(ValueError):
        S.target_words(25, 0)
