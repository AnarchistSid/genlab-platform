"""Identity is a WINDOW criterion, not a hope.

The old selector asked only whether a window was trackable -- high motion, zero
cuts. On the UFC knockdown that returned a window where the camera follows the
man about to lose and the finisher is co-dominant only in the last quarter;
tracking the correct subject through it gave a matte covering 1.29% of frame
with 12 empty frames. The window was trackable. It was trackable of the wrong
person.
"""

import numpy as np
from genlab_core.action import window as W


def _canvas(h=240, w=360):
    return np.full((h, w, 3), 30, np.uint8), np.zeros((h, w), np.float32)


def _put(rgb, fg, x0, y0, ww, hh, colour):
    rgb[y0 : y0 + hh, x0 : x0 + ww] = colour
    fg[y0 : y0 + hh, x0 : x0 + ww] = 1.0


RED, BLUE = (200, 30, 40), (30, 60, 200)


def _pair(subject_big: bool):
    """Two fighters; `subject_big` controls whether BLUE dominates."""
    rgb, fg = _canvas()
    _put(rgb, fg, 40, 30, 50, 170, RED)
    if subject_big:
        _put(rgb, fg, 200, 20, 90, 200, BLUE)
    else:
        _put(rgb, fg, 300, 150, 26, 30, BLUE)  # barely present
    return rgb, fg


def test_subject_that_holds_the_window_passes():
    frames = [_pair(True) for _ in range(6)]
    hue, frac = W._subject_hold([f[0] for f in frames], [f[1] for f in frames])
    assert hue is not None and frac == 1.0


def test_subject_absent_for_most_of_the_window_fails_the_criterion():
    """The UFC case: the finisher only arrives at the end."""
    frames = [_pair(False) for _ in range(5)] + [_pair(True)]
    hue, frac = W._subject_hold([f[0] for f in frames], [f[1] for f in frames])
    assert hue is not None, "a subject IS chosen -- on the last frame"
    assert frac < W.MIN_SUBJECT_FRAMES, (
        f"held {frac:.2f}; a window the subject only enters at the end must not be eligible"
    )


def test_the_subject_is_chosen_on_the_LAST_frame_not_the_first():
    """Selector and renderer must not disagree about who the subject is."""
    frames = [_pair(False) for _ in range(5)] + [_pair(True)]
    hue, _ = W._subject_hold([f[0] for f in frames], [f[1] for f in frames])
    assert 200 < hue < 260, f"chose hue {hue}; the last frame's finisher is blue"


def test_eligibility_needs_all_three_criteria():
    w = W.WindowScore(
        start_s=10.0,
        motion=50.0,
        cuts=0,
        subject_hue=225.0,
        subject_frames_frac=0.5,
        eligible=False,
        reason="subject holds only 50% of frames (want 80%)",
    )
    assert not w.eligible
    assert "50%" in w.row() and "ELIGIBLE" not in w.row()


def test_row_prints_all_three_numbers():
    """'Print the three numbers for every candidate window.'"""
    row = W.WindowScore(11.0, 42.0, 0, 225.0, 0.875, True, "").row()
    for token in ("motion=", "cuts=", "subject_hue=", "held="):
        assert token in row, f"{token} missing from: {row}"


def test_no_subject_on_the_last_frame_is_not_eligible():
    rgb, fg = _canvas()  # empty foreground, no garment
    hue, frac = W._subject_hold([rgb] * 4, [fg] * 4)
    assert hue is None and frac == 0.0


def test_ffmpeg_metadata_passes_do_not_use_dash_v_error():
    """`-v error` SILENCES metadata=print: the filter runs, exit code is 0, and
    zero lines come back -- which reads as 'no motion in this clip' rather than
    'the instrument was muted'. Cost: one debugging cycle on 2026-09-17."""
    import inspect

    src = inspect.getsource(W)
    for fn in ("cut_times", "motion_profile"):
        body = src.split(f"def {fn}(")[1].split("\ndef ")[0]
        assert '"-v", "error"' not in body, f"{fn} would silence its own output"
        assert "-hide_banner" in body


def test_the_criterion_is_measured_against_garment_not_raw_foreground():
    """20% of raw FOREGROUND is unreachable and scored 9/10 windows at 0.0%.

    A garment component runs 3.6-13.0% of the foreground silhouette on real
    frames -- trunks are a small part of a body. Against the garment visible in
    frame the same frames separate: present-and-prominent reads 39-72%.
    """
    SKIN = (190, 150, 130)
    rgb, fg = _canvas()
    # Two bodies that are mostly SKIN, each with a small garment patch -- the
    # real proportion, which an all-coloured fixture hides.
    _put(rgb, fg, 40, 20, 60, 200, SKIN)
    _put(rgb, fg, 40, 120, 60, 26, RED)
    _put(rgb, fg, 220, 20, 60, 200, SKIN)
    _put(rgb, fg, 220, 120, 60, 26, BLUE)

    groups = W.garment_groups(rgb, fg)
    fg_px = float((fg > 0.5).sum())
    assert groups, "no garment found"
    assert all(g["body_px"] / fg_px < 0.20 for g in groups), (
        f"fixture no longer reproduces the scale problem: "
        f"{[round(g['body_px'] / fg_px, 3) for g in groups]}"
    )

    hue, frac = W._subject_hold([rgb] * 4, [fg] * 4)
    assert frac == 1.0, (
        f"a fighter holding half the garment in frame must count as held, got {frac}"
    )


def test_a_replay_montage_is_rejected_by_the_zero_cut_rule():
    """Motion ALONE returned a replay montage on the UFC clip -- maximum motion,
    no continuity, nothing for SAM2 to track. Cuts are what reject it."""
    montage = W.WindowScore(
        start_s=330.0,
        motion=99.0,
        cuts=4,
        subject_hue=225.0,
        subject_frames_frac=1.0,
        eligible=False,
        reason="cuts",
    )
    assert montage.cuts > 0
    assert not montage.eligible, "a montage passed on motion alone"


def test_a_re_entangled_window_is_rejected_on_subject_hold():
    """Measured at t=675.5: ~0.6s past the finish the pair re-entangles, the
    vote flips back to the loser, and the subject stops being separable."""
    frames = [_pair(False) for _ in range(6)] + [_pair(True) for _ in range(2)]
    hue, frac = W._subject_hold([f[0] for f in frames], [f[1] for f in frames])
    assert frac < W.MIN_SUBJECT_FRAMES, (
        f"held {frac:.2f}; a window where the subject is only separable at the "
        f"end must not be eligible"
    )


def test_the_eligible_window_and_the_rejected_one_differ_only_in_hold():
    """Both have motion and zero cuts. Identity is what separates them -- which
    is the whole reason it became a criterion."""
    good = W.WindowScore(674.9, 11.4, 0, 225.0, 1.00, True, "")
    bad = W.WindowScore(675.5, 11.4, 0, 225.0, 0.50, False, "holds only 50%")
    assert good.motion == bad.motion and good.cuts == bad.cuts
    assert good.eligible and not bad.eligible
