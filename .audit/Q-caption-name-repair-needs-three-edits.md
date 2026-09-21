# Q — caption name repair fails at 3 edits; "Nerima" ships as "nareema"

**Found** 2026-09-21, ANIME-12, on reel A's burned captions.
**Where** `genlab-core/src/genlab_core/talk/captions.py` `fix_names()`.
**Scope** SHARED with the TALK template. Filed, not fixed, per the
out-of-scope-findings rule.

## What happens

The near-miss rule is: same first letter, length within 2, at most 2 edits.

    norm("Nerima") = "nerima"   ASR heard "nareema"
    n/n  e/a  r/r  i/e  m/m  a/a  -> 2 substitutions + 1 length = 3

Three, so the repair declines and the wrong spelling is burned into the
frame. "Kyoto" in the same cue was repaired correctly (exact match). The
reel therefore ships one correct proper noun and one wrong one in the same
line of text, which reads as carelessness rather than as a transcription
limit.

## Why not simply raise the threshold

The docstring's reasoning holds: "a half-corrected name is worse than an
uncorrected one, and an invented one is worse still." At 3 edits on a
6-character word the rule starts matching unrelated short words. Raising it
buys this case and sells others.

## The fix that is actually correct

Caption from the SCRIPT, not from the ASR. We generated the narration text;
we know every word of it. faster-whisper should supply TIMINGS only, aligned
to the known script by sequence match. Then a proper noun can never be
misspelled on screen, because the screen text never passed through ASR.

This also removes the "9th, 2026" rendering of "ninth, twenty twenty-six"
seen on reel B, and it removes `fix_names` from the still path entirely.

Estimated: one alignment function (needleman-wunsch over word sequences,
~60 lines), plus a pin per template. Not in ANIME-12's scope.
