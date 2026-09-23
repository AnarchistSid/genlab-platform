# Q — the caption layer cannot coexist with the corpus's text budget

**Measured** 2026-09-24, ANIME-15 §1, on reel B v4 with a control render.

§1 asks for text-density mean ≤ corpus p75 AND keeps the caption layer.
Measured, those two cannot both hold.

| variant | text density mean | max |
|---|---|---|
| all layers (shipped) | **0.0617** | 0.1405 |
| no overlays at all (control) | **0.0102** | 0.1047 |
| corpus p50 / p75 / p90 | 0.0053 / 0.0208 / 0.0389 | 0.0445 (p50) |

So:

* our overlays contribute **0.0515**
* the PV's own residual text contributes **0.0102** — already 2x the corpus
  p50 before we draw anything, and that is AFTER the OCR window gate, because
  the gate filters the peak windows while the broadcast slate we deliberately
  include IS a text frame
* the budget from 0.0102 to p75 is **0.0106**, and we spend 0.0515 — five
  times what is left

v4 already did what §1 asked: attribution dropped from a running line to two
windows, headline capped at three slams, captions baseline-locked. Density
still went 0.051 -> 0.062 rather than down, because the removed line was never
the bulk. **The captions are.** Sixteen cues of narration text are on screen
for most of a 25-second reel.

## The real conflict

The corpus's reels are MUSIC-led with sparse text. Ours is NARRATION-led, and
a narration-led reel needs captions for sound-off viewing — which is most
viewing. Those are different formats, and "match the corpus's text density"
is a decision to change format, not a tuning parameter.

Three ways out, all operator calls:

1. **Keep captions, drop the target.** Gate text density against a
   narration-led sub-corpus instead of the whole corpus. Requires labelling
   which of the 29 reels are narration-led — from the measurement we already
   have, the ones with `hook_text_onset` set and high `text_density_mean`.
2. **Keep the target, cut captions to key lines only.** Caption the hook, the
   turn, and the date; leave the rest to the voice. Loses sound-off
   comprehension of the middle.
3. **Drop the broadcast slate.** Cheapest single win — it is a full text frame
   we chose to include, and it is a meaningful share of the 0.0102 residual.

Not decided here because all three change what the reel IS.
