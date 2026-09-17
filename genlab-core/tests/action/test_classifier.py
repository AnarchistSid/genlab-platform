"""TREAT-01 classifier: the ordering is the design, not an implementation detail."""

import pytest
from genlab_core.action.classifier import Signals, classify


def s(speech=0.0, motion=0.0, face=0.0):
    return Signals(speech_ratio=speech, motion_energy=motion, face_persistence=face)


def test_a_press_conference_is_TALK():
    v = classify(s(speech=0.72, motion=3.5, face=0.81))
    assert v.treatment == "TALK" and "held face" in v.reason


def test_a_knockout_is_ACTION():
    v = classify(s(speech=0.30, motion=12.1, face=0.35))
    assert v.treatment == "ACTION"


def test_no_usable_footage_is_STILL():
    v = classify(s(speech=0.0, motion=0.4, face=0.0))
    assert v.treatment == "STILL" and "nothing to cut" in v.reason


def test_a_press_conference_with_lively_hands_is_not_ACTION():
    """TALK is tested before ACTION for exactly this: a lectern has real motion."""
    v = classify(s(speech=0.66, motion=7.8, face=0.78))
    assert v.treatment == "TALK", f"a press conference routed to {v.treatment}"


def test_a_held_face_over_a_fight_does_not_become_ACTION():
    """A face held this steadily is an interview, whatever else moves."""
    v = classify(s(speech=0.50, motion=14.0, face=0.92))
    assert v.treatment == "TALK"


def test_a_static_talking_head_is_STILL_not_TALK():
    """STILL is decided first: no amount of speech makes uncuttable footage cuttable."""
    v = classify(s(speech=0.90, motion=1.1, face=0.95))
    assert v.treatment == "STILL"


def test_a_crowd_shot_with_many_faces_but_no_speech_is_ACTION():
    """face_persistence weights STEADINESS, so a crowd scores low even with faces."""
    v = classify(s(speech=0.10, motion=9.0, face=0.22))
    assert v.treatment == "ACTION"


def test_the_ambiguous_middle_lands_on_STILL_never_ACTION():
    """ACTION on a lectern invents a fight; STILL works on any footage."""
    v = classify(s(speech=0.30, motion=4.0, face=0.40))
    assert v.treatment == "STILL" and "ambiguous" in v.reason


def test_confidence_rises_with_the_margin():
    near = classify(s(speech=0.46, motion=3.0, face=0.56))
    clear = classify(s(speech=0.95, motion=3.0, face=0.95))
    assert clear.confidence > near.confidence


def test_every_verdict_carries_its_signals_and_a_reason():
    v = classify(s(speech=0.72, motion=3.5, face=0.81))
    assert v.signals.speech_ratio == 0.72 and v.reason


@pytest.mark.parametrize("sig,expected", [
    (s(speech=0.80, motion=3.0, face=0.85), "TALK"),
    (s(speech=0.05, motion=18.0, face=0.10), "ACTION"),
    (s(speech=0.00, motion=0.1, face=0.00), "STILL"),
])
def test_the_three_clear_cases(sig, expected):
    assert classify(sig).treatment == expected


@pytest.mark.xfail(reason="TREAT-01's hand-labelled 20-clip corpus (15 sports + 5 "
                          "gaming) is not on disk; the >=18/20 gate cannot run "
                          "until it exists. Thresholds here are provisional.",
                   strict=True)
def test_labelled_corpus_gate():
    from pathlib import Path
    assert Path("genlab-core/tests/fixtures/treat01_corpus.json").exists()
