"""CONTENT-15 — finish. Bathed, not cut out.

Three measured defects drove every change here:
    offband_red    15.79 ref vs  7.90 v3   the world was half as hazy
    boundary_step  57.94 ref vs 84.13 v3   our grade edge was 45% harder
    bolt_len/h      0.834 ref vs  0.259 v3  the bolt was a third the length
"""
import math
import numpy as np
from PIL import Image, ImageFilter, ImageDraw
from v6_look import (OW, OH, WHITE, ORANGE, RED, BOLT, LUM, blur, dilate, erode,
                     screen, dist_outside, noise, flame_aura, full_red,
                     heat_shimmer, edge_launch)

# --------------------------------------------------------- §1 ambient haze ---
# Measured after the first pass: corner luma came back at 45.2 against the
# reference's 32.9 (+37%) and off-band red at 32.2 against 15.8 (+104%). The
# haze, the ambient lift and the world level stacked. Pulled back on all three.
def grade_bathed(f, m, world_luma=0.30, world_sat=0.22, subj_luma=0.92,
                 subj_sat=1.15, feather=34.0, vignette_amt=0.62,
                 ambient_red=0.018):
    """Dim and RED, not black, and with a WIDE grade boundary.

    v3 used a 10 px feather; the step across the matte edge measured 84 vs the
    reference's 58, which is the number behind "reads as a cutout". 30 px.
    """
    x = f / 255.0
    g = (x @ LUM)[..., None]
    world = (g + (x - g)*world_sat) * world_luma
    # a red ambient sitting on the shadows: the cage, ropes and crowd survive as
    # faint shapes under red instead of going to black
    world = world + (RED/255.0)[None, None, :] * ambient_red * (1.0 - g)
    yy, xx = np.ogrid[:OH, :OW]
    r = np.sqrt(((xx-OW/2)/(OW*0.80))**2 + ((yy-OH/2)/(OH*0.75))**2)
    world = world * (1.0 - vignette_amt*np.clip(r, 0, 1)**1.5)[..., None]
    subj = np.clip(g + (x - g)*subj_sat, 0, 1) * subj_luma
    a = np.clip(blur(np.clip(blur(m, 12.0), 0, 1), feather), 0, 1)[..., None]
    return np.clip(world*(1-a) + subj*a, 0, 1) * 255.0

def aura_ambient(f, m, subj_h, radius=300.0, opacity=0.08):
    """A very large soft bloom of the aura over the WHOLE frame. This is what
    turns a lit subject on a black field into a scene bathed in energy."""
    d = dist_outside(m, max_px=int(0.30*subj_h))
    near = np.clip(1.0 - d/(0.30*subj_h), 0, 1)
    haze = blur(near, radius)
    return np.clip(screen(f/255.0, haze[..., None]*(RED/255.0)*opacity), 0, 1)*255.0

def inner_spill(f, m, px=18.0, amount=0.30):
    """Let the aura bleed INTO the body. A glow that stops dead at the silhouette
    is a sticker; one that spills reads as light falling on the subject."""
    ms = np.clip(blur(m, 12.0), 0, 1)
    rim = np.clip(ms - erode(ms, px), 0, 1)
    return np.clip(screen(f/255.0, blur(rim, px*0.7)[..., None]*(ORANGE/255.0)*amount), 0, 1)*255.0

def bloom_soft(f, thr=0.85, sigma=26, amount=0.34):
    x = f/255.0
    y = x @ LUM
    hi = x * np.clip((y-thr)/(1.0-thr), 0, 1)[..., None]
    return np.clip(screen(x, blur(hi, sigma)*amount), 0, 1)*255.0

# ------------------------------------------------------- §3 one huge bolt ---
def mega_bolt(f, m, rng, subj_h, len_per_h=1.95, core_px=14, glow_px=60,
              branches=2, prev=None, afterglow=0.45, S=2):
    """ONE stroke, matched to the reference's LARGEST component (0.834 x subject
    height, 69 px mean thickness) rather than to the mean over fragments."""
    pts = edge_launch(m, 1, rng)
    im = Image.new("L", (OW*S, OH*S), 0)
    d = ImageDraw.Draw(im)
    if pts:
        x, y, nx, ny = pts[0]
        total = len_per_h*subj_h*rng.uniform(0.85, 1.15)
        steps = int(rng.integers(7, 11))
        ang = math.atan2(ny, nx); turn = rng.normal(0.0, 0.22)
        px, py = float(x), float(y); poly = [(px*S, py*S)]
        for k in range(steps):
            ang += turn + rng.normal(0.0, 0.08)
            px += math.cos(ang)*total/steps; py += math.sin(ang)*total/steps
            poly.append((px*S, py*S))
        d.line(poly, fill=255, width=max(1, int(core_px*S)), joint="curve")
        for b in range(branches):
            j = int(rng.integers(1, max(2, len(poly)-1)))
            bx, by = poly[j][0]/S, poly[j][1]/S
            ba = ang + rng.uniform(-1.1, 1.1); bp = [(bx*S, by*S)]
            for k in range(4):
                bx += math.cos(ba)*total*0.20; by += math.sin(ba)*total*0.20
                ba += rng.normal(0, 0.25); bp.append((bx*S, by*S))
            d.line(bp, fill=255, width=max(1, int(core_px*0.55*S)), joint="curve")
    core = np.asarray(im.resize((OW, OH), Image.LANCZOS), np.float32)/255.0
    if prev is not None: core = np.maximum(core, prev*afterglow)
    glow = np.clip(blur(core, glow_px*0.5)*3.4, 0, 1)
    x = f/255.0
    x = screen(x, glow[..., None]*(RED/255.0))
    x = screen(x, np.clip(blur(core, 2.5)*1.5, 0, 1)[..., None]*(BOLT/255.0))
    return np.clip(x, 0, 1)*255.0, core

# ---------------------------------------------------------- §4 debris -------
class Debris:
    """Outward AND downward, fewer and bigger, plus a handful of slow chunks.
    Masked BEHIND the subject so nothing falls across the face."""
    def __init__(self, cx, cy, n=150, seed=9):
        rng = np.random.default_rng(seed)
        a = rng.uniform(-math.pi, 0.35*math.pi, n)          # outward, biased down
        sp = rng.uniform(7.0, 30.0, n)
        self.p = np.stack([np.full(n, float(cx)), np.full(n, float(cy))], 1)
        self.v = np.stack([np.cos(a)*sp, np.abs(np.sin(a))*sp*0.5 - rng.uniform(0, 7, n)], 1)
        self.life = rng.integers(20, 38, n)
        self.size = rng.uniform(4.0, 10.0, n)
        mat = rng.random(n) < 0.45
        self.col = np.where(mat[:, None], np.array([150., 140., 132.])[None, :],
                                          np.array([255., 92., 70.])[None, :])
        big = rng.choice(n, size=4, replace=False)           # the mat chunks
        self.size[big] = rng.uniform(16.0, 30.0, 4)
        self.v[big] *= 0.45
        self.life[big] = 40
        self.g = 1.5
    def at(self, age):
        v = self.v + np.array([0.0, self.g])[None, :]*age
        p = self.p + self.v*age + 0.5*np.array([0.0, self.g])[None, :]*age*age
        return p, p - v, age < self.life, 1.0 - np.clip(age/self.life, 0, 1)

def draw_debris(f, parts, age, m, S=2):
    p, q, alive, fade = parts.at(age)
    if not alive.any(): return f
    im = Image.new("RGB", (OW*S, OH*S), (0, 0, 0))
    d = ImageDraw.Draw(im)
    for i in np.nonzero(alive)[0]:
        c = tuple(int(v*fade[i]) for v in parts.col[i])
        d.line([(p[i,0]*S, p[i,1]*S), (q[i,0]*S, q[i,1]*S)],
               fill=c, width=max(1, int(parts.size[i]*S)))
    lay = np.asarray(im.resize((OW, OH), Image.LANCZOS), np.float32)/255.0
    lay = lay * (1.0 - np.clip(blur(m, 5.0), 0, 1))[..., None]   # behind the body
    return np.clip(screen(f/255.0, np.clip(lay*2.0, 0, 1)), 0, 1)*255.0

# ------------------------------------------------- §5 directional blur ------
def motion_blur(f, prev, px=11.0, S=9):
    """Smear along the dominant motion vector, estimated from the frame diff.
    The reference's fast frames smear; ours snapped."""
    if prev is None or px <= 0: return f
    d = np.abs(f - prev).mean(2)
    if d.mean() < 1.0: return f
    gy, gx = np.gradient(d)
    vx, vy = float(gx.mean()), float(gy.mean())
    n = math.hypot(vx, vy)
    if n < 1e-6: vx, vy = 1.0, 0.0; n = 1.0
    vx, vy = vx/n, vy/n
    acc = np.zeros_like(f); w = 0.0
    for k in range(-S, S+1):
        sx, sy = int(round(vx*px*k/S)), int(round(vy*px*k/S))
        acc += np.roll(np.roll(f, sy, 0), sx, 1); w += 1.0
    return acc/w
