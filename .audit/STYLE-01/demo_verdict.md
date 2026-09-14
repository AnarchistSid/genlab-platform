# STYLE-01 §1 demo — BUILT, and it FAILS. 2026-09-14 ~04:30Z.
Artifact: `scratchpad/style01/clutchwire_anime_demo.mp4` (21.22s, 1080x1920, bt709).
Source: retained `base_sports.mp4` (MOTION-01 capture). Local FFmpeg only, no belt spend.

## Measured
| | baseline | demo |
|---|---|---|
| cuts (T>0.3) | 2 (0.103/s) | **4 (0.188/s)** |
| longest_static | 6.00s @ 0.90s | **6.00s @ 0.90s — UNCHANGED** |

Cadence improved; the dead stretch did not move, because every effect was concentrated on
one beat at 12.2s while the static run sits at 0.9-6.9s. A skin has to distribute
interrupts across the timeline, not stack them on the hit.

## Why the look fails — three reasons, by eye
1. **The impact frame inverts the COMPOSITED frame.** The ClutchWire logo goes negative,
   the hook text inverts, the attribution line inverts, and the embedded tweet card becomes
   unreadable. It reads as a broken render, not a style. **This is rule 5 — the
   composite boundary — and it applies to LOCAL destructive effects, not just generative
   ones.** Every effect in this kit must run pre-composite, with overlays composited once
   across the final timeline. The rule was written for belt tools; it is more general.
2. **The source is a talking-head podcast clip, not a fight.** Dana White at a microphone,
   then Ngannou reacting, with a tweet card overlaid. There is no hit to land an impact
   frame on. A speed ramp and impact frame on a man talking into a mic reads as an error.
3. Consequently the whole format premise is unmet by the current sourcing.

## The finding that matters more than the demo
**ClutchWire's sourcing does not deliver the footage this format needs.** Today's sports
blueprints come from `reddit:MMA`, `reddit:boxing`, `reddit:formula1`, `reddit:hockey`,
`reddit:baseball` and ScoreBat — and this sample is a podcast reaction clip. The
anime-edit skin assumes *action*: a hit, a landing, a save, a knockout.

§4 ranks sourcing by RIGHTS. This adds a second axis: **does the source supply action at
all?** A rights-clean feed of press-conference clips cannot carry this format either. Both
axes have to be satisfied by the same source, and that constrains §4's option 1
(creator programmes / athlete channels) much harder than rights alone does.

## Recommendation
Do NOT build the full kit yet. Two things first:
1. Re-run the demo on a clip that actually contains an impact — pulled deliberately, not
   whatever today's queue produced. If the format works on real action, the kit is worth
   building; if it does not, nothing downstream matters.
2. Move the effects pre-composite before judging the look again. The current artifact
   cannot be judged fairly for STYLE because a fixable layering error dominates the frame.

## Incidental — a lead on a filed item
Concat first failed with *"Nothing was written into output file, because at least one of
its streams received no packets"* — the exact error filed from the 09-13 ai_creators fire.
Cause here, measured: the chromatic-aberration segment carried **SAR 5793:5792** while the
other three were 1:1. A scale-based effect introduced a non-square SAR and the mismatch
killed the concat under a message that names neither SAR nor the offending input.
Fix: `setsar=1` + `settb=AVTB` on every segment before concat. Worth checking whether the
09-13 render failure has the same cause.
