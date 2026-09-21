# Q — layout() can return lines wider than MAX_LINE_PX

**Found** 2026-09-21, ANIME-12, while writing a caption-wrap pin.
**Where** `genlab-core/src/genlab_core/talk/captions.py` `layout()`.
**Scope** SHARED with the TALK template. Filed, not fixed.

`layout()` searches two-line splits and `continue`s past any split where
`max(w1, w2) > MAX_LINE_PX`. When NO split qualifies it falls through to a
fallback that returns lines over budget anyway — silently.

Measured on a 9-word cue: returned 1146px and 1322px against a 972px budget.

Reachable from real input: `segment()` caps a cue at MAX_WORDS = 5, and five
long words exceed what two lines can hold at CHAR_W = 44.08 (2 x 22 = 44
characters). Any five-word cue over ~44 characters lands in the fallback.

The still kit does not hit this today because its cues are short, and the
fallback is a real fallback rather than a crash. But it is the shape where
an unsatisfiable filter falls through to a default that looks valid —
over-budget lines are returned as if they were a successful layout, and the
caller has no way to tell. Either return three lines, or shrink the font for
that cue, or signal that the cue could not be laid out.
