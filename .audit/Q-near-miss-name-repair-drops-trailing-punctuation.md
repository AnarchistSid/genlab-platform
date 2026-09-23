# Q — fix_names drops trailing punctuation on a near-miss repair

**Found** 2026-09-23, ANIME-14 §3, while widening the near-miss distance rule.
**Where** `genlab-core/src/genlab_core/talk/captions.py` `fix_names()`.
**Status** deliberately LEFT ALONE. Filed, not fixed.

The exact-match branch re-appends the token's trailing punctuation:

    w.t, w.name = singles[key] + trail, True

The near-miss branch does not:

    w.t, w.name = nv, True

So an ASR token `"Gan."` repaired to the name `Gane` loses its full stop and
becomes `"Gane"` mid-sentence. `segment()` keys sentence boundaries off that
punctuation, so the boundary disappears.

Measured on the Dana v4 transcript, which is the operator-approved TALK
render the port oracle pins: restoring the trail takes the transcript from
**47 cues to 48**, because "Gane." then reads as a sentence end.

## Why it was not fixed here

Two reasons, both about scope rather than merit:

1. It is almost certainly a bug — dropping a sentence-ending period silently
   merges two sentences — but it changes the segmentation of an approved
   render, and every TALK reel would re-cut.
2. ANIME-14 §3 asked for the near-miss DISTANCE fix ("nareema"). Changing
   punctuation handling in the same edit would have shipped two changes under
   one justification, and the port-oracle gate is what caught it.

## What deciding it needs

A look at whether ASR's trailing period on a mis-heard name is usually real
sentence punctuation or an artefact. If real, restore the trail and re-approve
the TALK cue count; if an artefact, keep the current behaviour and say so in
the docstring so the asymmetry stops looking accidental.
