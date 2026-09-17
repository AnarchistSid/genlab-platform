"""Impact effects, pinned by CONTROL-RENDER DIFFERENCE.

Every assertion here measures `on - off` in a region, never a colour statistic
of the composite. That distinction is the lesson of the whole arc: an absolute
red-excess reading conflates the effect with the content, and a control render
with the effect disabled is what separates them. It is also what found a real
bug -- 40% of a flame landing back on the face after the mask.
"""

import numpy as np
import pytest
import yaml
from genlab_core.action.effects import impact as I
from genlab_core.action.effects._ops import dist_outside, erode, luma, screen

H, W = 400, 240


@pytest.fixture(scope="module")
def kit():
    import pathlib

    reg = __import__("genlab_core.action.kits.registry", fromlist=["x"])
    return yaml.safe_load((pathlib.Path(reg.__file__).parent / "impact.yaml").read_text())


@pytest.fixture
def scene():
    rng = np.random.default_rng(0)
    frame = rng.uniform(40, 200, (H, W, 3)).astype(np.float32)
    m = np.zeros((H, W), np.float32)
    m[80:320, 90:160] = 1.0  # a body: 240 tall, 70 wide
    return frame, m


def red_excess(rgb01):
    return np.clip(rgb01[..., 0] - 0.5 * (rgb01[..., 1] + rgb01[..., 2]), 0, None)


# ── world grade: solved per source, measured on the result ──────────────────


def test_the_grade_reaches_the_kit_targets_for_this_source(scene, kit):
    frame, m = scene
    wl, sl = I.solve_world_multipliers(frame, m, kit)
    out = I.grade_world(frame, m, kit, world_luma=wl, subject_luma=sl)
    y = luma(out / 255.0) * 255.0
    mk = m > 0.5
    world, subj = float(y[~mk].mean()), float(y[mk].mean())
    tw = I.kit_value(kit, "world_luma_target")
    ts = I.kit_value(kit, "subject_luma_target")
    tr = I.kit_value(kit, "contrast_ratio")
    assert abs(world - tw) / tw < 0.10, f"world {world:.1f} vs {tw}"
    assert abs(subj - ts) / ts < 0.10, f"subject {subj:.1f} vs {ts}"
    assert abs((subj / world) - tr) / tr < 0.05, f"ratio {subj / world:.2f} vs {tr}"


def test_a_brighter_source_gets_different_multipliers(scene, kit):
    """The point of solving: a constant cannot serve two sources. UFC's ungraded
    corner luma is 84.5 against the reference's 32.9."""
    frame, m = scene
    dim = I.solve_world_multipliers(frame * 0.4, m, kit)
    bright = I.solve_world_multipliers(np.clip(frame * 2.0, 0, 255), m, kit)
    assert dim[0] > bright[0] * 1.5, "same world multiplier for 5x different sources"


def test_the_world_is_dimmed_and_the_subject_is_not(scene, kit):
    frame, m = scene
    wl, sl = I.solve_world_multipliers(frame, m, kit)
    out = I.grade_world(frame, m, kit, world_luma=wl, subject_luma=sl)
    y0, y1 = luma(frame / 255.0), luma(out / 255.0)
    mk = m > 0.5
    assert y1[~mk].mean() < y0[~mk].mean(), "the world was not dimmed"


def test_the_world_keeps_red_rather_than_going_black(scene, kit):
    """A black world scores 0 on the off-band red-excess the reference is
    measured by; the cage and crowd must survive as faint red shapes."""
    frame, m = scene
    out = I.grade_world(frame, m, kit, world_luma=0.05, subject_luma=1.0)
    mk = m > 0.5
    assert float(red_excess(out / 255.0)[~mk].mean()) > 0.002


# ── aura: on - off ──────────────────────────────────────────────────────────


def test_the_aura_band_does_not_scale_with_subject_height(scene, kit):
    """The subject-height normalisation transfers at 1.78x and BREAKS at 2.5x:
    0.251 x subject height became 40% of frame width against the reference's
    11.4%. The frame-width cap is the invariant that survives a shot size
    change -- so DOUBLING the subject must not double the band.

    Measured on `on - off` energy as a function of distance, not on a
    threshold crossing, because the bloom tail spreads past the band by design.
    """
    frame, _ = scene

    def reach(mask):
        lay = I.aura(frame, mask, kit, 1.0, 0)
        d = dist_outside(mask, max_px=300)
        e = lay.max(axis=2)
        tot = float(e.sum()) or 1.0
        # distance containing 90% of the aura's energy
        for r in range(4, 200, 4):
            if float(e[(d > 0) & (d <= r)].sum()) / tot >= 0.90:
                return r
        return 200

    short = np.zeros((H, W), np.float32)
    short[180:280, 90:160] = 1.0  # 100 px tall
    tall = np.zeros((H, W), np.float32)
    tall[40:400, 90:160] = 1.0  # 360 px tall
    r_short, r_tall = reach(short), reach(tall)
    assert r_tall < r_short * 2.0, (
        f"band reach scaled with the subject: {r_short}px -> {r_tall}px for a "
        f"3.6x taller body; the frame-width cap is not holding"
    )


def test_the_aura_is_outside_the_body_not_on_it(scene, kit):
    """`on - off` energy inside the silhouette must be ~nothing: a 34px
    displacement once pulled the band inside and carved chunks out of arms."""
    frame, m = scene
    lay = I.aura(frame, m, kit, 1.0, 0)
    inside = float(lay[m > 0.5].mean())
    outside = float(lay[(m <= 0.5)].mean())
    assert inside < outside * 0.10, f"aura inside={inside:.4f} outside={outside:.4f}"


def test_aura_energy_beyond_the_band_is_small(scene, kit):
    """Arcs and debris leave the band by design; the AURA should not.

    Measured as a share of TOTAL off-body aura ENERGY, not as a mean over
    pixels: the far region is mostly near-zero, so its mean is dominated by how
    much empty frame you include, and widening the fixture would "improve" it.
    """
    frame, m = scene
    lay = I.aura(frame, m, kit, 1.0, 0)
    d = dist_outside(m, max_px=300)
    band = float(
        np.clip(I.kit_value(kit, "band_w") * 240, 60.0, I.kit_value(kit, "band_frame_cap") * W)
    )
    reach = 22.0 * 2.0  # the bloom_mix blur spreads by design
    e = lay.max(axis=2)
    total = float(e[d > 0].sum())
    assert total > 0, "no aura energy at all"
    beyond = float(e[(d > band + reach)].sum()) / total
    assert beyond < 0.12, f"{beyond:.1%} of aura energy lies beyond band+reach"


def test_the_aura_terminates_rather_than_hazing_the_whole_frame(scene, kit):
    """A band that never ends is the ambient haze, which is a separate layer."""
    frame, m = scene
    lay = I.aura(frame, m, kit, 1.0, 0)
    d = dist_outside(m, max_px=300)
    band = float(
        np.clip(I.kit_value(kit, "band_w") * 240, 60.0, I.kit_value(kit, "band_frame_cap") * W)
    )
    far = d > band + 22.0 * 4.0
    if far.sum() > 100:
        assert float(lay[far].max()) < 0.01, "aura energy in the far field"


def test_the_aura_has_a_white_hot_core_walking_to_red_outward(scene, kit):
    frame, m = scene
    lay = I.aura(frame, m, kit, 1.0, 0)
    d = dist_outside(m, max_px=300)
    core = (d > 0) & (d < I.kit_value(kit, "white_px"))
    outer = (d > 60) & (d < 120)
    outer = (d > 20) & (d < 40)
    if core.sum() > 50 and outer.sum() > 50:
        # Compare HUE, not the spread of a premultiplied layer: out in the band
        # the intensity is near zero, so its channel spread is near zero too and
        # the "less saturated" pixel is simply the dimmer one.
        def blue_over_red(sel):
            px = lay[sel]
            keep = px.max(1) > 1e-4
            px = px[keep] if keep.any() else px
            return float((px[:, 2] / np.maximum(px[:, 0], 1e-6)).mean())

        assert blue_over_red(core) > blue_over_red(outer), (
            "the core is not whiter (higher blue:red) than the outer band"
        )


def test_the_aura_flickers_between_frames(scene, kit):
    """The band scrolls upward; a static band reads as an outline."""
    frame, m = scene
    a0 = I.aura(frame, m, kit, 1.0, 0)
    a9 = I.aura(frame, m, kit, 1.0, 9)
    assert float(np.abs(a9 - a0).mean()) > 1e-4


def test_the_beat_phase_modulates_amplitude(scene, kit):
    frame, m = scene
    lo = I.aura(frame, m, kit, 0.0, 0)
    hi = I.aura(frame, m, kit, 1.0, 0)
    assert float(hi.mean()) > float(lo.mean())


# ── face protection: the pin that found a real bug ──────────────────────────


def test_no_effect_energy_survives_inside_the_head_region(scene, kit):
    """Measured as `on - off` inside the region, not as absolute redness --
    skin is redder than black trousers whatever the aura does."""
    frame, m = scene
    head = np.zeros((H, W), np.float32)
    head[80:150, 90:160] = 1.0
    lay = I.aura(frame, m, kit, 1.0, 0)
    masked = I.protect_face(lay, head, kit)
    on_face = float(red_excess(masked)[head > 0.5].sum())
    torso = np.zeros((H, W), bool)
    torso[150:320, 90:160] = True
    on_torso = float(red_excess(I.protect_face(lay, head, kit))[torso].sum())
    assert on_face <= max(on_torso, 1e-6) * 0.10, (
        f"face carries {on_face:.3f} against torso {on_torso:.3f}"
    )


def test_protect_face_is_a_no_op_without_a_region(scene, kit):
    frame, m = scene
    lay = I.aura(frame, m, kit, 1.0, 0)
    assert np.allclose(I.protect_face(lay, None, kit), lay)


# ── bolts ───────────────────────────────────────────────────────────────────


def test_a_bolt_is_one_large_stroke_not_a_cage_of_sparks(kit):
    """One long sweeping stroke, not a spray.

    Measured on a canvas big enough to HOLD the stroke. `bolt_len_per_h` is
    1.95, so on the shared 400x240 fixture a correct bolt is 468 px long and
    simply leaves frame -- the span would clip to ~90 px and the test would
    read a correct stroke as a spark. The fixture, not the effect, was wrong.
    """
    big_h, big_w = 1200, 900
    m = np.zeros((big_h, big_w), np.float32)
    m[400:700, 380:520] = 1.0  # a 300 px subject with room around it
    lay, core = I.bolts(m, kit, np.random.default_rng(3))
    assert float(core.max()) > 0.5, "no stroke drawn"
    ys, xs = np.nonzero(core > 0.3)
    span = float(max(ys.max() - ys.min(), xs.max() - xs.min()))
    expected = I.kit_value(kit, "bolt_len_per_h") * 300.0
    # A sweeping stroke curves, so its bounding span is well under its arc
    # length; the floor catches "a spark" without pinning the curvature.
    assert span > expected * 0.35, f"stroke span {span:.0f}px against ~{expected:.0f}px of arc"


def test_the_afterglow_only_fires_when_a_stroke_preceded_it(scene, kit):
    _, m = scene
    _, core = I.bolts(m, kit, np.random.default_rng(3))
    after, _ = I.bolts(m * 0, kit, np.random.default_rng(4), prev_core=core)
    cold, _ = I.bolts(m * 0, kit, np.random.default_rng(4), prev_core=None)
    assert float(after.mean()) > float(cold.mean()), "afterglow did not carry"
    assert float(cold.mean()) < 1e-6, "energy with no stroke and no previous core"


def test_the_afterglow_is_dimmer_than_the_stroke(scene, kit):
    _, m = scene
    lay, core = I.bolts(m, kit, np.random.default_rng(3))
    after, _ = I.bolts(m * 0, kit, np.random.default_rng(3), prev_core=core)
    assert float(after.mean()) < float(lay.mean())


# ── debris ──────────────────────────────────────────────────────────────────


def test_debris_starts_at_the_contact_point(scene, kit):
    _, m = scene
    deb = I.Debris.at(125.0, 200.0, kit)
    lay = I.debris(deb, 1, m * 0)
    ys, xs = np.nonzero(lay.max(axis=2) > 0.05)
    assert len(ys), "no debris drawn"
    assert abs(float(xs.mean()) - 125.0) < 40 and abs(float(ys.mean()) - 200.0) < 60


def test_debris_is_attenuated_behind_the_subject(scene, kit):
    """Debris is masked BEHIND the subject -- attenuated, not eliminated.

    The approved mask is `1 - blur(matte, 5.0)`, which passes a particle at up
    to ~55% just inside the silhouette edge and falls to zero a few pixels in.
    An earlier version of this test asserted a 2% ceiling everywhere inside the
    body; no approved render has ever met that, and the port was changed to a
    hard `dilate` cut to satisfy it. Pinning the real profile instead: the
    interior goes dark, the edge is allowed to leak.

    Measured profile of the leak on this fixture, max over ages 2/8/16:

        erode  0 px  0.925      erode 12 px  0.055
        erode  4 px  0.502      erode 20 px  0.000
        erode  8 px  0.202

    20 px is where it reaches zero, and that is not a guess: the mask is a
    sigma-5 blur, whose support runs to ~3 sigma.
    """
    _, m = scene
    deb = I.Debris.at(125.0, 200.0, kit)
    deep = erode(m, 20.0) > 0.5  # past the mask's ~3-sigma reach
    assert deep.any(), "fixture subject too thin to have an interior"
    for age in (2, 8, 16):
        lay = I.debris(deb, age, m)
        assert float(lay[deep].max()) < 0.02, f"debris deep inside the body at age {age}"


def test_debris_expires(scene, kit):
    _, m = scene
    deb = I.Debris.at(125.0, 200.0, kit)
    assert float(I.debris(deb, 200, m * 0).max()) == 0.0


# ── flash, trails, bloom ────────────────────────────────────────────────────


def test_the_body_flash_whitens_the_matte_and_lifts_the_frame(scene, kit):
    frame, m = scene
    lay = I.body_flash(m, kit)
    on_body = float(lay[m > 0.5].mean())
    off_body = float(lay[m <= 0.5].mean())
    assert on_body > 0.9, f"matte not whitened: {on_body:.2f}"
    assert off_body > 0.05, "no ambient lift off the body"
    composed = screen(frame / 255.0, lay) * 255.0
    assert float(luma(composed / 255.0)[m > 0.5].mean() * 255) >= 240


def test_trails_are_earned_by_motion(scene, kit):
    _, m = scene
    still = np.roll(m, 2, axis=1)
    fast = np.roll(m, 40, axis=1)
    assert I.moved_fast(m, still, kit) is False
    assert I.moved_fast(m, fast, kit) is True
    assert I.moved_fast(None, fast, kit) is False


def test_bloom_does_nothing_without_highlights(scene, kit):
    dark = np.full((H, W, 3), 20.0, np.float32)
    assert float(I.bloom(dark, kit).max()) == 0.0


def test_bloom_is_damped_on_an_already_hot_frame(scene, kit):
    """Undamped, a bright frame blooms into a white field -- the effect stops
    adding highlights and starts removing the image."""
    hi = np.full((H, W, 3), 40.0, np.float32)
    hi[:120, :] = 255.0  # 30% hot, well over the 5.5% cap

    import copy

    undamped = copy.deepcopy(kit)
    undamped["effect_constants"]["bloom_hot_frac"] = {"value": 1.0, "source": "test"}

    damped_mean = float(I.bloom(hi, kit).mean())
    raw_mean = float(I.bloom(hi, undamped).mean())
    assert damped_mean < raw_mean * 0.35, (
        f"damping barely applied: {damped_mean:.4f} vs raw {raw_mean:.4f}"
    )


# ── kit agreement ───────────────────────────────────────────────────────────


def test_every_effect_constant_names_its_source(kit):
    ec = kit["effect_constants"]
    missing = [k for k, v in ec.items() if not isinstance(v, dict) or "source" not in v]
    assert not missing, f"constants without a source: {missing}"


def test_kit_value_reads_the_yaml_not_a_literal(kit):
    assert I.kit_value(kit, "band_w") == 0.251
    assert I.kit_value(kit, "intensity_exponent") == 4.2
    assert I.kit_value(kit, "missing_thing", 7) == 7
