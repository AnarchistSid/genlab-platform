# CALIBRATION-10 — PEAK-14: anchoring on the action

## Why the sweep was a sliver, in one number

A 9:16 crop of a 16:9 frame is **31.6% of its width**. Measured from the
VLM's `action_extent`, the action in the Tanjiro window spans:

| shot | type | action width |
|---|---|---|
| 76.0–76.5 | clash | 100% |
| 80.0–81.0 | clash | 80% |
| 83.5–85.5 | clash | 97% |
| 85.5–87.5 | clash | 58% |
| 87.5–88.0 | impact | 89% |

Every action shot is 58–100% wide against a crop that shows 31.6%. One anchor
rule for both kinds of shot could not have worked; it was not a tuning
problem.

Character shots still anchor on the face. Action shots anchor on the focal
point when the action fits with a 10% margin, and go **wide on a clean matte**
when it does not — which, on this window, is all six.

## The cost of the wide layout, stated

A 16:9 frame scaled to 1080 wide is 608 tall, so a wide shot fills **32% of
the reel's height** and the rest is matte. That is the arithmetic of the
instruction, and it is tolerable only because those shots run 0.5–1.8 s and
carry pulse and shake. It is a big matte and it should be looked at.

The matte also breaks a metric: the whole-frame duplicate reading rose from
1% (v6) to 9% (v7), but measured on the **content strip alone** it is 2%. The
matte is 68% of a wide frame and never changes, so a whole-frame difference is
scaled down and more frames fall under the threshold. The picture did not
regress; the meter did.

## The hit had to be re-framed BEFORE interpolation

Topaz bakes the framing in: the first hit clip was cut from a 9:16 crop, so
the layout decision said "wide" while the file on disk was cropped. A second
run on a wide-framed, watermark-masked source ($0.1429) fixed it. Any
generative step in the chain fixes everything upstream of it — the decision
has to be made before the money is spent.

## §3 The framing gate

Six action shots, two rendered frames each, back to the model: **6 of 6 pass**,
with reasons that show it is reading the action rather than the presence of a
character — "Water slash severs Akaza's arm", "Tanjiro performs a spinning
flame sword", "Akaza thrusts his fist forward beside Ta…".

This is the first gate in fourteen packets that measures the thing that
ships rather than a proxy on the source.

## §4 Holds

One held-drawing cut fired, at the opening shot (69.0–71.0 s split at 71.0).
The `hold` field came back true on 14 of 54 frames.

## v6 → v7

| dimension | v6 | v7 |
|---|---|---|
| duration | 24.0 s | 23.9 s |
| segments | 17 | 18 |
| wide action shots | 0 | **6** |
| held-drawing cuts | 0 | 1 |
| framing gate | none | **6/6 pass** |
| duplicate frames (whole) | 1% | 9% |
| duplicate frames (content strip) | 1% | **2%** |

## Still open

No reference reel. YouTube returns 403 on every media fetch from this
machine and nothing has been placed on the Desktop, so the reference column
is empty for the fourth packet running.
