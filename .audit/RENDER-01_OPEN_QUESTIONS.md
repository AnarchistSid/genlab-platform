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

---

## Q5 — the grade solver has no original to port

`solve_world_multipliers` in `impact.py` is NOT a port. UFC-05's build reads
`w2/grade_solved.json`, and nothing in the archived scratch writes that file --
the solver was an inline heredoc in a session, so there is no code to diff
against. (The one archived solver, `u3_grade.py`, writes a different path with a
different schema and grid-searches `world_luma` alone.)

The reimplementation lands somewhere else, and closer to the targets:

    shipped   world_luma 0.400  subj_luma 1.750  ->  world 39.17  subject 79.46
    port      world_luma 0.443  subj_luma 2.063  ->  world 37.20  subject 76.81
    targets                                          world 37.10  subject 76.90

Under "no behaviour change inside a port" this is a divergence. Reverting is not
available -- there is nothing to revert to. The test pins the PROPERTY (the
solver must hit the kit's stated targets) rather than the multipliers.

**To decide:** render the UFC-05 window at both and look. The port is
arithmetically better; whether it is better on screen is a different question,
and a 10% brighter world is visible.

**Archiving lesson:** a build step run as an inline heredoc leaves no artifact.
`manifest_complete` catches un-archived *imports*, not un-archived *steps*, so
this class of gap is still open. Worth a rule: a step that writes an input the
build reads must exist as a file.

---

## Q6 — the TALK caption gates are preferences, not limits

The packet specified "zero function-word line endings and zero orphans" as pins
from the Dana/Ngannou v4 files. The approved run's own gate output says
otherwise:

    words 192 · cues 47 · orphans 0 · max visible 1 · widest 970 px (89.8%)
    max words/cue          6    against a ceiling of 5
    function-word endings  1    ("Where was")

Both misses are structural. `MAX_WORDS` is consulted only when DECIDING WHERE to
split an over-long phrase, so a 6-word phrase is never split and survives whole.
The function-word rule is a -100 score penalty applied at the same decision
point, so an unsplit phrase can still end on one. Neither is a post-check.

The port reproduces all seven numbers exactly, violations included, and they are
pinned as measured.

**To decide:** whether to add a post-pass that re-splits a cue ending on a
function word. It would have changed one cue out of 47 in the approved reel.
Cheap to do, and it would make the stated gate true rather than aspirational.

---

## Q7 — the 35 dB render gate cannot detect a missing effect

Measured, by dropping `bloom` from the compose chain entirely:

    correct port     PSNR 97.96 dB · worst frame 2/255 · 85/96 frames bit-exact
    bloom dropped    PSNR 74.70 dB · worst frame 5/255 · 59/96 frames bit-exact

74.70 dB is more than twice the packet's 35 dB gate, so a render missing a whole
effect would have passed. PSNR is a poor instrument here because the frames are
graded dark (world luma ~37/255) and bloom only touches highlights above 0.85.

The gate now asserts on WORST-FRAME LEVELS (<= 2) and BIT-EXACT COUNT (>= 80),
which both catch it. PSNR is retained only because the packet named it.

**No decision needed** -- recorded so the 35 dB figure is not reused elsewhere
as though it were meaningful.

---

## Q8 — the classifier corpus is blocked on a missing UNIT, not just missing clips

`test_labelled_corpus_gate` stays xfail. Two blockers:

1. **Clips.** Only two distinct real sources exist on disk (one 754 s UFC fight,
   one 71.6 s Dana interview); the rest are renders of those two. Four
   defensible labels can be cut from them. Slicing the same two sources into 20
   would produce a gate that passes and measures nothing.
2. **The unit.** `measure()` takes `motion_fn` injected and no canonical
   implementation exists, so `ACTION_MOTION_MIN = 6.0` has no defined unit. An
   ffmpeg scene-score reading of the UFC footage lands at 0.20-0.31 -- two orders
   out, which says nothing about the clip and everything about measuring the
   wrong quantity.

**To unblock:** land a canonical `motion_fn` with its unit stated, then pull 20
clips from the niche fetchers -- they already return exactly this material daily
-- and label them. Doing (2) first matters: labelling against an undefined
measure bakes the confusion into fixtures.

---

# Part 4 findings

## Q9 — prod disk is at 97%, and run-dir pruning has stopped

`/opt/genlab/.tmp/runs` holds **6.1 GB across 51 run directories**.
`CLEANUP_KEEP_RUNS=3`, so pruning is not running. Free space fell from 2.5 GB to
1.4 GB during a single session.

This matters beyond tidiness: **Postgres crashed at 100% disk on 2026-07-01**,
and `.venv` alone is 2.5 GB. A deploy onto a 97% disk is not safe.

`scripts/disk_cleanup.sh` exists and `.claude/rules/cleanup_safety.md` marks
`.tmp/runs/` as always-safe to prune, keeping at least 3 recent runs. **Not run
here** -- pruning prod state is an operational action outside this packet, and
those run dirs were the corpus source. It is a prerequisite for §4.

**To close:** run `disk_cleanup.sh` on prod, then re-check `df`. Worth also
finding why pruning stopped, since 51 dirs against a keep-3 policy is a
scheduler or exit-code failure, not drift.

---

## Q10 — the matte worker needs its own venv before it can matte anything

The worker entrypoint, transport and launchd plist are done and the SSH path is
proven end to end. The **backend is not wired**: `sam2` and `rembg` are not in
the project venv, and the craft-era environment that had them is gone.

Installing them into the shared venv is what **rule #31** forbids -- a
torch-adjacent install without an index pin replaced `torch==2.12.1+cpu` with
the full CUDA stack on a GPU-less box. The worker should get a dedicated venv
(`uv venv ~/.genlab-matte`, `--index-url` pinned), and the plist's
ProgramArguments should point at it rather than at `uv run --project`.

Until then `matte_fn` returns `{"frames": 0, "reason": "sam2_backend not
available on this worker"}`, prod reads that as worker-unavailable and renders
legacy -- the fallback working. The packet's IoU >= 0.98 diff against the
archived UFC-05 mattes could not be run.

**Decide:** is a second venv on the laptop acceptable (~3-5 GB), or should the
worker move to a box that can hold one?

---

## Q11 — STILL-first ordering is incompatible with the canonical motion unit

`classify()` returns STILL before it ever checks speech and faces:

    if sig.motion_energy < STILL_MOTION_MAX: return STILL

In the canonical unit a locked-off interview measures **0.19-0.21 levels/s**,
BELOW a footage-free still with a slow push at **1.914**. So a talking head --
the archetypal TALK clip -- classifies as STILL, and no threshold fixes it,
because TALK sits below STILL on this axis rather than above it.

The ordering's stated rationale is about CUTTING ("nothing to cut to"), which is
ACTION's requirement. TALK is speech-led and does not need cuts; a static
talking head is perfectly serviceable TALK.

I did **not** reorder it. A reorder is a behaviour change beyond the threshold
move this packet authorised, and the grid search showed it does not rescue the
gate anyway (TALK-first scores 14/27 against STILL-first's 12/27; the best of
either ordering is 18/27).

**Decide:** reorder so TALK is checked before STILL, or accept that TALK clips
land in STILL until the classifier gets a better discriminator (Q8).

---

## Q12 — the corpus's STILL items are too easy, by construction

The 7 STILL fixtures are generated the way the pipeline renders a footage-free
blueprint -- a held frame with a slow push -- but they carry **no audio track**,
so `speech_ratio` is trivially 0.00 and STILL scores 7/7 in every variant of the
confusion matrix.

A real footage-free item has TTS narration and would score a HIGH speech ratio
with near-zero motion, which is a materially harder case and one the current
rules would likely send to TALK.

**To close:** regenerate the STILL fixtures with a TTS track over them, then
re-run the matrix. Expect the 7/7 to fall, and expect that to be informative
rather than a regression.

---

# Part 5 findings

## Q13 — the classifier becomes a learned model, or it stays advisory forever

Retired as a router (Part 5 §3). Three scalars -- motion, speech, faces -- ceiling
at **18/27 = 67%** on live production material under exhaustive search. Provenance
routes now; the classifier's verdict is persisted beside every route and its
agreement reported per fire.

**The path forward is a learned model on frames** -- CLIP embeddings with a linear
head, or a small CNN -- not more thresholds. What it needs is a few hundred
labelled clips, and `agreement_report()` now accumulates exactly that as a side
effect of every fire: each routed candidate is a clip labelled by provenance.

**Decide when:** a few hundred rows exist. Until then the right move is to keep
collecting and leave the scalars advisory.

**Do not read the STILL column** of the 27-clip matrix as evidence of anything.
It scores 7/7 in every variant because those fixtures were generated without an
audio track, so `speech_ratio` is trivially 0.00. A real footage-free item has
TTS narration (Q12).

---

## Q14 — the full-suite baseline takes hours on this laptop

`baseline_compare.sh 36ac4d38 5efa33ab` reached **46% of the BASE suite in ~3
hours** and both pytest processes sat at **0.0% CPU** -- blocked on I/O, not
computing. With `--timeout=300`, every network-bound test that cannot reach its
endpoint costs a full five minutes.

Two suites plus the isolation re-runs makes this a many-hour job, which means
the deploy gate in §4 cannot be satisfied inside a working session.

**To fix:** mark the network-bound tests and deselect them in
`baseline_compare.deselect`, or give them a much shorter timeout. The harness is
sound -- it is the suite that is slow, and slow for a reason worth removing.
Until then, run the baseline overnight or scope it to the packages a change
touches.
