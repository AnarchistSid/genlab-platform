"""Impact-kit effects. Ported from the deliverables that produced the approved reels.

Every function takes ``(frame, matte, kit, ...)`` and returns a SCREEN LAYER in
[0,1] -- the renderer composites them in a fixed order. The originals each
screened onto the frame internally, which made the order implicit and the
individual contributions impossible to measure. Returning layers is what makes a
control-render difference pin possible at all: `on - off` for one effect.

``grade_world`` is the exception and returns a FRAME: it replaces pixels rather
than adding light, and everything else composites on top of its result.

Constants come from the kit's ``effect_constants`` block, never from literals
here. Where a default appears in a signature it exists so the function is usable
standalone; a pin asserts it agrees with the YAML.

COMPOSE ORDER (fixed, executed by the renderer):
    world grade -> ambient haze -> aura -> inner glow -> trails -> subject
    -> bolts -> debris -> body flash -> shimmer -> bloom -> overlays (once, last)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

from genlab_core.action.effects._ops import (
    BOLT,
    ORANGE,
    RED,
    blur,
    centroid,
    dilate,
    dist_outside,
    erode,
    limb_weight,
    luma,
    noise,
    screen,
    subject_height,
    vertical_weight,
)

logger = logging.getLogger(__name__)


def kit_value(kit: dict, name: str, default=None):
    """Read a constant from the kit's ``effect_constants``.

    Entries are ``{value, source}`` -- the source is the point. A bare scalar is
    accepted so a test can pass a stripped-down kit.
    """
    node = (kit or {}).get("effect_constants", {}).get(name, default)
    if isinstance(node, dict):
        return node.get("value", default)
    return node if node is not None else default


# ───────────────────────────── grading ──────────────────────────────────────


def solve_world_multipliers(
    frame: np.ndarray, matte: np.ndarray, kit: dict, *, iterations: int = 12
) -> tuple[float, float]:
    """Solve (world_luma, subject_luma) for THIS source against the kit targets.

    Not a constant: UFC's ungraded corner luma is 84.5 against the reference's
    32.9, so WWE's solved 0.30 lands nowhere on it. The targets are OUTPUTS --
    world 37.1, subject 76.9, ratio 2.07 -- and the multipliers that reach them
    depend entirely on how bright the source already is.
    """
    want_world = float(kit_value(kit, "world_luma_target", 37.1))
    want_subj = float(kit_value(kit, "subject_luma_target", 76.9))
    m = matte > 0.5
    y = luma(frame / 255.0) * 255.0
    src_world = float(y[~m].mean()) if (~m).any() else 1.0
    src_subj = float(y[m].mean()) if m.any() else 1.0
    wl = want_world / max(src_world, 1e-6)
    sl = want_subj / max(src_subj, 1e-6)
    # The vignette darkens the world after scaling, so one pass undershoots.
    # Correct by measuring the graded result rather than by adding a fudge.
    for _ in range(max(iterations, 0)):
        out = grade_world(frame, matte, kit, world_luma=wl, subject_luma=sl)
        oy = luma(out / 255.0) * 255.0
        got_world = float(oy[~m].mean()) if (~m).any() else want_world
        got_subj = float(oy[m].mean()) if m.any() else want_subj
        if abs(got_world - want_world) < 0.25 and abs(got_subj - want_subj) < 0.25:
            break
        wl *= want_world / max(got_world, 1e-6)
        sl *= want_subj / max(got_subj, 1e-6)
    return float(wl), float(sl)


def grade_world(
    frame: np.ndarray,
    matte: np.ndarray,
    kit: dict,
    *,
    world_luma: float,
    subject_luma: float,
    vignette: float | None = None,
    feather: float | None = None,
) -> np.ndarray:
    """Dim the world, light the subject. Returns a FRAME, not a layer.

    The background is darkened and the subject never is -- only possible because
    a per-frame matte exists. The world keeps a red ambient so the cage, ropes
    and crowd survive as faint shapes instead of going to black; a black world
    scores 0 on the off-band red-excess the reference is measured by.
    """
    h, w = matte.shape
    x = frame / 255.0
    g = luma(x)[..., None]
    world = (g + (x - g) * float(kit_value(kit, "world_sat", 0.22))) * world_luma
    world = world + (RED / 255.0)[None, None, :] * float(kit_value(kit, "ambient_red", 0.018)) * (
        1.0 - g
    )
    yy, xx = np.ogrid[:h, :w]
    r = np.sqrt(((xx - w / 2) / (w * 0.80)) ** 2 + ((yy - h / 2) / (h * 0.75)) ** 2)
    # PER-CLIP, like world_luma and subject_luma -- not a kit constant. UFC-05
    # solved it to 0.30 for its source; WWE v6 ran grade_bathed's 0.62. The port
    # first read it only from the kit, which made it unable to reproduce v6 at
    # all: an 18.7/255 error on every single frame. The kit value is the default,
    # not the law.
    vig = float(kit_value(kit, "vignette_amt", 0.30) if vignette is None else vignette)
    world = world * (1.0 - vig * np.clip(r, 0, 1) ** 1.5)[..., None]
    subj = np.clip(g + (x - g) * float(kit_value(kit, "subject_sat", 1.15)), 0, 1) * subject_luma
    # Likewise per-call: the build grades twice per frame, once at the subject
    # feather and once at 1.0 for the whole-frame pass.
    fth = float(kit_value(kit, "feather_px", 18) if feather is None else feather)
    a = np.clip(blur(np.clip(blur(matte, 12.0), 0, 1), fth), 0, 1)[..., None]
    return np.clip(world * (1 - a) + subj * a, 0, 1) * 255.0


# ───────────────────────────── aura ─────────────────────────────────────────


def ambient_haze(
    matte: np.ndarray,
    kit: dict,
    subj_h: float | None = None,
    radius: float = 300.0,
    opacity: float = 0.08,
) -> np.ndarray:
    """A very large soft bloom over the WHOLE frame -- what turns a lit subject
    on a dark field into a scene bathed in energy."""
    subj_h = subj_h or subject_height(matte)
    d = dist_outside(matte, max_px=max(int(0.30 * subj_h), 8))
    near = np.clip(1.0 - d / max(0.30 * subj_h, 1.0), 0, 1)
    return np.clip(blur(near, radius)[..., None] * (RED / 255.0) * opacity, 0, 1)


def aura(
    frame: np.ndarray,
    matte: np.ndarray,
    kit: dict,
    phase: float = 1.0,
    t_frame: int = 0,
    subj_h: float | None = None,
) -> np.ndarray:
    """A wide displaced band with a white-hot core, not a rim.

    The shell is a DISTANCE RAMP displaced by turbulent noise scrolling upward,
    so its edge licks every frame. A dilated matte is static and reads as an
    outline however thick it is.
    """
    h, w = matte.shape
    subj_h = subj_h or subject_height(matte)
    # Subject-height normalisation transfers at 1.78x and BREAKS at 2.5x: at
    # close-up 0.251 x subject height was 432 px = 40% of frame width against
    # the reference's 218 px = 11.4%. Capped by the frame-width share, which is
    # the invariant that survives a change of shot size.
    band = float(
        np.clip(
            float(kit_value(kit, "band_w", 0.251)) * subj_h,
            60.0,
            float(kit_value(kit, "band_frame_cap", 0.1135)) * w,
        )
    )
    peak = float(
        np.clip(
            float(kit_value(kit, "band_peak", 0.085)) * subj_h,
            20.0,
            float(kit_value(kit, "peak_frame_cap", 0.0385)) * w,
        )
    )
    d = dist_outside(matte, max_px=int(band * 1.25))
    dy = t_frame * float(kit_value(kit, "scroll_px_s", 250.0)) / 30.0
    n1 = np.roll(noise(101, (h, w)), -int(dy) % h, axis=0)
    n2 = np.roll(noise(202, (h, w), (40, 90)), -int(dy * 1.7) % h, axis=0)
    d_true = d.copy()  # the inner guard must use the UNDISPLACED field
    disp = float(kit_value(kit, "displace_px", 34.0))
    d = d - (n1 * 2.0 - 1.0 + (n2 * 2.0 - 1.0) * 0.85) * disp

    # Intensity falls MONOTONICALLY outward; colour walks white -> orange -> red
    # along the same axis. Summing three overlapping lobes clipped to 1.0 across
    # 200 px and rendered as a white wall that swallowed the subject.
    expo = float(kit_value(kit, "intensity_exponent", 4.2))
    white_px = float(kit_value(kit, "white_px", 8.0))
    inten = np.clip(1.0 - d / band, 0, 1) ** expo * (d > 0)
    w_white = np.clip(1.0 - d / white_px, 0, 1) ** 1.2
    w_orange = np.clip(1.0 - np.abs(d - peak * 0.55) / (peak * 0.85), 0, 1) ** 1.1
    w_red = np.clip((d - peak * 0.35) / (band - peak * 0.35), 0, 1) ** 0.55
    wsum = w_white + w_orange + w_red + 1e-6
    from genlab_core.action.effects._ops import WHITE

    rgb = (
        (w_white[..., None] * WHITE + w_orange[..., None] * ORANGE + w_red[..., None] * RED)
        / wsum[..., None]
    ) / 255.0

    flick = 0.82 + 0.36 * np.roll(noise(303, (h, w)), -int(dy * 2.3) % h, axis=0)
    amp = (0.86 + 0.50 * phase) * float(kit_value(kit, "aura_gain", 0.78)) * flick
    lay = np.clip(rgb * (inten * amp)[..., None], 0, 1)
    mix = float(kit_value(kit, "bloom_mix", 0.26))
    lay = np.clip(lay + np.stack([blur(lay[..., c], 22.0) for c in range(3)], -1) * mix, 0, 1)

    # Guard the body on the UNDISPLACED distance. Masking with a blur of the
    # matte let a 34 px displacement pull the band INSIDE the silhouette, which
    # at close-up carved visible chunks out of arms and head. Ramp from the edge
    # itself: a wide guard plus an offset start leaves a dark ring where neither
    # body nor flame is lit, and that reads as a black fringe.
    guard = np.clip(d_true / 6.0, 0, 1)
    lay = lay * guard[..., None] * (1.0 - np.clip(blur(matte, 3.0), 0, 1))[..., None]
    wgt = (vertical_weight(matte) * limb_weight(matte, float(kit_value(kit, "limb_weight", 0.7))))[
        ..., None
    ]
    return np.clip(lay * wgt, 0, 1)


def inner_glow(matte: np.ndarray, kit: dict) -> np.ndarray:
    """Let the aura bleed INTO the body. A glow that stops dead at the
    silhouette is a sticker; one that spills reads as light falling on it."""
    px = float(kit_value(kit, "inner_spill_px", 18.0))
    amount = float(kit_value(kit, "inner_spill_amount", 0.30))
    ms = np.clip(blur(matte, 12.0), 0, 1)
    rim = np.clip(ms - erode(ms, px), 0, 1)
    return np.clip(blur(rim, px * 0.7)[..., None] * (ORANGE / 255.0) * amount, 0, 1)


# ───────────────────────────── bolts ────────────────────────────────────────


def edge_launch(matte: np.ndarray, n: int, rng) -> list[tuple[float, float, float, float]]:
    """Launch points on the silhouette edge, with outward SURFACE normals.

    The normal comes from the gradient of a blurred matte, not from the ray out
    of the subject's centroid. On a convex blob the two agree; on a real body
    they do not -- at an armpit, between the legs, or anywhere the silhouette is
    concave, the centroid ray points across the body while the gradient points
    out of the surface the bolt is leaving. A port that used the centroid ray
    put bolts through the torso.
    """
    e = np.clip(dilate(matte, 3) - erode(matte, 3), 0, 1)
    ys, xs = np.nonzero(e > 0.5)
    if len(xs) < n:
        return []
    gy, gx = np.gradient(blur(matte, 11.0))
    out = []
    for i in rng.choice(len(xs), size=n, replace=False):
        x, y = int(xs[i]), int(ys[i])
        nx, ny = -float(gx[y, x]), -float(gy[y, x])
        length = math.hypot(nx, ny) + 1e-6
        out.append((x, y, nx / length, ny / length))
    return out


def bolts(
    matte: np.ndarray,
    kit: dict,
    rng,
    *,
    subj_h: float | None = None,
    prev_core: np.ndarray | None = None,
    scale: int = 2,
) -> tuple[np.ndarray, np.ndarray]:
    """One long sweeping stroke per call, not a spray of sparks.

    Length is `bolt_len_per_h` = 1.95 x subject height -- the value that
    rendered every approved reel, taken from `v8_look.mega_bolt`'s signature
    default because no build ever passed the argument.

    That function's docstring claims it is "matched to the reference's LARGEST
    component (0.834 x subject height, 69 px mean thickness)". It is not: its
    own default is 1.95, and 1.95 is what shipped. This port originally carried
    the docstring's number across and drew bolts 2.34x too short. Whether 0.834
    is the better value is filed as an open question, not decided here.

    Returns ``(layer, core)``; pass ``core`` back as ``prev_core`` next frame for
    the afterglow.
    """
    h, w = matte.shape
    subj_h = subj_h or subject_height(matte)
    core_px = float(kit_value(kit, "bolt_core_px", 14))
    glow_px = float(kit_value(kit, "bolt_glow_px", 60))
    len_per_h = float(kit_value(kit, "bolt_len_per_h", 1.95))
    pts = edge_launch(matte, 1, rng)
    im = Image.new("L", (w * scale, h * scale), 0)
    dr = ImageDraw.Draw(im)
    if pts:
        x, y, nx, ny = pts[0]
        total = len_per_h * subj_h * rng.uniform(0.85, 1.15)
        steps = int(rng.integers(7, 11))
        ang = math.atan2(ny, nx)
        turn = rng.normal(0.0, 0.22)
        px, py = float(x), float(y)
        poly = [(px * scale, py * scale)]
        for _ in range(steps):
            ang += turn + rng.normal(0.0, 0.08)
            px += math.cos(ang) * total / steps
            py += math.sin(ang) * total / steps
            poly.append((px * scale, py * scale))
        dr.line(poly, fill=255, width=max(1, int(core_px * scale)), joint="curve")
        for _ in range(2):
            j = int(rng.integers(1, max(2, len(poly) - 1)))
            bx, by = poly[j][0] / scale, poly[j][1] / scale
            ba = ang + rng.uniform(-1.1, 1.1)
            bp = [(bx * scale, by * scale)]
            for _ in range(4):
                bx += math.cos(ba) * total * 0.20
                by += math.sin(ba) * total * 0.20
                ba += rng.normal(0, 0.25)
                bp.append((bx * scale, by * scale))
            dr.line(bp, fill=255, width=max(1, int(core_px * 0.55 * scale)), joint="curve")
    core = np.asarray(im.resize((w, h), Image.LANCZOS), np.float32) / 255.0
    if prev_core is not None:
        core = np.maximum(core, prev_core * float(kit_value(kit, "bolt_afterglow", 0.45)))
    glow = np.clip(blur(core, glow_px * 0.5) * 3.4, 0, 1)
    lay = np.clip(glow[..., None] * (RED / 255.0), 0, 1)
    lay = screen(lay, np.clip(blur(core, 2.5) * 1.5, 0, 1)[..., None] * (BOLT / 255.0))
    return np.clip(lay, 0, 1), core


# ───────────────────────────── impact ───────────────────────────────────────


@dataclass
class Debris:
    """Outward AND downward, fewer and bigger, plus a handful of slow chunks.

    Particles are drawn as STREAKS (``p`` back to ``p - v``), not dots: at these
    speeds a round particle reads as a floating speck and a streak reads as
    something thrown. They carry two materials -- 45% grey mat debris, the rest
    hot orange -- and four oversized slow chunks, because a spray of uniform
    particles reads as a particle system rather than as a mat coming apart.

    An earlier pass of this port drew uniform 2.4 px orange dots with no fade,
    no size variation and no mat chunks. That is a different effect, not this
    one; the fields below are the approved ones.
    """

    p: np.ndarray
    v: np.ndarray
    life: np.ndarray
    size: np.ndarray
    col: np.ndarray
    g: float
    origin: tuple[float, float]

    @classmethod
    def at(cls, cx: float, cy: float, kit: dict, seed: int = 9) -> Debris:
        n = int(kit_value(kit, "debris_count", 150))
        lo, hi = kit_value(kit, "debris_life_frames", [20, 38])
        rng = np.random.default_rng(seed)
        a = rng.uniform(-math.pi, 0.35 * math.pi, n)  # outward, biased down
        sp = rng.uniform(7.0, 30.0, n)
        p = np.stack([np.full(n, float(cx)), np.full(n, float(cy))], 1)
        v = np.stack([np.cos(a) * sp, np.abs(np.sin(a)) * sp * 0.5 - rng.uniform(0, 7, n)], 1)
        life = rng.integers(int(lo), int(hi), n)
        size = rng.uniform(4.0, 10.0, n)
        mat = rng.random(n) < float(kit_value(kit, "debris_mat_frac", 0.45))
        col = np.where(
            mat[:, None],
            np.array([150.0, 140.0, 132.0])[None, :],
            np.array([255.0, 92.0, 70.0])[None, :],
        )
        big = rng.choice(n, size=4, replace=False)  # the mat chunks
        size[big] = rng.uniform(16.0, 30.0, 4)
        v[big] *= 0.45
        life[big] = 40
        return cls(p=p, v=v, life=life, size=size, col=col, g=1.5, origin=(float(cx), float(cy)))

    def at_age(self, age: int):
        """Returns ``(head, tail, alive, fade)`` -- the streak runs head->tail."""
        gv = np.array([0.0, self.g])[None, :]
        v = self.v + gv * age
        p = self.p + self.v * age + 0.5 * gv * age * age
        return p, p - v, age < self.life, 1.0 - np.clip(age / self.life, 0, 1)


def debris(deb: Debris, age: int, matte: np.ndarray, *, scale: int = 2) -> np.ndarray:
    """Draw the particles at ``age`` frames after impact, behind the subject."""
    h, w = matte.shape
    if age < 0:
        return np.zeros((h, w, 3), np.float32)
    head, tail, alive, fade = deb.at_age(age)
    if not alive.any():
        return np.zeros((h, w, 3), np.float32)
    im = Image.new("RGB", (w * scale, h * scale), (0, 0, 0))
    dr = ImageDraw.Draw(im)
    for i in np.nonzero(alive)[0]:
        c = tuple(int(v * fade[i]) for v in deb.col[i])
        dr.line(
            [(head[i, 0] * scale, head[i, 1] * scale), (tail[i, 0] * scale, tail[i, 1] * scale)],
            fill=c,
            width=max(1, int(deb.size[i] * scale)),
        )
    lay = np.asarray(im.resize((w, h), Image.LANCZOS), np.float32) / 255.0
    # Behind the body. This is the APPROVED mask -- a sigma-5 blur of the matte,
    # which leaks a particle at up to 55% strength across the ~28k pixels just
    # inside the silhouette edge. An earlier pass here hardened this to
    # `dilate(matte, 10) < 0.5` to satisfy a 2%-leak assertion, but that
    # assertion was invented during the port, not measured off the reference,
    # and no approved render ever met it. Hardening it is a CHANGE to propose,
    # not a defect to fix inside a port.
    lay = lay * (1.0 - np.clip(blur(matte, 5.0), 0, 1))[..., None]
    return np.clip(lay * 2.0, 0, 1)


def body_flash(matte: np.ndarray, kit: dict, amount: float = 0.92) -> np.ndarray:
    """Body to white, inside an orange halo thrown into the surrounding air.

    Two parts, and the halo is the one that makes it read as light in the room
    rather than a white sticker on the body: a 40 px dilate blurred by 30 and
    screened in orange at 0.85, THEN the silhouette itself taken to white.

    Returned as a layer. Screening a white layer at alpha `a` is exactly the
    lerp-to-white the approved build wrote by hand -- ``1-(1-x)(1-a)`` equals
    ``x(1-a) + a`` -- so composing this layer reproduces it.
    """
    halo_amt = float(kit_value(kit, "flash_halo", 0.85))
    core = np.clip(blur(matte, 3.0), 0, 1)[..., None]
    halo = blur(dilate(matte, 40), 30.0)[..., None]
    return np.clip(screen(halo * halo_amt * (ORANGE / 255.0), core * amount), 0, 1)


def heat_shimmer(
    frame: np.ndarray, matte: np.ndarray, t_frame: int, amount: float = 7.0, band: float = 160.0
) -> np.ndarray:
    """Displacement around the aura for the frames after the hit.

    Returns a FRAME (it warps pixels rather than adding light).

    The `(d > 0)` factor is load-bearing and not decoration: `dist_outside` is
    zero INSIDE the silhouette, so `1 - d/band` is 1.0 there -- the maximum
    displacement -- and without the gate the shimmer warps the subject's own
    body at full strength instead of the air around it. A port that dropped it
    measured 1.000 mean interior weight against the approved 0.012.
    """
    h, w = matte.shape
    d = dist_outside(matte, max_px=int(band * 1.3))
    near = np.clip(1.0 - d / band, 0, 1) * (d > 0)
    n = np.roll(noise(404, (h, w)), -int(t_frame * 11) % h, axis=0)
    off = ((n - 0.5) * 2.0 * amount * near).astype(np.float32)
    yy, xx = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
    sx = np.clip(xx + off, 0, w - 1).astype(np.int32)
    sy = np.clip(yy + off * 0.6, 0, h - 1).astype(np.int32)
    return frame[sy, sx]


def directional_blur(
    frame: np.ndarray, prev: np.ndarray | None, kit: dict, *, scale: int = 9
) -> np.ndarray:
    """Smear along the dominant motion vector, estimated from the frame diff.

    The reference's fast frames smear; an un-blurred render snaps. Applied on
    the handful of frames before the finish (`IMPACT-6 .. IMPACT`), which is
    where the motion actually is.

    This effect was MISSING from the first pass of this module -- both approved
    builds call it at `px=12.0` on those frames and the port shipped only
    `trails()`, which is a different thing (ghosts of the silhouette, not a
    smear of the frame).
    """
    px = float(kit_value(kit, "fast_blur_px", 12.0))
    if prev is None or px <= 0:
        return frame
    d = np.abs(frame - prev).mean(2)
    if d.mean() < 1.0:  # nothing moved; a smear here would just soften the frame
        return frame
    gy, gx = np.gradient(d)
    vx, vy = float(gx.mean()), float(gy.mean())
    n = math.hypot(vx, vy)
    if n < 1e-6:
        vx, vy, n = 1.0, 0.0, 1.0
    vx, vy = vx / n, vy / n
    acc = np.zeros_like(frame)
    w = 0.0
    for k in range(-scale, scale + 1):
        sx, sy = int(round(vx * px * k / scale)), int(round(vy * px * k / scale))
        acc += np.roll(np.roll(frame, sy, 0), sx, 1)
        w += 1.0
    return acc / w


def trails(prev_mattes: list[np.ndarray], kit: dict) -> np.ndarray:
    """Three ghosts of the recent silhouette, aura-tinted."""
    if not prev_mattes:
        return np.zeros((1, 1, 3), np.float32)
    ops = kit_value(kit, "trail_opacities", [0.35, 0.20, 0.10])
    h, w = prev_mattes[0].shape
    acc = np.zeros((h, w), np.float32)
    for m, o in zip(reversed(prev_mattes[-3:]), ops, strict=False):
        acc = np.maximum(acc, np.clip(blur(m, 6.0), 0, 1) * float(o))
    return np.clip(acc[..., None] * (ORANGE / 255.0), 0, 1)


def moved_fast(prev: np.ndarray | None, cur: np.ndarray, kit: dict) -> bool:
    """Trails are earned by motion, not drawn every frame."""
    if prev is None:
        return False
    a, b = centroid(prev), centroid(cur)
    if a is None or b is None:
        return False
    return math.hypot(b[0] - a[0], b[1] - a[1]) > float(kit_value(kit, "fast_px", 12))


# ───────────────────────────── face + bloom ─────────────────────────────────


def protect_face(layer: np.ndarray, head_region: np.ndarray | None, kit: dict) -> np.ndarray:
    """Mask an effect layer inside the head region; the halo resumes below the jaw.

    The face is a protected region in EVERY template -- a silhouette-hugging
    effect hugs the head unless told not to. The earlier version applied the
    halo across the WHOLE head mask, which put 40% of a flame back onto the
    face; it belongs in the head region but OFF the body.
    """
    if head_region is None:
        return layer
    # `head_region` is the head MINUS the body below the jaw -- the caller
    # builds it that way (YuNet box inside the subject bbox, dilated 15 px,
    # feathered 20). So masking it wholly is correct, and the halo resuming
    # below the jaw is a property of the REGION, not of a second coefficient
    # applied here. head_halo is the strength the renderer gives that
    # below-jaw band; it is deliberately not used in this function.
    keep = 1.0 - np.clip(head_region, 0, 1)
    k = keep[..., None] if layer.ndim == 3 else keep
    return np.clip(layer * k, 0, 1)


def bloom(frame: np.ndarray, kit: dict, sigma: float = 26.0, amount: float = 0.34) -> np.ndarray:
    """Highlights only, damped when the frame is already hot.

    Without the damping a frame that is already bright blooms into a white
    field -- the effect stops adding highlights and starts removing the image.
    """
    thr = float(kit_value(kit, "bloom_thresh", 0.85))
    hot_cap = float(kit_value(kit, "bloom_hot_frac", 0.055))
    x = frame / 255.0
    y = luma(x)
    hot = float((y > thr).mean())
    if hot <= 0.0:
        return np.zeros_like(x)
    damp = 1.0 if hot <= hot_cap else hot_cap / hot
    hi = x * np.clip((y - thr) / (1.0 - thr), 0, 1)[..., None]
    return np.clip(blur(hi, sigma) * amount * damp, 0, 1)
