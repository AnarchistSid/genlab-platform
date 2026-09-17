"""The photometric bar test must be derived per source, not assumed.

UFC cage-side footage has a genuinely near-black, flat, nearly static shadow
along the top of the crop (mean 1.40-1.67, std 0.45-0.66, diff 0.42-0.83). The
absolute test tuned on an arena source flagged 12 of 95 frames on a crop that is
full-bleed BY CONSTRUCTION.
"""

import numpy as np
from genlab_core.action.full_bleed import (
    derive_std_threshold,
    photometric_full_bleed,
    structural_full_bleed,
)
from genlab_core.action.source_score import full_bleed_floor

H, W = 240, 135


def _clip(n=40, *, shadow=True, bar=False, seed=5):
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        f = rng.uniform(40, 200, (H, W, 3)).astype(np.float32)
        if shadow:  # dark, flat, but NOT uniform
            f[:8] = rng.uniform(1.0, 2.5, (8, W, 3))
        if bar:  # a real letterbox bar
            f[:20] = 0.0
        out.append(f)
    return out


def test_genuine_dark_shadow_is_not_a_bar():
    r = photometric_full_bleed(_clip(shadow=True))
    assert r["full_bleed_pct"] == 100.0, f"false positives: {r}"


def test_a_real_black_bar_is_still_caught():
    r = photometric_full_bleed(_clip(shadow=False, bar=True))
    assert r["flagged"] == r["frames"], f"missed the bar: {r}"


def test_threshold_is_derived_from_the_source_not_fixed():
    quiet = derive_std_threshold(_clip(shadow=True))
    noisy = derive_std_threshold(_clip(shadow=False))
    assert quiet != noisy, "threshold ignored the source"
    assert quiet > 0


def test_an_absolute_threshold_would_have_flagged_the_shadow():
    """Pins that the fixture reproduces the real defect before asserting the fix."""
    r_abs = photometric_full_bleed(_clip(shadow=True), std_threshold=2.0)
    assert r_abs["flagged"] > 0, "fixture no longer reproduces the false positive"
    assert photometric_full_bleed(_clip(shadow=True))["flagged"] == 0


# ── structural: the authoritative check ────────────────────────────────────


def test_crop_at_the_floor_inside_the_source_cannot_letterbox():
    floor = full_bleed_floor(943)
    ok, why = structural_full_bleed(floor, floor, (0, 0, 530, 943), (1920, 943))
    assert ok, why


def test_below_the_floor_is_structurally_rejected():
    ok, why = structural_full_bleed(1.3, full_bleed_floor(1080), (0, 0, 830, 1080), (1920, 1080))
    assert not ok and "floor" in why


def test_a_crop_leaving_the_source_rect_is_rejected():
    floor = full_bleed_floor(943)
    ok, why = structural_full_bleed(floor, floor, (1600, 0, 530, 943), (1920, 943))
    assert not ok and "source rect" in why


def test_a_bar_in_every_frame_cannot_define_itself_as_normal():
    """Without a floor the derivation takes the bar's std~0 as the baseline."""
    from genlab_core.action.full_bleed import MIN_BAR_STD

    thr = derive_std_threshold(_clip(shadow=False, bar=True))
    assert thr >= MIN_BAR_STD
    assert photometric_full_bleed(_clip(shadow=False, bar=True))["flagged"] > 0
