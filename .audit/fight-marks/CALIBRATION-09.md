# CALIBRATION-09 — PEAK-12: a model that can see, measured against a hand

## §1 The pin, on the fixture the packet specified

Ten v5 proof frames, hand-labelled before any model ran.

| model | character agreement | shot type | unknown |
|---|---|---|---|
| `anthropic/claude-haiku-4-5` | 40% | 50% | 0 |
| `google/gemini-3-6-flash` | **80%** | **80%** | 0 |

Pin: characters ≥ 0.9, shot type ≥ 0.8. **Shot type passes; characters miss
at 0.80.**

One hand label was wrong and was corrected: on frame 2 the model saw
Tanjiro's haori in the bottom-right and the first pass missed it. Three other
disagreements were adjudicated at 560 px and the hand label held. The labels
were NOT revised further to make the model pass — the two remaining misses
are a small haori patch and a purple limb, both genuinely marginal.

**The fixture is harder than the task.** Those proof frames are the rendered
reel: cropped to a third of the source width, magnified ~1.8x, motion-blurred
and carrying a burnt-in watermark. §1's real input is the full 16:9 source
frame. On those, over the whole 27 s window:

* 54 frames at 2 fps, **0 unknown**, 250 s wall clock
* character presence on **94%** of frames
* face boxes returned on most frames
* the Crunchyroll watermark box found on **54 of 54** — which is what §2
  needs in order to crop or mask it rather than magnify it

## §4 The v5 "slow motion" was a freeze, and PEAK-11 chose the wrong tool

The packet names v5 as the negative fixture. Measured:

| v5 segment | frames | longest identical run |
|---|---|---|
| 0.28x "interpolated" | 43 | **11** |
| 0.12x "hold" | 25 | **20** (0.83 s frozen) |
| 0.60x reaction | 77 | 1 |

The cause is the material: the 0.5 s before the impact is **12 frames with 7
distinct** — the anime is animated on 2s. An interpolator can only invent
motion between frames that DIFFER, so interpolating the duplicate pairs
reproduces them.

The run-length threshold is itself a meter and needed calibrating: at 96 fps
consecutive frames SHOULD be nearly identical, so a coarse threshold reads
smooth motion as a freeze. At a fine threshold, true repeats separate:

| method | thr 0.40 | thr 0.10 | thr 0.03 |
|---|---|---|---|
| plain duplication | 8 | 7 | 7 |
| ffmpeg `minterpolate` | 12 | 11 | 11 |
| `mpdecimate` → `minterpolate` | 10 | 9 | 6 |
| **topaz 4x @ 24 fps** | 8 | **1** | **1** |

**Topaz is the only one that produces real slow motion here**, at $0.1429 per
2 s clip. PEAK-11 chose `minterpolate` on a judder comparison measured at
0.6x, where the source has enough distinct frames; at 0.28x, where it
matters, it freezes. That conclusion is withdrawn.

An earlier "mpdecimate → minterpolate" test was also invalid: the thresholds
passed (`hi=200:lo=100:frac=0.1`) dropped ZERO frames, so it was plain
minterpolate under another name.

## §2 The shot list, and what a person still has to fix

15 shots from 54 frames. All four beats present, effect share 0%, 4 shots
unanchored, 27.0 s total — the whole marked window.

A face box came back with its centre at **y = 1.32**, outside the frame.
Validating that a list holds four numbers says nothing about whether they are
coordinates; boxes are now range-checked.

Four shots I would question before rendering, listed on the delivered table:
one typed `other` that is plainly a wind-up, one dark motion smear typed
`clash Tanjiro` with no readable character, one whose subject is the less
prominent fighter, and one Tanjiro close-up the model read as `both/none`.

**Nothing has been rendered.** The packet's order is shot list → review →
render, and the review has not happened.
