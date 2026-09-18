"""The standing matte gate: area band, no empty frames, and a judged A/B.

Replaces `IoU >= 0.98 on >= 90/96` against the archived UFC-05 mattes. That gate
failed four rounds while the mattes got steadily better, because it measured the
wrong artifact: the archive's mattes are the output of scripts that no longer
run, and the approved thing was a REEL. See matte_gate's docstring.
"""

from __future__ import annotations

import numpy as np
from genlab_core.action.matte_gate import AREA_TOLERANCE, check

#: UFC-05's archived mattes: (min, mean, max) subject area as a fraction of frame.
UFC05_BAND = (0.2265, 0.3092, 0.4272)


def masks_with_area(fracs, h=100, w=100):
    """Masks whose subject area is exactly each given fraction."""
    out = {}
    for i, fr in enumerate(fracs):
        m = np.zeros((h, w), np.float32)
        rows = int(round(fr * h * w / w))
        m[:rows, :] = 1.0
        out[i] = m
    return out


def test_the_ported_backends_measured_band_passes():
    """The numbers that were judged indistinguishable: 0.2254-0.4312 mean 0.3084
    against the archive's 0.2265-0.4272 mean 0.3092."""
    masks = masks_with_area([0.2254] + [0.3084] * 94 + [0.4312])
    r = check(masks, UFC05_BAND, expected_frames=96, judged_ab=True)
    assert r.passed, r


def test_a_band_outside_tolerance_fails():
    masks = masks_with_area([0.45] * 96)
    r = check(masks, UFC05_BAND, expected_frames=96, judged_ab=True)
    assert not r.passed and any("area" in x for x in r.reasons)


def test_a_single_empty_frame_fails_absolutely():
    """Not a small error: a frame with no subject for the effects to attach to,
    which reads as a glitch."""
    masks = masks_with_area([0.3084] * 95)
    masks[95] = np.zeros((100, 100), np.float32)
    r = check(masks, UFC05_BAND, expected_frames=96, judged_ab=True)
    assert not r.passed and any("empty" in x for x in r.reasons)


def test_an_unjudged_backend_does_not_pass_on_numbers_alone():
    """The area band is necessary, not sufficient — a backend could hold it while
    tracking the wrong fighter. Somebody looks, once, per backend."""
    masks = masks_with_area([0.3084] * 96)
    r = check(masks, UFC05_BAND, expected_frames=96, judged_ab=None)
    assert not r.passed and any("judged" in x for x in r.reasons)


def test_a_rejected_ab_fails_however_good_the_numbers():
    masks = masks_with_area([0.3084] * 96)
    r = check(masks, UFC05_BAND, expected_frames=96, judged_ab=False)
    assert not r.passed and any("rejected" in x for x in r.reasons)


def test_a_short_matte_set_fails():
    masks = masks_with_area([0.3084] * 48)
    r = check(masks, UFC05_BAND, expected_frames=96, judged_ab=True)
    assert not r.passed and any("expected 96" in x for x in r.reasons)


def test_the_tolerance_is_the_stated_two_percent():
    assert AREA_TOLERANCE == 0.02
    just_in = 0.3092 * (1 + 0.019)
    just_out = 0.3092 * (1 + 0.021)
    assert check(
        masks_with_area([just_in] * 96),
        (just_in, just_in, just_in),
        expected_frames=96,
        judged_ab=True,
    ).passed
    r = check(masks_with_area([just_out] * 96), UFC05_BAND, expected_frames=96, judged_ab=True)
    assert not r.passed


def test_no_mattes_is_a_failure_not_a_vacuous_pass():
    assert not check({}, UFC05_BAND, expected_frames=96, judged_ab=True).passed
