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
    crop_rect_for,
    is_garment_not_person,
    summarise,
    warp_to_crop,
)

H, W = 200, 300
NAVY, CRIMSON = 225.0, 15.0


def box(x0, y0, w, h, shape=(H, W)):
    m = np.zeros(shape, np.float32)
    m[y0 : y0 + h, x0 : x0 + w] = 1.0
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


def two_bodies(h=200, w=200):
    """A foreground holding TWO person-sized blobs -- the only situation in
    which 'fraction of the foreground' says anything about garment-vs-person."""
    fg = np.zeros((h, w), np.float32)
    fg[20:180, 20:80] = 1.0
    fg[20:180, 120:180] = 1.0
    return fg


def test_a_normal_body_is_not_rejected():
    fg = two_bodies()
    subject = np.zeros_like(fg)
    subject[20:180, 20:80] = 1.0  # one of the two bodies
    assert is_garment_not_person(subject, fg) is False


def test_a_matte_covering_BOTH_bodies_is_a_garment_not_a_person():
    """The case the guard exists for: the tracker holding trunks plus canvas
    plus the other fighter."""
    fg = two_bodies()
    assert is_garment_not_person(fg.copy(), fg) is True


def test_a_lone_subject_filling_its_foreground_is_NOT_a_garment():
    """Measured against the operator-approved UFC-05 mattes: they cover 53-97%
    of the foreground (mean 77%) across the nine annotation frames. Comparing
    against 60% unconditionally rejected the APPROVED matte on 8 of 9, leaving
    one annotation to seed 96 frames and half the clip unmasked.

    When the subject is alone they ARE the foreground, so the fraction carries
    no information. This pin replaces one that asserted the opposite
    (`mask == whole foreground -> garment`), which was true of a two-body frame
    and false of the reference clip.
    """
    fg = np.zeros((200, 200), np.float32)
    fg[20:180, 60:140] = 1.0  # ONE body
    assert is_garment_not_person(fg.copy(), fg) is False
    almost = fg.copy()
    almost[20:30, 60:140] = 0.0  # 94% of the foreground
    assert is_garment_not_person(almost, fg) is False


def test_the_threshold_is_the_measured_sixty_percent():
    """Geometric, so the fraction is exact and readable.

    Two bodies of 60x160 = 9600 px each, 19200 total. A mask holding one whole
    body plus N rows of the other crosses 60% between N=28 (58.8%) and N=36
    (61.3%).
    """
    assert GARMENT_NOT_PERSON_FRAC == 0.60
    fg = np.zeros((200, 200), np.float32)
    fg[20:180, 20:80] = 1.0
    fg[20:180, 120:180] = 1.0

    def one_body_plus(rows: int) -> np.ndarray:
        m = np.zeros_like(fg)
        m[20:180, 20:80] = 1.0
        if rows:
            m[20 : 20 + rows, 120:180] = 1.0
        return m

    over, under = one_body_plus(36), one_body_plus(28)
    assert float((over > 0.5).sum()) / float((fg > 0.5).sum()) > 0.60
    assert float((under > 0.5).sum()) / float((fg > 0.5).sum()) < 0.60
    assert is_garment_not_person(over, fg) is True
    assert is_garment_not_person(under, fg) is False


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
        f"area ratio {ratio:.2f} vs expected ~{expected:.2f} — wrong space"
    )


def test_a_crop_outside_the_subject_yields_an_empty_matte_not_a_crash():
    native = box(0, 0, 40, 40, shape=(1080, 1920))
    out = warp_to_crop(native, (1500, 900, 400, 170))
    assert out.shape == (1920, 1080) and float((out > 0.5).mean()) == 0.0


def test_a_degenerate_rect_does_not_raise():
    assert warp_to_crop(box(0, 0, 10, 10, (100, 100)), (0, 0, 0, 0)).shape == (1920, 1080)


# ── report ──────────────────────────────────────────────────────────────────


def test_empty_frames_are_named_not_just_counted():
    masks = {0: box(10, 10, 80, 120), 1: np.zeros((H, W), np.float32), 2: box(10, 10, 80, 120)}
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
        propagate_fn=lambda seeds: {
            i: next(iter(seeds.values())).astype(np.float32) for i in range(96)
        },
    )


def test_seeds_use_the_largest_mask_for_the_subject_hue():
    small, large = box(50, 20, 20, 40), box(50, 20, 70, 150)
    masks, rep = build_mattes(96, subject_hue=NAVY, **_fns({NAVY: large, CRIMSON: small}))
    assert rep.annotations > 0 and rep.frames == 96 and rep.ok


def test_a_garment_sized_seed_is_refused_and_named():
    """Two bodies in the foreground and a seed covering both -- the shape the
    guard exists to catch. With ONE body the same seed is the subject and must
    be accepted (see test_a_lone_subject_filling_its_foreground_is_NOT_a_garment)."""
    fg = np.zeros((200, 200), np.float32)
    fg[20:180, 20:80] = 1.0
    fg[20:180, 120:180] = 1.0
    masks, rep = build_mattes(96, subject_hue=NAVY, **_fns({NAVY: fg.copy()}, fg=fg))
    assert rep.rejected_garment, "a seed covering both bodies was accepted"
    assert masks == {} and rep.ok is False


def test_no_silhouette_for_the_subject_hue_produces_no_seed():
    masks, rep = build_mattes(96, subject_hue=NAVY, **_fns({CRIMSON: box(50, 20, 60, 140)}))
    assert masks == {} and rep.annotations == 0


def test_a_negative_click_is_counted_when_both_bodies_are_present():
    masks, rep = build_mattes(
        96, subject_hue=NAVY, **_fns({NAVY: box(50, 20, 60, 140), CRIMSON: box(150, 20, 60, 140)})
    )
    assert rep.negatives > 0, "two bodies present but no negative recorded"


def test_cuts_add_seeds_beyond_the_cadence():
    sil = {NAVY: box(50, 20, 60, 140)}
    _, plain = build_mattes(96, subject_hue=NAVY, **_fns(sil))
    _, cut = build_mattes(96, cuts=[7, 43, 71], subject_hue=NAVY, **_fns(sil))
    assert cut.annotations > plain.annotations


# ── crop geometry (RENDER-01 Part 7 §2) ─────────────────────────────────────


def test_crop_rect_reproduces_the_approved_ufc05_geometry():
    """The divergence hunt's answer, pinned.

    The archived matte is computed on the NATIVE 1920x1080 frame and warped into
    the reel's portrait crop; the backend was running SAM2 on the graded portrait
    crop directly and never warping. Warping the archived native mattes through
    this rect reproduces the archived portrait mattes at IoU 1.0000 on 96/96.

    Pinned here as the GEOMETRY rather than the pixels, so it holds without the
    deliverable present: the full-bleed floor crops the whole frame height.
    """
    r = crop_rect_for(mag=1920 / 1080, cx=0.5, cy=0.5, src_h=1080)
    x0, y0, cw, ch = r
    assert round(ch) == 1080, "at the full-bleed floor the crop is the full height"
    assert round(cw) == 608, "and 9:16 of it"
    assert y0 == 0.0


def test_the_clamp_stops_a_rect_taller_than_the_frame():
    """Without it a plan row asking for more than the frame height returns a
    rect the image cannot satisfy, and the crop silently comes out short."""
    r = crop_rect_for(mag=1.0, cx=0.5, cy=0.5, src_h=1080)
    _, _, cw, ch = r
    assert ch <= 1080
    assert abs(cw / ch - 1080 / 1920) < 1e-6, "aspect preserved by the clamp"


def test_the_rect_stays_inside_the_frame_at_the_edges():
    for cx in (0.0, 1.0):
        x0, _, cw, _ = crop_rect_for(mag=2.5, cx=cx, cy=0.5, src_h=1080)
        assert x0 >= 0 and x0 + cw <= 1920
