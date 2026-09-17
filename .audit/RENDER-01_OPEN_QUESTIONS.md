# RENDER-01 — questions the oracle gate raised, for Aditya

Standing rule for the port arc: **no behaviour changes inside a port.** Where the
oracle does something that looks wrong, the port reproduces it and the question
lands here. Each entry says what shipped, what the alternative is, and what it
would cost to decide.

---

## Q1 — bolt length: 0.834 or 1.95 x subject height?

**Ported as:** `bolt_len_per_h = 1.95`.

`v8_look.mega_bolt` defaults to `len_per_h=1.95`, and neither approved build
(`v10_build.py`, `w2_build4.py`) passes the argument — so **1.95 rendered every
approved reel**, including UFC-05 v4 and WWE v6.

Its docstring, however, says it is "matched to the reference's LARGEST component
(0.834 x subject height, 69 px mean thickness)". Both cannot be true. Either the
measurement was taken and never wired in, or it was superseded and the docstring
went stale.

On a 1000 px subject that is a 1950 px stroke against an 834 px one — on a
1080x1920 frame, the difference between a bolt that crosses the whole frame and
one that spans the subject.

**To decide:** render the UFC-05 window at both values and look. ~10 min once
Port 8 can drive the module (the archive makes this runnable). It is a judgement
about how the reel reads, not something a measurement settles — the reference
measurement is what produced 0.834, and 1.95 is what you approved by eye.

---

## Q2 — harden the debris mask?

**Ported as:** the approved `1 - blur(matte, 5.0)`.

That mask passes a particle at up to **55% strength** across the ~28,600 pixels
just inside the silhouette edge, falling to zero about 20 px in (the blur's 3
sigma). So debris does land on the body's edge in every approved reel.

A previous pass of Port 4 hardened this to `dilate(matte, 10) < 0.5` — a clean
cut, zero leak — to satisfy a 2% ceiling. That ceiling was invented during the
port and no approved render has ever met it, so it was reverted.

**Argument for hardening:** debris on the arm is a defect under any reading;
nobody chose 55%, it fell out of a blur radius picked for softness.
**Argument against:** it has shipped in every approved reel and you have not
flagged it, and a hard cut can read as a stencil edge where a feathered one
reads as depth.

**To decide:** one A/B on the UFC-05 impact frames (f13-f52 carry debris).

---

## Q3 — aura `band_peak`: 0.085 vs the approved 0.070 (no action needed yet)

The port carries 0.085; `flame_aura`'s default, used by both approved builds, is
0.070. **Today this cannot bite**: both clamp to the same 41.6 px frame-width cap
for any subject taller than ~594 px, which is every shot size ACTION uses.

`test_the_latent_band_peak_difference_cannot_bite` asserts exactly that, so if
ACTION ever adopts a wider shot the gate fails and this becomes a real decision
rather than a silent divergence.

---

## Q4 — WWE v6 is not reproducible, and that is now permanent

The v6 deliverable cannot be re-rendered: its source clip and its 96 SAM2 mattes
were in an ephemeral scratch dir since cleaned, no source video id was recorded,
and `v7_crop.py` imports `v4_build` / `v4_look` which were never archived.

Nothing recovers it. What it cost: the port gate the packet specified had to be
replaced (with a stricter one, function-level equivalence, but the render-level
proof is gone for good). The archiving rule in `scripts/archive_deliverable.py`
exists so this cannot recur; UFC-05 v4 has been backfilled while its scratch
still existed.
