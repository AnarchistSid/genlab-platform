"""Pins for the full-screen, one-character-per-shot layout.

The band existed because the action box had to CONTAIN every fighter, and two
fighters staged apart make a box wider than any 9:16 crop of a 16:9 source.
One subject per shot removes that constraint: the crop needs a centre, not a
containing box.
"""

from __future__ import annotations

from genlab_core.still import fight_layout as FL


def test_every_crop_is_full_screen():
    """There is no band: the crop is never wider than full bleed."""
    for cx in (0.0, 300.0, 960.0, 1900.0):
        w, x, mag = FL.crop_for_subject(cx, 500.0, 1920, 1080)
        assert w <= 1920 * 9 / 16 + 1
        assert mag >= 1.77, mag
        assert 0 <= x <= 1920 - w


def test_the_crop_is_never_narrower_than_the_subject():
    """The defect: a 542 px crop around a 500-900 px fighter framed cloth."""
    full_bleed = int(1080 * 9 // 16)
    for bw in (300.0, 500.0, 620.0, 900.0):
        w, _, _ = FL.crop_for_subject(960.0, bw, 1920, 1080)
        assert w >= min(bw, full_bleed) - 2, f"crop {w} narrower than subject {bw}"


def test_magnification_is_capped():
    _, _, mag = FL.crop_for_subject(960.0, 10.0, 1920, 1080)
    assert mag <= FL.MAX_MAG + 1e-6


def test_a_centre_at_the_edge_does_not_run_off_the_frame():
    w, x, _ = FL.crop_for_subject(5.0, 400.0, 1920, 1080)
    assert x == 0
    w, x, _ = FL.crop_for_subject(1915.0, 400.0, 1920, 1080)
    assert x + w == 1920


def test_crop_width_is_even():
    for bw in (100.0, 440.0, 700.0):
        w, _, _ = FL.crop_for_subject(960.0, bw, 1920, 1080)
        assert w % 2 == 0, "odd widths break yuv420p"


def test_shot_filter_has_no_band_and_carries_the_movement():
    s = FL.SubjectShot(0.0, 2.0, "tanjiro", 900.0, 542, 629, 1920, 1080, 1.99, True)
    f = FL.shot_filter(s, pulse=0.02, rgb_split_at=(1.0,))
    assert "overlay" not in f and "boxblur" not in f, "a band leaked back in"
    assert "sin(2*PI*2.0*t)" in f.replace("2*t", "2.0*t") or "sin(2*PI*2" in f
    assert "rgbashift" in f and "enable=" in f
    assert f.endswith("setsar=1")


def test_720p_source_cannot_meet_the_magnification_cap():
    """Full bleed on a 720p source is already 2.667x; the cap is unsatisfiable."""
    w, x, mag = FL.crop_for_subject(640.0, 350.0, 1280, 720)
    assert w <= 720 * 9 / 16 + 1, "the crop must never exceed full bleed"
    assert 0 <= x <= 1280 - w
    assert mag > FL.MAX_MAG, "a 720p source is smaller than the output; say so"


def test_shot_filter_does_not_double_the_zoom():
    """The defect: scaling to OUT_W*2 then centre-cropping OUT_W x OUT_H
    discarded half the width and half the height -- a silent 2x zoom that
    filled the frame with garment while crop_w read a correct 606 px."""
    s = FL.SubjectShot(0.0, 2.0, "tanjiro", 900.0, 606, 597, 1920, 1080, 1.78, True)
    f = FL.shot_filter(s, shake_px=3.0)
    import re
    m = re.search(r"scale=(\d+):-2", f)
    assert m, f
    assert int(m.group(1)) < FL.OUT_W * 1.2, (
        f"scaled to {m.group(1)} before a {FL.OUT_W}-wide crop: a hidden zoom")


def test_the_scaled_frame_is_tall_enough_to_crop():
    """A padded crop scaled by the padded width shrinks below OUT_H."""
    import re
    for cw in (540, 574, 606):
        s = FL.SubjectShot(0.0, 2.0, "x", 900.0, cw, 100, 1920, 1080, 1.8, True)
        f = FL.shot_filter(s, shake_px=3.0)
        crop_m = re.search(r"^crop=(\d+):(\d+)", f)
        scale_m = re.search(r"scale=(\d+):-2", f)
        padded_w, src_h = int(crop_m.group(1)), int(crop_m.group(2))
        scaled_h = src_h * int(scale_m.group(1)) / padded_w
        assert scaled_h >= FL.OUT_H, f"crop_w={cw}: scaled height {scaled_h:.0f} < {FL.OUT_H}"
