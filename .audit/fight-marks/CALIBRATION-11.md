# CALIBRATION-11 — PEAK-15: a still camera, and the aspect ladder

## §1 The camera gate needs a CONTROL, not an absolute

Phase correlation cannot tell a camera move from the anime's own pan, and the
reel runs most shots at 1.6x, which multiplies the content's own motion by
1.6. Measured as an absolute, v8 reads 2.44 px against a 1.5 px cap and looks
like a failure. Against a control render with the camera moves disabled and
everything else identical:

| render | mean | p95 |
|---|---|---|
| control (same cuts, no camera move) | 2.22 px | 9.36 |
| **v8** (kick at the cut + shake on the hit) | 2.44 px | 10.19 |
| v7 (continuous shake + pulse) | 3.46 px | 11.15 |

**Camera contribution: v8 +0.22 px, v7 +1.24 px** against a 1.5 px cap. The
2.22 px in the control is content, not camera. The slow-motion segment
measures **0.02 px** — still, as asked. One shake window against a cap of 3.

The continuous pulse was the first thing to go: a sine over the whole shot is
a camera that never stops. It is now a boxcar kick at the cut, which is on
the grid, decaying over 0.125 s.

## §3 The ladder, and where it lands

| aspect | crop width | holds action up to (10% margin) |
|---|---|---|
| 9:16 | 31.6% | 28.8% |
| 3:4 | 42.2% | 38.4% |
| 4:5 | 45.0% | 40.9% |
| 1:1 | 56.2% | 51.1% |
| 16:9 | 100% | 90.9% |

**The packet's order is not tightest-first.** It lists 9:16 → 3:4 → 1:1 →
4:5, but a full-height 1:1 crop is 56.2% of the width and 4:5 is 45.0%. The
Record says "3:4 before 4:5", so the monotonic ladder 9:16 → 3:4 → 4:5 → 1:1
→ 16:9 is used and the swap is recorded here rather than silently kept.

**The pin does not hold, and the numbers say why.** The water-breathing sweep
was to land at 3:4. Measured, the action spans:

| shot | action width | lands at |
|---|---|---|
| 76.0–76.5 | 100% | 16:9 |
| 80.0–81.0 | 80% | 16:9 |
| 83.5–85.5 | 97% | 16:9 |
| 85.5–87.5 | 58% | 16:9 |
| 87.5–88.0 (the sweep) | 89% | 16:9 |

3:4 holds 38.4% and 1:1 holds 51.1%; nothing in this window is that narrow.
The boxes were checked by drawing them back on the frames — at 80.5 s the
action really is the whole flame circle, at 87.5 s the water arc really does
span the frame. The ladder is working; the material is wide.

Wide (1:1 and beyond) is 35% of the reel, inside the 40% budget.

## §4 Asking what is happening, at phone size

"Is it visible" passed everything in PEAK-14. Asking for a one-sentence
description at 360 px failed a shot that had passed the old check: the Topaz
hit's tail read as *"A blurry, blue-tinted impact effect covers the screen"*,
`legible=false`.

The shot was already at the widest aspect, so there was no step wider to flip
to; the remaining lever is DURATION, and it was trimmed 3.17 s → 1.92 s to the
part that reads. All six action shots then pass, with descriptions that name
the action: "Tanjiro cuts off Akaza's forearm with a circular water slice".

## A silent fallback worth remembering

The hit branch selected its clip with `layout == "wide-4:5"`. PEAK-15 renamed
layouts to `wide-{aspect}`, so the test stopped matching and the renderer fell
back to the CROPPED hit without a word. An equality test against a
constructed name is a silent dependency on the name.
