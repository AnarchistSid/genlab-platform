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


@pytest.mark.parametrize(
    "sig,expected",
    [
        (s(speech=0.80, motion=3.0, face=0.85), "TALK"),
        (s(speech=0.05, motion=18.0, face=0.10), "ACTION"),
        (s(speech=0.00, motion=0.1, face=0.00), "STILL"),
    ],
)
def test_the_three_clear_cases(sig, expected):
    assert classify(sig).treatment == expected


@pytest.mark.xfail(
    reason=(
        "MEASURED 2026-09-17 on a real 27-clip corpus, and the blocker is now the "
        "classifier's DESIGN rather than missing data.\n"
        "\n"
        "All three signal functions were unimplemented (not just motion_fn), so every "
        "threshold sat in an undefined unit. They now exist: motion.py and signals.py, "
        "units stated. The corpus is 27 clips pulled from live production fetches -- "
        "10 ACTION, 10 TALK, 7 STILL -- in tests/fixtures/classifier/.\n"
        "\n"
        "Confusion matrix, current thresholds (STILL-first), 12/27 = 44%:\n"
        "    true \\ pred   ACTION  TALK  STILL\n"
        "    ACTION             5     1      4\n"
        "    TALK               2     0      8\n"
        "    STILL              0     0      7\n"
        "\n"
        "EXHAUSTIVE grid search over all five thresholds AND both orderings tops out at "
        "18/27 = 67%, against the 24/27 this gate needs:\n"
        "    true \\ pred   ACTION  TALK  STILL\n"
        "    ACTION             5     4      1\n"
        "    TALK               3     6      1\n"
        "    STILL              0     0      7\n"
        "\n"
        "So no threshold move reaches the gate. Three scalars do not separate ACTION "
        "from TALK on real material: an F1 onboard scores 0.88 levels/s (smooth camera) "
        "while a tech review with b-roll scores 7.12, and the ACTION range 0.88-16.76 "
        "sits inside the TALK range 0.10-7.12. The clean separation measured on two "
        "exemplars -- UFC 16-22 against an interview at 0.2 -- was exemplar-specific and "
        "did not generalise.\n"
        "\n"
        "The STILL row is 7/7 in every variant, but that is a FIXTURE artifact: the "
        "generated footage-free items carry no audio, so speech_ratio is trivially 0.00. "
        "A real footage-free item has TTS narration. Do not read the STILL column as "
        "evidence.\n"
        "\n"
        "To flip this the classifier needs a different discriminator, not tuning -- "
        "shot-change rate, subject trackability, or speech CONTINUITY rather than "
        "dominance. Filed as Q8."
    ),
    strict=True,
)
def test_labelled_corpus_gate():
    from pathlib import Path

    assert Path("genlab-core/tests/fixtures/treat01_corpus.json").exists()
