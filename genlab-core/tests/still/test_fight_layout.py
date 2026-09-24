"""Pins for the full-screen, one-character-per-shot layout.

The band existed because the action box had to CONTAIN every fighter, and two
fighters staged apart make a box wider than any 9:16 crop of a 16:9 source.
One subject per shot removes that constraint: the crop needs a centre, not a
containing box.
"""

from __future__ import annotations

import pytest
from genlab_core.still import fight_layout as FL


def test_every_crop_is_full_screen():
    """There is no band: the crop is never wider than full bleed."""
    for cx in (0.0, 300.0, 960.0, 1900.0):
        w, x, mag = FL.crop_for_centre(cx, 1920, 1080)
        assert w <= 1920 * 9 / 16 + 1
        assert mag >= 1080 * 0.0 + 1.77, mag
        assert 0 <= x <= 1920 - w


def test_magnification_is_capped():
    _, _, mag = FL.crop_for_centre(960.0, 1920, 1080, mag=4.0)
    assert mag <= FL.MAX_MAG + 1e-6


def test_a_centre_at_the_edge_does_not_run_off_the_frame():
    w, x, _ = FL.crop_for_centre(5.0, 1920, 1080, mag=1.2)
    assert x == 0
    w, x, _ = FL.crop_for_centre(1915.0, 1920, 1080, mag=1.2)
    assert x + w == 1920


def test_crop_width_is_even():
    for cx in (100.0, 640.0, 1500.0):
        w, _, _ = FL.crop_for_centre(cx, 1920, 1080, mag=1.13)
        assert w % 2 == 0, "odd widths break yuv420p"


def test_shot_filter_has_no_band_and_carries_the_movement():
    s = FL.SubjectShot(0.0, 2.0, "tanjiro", 900.0, 542, 629, 1920, 1080, 1.99, True)
    f = FL.shot_filter(s, pulse=0.02, rgb_split_at=(1.0,))
    assert "overlay" not in f and "boxblur" not in f, "a band leaked back in"
    assert "sin(2*PI*2.0*t)" in f.replace("2*t", "2.0*t") or "sin(2*PI*2" in f
    assert "rgbashift" in f
    assert f.endswith("setsar=1")


def test_720p_source_still_gets_a_full_screen_crop():
    w, x, mag = FL.crop_for_centre(640.0, 1280, 720)
    assert w <= 720 * 9 / 16 + 1 and 0 <= x <= 1280 - w
    assert mag == pytest.approx(1920 / 720, rel=0.02)
