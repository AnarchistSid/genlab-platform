# CALIBRATION-05 — PEAK-08: what each gate measured, and where each one stops

## §1 Outro card

Every licensed pilot clip ends in a platform outro. Detected as a near-static
tail rather than by logo template — one template per platform per locale would
miss the next one.

| clip | card at | length |
|---|---|---|
| zoro_vs_king | 88.4 s | 9.5 s |
| luffy_vs_kaido | 89.9 s | 9.5 s |
| gojo_vs_jogo | 121.0 s | 19.0 s |
| deku_vs_overhaul | 91.1 s | 9.5 s |
| tanjiro_vs_akaza | 97.9 s | 9.0 s |

Two measurement corrections, both of which changed the answer:

* **Compare to the FINAL frame, not the predecessor.** The JJK card contains a
  one-frame wipe between thumbnail panels. Against its predecessor the run
  ended there and reported 9.5 s against a real 19 s — half the subscribe
  panel would have stayed inside the searchable window.
* **Count changed PIXELS, not mean absolute difference.** MAD averages a small
  moving element into a large still background, so a held shot with a talking
  mouth reads as a card. Measured: content holds 3–7% of its pixels still
  between samples, cards hold 84–100%.

The threshold moved 0.95 → 0.50 with that metric change. **A threshold belongs
to its metric, not to its task** — carrying the old value across lost three of
the five cards.

## §2 The action box

Boxes come from per-fighter colour seeds, or motion energy where a fighter has
no nameable hue (King is black-purple; black has no hue). Four corrections:

1. **Connected components, not pixel extent.** Anime backgrounds carry the same
   saturated hues as the costumes, so a box over the raw mask was full-frame on
   every shot. Percentile trimming does not help when outliers are spread
   rather than few.
2. **Crop only when the evidence covers every fighter.** The measured failure
   was coverage, not accuracy: on a two-fighter clash where one seed fires, the
   box goes narrow and the crop cuts the other fighter out. A seed that does
   not fire is ambiguous, so the fighter's raw colour share separates "out of
   shot" (crop may proceed) from "present but unlocated" (band).
3. **The crop width follows the box.** The first version chose between a 608 px
   full-bleed crop and the whole frame; rendered, that put most shots in a strip
   filling 32% of the reel and reads as black bars. Cropping to the box and
   letterboxing only the remainder took measured fills from a flat 32% to
   32–100%.
4. **Never magnify what is already large.** A colour seed locates a GARMENT.
   Two Demon Slayer boxes were the same width (499 vs 551 px) and 34% vs 58% of
   frame height; the first held the whole fighter, the second was haori pattern
   magnified into fabric.

Frame dimensions are probed, never assumed — the My Hero Academia source is
1280×720, and a crop computed against 1920×1080 fails as "could not open
encoder", which names nothing.

## §3 The print gate, and where it stops

Runs at two units. `check()` decides whether a SOURCE is usable and tolerates
15%. `prints_in()` decides per SHOT with zero tolerance — at 7% over its
window one My Hero Academia shot still burned "...a hero who saves everyone?"
into a finished reel.

**Measured limit.** A short, low-contrast line stays below the gate.
"K-Kaido-san..." is legible to a viewer and invisible to tesseract at every
scale from 1080 to 2880 px. Four preprocessing variants were tried; only
threshold-then-invert returned it, and that variant pushed the CLEAN Demon
Slayer source to a **56% false positive** — it would reject the best source in
the pilot. Past that point every step traded a false negative for a false
positive with no net gain. The gate stops where it was measured good; Zoro's
body was trimmed to 86.4 s by hand and the trim is recorded in the plan.

Do not re-tune without a labelled set of subtitled and clean frames.

## §4 The pose gate is not a pose gate

Implemented as colour-box IoU between the impact frame and its redraw, because
no pose estimator is available on this host. It conflates three things:

* **style** — Zoro's seed keys an energy AURA, and any ink redraw removes the
  glow. Three draws scored 0.0, 0.0, 0.022 while the pose was well preserved.
  The gate is not failing there; it is not evaluable.
* **framing** — Tanjiro's redraw preserved pose and colour but was reframed,
  scoring 0.349.
* **generator variance** — the same prompt on the same frame scored 0.349 /
  0.666 / 0.441 across three draws. The gate sits inside the generator's
  spread, so it is sampled a bounded three times and every attempt recorded.

The prompt was also fighting the gate: it asked for a stylised monochrome manga
panel while the gate measured colour-region overlap. Asking for the original
palette and framing moved Tanjiro 0.349 → 0.666 in one change.
