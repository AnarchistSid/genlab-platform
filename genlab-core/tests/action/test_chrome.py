"""Chrome detection must survive a RUNNING CLOCK.

Motion energy is the wrong instrument for a broadcast bug and fails silently:
on the UFC knockdown the bottom band read 50% of peak YDIF and the detector
said "no chrome", while the frame plainly carried
``HUNT | DWCS 4:35 R1 | PEREA`` -- the clock ticks 4:35, 4:34, 4:32, so the
band is full of motion.
"""

import numpy as np
from genlab_core.action.chrome import detect_static_band, frozen_row_fraction

H, W = 400, 600
BAND_Y0, BAND_Y1 = 350, 380


def _frames(n=12, *, ticking=True, band=True):
    out = []
    rng = np.random.default_rng(7)
    for i in range(n):
        f = rng.integers(0, 255, (H, W)).astype(np.float32)  # live picture
        if band:
            f[BAND_Y0:BAND_Y1, 200:400] = 60.0  # static box
            if ticking:
                # the digits: a small region that changes every frame
                f[BAND_Y0 + 10 : BAND_Y0 + 20, 290:310] = float(20 + (i * 17) % 200)
        out.append(f)
    return out


def test_a_ticking_clock_does_not_hide_the_band():
    r = detect_static_band(_frames(ticking=True))
    assert r["found"], "the band was missed because its clock moves"
    assert r["y0"] <= BAND_Y0 + 4 and r["y1"] >= BAND_Y1 - 6


def test_a_fully_static_band_is_found_too():
    r = detect_static_band(_frames(ticking=False))
    assert r["found"]


def test_clean_footage_yields_no_band():
    r = detect_static_band(_frames(band=False))
    assert not r["found"] and r["video_rect"][3] == H


def test_video_rect_keeps_everything_above_the_band():
    r = detect_static_band(_frames())
    assert r["video_rect"][3] == r["y0"] < BAND_Y0 + 5


def test_frozen_fraction_is_measured_against_the_frames_own_baseline():
    """The bug is a centred box ~37% of frame width, so even a perfectly static
    one cannot push a whole row past ~0.4. An absolute 0.5 threshold misses it."""
    frac = frozen_row_fraction(_frames())
    assert frac[BAND_Y0 + 5] > frac[10] * 5
    assert frac[BAND_Y0 + 5] < 0.5, "fixture no longer reproduces the partial-row case"


def test_a_static_band_in_the_middle_is_not_chrome():
    """A frozen band across the middle is a composition choice, not a scoreboard."""
    rng = np.random.default_rng(3)
    fs = []
    for _ in range(12):
        f = rng.integers(0, 255, (H, W)).astype(np.float32)
        f[150:180, 200:400] = 60.0
        fs.append(f)
    assert not detect_static_band(fs)["found"]
