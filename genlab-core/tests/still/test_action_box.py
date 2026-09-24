"""Pins for the action-box layout.

Three of these encode defects that reached a rendered reel:

* the crop width was computed against a hardcoded 1920x1080 while one pilot
  source is 1280x720, which fails as "could not open encoder";
* the choice was binary between a 608 px full-bleed crop and the whole frame,
  which put most shots in a strip filling 32% of the reel;
* a colour seed locates a GARMENT, so a close-up of a haori was magnified
  into fabric.
"""

from __future__ import annotations

import pytest
from genlab_core.still import action_box as AB


def box(x0, y0, x1, y1):
    return AB.Box(x0, y0, x1, y1, "test")


def test_full_bleed_width_follows_the_source_height():
    assert AB.full_bleed_w(1080) == 607
    assert AB.full_bleed_w(720) == 405


def test_crop_never_exceeds_a_720p_source():
    """The defect: a 1510 px crop requested from a 1280 px frame."""
    lay = AB.layout_for(box(20, 40, 1260, 700), (1280, 720))
    assert lay.crop_x >= 0
    assert lay.crop_x + lay.crop_w <= 1280


def test_a_narrow_box_gets_the_full_bleed_crop():
    lay = AB.layout_for(box(700, 300, 1100, 700), (1920, 1080))
    assert lay.kind == "box"
    assert lay.crop_w == 607
    assert lay.band_height == AB.OUT_H          # fills the reel
    assert lay.fighters_inside


def test_a_medium_box_crops_to_itself_rather_than_the_whole_frame():
    """The defect: every non-full-bleed shot fell back to the 1920 px band."""
    lay = AB.layout_for(box(500, 200, 1400, 900), (1920, 1080))
    assert lay.crop_w < 1920, "a 900 px box should not take the whole frame"
    assert lay.band_height > AB.OUT_H * 0.4
    assert lay.fighters_inside


def test_uncovered_fighters_force_the_band():
    """Cropping is only safe when the evidence accounts for everyone."""
    tight = box(800, 400, 1000, 700)
    covered = AB.layout_for(tight, (1920, 1080), covers_everyone=True)
    uncovered = AB.layout_for(tight, (1920, 1080), covers_everyone=False)
    assert covered.kind == "box"
    assert uncovered.kind == "band" and uncovered.crop_w == 1920


def test_a_box_filling_half_the_height_is_not_magnified():
    """A garment in a close-up: magnifying it crops into fabric."""
    shallow = AB.layout_for(box(700, 350, 1200, 720), (1920, 1080))   # h = 34%
    tall = AB.layout_for(box(700, 150, 1200, 900), (1920, 1080))      # h = 69%
    assert shallow.crop_w < tall.crop_w
    assert tall.crop_w >= int(AB.TIGHT_MIN_CROP_FRAC * 1920)


def test_union_and_pad():
    u = box(100, 100, 300, 300).union(box(500, 200, 700, 400))
    assert (u.x0, u.x1) == (100, 700)
    p = u.padded(0.10, 1920, 1080)
    assert p.x0 == pytest.approx(40.0) and p.x1 == pytest.approx(760.0)
    assert p.x0 >= 0
