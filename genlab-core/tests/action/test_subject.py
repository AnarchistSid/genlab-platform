"""Subject identity is a decision made ONCE per window, not per annotation.

Measured defect (UFC knockdown, 2026-09-17): the aura walked off the red-trunked
fighter. 58 of 96 frames carried >15% of the opponent's garment inside the matte
and 12 frames were majority the wrong man.
"""

import numpy as np
from genlab_core.action.subject import (
    UPRIGHT_ASPECT,
    choose_subject,
    garment_groups,
    subject_clicks,
)


def _canvas(h=400, w=600):
    return np.full((h, w, 3), 30, np.uint8), np.zeros((h, w), np.float32)


def _body(rgb, fg, x0, y0, w, h, rgb_colour):
    rgb[y0 : y0 + h, x0 : x0 + w] = rgb_colour
    fg[y0 : y0 + h, x0 : x0 + w] = 1.0


RED, BLUE = (200, 30, 40), (30, 60, 200)


def test_two_merged_bodies_separate_by_hue_not_by_component():
    """Connected components cannot split a clinch; hue can."""
    rgb, fg = _canvas()
    _body(rgb, fg, 100, 80, 90, 260, RED)
    _body(rgb, fg, 190, 80, 90, 260, BLUE)  # touching -> ONE component
    groups = garment_groups(rgb, fg)
    assert len(groups) == 2, f"expected 2 bodies, got {[g['hue'] for g in groups]}"


def test_upright_body_is_chosen_as_the_finisher():
    rgb, fg = _canvas()
    _body(rgb, fg, 100, 60, 80, 280, RED)  # upright
    _body(rgb, fg, 260, 300, 260, 70, BLUE)  # floored
    s = choose_subject(rgb, fg, last_frame_index=95)
    assert s is not None
    assert abs(s.hue_deg - 357) < 25 or s.hue_deg < 25, f"picked hue {s.hue_deg}"
    assert s.aspect > UPRIGHT_ASPECT
    assert "upright" in s.reason


def test_the_reason_is_logged_not_just_the_answer():
    rgb, fg = _canvas()
    _body(rgb, fg, 100, 60, 80, 280, RED)
    _body(rgb, fg, 260, 300, 260, 70, BLUE)
    s = choose_subject(rgb, fg, last_frame_index=95)
    assert s.reason and s.chosen_on_frame == 95


def test_bare_torso_window_gets_no_subject_rather_than_a_guess():
    rgb, fg = _canvas()
    _body(rgb, fg, 100, 60, 80, 280, (190, 150, 130))  # skin, no garment
    assert choose_subject(rgb, fg) is None


def test_annotations_do_not_re_derive_the_hue():
    """The whole defect: a frame-local answer to a window-level question."""
    rgb, fg = _canvas()
    _body(rgb, fg, 100, 60, 80, 280, RED)
    _body(rgb, fg, 260, 300, 260, 70, BLUE)
    s = choose_subject(rgb, fg, last_frame_index=95)

    # A later frame where the OPPONENT is much larger. A per-frame derivation
    # would follow the biggest garment; a bound one must not.
    rgb2, fg2 = _canvas()
    _body(rgb2, fg2, 60, 40, 340, 320, BLUE)
    _body(rgb2, fg2, 420, 120, 60, 200, RED)
    pos, neg, d = subject_clicks(rgb2, fg2, s)
    assert pos is not None and pos[0] > 380, "click left the subject for the bigger body"
    assert neg is not None and neg[0] < 380, "negative must land on the opponent"


def test_positive_is_the_largest_component_not_the_mean_of_all_matches():
    """Centroid-of-all-matches lands between disjoint parts, on neither body.

    Cost of getting this wrong on the real clip: the matte collapsed to 0.5% of
    frame on frames 0-8 and what survived sat on the opponent.
    """
    rgb, fg = _canvas()
    _body(rgb, fg, 40, 100, 70, 220, RED)  # big left
    _body(rgb, fg, 500, 100, 40, 80, RED)  # small right, same hue
    s = choose_subject(rgb, fg, last_frame_index=0)
    pos, _, _ = subject_clicks(rgb, fg, s)
    assert pos[0] < 200, f"centroid fell between the parts at x={pos[0]}"


def test_subject_spec_is_a_colour_seed_spec():
    rgb, fg = _canvas()
    _body(rgb, fg, 100, 60, 80, 280, RED)
    s = choose_subject(rgb, fg)
    spec = s.spec()
    assert set(spec) == {"hue_deg", "hue_tol", "sat_min", "val_min"}
    assert spec["hue_deg"] == s.hue_deg
