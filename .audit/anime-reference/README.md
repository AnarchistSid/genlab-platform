# anime_reference.json — the corpus FrameDrift's gates quote

ANIME-14 §9. 34 top anime reels collected across both lanes and both
platforms; 29 kept after filtering to short-form (3-90 s).

| lane | Instagram | YouTube |
|---|---|---|
| news / hype | 10 | 6 |
| edit page | 3 | 10 |

Instagram via `business_discovery` on the accounts that expose it (top media
by like_count, 3 each); YouTube via six `ytsearch` queries. Five items were
skipped as not short-form — one was a 315 s upload sitting in an IG feed.

Every number is produced by `genlab_core.still.reference.measure`, the SAME
function that measures our own reels, with its detectors recorded in the file:

* cuts — `ffmpeg select='gt(scene,0.30)'`
* visual changes — `select='gt(scene,0.10)'`, a strict superset, so it also
  counts a zoom pulse or a flash inside one shot
* longest static — `freezedetect n=-55dB d=0.5`
* text density — `tesseract --psm 11 tsv`, conf>=60, boxes summed over frame
  area
* music — the beat grid's lift

## What the corpus says

| dimension | p10 | p25 | **p50** | p75 | p90 |
|---|---|---|---|---|---|
| cuts/min | 4.9 | 15.8 | **41.4** | 58.7 | 84.0 |
| changes / 20 s | 7.9 | 17.0 | **39.3** | 52.9 | 103.4 |
| longest static (s) | 0.0 | 0.0 | **0.0** | 1.47 | 1.77 |
| shot-length cv | 0.46 | 0.81 | **1.17** | 1.63 | 2.45 |
| shot-length mean (s) | 0.68 | 0.99 | **1.41** | 3.64 | 6.24 |
| text density mean | 0.002 | 0.003 | **0.005** | 0.021 | 0.039 |
| text density max | 0.010 | 0.029 | **0.044** | 0.079 | 0.138 |
| hook-text onset (s) | 0.0 | 0.0 | **0.5** | 1.5 | 1.5 |
| duration (s) | 10.9 | 15.4 | **45.0** | 58.0 | 69.2 |

Music present in **100%** of them. Hook text on screen in **72%**.

## Three places the corpus disagrees with the packet

1. **"≥ 9 visual changes per 20 s (the benchmark's band is 6-12)."** Measured
   p50 is 39.3, and **1 of 29** reels falls inside 6-12. The band is roughly
   a quarter of what the top of this genre actually does. A gate at 9 is a
   floor so low that failing it would take real effort.

2. **Text.** Our v3 reels carry a mean text density of 0.051-0.062 against a
   corpus p50 of 0.005 — ten times the median and above p90. Two text layers
   plus a caption plate plus an attribution slate is more text than the
   reference uses, and the reference is the thing being copied.

3. **Duration.** Corpus p50 is 45 s; the packet targets 22-30 s. Both are
   defensible, but they are not the same reel, and the gates should say which
   one is intended rather than inheriting 25 s from an earlier packet.

## A limit of the shot-length comparison

`shot_length_cv` is measured on the OUTPUT with the cut detector, so our
flash and zoom interrupts count as boundaries and the measured value is not
the planned one. Reel B's PLANNED lengths after the variance fix are
1.79/3.6/2.2/3.6/1.6/2.2/2.6/2.0/1.8/2.6/2.2 — cv 0.272 — while the corpus
p50 is 1.17. The gap is structural: ANIME-14 §2's own bands (1.0-2.5 s on
footage, 2.5-3.5 s on key art) cannot produce a cv near 1.17, because the
corpus reaches it by mixing 0.3 s flash cuts with 4 s holds. Widening the
bands is a spec decision, not a tuning one.
