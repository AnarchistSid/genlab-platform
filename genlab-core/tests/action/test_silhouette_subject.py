"""Identity is decided on SILHOUETTES, by a vote, behind an area floor.

The garment-geometry rule flipped eight times across 0.7s of real footage
(235, 225, 15, 15, 15, 225, 225, 15, 235, 235, 235, 225, 225, 15) because a
downed fighter's trunks elongate with leg position while a standing fighter's
stay square. A rule that changes with a 33ms shift is not a rule.
"""

import numpy as np
from genlab_core.action.silhouette_subject import (
    MIN_BODY_FRAC,
    choose_subject_by_vote,
)

H, W = 400, 300
NAVY, CRIMSON = 225.0, 15.0


def _mask(x0, y0, w, h):
    m = np.zeros((H, W), np.float32)
    m[y0:y0 + h, x0:x0 + w] = 1.0
    return m


def _standing_over_downed():
    """Navy upright; crimson flat on the canvas."""
    return {NAVY: _mask(60, 40, 60, 240), CRIMSON: _mask(150, 330, 130, 40)}


def _both_standing():
    """Both upright; navy's centroid is higher."""
    return {NAVY: _mask(50, 30, 60, 230), CRIMSON: _mask(180, 120, 60, 230)}


def _scattered_tall_garment():
    """THE CASE THAT BROKE THE OLD RULE.

    Crimson residue spans tall but is tiny -- a glove high, trunks low. As a
    garment bbox it reads aspect 3.4 and wins 'only upright'. As a SILHOUETTE
    under the area floor it cannot compete at all.
    """
    m = np.zeros((H, W), np.float32)
    m[40:58, 200:218] = 1.0       # glove, high
    m[330:352, 150:186] = 1.0     # trunks, low
    return {NAVY: _mask(60, 40, 60, 240), CRIMSON: m}


def _one_under_floor():
    return {NAVY: _mask(60, 40, 60, 240), CRIMSON: _mask(10, 10, 8, 8)}


def _vote(scene_fn, n=10):
    frames = list(range(n))
    return choose_subject_by_vote(frames, lambda _f: scene_fn(), vote_frames=n)


def test_standing_over_downed_is_unanimous_for_the_stander():
    v = _vote(_standing_over_downed)
    assert v.hue_deg == NAVY and v.unanimous and v.votes == 10


def test_both_standing_position_decides():
    v = _vote(_both_standing)
    assert v.hue_deg == NAVY
    assert any("higher in frame" in fv.reason for fv in v.verdicts)


def test_scattered_tall_garment_cannot_win():
    """The exact defect: residue that is accidentally tall must not be 'upright'."""
    scene = _scattered_tall_garment()
    ys, xs = np.nonzero(scene[CRIMSON] > 0.5)
    bbox_aspect = (ys.max() - ys.min() + 1) / (xs.max() - xs.min() + 1)
    assert bbox_aspect > 3.0, "fixture no longer reproduces the tall-scatter case"
    assert scene[CRIMSON].mean() < MIN_BODY_FRAC, "fixture residue is too big"

    v = _vote(_scattered_tall_garment)
    assert v.hue_deg == NAVY, f"residue won with bbox aspect {bbox_aspect:.1f}"


def test_a_silhouette_under_the_floor_is_not_a_body():
    v = _vote(_one_under_floor)
    assert v.hue_deg == NAVY
    assert all("only body over the floor" in fv.reason for fv in v.verdicts)


def test_area_floor_is_applied_before_any_shape_test():
    """Order matters: a tall sliver must be excluded, not merely out-scored."""
    tall_sliver = {NAVY: _mask(60, 40, 60, 240), CRIMSON: _mask(250, 20, 4, 360)}
    v = choose_subject_by_vote([0], lambda _f: tall_sliver, vote_frames=1)
    assert v.hue_deg == NAVY
    assert "floor" in v.verdicts[0].reason


def test_a_flapping_decision_still_resolves_by_majority():
    """Frame-to-frame disagreement is expected; the VOTE is the answer."""
    scenes = [_standing_over_downed()] * 8 + [_both_standing()] * 2
    it = iter(scenes)
    v = choose_subject_by_vote(list(range(10)), lambda _f: next(it), vote_frames=10)
    assert v.hue_deg == NAVY and v.votes == 10


def test_a_genuine_split_is_reported_not_hidden():
    flip = [{NAVY: _mask(60, 40, 60, 240), CRIMSON: _mask(150, 330, 130, 40)}] * 5 + \
           [{CRIMSON: _mask(60, 40, 60, 240), NAVY: _mask(150, 330, 130, 40)}] * 5
    it = iter(flip)
    v = choose_subject_by_vote(list(range(10)), lambda _f: next(it), vote_frames=10)
    assert not v.unanimous and v.votes == 5
    assert "tie" in v.reason and v.margin == 0.5


def test_no_body_anywhere_returns_none_rather_than_guessing():
    tiny = {NAVY: _mask(0, 0, 5, 5), CRIMSON: _mask(20, 20, 5, 5)}
    v = choose_subject_by_vote([0, 1], lambda _f: tiny, vote_frames=2)
    assert v.hue_deg is None and "area floor" in v.reason


def test_per_frame_verdicts_are_logged_for_the_proof():
    v = _vote(_standing_over_downed)
    assert len(v.verdicts) == 10
    assert all(fv.reason and fv.areas for fv in v.verdicts)


def test_votes_for_the_same_body_under_different_hue_bins_are_merged():
    """The hue histogram bins at 10 degrees, so one fighter can be named 225 on
    nine frames and 235 on the tenth. Counting those apart understates the
    majority -- measured on the real window it read 8/10 instead of 9/10."""
    scenes = ([{225.0: _mask(60, 40, 60, 240), CRIMSON: _mask(150, 330, 130, 40)}] * 9 +
              [{235.0: _mask(60, 40, 60, 240), CRIMSON: _mask(150, 330, 130, 40)}])
    it = iter(scenes)
    v = choose_subject_by_vote(list(range(10)), lambda _f: next(it), vote_frames=10)
    assert v.votes == 10, f"same body split across bins: got {v.votes}/10"
    assert abs(v.hue_deg - 225.0) <= 45.0


def test_distinct_bodies_are_not_merged():
    """15 and 225 are 150 degrees apart and must stay separate candidates."""
    flip = [_standing_over_downed()] * 6 + [
        {CRIMSON: _mask(60, 40, 60, 240), NAVY: _mask(150, 330, 130, 40)}] * 4
    it = iter(flip)
    v = choose_subject_by_vote(list(range(10)), lambda _f: next(it), vote_frames=10)
    assert v.hue_deg == NAVY and v.votes == 6


def test_ground_finish_picks_the_man_on_top_whatever_his_posture():
    """A crouching finisher is not upright; the sprawled loser can be taller.

    Measured at t=678.7 on the real window: the navy finisher drops into
    ground-and-pound, his silhouette goes wide, and an upright-first rule voted
    10/10 UNANIMOUSLY for the man being hit.
    """
    crouched_on_top = _mask(80, 120, 150, 90)      # wide, aspect 0.6, HIGH
    sprawled_below = _mask(100, 250, 60, 130)      # taller, aspect 2.2, LOW
    scene = {NAVY: crouched_on_top, CRIMSON: sprawled_below}

    ys, xs = np.nonzero(scene[CRIMSON] > 0.5)
    assert (ys.max() - ys.min() + 1) / (xs.max() - xs.min() + 1) > 1.2, (
        "fixture: the loser must look 'upright' for this to bite")

    v = choose_subject_by_vote([0], lambda _f: scene, vote_frames=1)
    assert v.hue_deg == NAVY, "voted for the man being hit"
    assert "stacked" in v.verdicts[0].reason


def test_side_by_side_bodies_still_use_posture():
    """The stacked guard must not swallow the standing case it sits in front of."""
    v = _vote(_both_standing)
    assert v.hue_deg == NAVY
    assert not any("stacked" in fv.reason for fv in v.verdicts)
