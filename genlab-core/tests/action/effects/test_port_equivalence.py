"""The port gate: `impact.py` must compute what the approved renders computed.

WHY THIS FILE EXISTS, AND WHY IT IS NOT THE GATE THAT WAS ASKED FOR
-------------------------------------------------------------------
The packet specified: re-render the WWE hit3s segment through this module and
require PSNR >= 35 dB against the approved v6, with an identical gate table.
That gate is unrunnable, for two independent reasons:

  1. Its inputs are gone. The WWE source clip and the 96 SAM2 per-instance
     mattes lived in an ephemeral scratch dir that has been cleaned. No source
     video id was recorded in the deliverable, and `v7_crop.py` imports
     `v4_build` / `v4_look`, which were never archived -- so the render chain is
     broken in two places, not one.
  2. It named a superseded artifact. v6 is the WWE segment; the latest approved
     ACTION render is UFC-05 v4. But `w2_build4.py` (that build) still imports
     `v6_look` / `v8_look` -- the UFC arc changed the BUILD layer (per-source
     solved grade, flash anchored to the derived finish frame), not the effect
     primitives. So the primitives, not the render, are the thing to pin.

Function-level equivalence is also the stricter instrument. PSNR 35 dB tolerates
differences a viewer can see; this tolerates one float32 ULP. The oracle is
vendored under `_oracle/` precisely so this gate cannot rot the way that one did.

WHAT THE GATE CAUGHT
--------------------
Six real divergences, all of the same shape -- the port had re-described the
effect rather than reproduced it:

  * `bolt_len_per_h` 0.834 vs 1.95. Both approved builds call `mega_bolt()`
    without `len_per_h`, so every approved render used its signature default of
    1.95. The port shipped 0.834 -- the number in the function's DOCSTRING,
    which contradicts its own default. Bolts were 2.34x too short.
  * `heat_shimmer` dropped the `* (d > 0)` factor. `dist_outside` is zero INSIDE
    the silhouette, so `1 - d/band` is 1.0 there: the port warped the subject's
    own body at full amplitude. Interior weight 1.000 against the approved 0.012.
  * `edge_launch` took the outward normal from the ray out of the subject's
    centroid instead of from the matte gradient. On a concave silhouette
    (armpit, between the legs) that points across the body, not out of it.
  * `body_flash` lost the orange halo (`blur(dilate(m, 40), 30)`) and replaced
    it with a flat full-frame lift that no approved build had.
  * `Debris` drew uniform 2.4 px orange dots. The approved particles are motion
    STREAKS with per-particle size (4-10 px, plus four 16-30 px mat chunks), two
    materials (45% grey mat / rest hot orange) and an age fade.
  * `motion_blur` was not ported at all, though both approved builds call it at
    px=12.0 on the frames before the finish. It is now `directional_blur`.

ONE DIFFERENCE IS DELIBERATE and is asserted as such below: `aura()` folds the
build-layer weighting (`vertical_weight * limb_weight`) into the returned layer.
That is exact, not approximate -- the approved build lerps
`img*(1-w) + screen(img, lay)*w`, and since `screen(x, a) == x + a*(1-x)`, that
equals `screen(x, lay*w)`.

ONE DIFFERENCE IS LATENT and is left alone: aura `band_peak` is 0.085 here
against the approved 0.070. Both clamp to the same 41.6 px frame-width cap for
any subject taller than ~594 px, which is every shot size ACTION uses. It is
recorded rather than "fixed" because changing it would alter nothing today.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml
from genlab_core.action.effects import _ops as P
from genlab_core.action.effects import impact as I

_ORACLE = Path(__file__).parent / "_oracle"
sys.path.insert(0, str(_ORACLE))

v6_look = pytest.importorskip("v6_look", reason="frozen oracle missing")
v8_look = pytest.importorskip("v8_look", reason="frozen oracle missing")

# The oracle hardcodes OW, OH = 1080, 1920 as module globals -- that is the only
# shape at which it is even defined, so equivalence is measured there.
H, W = 1920, 1080
ULP = 1e-4  # float32 round-trip through the layer/screen refactor


def test_the_oracle_is_unmodified():
    """The oracle only means something if it is the approved bytes.

    `ruff format` over the test tree rewrote both files once already -- the
    equivalence tests still passed, because the reformat was semantics-
    preserving, which is exactly why this needs its own assertion rather than
    trusting the other gates to notice. `**/tests/**/_oracle/` is excluded in
    pyproject.toml so the formatter cannot reach them again; this pin is what
    catches it if that exclusion is ever dropped.

    To re-vendor deliberately, update these hashes in the same commit and say
    in the message which deliverable they came from.
    """
    want = {
        "v6_look.py": "fbb53e0a3edd507016fd4e6f6435d211df4ad8544b9823e648a90cfb5f742520",
        "v8_look.py": "6435a45447721ab04c04f746045a12e3d2262c554d4273aa8e9b22806c3897b0",
    }
    for name, digest in want.items():
        got = hashlib.sha256((_ORACLE / name).read_bytes()).hexdigest()
        assert got == digest, (
            f"{name} has been modified since it was vendored from "
            f".deliverables/clutchwire_action_hit3s_v6/scripts/ (got {got[:12]})"
        )


@pytest.fixture(scope="module")
def kit() -> dict:
    p = Path(I.__file__).parents[1] / "kits" / "impact.yaml"
    return yaml.safe_load(p.read_text())


@pytest.fixture(scope="module")
def scene():
    rng = np.random.default_rng(7)
    frame = rng.uniform(10, 240, (H, W, 3)).astype(np.float32)
    m = np.zeros((H, W), np.float32)
    m[500:1500, 300:760] = 1.0
    m = (P.blur(m, 9.0) > 0.5).astype(np.float32)  # a non-trivial silhouette
    return frame, m, P.subject_height(m)


def same(name: str, a, b, tol: float = ULP) -> None:
    a, b = np.asarray(a, np.float64), np.asarray(b, np.float64)
    assert a.shape == b.shape, f"{name}: shape {a.shape} vs {b.shape}"
    mx = float(np.abs(a - b).max())
    assert mx <= tol, f"{name}: max abs difference {mx:.3e} exceeds {tol:.1e}"


# ───────────────────────────── primitives ───────────────────────────────────


def test_primitives_are_bit_identical(scene):
    """The shape-parameterised `_ops` must equal the OW/OH-hardcoded oracle."""
    frame, m, _ = scene
    same("blur", v6_look.blur(m, 13.0), P.blur(m, 13.0), 0.0)
    same("dilate", v6_look.dilate(m, 6.0), P.dilate(m, 6.0), 0.0)
    same("erode", v6_look.erode(m, 6.0), P.erode(m, 6.0), 0.0)
    same(
        "screen",
        v6_look.screen(frame / 255, m[..., None] * 0.3),
        P.screen(frame / 255, m[..., None] * 0.3),
        0.0,
    )
    same("dist_outside", v6_look.dist_outside(m, 360), P.dist_outside(m, 360), 0.0)
    same("noise", v6_look.noise(404), P.noise(404, (H, W)), 0.0)
    same("noise octaves", v6_look.noise(11, (6, 18)), P.noise(11, (H, W), (6, 18)), 0.0)


def test_the_build_layer_weights_are_reproduced(scene):
    """`limb_weight` / `vertical_weight` lived in the build script, not the
    oracle modules -- they are the easiest thing in the port to get wrong,
    because there is no obvious file to diff them against."""
    _, m, _ = scene
    same(
        "limb_weight",
        0.70 + 0.30 * np.clip(v6_look.blur(v6_look.erode(m, 46.0), 22.0), 0, 1),
        P.limb_weight(m, 0.7),
        0.0,
    )
    ys, _ = np.nonzero(m > 0.5)
    y0, y1 = float(ys.min()), float(ys.max())
    t = np.clip((np.arange(H, dtype=np.float32) - y0) / max(y1 - y0, 1.0), 0, 1)
    prof = np.repeat(
        np.interp(t, [0.0, 0.55, 1.0], [1.0, 0.6, 0.4]).astype(np.float32)[:, None], W, axis=1
    )
    same("vertical_weight", prof, P.vertical_weight(m), 0.0)


# ───────────────────────────── effects ──────────────────────────────────────


def test_aura_matches_once_the_build_weighting_is_divided_out(scene, kit):
    """The port folds `vertical_weight * limb_weight` into the layer. Dividing
    it back out must recover the approved composite exactly."""
    frame, m, sh = scene
    approved = v6_look.flame_aura(frame, m, 3, 1.0, sh)
    lay = I.aura(frame, m, kit, 1.0, 3, sh)
    wgt = (P.vertical_weight(m) * P.limb_weight(m, float(I.kit_value(kit, "limb_weight", 0.7))))[
        ..., None
    ]
    inner = np.clip(P.blur(m, 26.0) - P.erode(m, 40), 0, 1)[..., None] * 0.34 * (0.9 + 0.4 * 1.0)
    got = (
        P.screen(
            P.screen(frame / 255.0, lay / np.maximum(wgt, 1e-6)), inner * (v6_look.ORANGE / 255.0)
        )
        * 255.0
    )
    same("aura", approved, got)


def test_the_folded_weighting_is_not_a_no_op(scene, kit):
    """Guard the guard: if the fold were accidentally dropped, the test above
    would still pass by dividing by ones. It must actually change the layer."""
    frame, m, sh = scene
    lay = I.aura(frame, m, kit, 1.0, 3, sh)
    unweighted = v6_look.flame_aura(frame, m, 3, 1.0, sh)
    assert float(np.abs(unweighted - P.screen(frame / 255.0, lay) * 255.0).max()) > 50.0, (
        "the build-layer weighting is not being applied"
    )


def test_bolt_matches_the_approved_stroke(scene, kit):
    frame, m, sh = scene
    approved, core_a = v8_look.mega_bolt(frame, m, np.random.default_rng(5), sh)
    lay, core_b = I.bolts(m, kit, np.random.default_rng(5), subj_h=sh)
    same("bolt core", core_a, core_b, 0.0)
    same("bolt composite", approved, P.screen(frame / 255.0, lay) * 255.0)


def test_bolt_length_is_the_value_the_approved_builds_ran(kit):
    """Both builds call `mega_bolt()` with no `len_per_h`, so the approved value
    is its signature default. The docstring's 0.834 is NOT what shipped."""
    import inspect

    default = inspect.signature(v8_look.mega_bolt).parameters["len_per_h"].default
    assert float(I.kit_value(kit, "bolt_len_per_h")) == float(default) == 1.95


def test_inner_glow_matches(scene, kit):
    frame, m, _ = scene
    same(
        "inner_spill",
        v8_look.inner_spill(frame, m),
        P.screen(frame / 255.0, I.inner_glow(m, kit)) * 255.0,
    )


def test_ambient_haze_matches(scene, kit):
    frame, m, sh = scene
    same(
        "aura_ambient",
        v8_look.aura_ambient(frame, m, sh),
        P.screen(frame / 255.0, I.ambient_haze(m, kit, sh)) * 255.0,
    )


def test_body_flash_matches_including_the_halo(scene, kit):
    frame, m, _ = scene
    same(
        "body_flash",
        v6_look.body_flash(frame, m, 0.92),
        P.screen(frame / 255.0, I.body_flash(m, kit, 0.92)) * 255.0,
    )


def test_heat_shimmer_matches_and_spares_the_body(scene, kit):
    frame, m, _ = scene
    same("heat_shimmer", v6_look.heat_shimmer(frame, m, 5), I.heat_shimmer(frame, m, 5), 0.0)
    # and state the property directly, so a future edit that drops `(d > 0)`
    # fails with a readable reason rather than a pixel diff
    band = 160.0
    d = P.dist_outside(m, max_px=int(band * 1.3))
    w = np.clip(1.0 - d / band, 0, 1) * (d > 0)
    assert float(w[m > 0.5].mean()) < 0.05, "the shimmer is displacing the subject itself"


def test_bloom_matches(scene, kit):
    frame, _, _ = scene
    same(
        "bloom_soft",
        v8_look.bloom_soft(frame),
        P.screen(frame / 255.0, I.bloom(frame, kit)) * 255.0,
    )


def test_directional_blur_matches(scene, kit):
    frame, _, _ = scene
    same(
        "motion_blur",
        v8_look.motion_blur(frame, frame * 0.8 + 9, px=12.0),
        I.directional_blur(frame, frame * 0.8 + 9, kit),
        0.0,
    )


def test_debris_particles_match_field_for_field(scene, kit):
    frame, m, _ = scene
    approved, port = v8_look.Debris(540, 900), I.Debris.at(540.0, 900.0, kit)
    for f in ("p", "v", "life", "size", "col"):
        same(f"Debris.{f}", getattr(approved, f), getattr(port, f), 0.0)
    same(
        "debris composite",
        v8_look.draw_debris(frame, approved, 6, m),
        P.screen(frame / 255.0, I.debris(port, 6, m)) * 255.0,
        0.0,
    )


def test_the_latent_band_peak_difference_cannot_bite(scene, kit):
    """0.085 here vs the approved 0.070 -- recorded, not fixed, because the
    frame-width cap swallows both at every shot size ACTION uses."""
    _, _, sh = scene
    cap = float(I.kit_value(kit, "peak_frame_cap", 0.0385)) * W
    approved = float(np.clip(0.070 * sh, 20.0, cap))
    port = float(np.clip(float(I.kit_value(kit, "band_peak")) * sh, 20.0, cap))
    assert approved == port == cap, (
        f"band_peak now bites: approved {approved:.1f} vs port {port:.1f}. "
        "ACTION subjects are close-ups; if this fires, the shot size changed."
    )
