# TREAT-01 §1 — classifier gate CANNOT RUN AS SPECIFIED. 2026-09-14 ~05:00Z.
15 raw pre-composite clips pulled from `.tmp/runs/*/clips/` (ai_creators 4, sports 4,
movies 4, anime 3; **gaming 0** — none matched the window). Features computed locally
(faster-whisper VAD + validated scene-score detector). No belt spend. No prod writes.

## The blocker: there is no ACTION class in the corpus to gate against

Hand-labelled from frame samples:
| clip | content | label |
|---|---|---|
| 01_ai_creators | man talking to camera (OpenAI dev, office) | TALK |
| 06_sports | three F1 drivers at an FIA press backdrop | **TALK** |
| 07_sports | driver at a microphone, paddock | **TALK** |
| 08_sports | Hülkenberg in a press scrum, mic in frame | **TALK** |
| 11_movies | silhouette at sunset, trailer shot | MIXED |
| 13_anime | Japanese title card over texture | STILL/title |

**All four sports clips are press conferences or interviews. Not one is action.**
This is the STYLE-01 finding again, arriving independently and harder: ClutchWire's
sourcing does not deliver fight or play footage. STYLE-01 found it in one clip; this
found it in four of four.

## Correction to a reading I nearly reported
`speech_ratio >= 0.72` on 13 of 15 clips, and my first reaction was "the feature is
broken — it cannot discriminate". **That is wrong.** The corpus is overwhelmingly
talk, so a uniformly high speech ratio is an ACCURATE measurement, not a broken one.
The feature is **unvalidated**, not refuted: there is no confirmed ACTION clip to serve
as the negative class. A discriminator cannot be tested against one class.

## Feature table (15 clips)
Motion sits in a narrow band (0.003–0.026) and `cuts_per_s` spreads much wider
(0.000–0.566), so on this corpus cut density separates more than motion energy does.
Neither is gated yet, for the reason above.

## Two data defects found by the extraction
1. **`08_sports_86686fc31f0bc940.mp4` has NO AUDIO STREAM** — and it is an interview.
   A talking-head clip with no audio cannot take the TALK treatment at all: there is no
   speech to transcribe, caption, or keep unducked. It would silently fall through to
   whatever the default is.
2. **`15_anime_589246a1fa5ee8fd.mp4` has duration 0.0** — a corrupt or empty source clip
   sitting in a production run directory.
Neither is visible anywhere today; both were found only because the classifier reads the
raw clip rather than the finished render.

## Also measured, not in scope but consequential
Source clips are **640x360** (movies) and **1248x720** (sports), upscaled to 1080x1920 at
render. That is the softness the upscaling item in BUILD-01 Phase 5 targets — now with a
number. Several sources are also very long: 480.8s (sports), 336.3s (movies), 174.8s.

## What the gate needs before it can run
The spec's gate is "hand-label 20 retained clips; the classifier must agree on >= 18",
with positive controls "a press-conference clip -> TALK; a knockout -> ACTION".
**The press-conference control is satisfied four times over. The knockout control does not
exist in our corpus.** Options, operator's call:
1. Source ACTION clips deliberately (a handful of knockouts / goals / plays) purely as
   classifier fixtures — they need not be publishable, only labelled.
2. Accept a TALK/NOT-TALK binary v1, which the current corpus CAN gate, and defer ACTION
   until sourcing produces it.
3. Fix sourcing first — if sports can only fetch press conferences, the ACTION template
   has nothing to run on regardless of how good the classifier is.

**Recommendation: (3) then (1).** Building an ACTION template for footage the pipeline
never fetches is the same error as building the anime kit before checking the clip had a
hit in it — which is the thing STYLE-01 already caught once.
