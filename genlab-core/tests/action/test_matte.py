"""Matte construction. Fixtures carried over from the WWE and UFC runs.

Every constant here was measured, not chosen: mask-seeding vs point-seeding gave
7.87% vs 1.42% mean area and 0 vs 9 empty frames; a matte over ~60% of the
foreground is a colour field, not a body.
"""

import numpy as np
from genlab_core.action.matte import (
    GARMENT_NOT_PERSON_FRAC,
    MATTE_AREA_BAND,
    annotation_frames,
    build_mattes,
    is_garment_not_person,
    summarise,
    warp_to_crop,
)

H, W = 200, 300
NAVY, CRIMSON = 225.0, 15.0


def box(x0, y0, w, h, shape=(H, W)):
    m = np.zeros(shape, np.float32)
    m[y0:y0 + h, x0:x0 + w] = 1.0
    return m


# ── annotation frames: the cut rule is the load-bearing half ────────────────

def test_a_cut_always_gets_an_annotation_even_off_cadence():
    """SAM2 across a cut tracks whatever now occupies those coordinates."""
    anns = annotation_frames(96, cuts=[37], every=12)
    assert 37 in anns and 38 in anns


def test_the_last_frame_is_always_annotated():
    assert 95 in annotation_frames(96, every=12)


def test_no_cuts_still_gives_a_regular_cadence():
    anns = annotation_frames(96, cuts=[], every=12)
    assert anns[:3] == [0, 12, 24] and len(anns) >= 8


def test_cuts_outside_the_window_are_ignored():
    assert annotation_frames(10, cuts=[-5, 200], every=5) == [0, 5, 9]


def test_an_empty_window_annotates_nothing():
    assert annotation_frames(0) == []


# ── garment rejection ───────────────────────────────────────────────────────

def test_a_normal_body_is_not_rejected():
    fg = box(50, 20, 120, 160)
    subject = box(50, 20, 60, 160)          # half the foreground
    assert is_garment_not_person(subject, fg) is False


def test_a_matte_covering_the_whole_foreground_is_a_garment_not_a_person():
    fg = box(50, 20, 120, 160)
    assert is_garment_not_person(fg.copy(), fg) is True


def test_the_threshold_is_the_measured_sixty_percent():
    assert GARMENT_NOT_PERSON_FRAC == 0.60
    fg = box(0, 0, 100, 100)
    just_over = box(0, 0, 100, 61)
    just_under = box(0, 0, 100, 59)
    assert is_garment_not_person(just_over, fg) is True
    assert is_garment_not_person(just_under, fg) is False


def test_an_empty_foreground_never_rejects():
    assert is_garment_not_person(box(0, 0, 10, 10), np.zeros((H, W), np.float32)) is False


# ── warp: wide -> tight ─────────────────────────────────────────────────────

def test_the_warp_magnifies_area_because_the_crop_is_tighter_than_the_frame():
    """An earlier pass warped a native mask with a crop-space transform and got
    1.4% area where 28% was correct. Nothing raised, because both numbers are
    plausible area fractions -- only the RATIO betrays the wrong space.

    A subject occupying X of the native frame occupies MORE of a tighter crop.
    Here: 0.29% of 1080x1920, 1.21% of a 530x943 crop -- 4.2x.
    """
    native = box(100, 50, 60, 100, shape=(1080, 1920))
    native_frac = float((native > 0.5).mean())
    out = warp_to_crop(native, (80, 0, 530, 943), out_w=1080, out_h=1920)
    assert out.shape == (1920, 1080)
    crop_frac = float((out > 0.5).mean())
    ratio = crop_frac / native_frac
    expected = (1080 * 1920) / (530 * 943)
    assert abs(ratio - expected) / expected < 0.15, (
        f"area ratio {ratio:.2f} vs expected ~{expected:.2f} — wrong space")


def test_a_crop_outside_the_subject_yields_an_empty_matte_not_a_crash():
    native = box(0, 0, 40, 40, shape=(1080, 1920))
    out = warp_to_crop(native, (1500, 900, 400, 170))
    assert out.shape == (1920, 1080) and float((out > 0.5).mean()) == 0.0


def test_a_degenerate_rect_does_not_raise():
    assert warp_to_crop(box(0, 0, 10, 10, (100, 100)), (0, 0, 0, 0)).shape == (1920, 1080)


# ── report ──────────────────────────────────────────────────────────────────

def test_empty_frames_are_named_not_just_counted():
    masks = {0: box(10, 10, 80, 120), 1: np.zeros((H, W), np.float32),
             2: box(10, 10, 80, 120)}
    rep = summarise(masks)
    assert rep.empty_frames == [1] and rep.ok is False


def test_a_clean_run_reports_ok_and_a_band():
    masks = {i: box(10, 10, 90, 130) for i in range(5)}
    rep = summarise(masks)
    assert rep.ok and rep.frames == 5
    assert MATTE_AREA_BAND[0] <= rep.area_mean <= MATTE_AREA_BAND[1]


# ── build: seeding ──────────────────────────────────────────────────────────

def _fns(sil_map, fg=None):
    fg = fg if fg is not None else box(40, 10, 160, 180)
    return dict(
        foreground_fn=lambda f: fg,
        silhouette_fn=lambda f: sil_map,
        propagate_fn=lambda seeds: {i: next(iter(seeds.values())).astype(np.float32)
                                    for i in range(96)},
    )


def test_seeds_use_the_largest_mask_for_the_subject_hue():
    small, large = box(50, 20, 20, 40), box(50, 20, 70, 150)
    masks, rep = build_mattes(
        96, subject_hue=NAVY,
        **_fns({NAVY: large, CRIMSON: small}))
    assert rep.annotations > 0 and rep.frames == 96 and rep.ok


def test_a_garment_sized_seed_is_refused_and_named():
    fg = box(40, 10, 160, 180)
    masks, rep = build_mattes(96, subject_hue=NAVY, **_fns({NAVY: fg.copy()}, fg=fg))
    assert rep.rejected_garment, "a foreground-sized seed was accepted"
    assert masks == {} and rep.ok is False


def test_no_silhouette_for_the_subject_hue_produces_no_seed():
    masks, rep = build_mattes(96, subject_hue=NAVY, **_fns({CRIMSON: box(50, 20, 60, 140)}))
    assert masks == {} and rep.annotations == 0


def test_a_negative_click_is_counted_when_both_bodies_are_present():
    masks, rep = build_mattes(
        96, subject_hue=NAVY,
        **_fns({NAVY: box(50, 20, 60, 140), CRIMSON: box(150, 20, 60, 140)}))
    assert rep.negatives > 0, "two bodies present but no negative recorded"


def test_cuts_add_seeds_beyond_the_cadence():
    sil = {NAVY: box(50, 20, 60, 140)}
    _, plain = build_mattes(96, subject_hue=NAVY, **_fns(sil))
    _, cut = build_mattes(96, cuts=[7, 43, 71], subject_hue=NAVY, **_fns(sil))
    assert cut.annotations > plain.annotations
