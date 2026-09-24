# CALIBRATION-08 — PEAK-11: one reel by hand, and the defect the gates never saw

## The cadence bug, on every reel since v1

All four fight sources are 23.976/24 fps. Every reel was rendered at **30
fps** — a 5:4 pulldown that duplicates one frame in five. Measured mid-reel:

| reel | duplicate frames | judder (cv) |
|---|---|---|
| zoro_vs_king v4 | 32% | — |
| luffy_vs_kaido v4 | 21% | — |
| deku_vs_overhaul v4 | 24% | — |
| tanjiro_vs_akaza v4 | 38% | 1.95 |
| tanjiro source @24 fps | 25% | — |

The anime's own held frames account for part of it — it is animated on 2s in
places — and the pulldown adds the rest. No gate was looking at cadence.
v5 renders at the source cadence: **21% duplicates, judder 1.49**.

## Three defects between the first v5 render and a usable one

Each was found by looking at proof frames, and each measured correct in the
number it was supposed to be checked by.

1. **`-t` after `-i` bounds the OUTPUT.** Under a speed change ffmpeg reads
   whatever SOURCE it needs to fill that: a 1.6x segment asked for 2.0 s of
   source consumed 3.2 s and still emitted 2.0 s. Every segment played a span
   the plan never chose. With a time remap, input-side and output-side limits
   are not interchangeable. Fixed: source consumed is now 27.00 s against a
   27.0 s marked window.
2. **The crop was narrower than the fighter.** At 1.99x the crop was 542 px
   while the fighters' boxes measured 500–900 px, so a partial view was
   guaranteed and the proof frames came back as Akaza's stripes and Tanjiro's
   haori filling the screen. The crop is now SIZED to the subject, not merely
   centred on them.
3. **A hidden 2x zoom.** `shot_filter` scaled the crop to `OUT_W*2` to give
   the shake room, then cropped `OUT_W x OUT_H` from the centre — discarding
   half the width and half the height. `crop_w` read a correct 606 px and the
   picture was twice as tight as that number implied. This is what actually
   filled the frame with fabric; fixing (2) alone changed almost nothing.

Related geometry worth keeping: on a **720p source the full-bleed 9:16 crop
is already 2.667x**, so a "≤ 2.0x magnification" rule is unsatisfiable there
— the source is simply smaller than the output.

## §2 interpolation, probed

| app | cost | notes |
|---|---|---|
| `infsh/rife-video-interpolation` | **$0.00** | target_fps constrained to 24/30/60/120 |
| `topaz/frame-interpolation` | **$0.1429** / 2 s | settled at the LOW end of a $0.07–$1.43 estimate — a 20x range, the case the standing rule says is not an estimate |
| ffmpeg `minterpolate` (chosen) | free, local | judder 1.03 vs 2.31 for frame duplication on the same half-second |

Duplication and interpolation were compared on the 0.5 s before the impact.
Judder — the variance of inter-frame motion — is the discriminating metric;
raw duplicate ratio is not, because anime holds frames deliberately. Both
were inspected for warping on the anime's flat colour and neither showed any.

## v4 versus v5

| dimension | v4 (band, 30 fps) | v5 (full-screen, 24 fps) |
|---|---|---|
| duration | 29.0 s | 35.1 s |
| cuts / min | 51.7 | 34.2 |
| duplicate frames | 38% | 21% |
| judder (cv) | 1.95 | 1.49 |
| text density | 0.030 | 0.018 |
| full-screen fraction | 0% (band on 7 of 8 shots) | **100%** (8 of 8) |
| remapped segments | 0 | **10** |
| interpolated segments | 0 | **5** |

## §0 could not be done

Six yt-dlp attempts across five player clients, with and without browser
cookies: manifests resolve (one to 4K) and every media fetch returns HTTP
403. It is not video-specific — four different uploads, same result. This is
YouTube's poToken enforcement on this machine. **No reference reel was
measured, so every "reference" column above is empty and the reel has not
been compared to a real one.** The packet's judgement step needs that file.
