"""CONTENT-13 — rebuilt to the reference's measured pixel statistics.

Targets, from v6/ref_stats.json, normalised by SUBJECT HEIGHT so they transfer
from a 1920x1080 reference to a 1080x1920 reel:

    world luma            37.1        (v5 was 62.5 -- 1.68x too bright)
    subject luma          76.9        subject/world contrast 2.07x (v5: 1.55x)
    band width       0.251 x subj_h   (v5: 0.173 -- 1.4x too narrow)
    band peak at     0.085 x subj_h   (v5: 0.052 -- flame core too close in)
    band peak red        128.2        (v5: 135.8 -- already right)
    core luma            125.7        (v5: 131.1 -- already right)
    bolts per frame        4.4        (v5: 22.9 -- 5.2x TOO MANY)
    bolt length      0.307 x subj_h   (v5: 0.067 -- 4.6x too short)
    bolt thickness   0.058 x subj_h   (v5: 0.009 -- 6.4x too thin)

So the correction is not uniform: the band was slightly narrow, the intensity
was already right, and the bolts were five times too many and five times too
small. A written spec ("more arcs, brighter") would have moved three of those
the wrong way.
"""
import math
import numpy as np
from PIL import Image, ImageFilter, ImageDraw

OW, OH = 1080, 1920
WHITE  = np.array([255.0, 250.0, 242.0], np.float32)
# The reference's bolts are PINK end to end, not white-cored. A white core has
# red-excess ~0 by definition (R=G=B), so it is invisible to the very statistic
# the gate is measured in -- and it fragmented every stroke into glow-only
# pieces, which read as 16 short bolts instead of 4 long ones.
BOLT   = np.array([255.0, 118.0, 150.0], np.float32)
ORANGE = np.array([255.0,  92.0,  54.0], np.float32)   # crimson, not amber
RED    = np.array([255.0,  46.0,  58.0], np.float32)
LUM    = np.array([0.299, 0.587, 0.114], np.float32)

def blur(a, s):
    if s <= 0: return a
    return np.asarray(Image.fromarray(np.clip(a*255,0,255).astype(np.uint8))
                      .filter(ImageFilter.GaussianBlur(s)), np.float32)/255.0

def dilate(m, r): return (blur(m, r*0.55) > 0.10).astype(np.float32)
def erode(m, r):  return 1.0 - dilate(1.0 - m, r)
def screen(b, a): return 1.0 - (1.0 - b) * (1.0 - a)

# --------------------------------------------------------- distance field ---
def dist_outside(m, max_px=360, scale=4):
    """Distance from the silhouette, in pixels, for everything outside it.

    Built by iterated 3x3 max-filter on a 1/4-scale mask: each pass grows the
    region by one low-res pixel, i.e. `scale` full-res pixels, so the pass index
    at which a pixel is first covered IS its distance. A blur cannot substitute
    -- the aura needs a real radial coordinate to run a colour ramp along.
    """
    h, w = OH // scale, OW // scale
    small = Image.fromarray(((m > 0.5) * 255).astype(np.uint8)).resize((w, h), Image.NEAREST)
    cur = np.asarray(small, np.float32) / 255.0
    d = np.full((h, w), np.inf, np.float32)
    d[cur > 0.5] = 0.0
    img = small
    for i in range(1, max_px // scale + 1):
        img = img.filter(ImageFilter.MaxFilter(3))
        nxt = np.asarray(img, np.float32) / 255.0
        newly = (nxt > 0.5) & ~np.isfinite(d)
        d[newly] = i * scale
        if not (nxt > 0.5).any(): break
    d[~np.isfinite(d)] = max_px + scale
    up = np.asarray(Image.fromarray(np.clip(d, 0, 65535).astype(np.uint16))
                    .resize((OW, OH), Image.BILINEAR), np.float32)
    return up

# ------------------------------------------------------------------ noise ---
_N = {}
def noise(seed, octaves=(10, 26)):
    if seed in _N: return _N[seed]
    rng = np.random.default_rng(seed)
    acc = np.zeros((OH, OW), np.float32); amp = 1.0; tot = 0.0
    for o in octaves:
        s = rng.random((o, o)).astype(np.float32)
        up = np.asarray(Image.fromarray((s*255).astype(np.uint8))
                        .resize((OW, OH), Image.BICUBIC), np.float32)/255.0
        acc += up*amp; tot += amp; amp *= 0.55
    _N[seed] = acc/tot
    return _N[seed]

# ------------------------------------------------------------- §1 grading ---
# world_luma / subj_luma solved from the measured output, not guessed: at
# 0.44 / 1.10 the world came back at 24.0 against the reference's 37.1 and the
# subject at 99.7 against 76.9 -- over-separated in BOTH directions at once.
def grade_inverted(f, m, world_luma=0.62, world_sat=0.38, subj_luma=0.88,
                   subj_sat=1.15, feather=10.0, vignette_amt=0.55):
    """Dim the WORLD, light the SUBJECT. The matte is what makes this possible.

    Replaces CONTENT-12's rule, which was the wrong way round: the reference
    does not put effects on bright footage, it darkens everything that is not
    the fighter and lets the aura be the brightest thing in frame.
    """
    x = f / 255.0
    g = (x @ LUM)[..., None]
    world = g + (x - g) * world_sat
    world = world * world_luma
    world = world + np.array([-0.012, 0.0, 0.030], np.float32) * (1.0 - g) * 0.8
    yy, xx = np.ogrid[:OH, :OW]
    r = np.sqrt(((xx - OW/2)/(OW*0.78))**2 + ((yy - OH/2)/(OH*0.72))**2)
    world = world * (1.0 - vignette_amt * np.clip(r, 0, 1)**1.5)[..., None]
    subj = np.clip(g + (x - g) * subj_sat, 0, 1) * subj_luma
    a = np.clip(blur(np.clip(m, 0, 1), feather), 0, 1)[..., None]
    return np.clip(world*(1-a) + subj*a, 0, 1) * 255.0

# ---------------------------------------------------------------- §2 aura ---
def flame_aura(f, m, t_frame, beat, subj_h,
               band_per_h=0.251, peak_per_h=0.070, scroll_px_s=250.0,
               displace_px=34.0, gain=0.78, white_px=8.0, bloom_mix=0.26,
               band_cap=1.0):
    # gain / white_px / bloom_mix / peak_per_h were grid-searched against the
    # reference's four band numbers, not chosen by eye:
    #   band_peak 112.0 vs 128.2 (-13%)   core_luma 145.4 vs 125.7 (+16%)
    #   band_w    295.5 vs 320.2 ( -8%)   peak_at   122.5 vs 108.5 (+13%)
    """A wide displaced band with a white-hot core, not a rim.

    The shell is a DISTANCE RAMP, displaced by turbulent noise that scrolls
    upward, so its edge licks every frame. A dilated matte is static and reads
    as an outline no matter how thick you make it.
    """
    # The subject-height normalisation transfers at 1.78x and BREAKS at 2.5x.
    # At close-up the subject nearly fills the frame, so 0.251 x subject height
    # is 432 px = 40% of frame width, against the reference's 218 px = 11.4%.
    # The flame stopped being a band around a body and became the whole frame.
    # Capped by the reference's FRAME-WIDTH share, which is the invariant that
    # survives a change of shot size.
    band = float(np.clip(band_per_h * subj_h, 60.0, 0.1135 * OW * band_cap))
    peak = float(np.clip(peak_per_h * subj_h, 20.0, 0.0385 * OW * band_cap))
    d = dist_outside(m, max_px=int(band*1.25))
    dy = (t_frame * scroll_px_s / 30.0)
    n1 = np.roll(noise(101), -int(dy) % OH, axis=0)
    n2 = np.roll(noise(202, (40, 90)), -int(dy*1.7) % OH, axis=0)   # high-frequency
                                                                    # octaves give the
                                                                    # edge its tongues
    d_true = d.copy()          # undisplaced: the inner guard must use THIS
    d = d - (n1*2.0 - 1.0 + (n2*2.0 - 1.0)*0.85) * displace_px

    # Intensity falls MONOTONICALLY from the silhouette edge outward; colour
    # walks white-hot -> orange -> red along the same axis. The first version
    # summed three overlapping lobes, which clipped to 1.0 across 200 px and
    # rendered as a solid white wall that swallowed the subject.
    # The 10%-of-peak width (0.251 x subj_h) is the extent of the FAINT tail,
    # not the visible flame. Running full intensity across it produced a 300 px
    # amber halo; the reference's flame is tight to the contour and its energy
    # is essentially gone by peak_at. Exponent 4.2 puts it there.
    inten = np.clip(1.0 - d/band, 0, 1) ** 4.2 * (d > 0)
    w_white  = np.clip(1.0 - d/white_px, 0, 1) ** 1.2
    w_orange = np.clip(1.0 - np.abs(d - peak*0.55)/(peak*0.85), 0, 1) ** 1.1
    w_red    = np.clip((d - peak*0.35)/(band - peak*0.35), 0, 1) ** 0.55
    wsum = w_white + w_orange + w_red + 1e-6
    rgb = ((w_white[..., None]*WHITE + w_orange[..., None]*ORANGE
            + w_red[..., None]*RED) / wsum[..., None]) / 255.0

    flick = 0.82 + 0.36 * np.roll(noise(303), -int(dy*2.3) % OH, axis=0)
    amp = (0.86 + 0.50*beat) * gain * flick

    lay = np.clip(rgb * (inten*amp)[..., None], 0, 1)
    lay = np.clip(lay + np.stack([blur(lay[..., c], 22.0) for c in range(3)], -1)*bloom_mix, 0, 1)
    # Guard the body on the UNDISPLACED distance. Masking with a blur of the
    # matte let a 34 px displacement pull the band inside the silhouette; at
    # close-up magnification that carved visible chunks out of the arms and head.
    # Ramp from the edge itself, not from 3 px out, and guard with a tight blur:
    # a wide guard plus an offset start left a dark ring where neither the body
    # nor the flame was lit, which reads as a black fringe around the silhouette.
    guard = np.clip(d_true / 6.0, 0, 1)
    lay = lay * guard[..., None] * (1.0 - np.clip(blur(m, 3.0), 0, 1))[..., None]

    inner = np.clip(blur(m, 26.0) - erode(m, 40), 0, 1)[..., None] * 0.34 * (0.9+0.4*beat)
    x = screen(f/255.0, lay)
    x = screen(x, inner * (ORANGE/255.0))
    return np.clip(x, 0, 1) * 255.0

# --------------------------------------------------------------- §3 bolts ---
def edge_launch(m, n, rng):
    e = np.clip(dilate(m, 3) - erode(m, 3), 0, 1)
    ys, xs = np.nonzero(e > 0.5)
    if len(xs) < n: return []
    gy, gx = np.gradient(blur(m, 11.0))
    out = []
    for i in rng.choice(len(xs), size=n, replace=False):
        x, y = int(xs[i]), int(ys[i])
        nx, ny = -float(gx[y, x]), -float(gy[y, x])
        L = math.hypot(nx, ny) + 1e-6
        out.append((x, y, nx/L, ny/L))
    return out

def bolts(f, m, rng, subj_h, n=3, len_per_h=0.34, core_px=5, glow_px=34, turn_sd=0.34,
          prev=None, afterglow=0.45, S=2):
    """2-4 long SWEEPING strokes, not a spray of sparks.

    Measured off the reference: 4.4 per frame, 0.307 x subject height long,
    0.058 x subject height thick including glow. v5 drew 22.9 per frame at
    0.067 long -- five times too many and five times too small.
    """
    L0 = len_per_h * subj_h
    im = Image.new("L", (OW*S, OH*S), 0)
    d = ImageDraw.Draw(im)
    for (x, y, nx, ny) in edge_launch(m, n, rng):
        total = L0 * rng.uniform(0.75, 1.25)
        steps = int(rng.integers(5, 8))
        ang = math.atan2(ny, nx)
        turn = rng.normal(0.0, turn_sd)       # one consistent sweep, not jitter
        px, py = float(x), float(y)
        poly = [(px*S, py*S)]
        for k in range(steps):
            ang += turn + rng.normal(0.0, 0.10)
            px += math.cos(ang)*total/steps
            py += math.sin(ang)*total/steps
            poly.append((px*S, py*S))
        d.line(poly, fill=255, width=max(1, int(core_px*S)), joint="curve")
        if rng.random() < 0.6 and len(poly) > 3:       # one branch
            j = int(rng.integers(1, len(poly)-1))
            bx, by = poly[j][0]/S, poly[j][1]/S
            ba = ang + rng.uniform(-1.2, 1.2)
            bp = [(bx*S, by*S)]
            for k in range(3):
                bx += math.cos(ba)*total*0.22; by += math.sin(ba)*total*0.22
                ba += rng.normal(0, 0.3); bp.append((bx*S, by*S))
            d.line(bp, fill=255, width=max(1, int(core_px*0.6*S)), joint="curve")
    core = np.asarray(im.resize((OW, OH), Image.LANCZOS), np.float32)/255.0
    if prev is not None: core = np.maximum(core, prev*afterglow)
    glow = np.clip(blur(core, glow_px*0.5) * 3.6, 0, 1)
    x = f/255.0
    x = screen(x, glow[..., None] * (RED/255.0))
    x = screen(x, np.clip(blur(core, 2.0)*1.5, 0, 1)[..., None] * (BOLT/255.0))
    return np.clip(x, 0, 1)*255.0, core

# -------------------------------------------------------------- §5 impact ---
def full_red(f, amount=0.72):
    x = f/255.0
    return np.clip(screen(x*(1-0.35*amount), np.ones_like(x)*(RED/255.0)*amount), 0, 1)*255.0

def body_flash(f, m, amount=0.90):
    core = np.clip(blur(m, 3.0), 0, 1)[..., None]
    halo = blur(dilate(m, 40), 30.0)[..., None]
    x = f/255.0
    x = screen(x, halo*0.85*(ORANGE/255.0))
    x = x*(1-core*amount) + core*amount
    return np.clip(x, 0, 1)*255.0

def heat_shimmer(f, m, t_frame, amount=7.0, band=160.0):
    """Displacement around the aura for the frames after the hit."""
    d = dist_outside(m, max_px=int(band*1.3))
    w = np.clip(1.0 - d/band, 0, 1) * (d > 0)
    n = np.roll(noise(404), -int(t_frame*11) % OH, axis=0)
    off = ((n - 0.5) * 2.0 * amount * w).astype(np.float32)
    yy, xx = np.meshgrid(np.arange(OH), np.arange(OW), indexing="ij")
    sx = np.clip(xx + off, 0, OW-1).astype(np.int32)
    sy = np.clip(yy + off*0.6, 0, OH-1).astype(np.int32)
    return f[sy, sx]

def bloom_high(f, thr=0.90, sigma=18, amount=0.28):
    x = f/255.0
    y = x @ LUM
    hi = x * np.clip((y-thr)/(1.0-thr), 0, 1)[..., None]
    return np.clip(screen(x, blur(hi, sigma)*amount), 0, 1)*255.0
