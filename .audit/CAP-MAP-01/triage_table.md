# CAP-MAP-01 triage — steps 1-2 (cost, access). Steps 3-4 HELD behind morning checks.
# Measured 2026-09-12 ~19:30Z. Belt spend for this triage: $0.00 (balance $92.88 unchanged).
# Basis: 20s reel, 5 niches/day, 2 generated segments per reel unless noted.

## HEADLINE: procurement gate, not cost, is the binding constraint.
## 11 of 13 candidates require a provider API key. `belt secrets list` => "no secrets found".
## CONTROL: the 3 belt apps we already run in prod (inworld-tts-2, flux-2, gpt-image-1)
## all declare "none required". Every blocked candidate declares a key. Correlation is
## strong but NOT proven mandatory (`optional` field is unset, not false).
## One ~$0.05 run of pixverse/extend settles it definitively: it either fails at $0 with
## a missing-secret error, or it succeeds and the gate is illusory.

| § | app | $/call (measured est) | $/reel | $/day x5 | key required | status |
|---|---|---|---|---|---|---|
| 1 | pixverse/extend | $0.090 (1080p/5s); $0.045 (720p) | $0.18 | $0.90 | PIXVERSE_KEY | BLOCKED-procurement |
| 1 | bytedance/seedance-2-0-mini | $0.2268 / 5s | $0.45 | $2.27 | ARK_API_KEY | BLOCKED-procurement |
| 1 | bytedance/seedance-2-5 | unknown until run | ? | ? | ARK_API_KEY | BLOCKED-procurement |
| 1 | minimax/h3 | $0.40 (768P/5s); $0.65 (2K) | $0.80 | $4.00 | MINIMAX_KEY | BLOCKED-procurement |
| 1 | bfl/flux-3-video | $0.60 draft/5s; $2.05 standard | $1.20 | $6.00 | BFL_KEY | BLOCKED + OVER BUDGET |
| 1 | runway/gen-4-turbo | unknown until run ($0.05/s listed) | $0.50 | $2.50 | RUNWAY_KEY | BLOCKED-procurement |
| 1 | pruna/p-video-edit | $0.125 draft; $0.225 standard | $0.25 | $1.25 | PRUNA_KEY | BLOCKED-procurement |
| 2 | bytedance/seedream-5-pro | $0.090 / image | - | - | ARK_API_KEY | BLOCKED-procurement |
| 2 | reve/create | $0.032 / image | - | - | REVE_KEY | BLOCKED-procurement |
| 2 | pruna/p-image-ideogram | $0.015 / image | - | - | PRUNA_KEY | BLOCKED-procurement |
| 2 | chart-broll-renderer (ours) | free | - | $0 | none | AVAILABLE (baseline) |
| 4 | fuplus/seamless-loop-inspector | unpriced (no published pricing) | ? | ? | **none** | **READY FOR PoC** |
| 5 | topaz/frame-interpolation | $0.0714-$1.4286 | - | - | TOPAZ_KEY | BLOCKED-procurement |
| 6 | plimsoll/subtitle-forge | unpriced | ? | ? | **none** | **READY FOR PoC** |
| 6 | mirage/text-overlays | unpriced | ? | ? | CAPTIONS_KEY | BLOCKED-procurement |

## Corrections to assumptions in the brief
1. `pixverse/extend` "5s or 8s": **1080p supports ONLY 5s** (input-schema description).
   Cost is flat per video ($0.090 for both 5s and 8s), so 8s looked like strictly better
   value until the constraint surfaced. 8s requires dropping to 720p ($0.045).
2. `bfl/flux-3-video` headline "From $17/sec (HD)" is a **catalog display bug** — the
   underlying variable is hd_t2v_per_second = $0.17. The summary template drops the
   decimal. Nearly disqualified a candidate on a formatting defect.
3. `pixverse/extend` input limits: MP4/MOV, max 50MB, max 1920px, **max 30s**.
   Our ai_creators baseline clip is 31.41s and would be REJECTED as-is.
4. **No terms data exists in the catalog** for any app — `belt app get` exposes no
   licence/terms field. Step 2 cannot be completed from the CLI. Since every §1
   candidate needs a provider account anyway, the terms question collapses into the
   procurement question: reading PixVerse/MiniMax/BFL terms is part of signing up.

## Zero-procurement path (what is actually actionable today)
- §4 fuplus/seamless-loop-inspector — no key, real output schema (best_trim_time_seconds,
  motion_continuity, edge_similarity, difference_heatmap). Takes a URL.
- §6 plimsoll/subtitle-forge — no key.
- §2 chart-broll-renderer — ours, free, already specced.
- Retained MOTION-01 clips are already hosted on cloud.inference.sh, so a PoC needs no
  upload (avoids the known local-path trap).

# =====================================================================
# §A-§C RESULTS — 2026-09-12 ~19:45Z. Total spend $0.13 (balance 92.88 -> 92.75).
# =====================================================================

## §A VERDICT: THE GATE IS NOT REAL. No procurement needed.
`pixverse/extend` ran with NO PIXVERSE_KEY configured (`belt secrets list` = empty).
  task 0xqvppatxkcxwb8y0yqq5vbaa0 | charged $0.045 | status completed
  payload VERIFIED: 5,415,423 bytes, h264 1080x1920, duration 21.967s
  input was 16.81s -> output 21.97s = a real 5.16s extension.
Independent confirmation: `inworld/text-to-speech-2` declares INWORLD_KEY and we
run it in PRODUCTION today. Declaring a secret does not gate access; the platform
supplies provider keys and bills us. ALL 11 "blocked" candidates are available.
`optional` is ABSENT from the JSON entirely — not false, not null.

### CORRECTION: my earlier control was invalid.
I reported that 3 "already-working" apps declare `required_secrets: none`, and used
that correlation to call the gate credible. Two of those app names DO NOT EXIST
(`bfl/flux-2`, `openai/gpt-image-1` both return "App not found"). With `--json` the
error response still parsed as valid JSON, contained no `required_secrets` key, and
my parser printed "none required". The control was measuring a 404, not an app.
Real names: infsh/flux-1-dev, infsh/flux-2-klein, openai/gpt-image-2,
inworld/text-to-speech-2 — and ALL of them declare a provider key.
Lesson: a parser that cannot distinguish "absent field" from "error payload" will
manufacture whatever the absent field implies. Same family as T-58.

### NEW BLOCKER for §1 (found by the PoC, invisible in the catalog)
Extending a COMPOSITED render makes the model REGENERATE our overlays as pixels.
Measured by OCR on the output:
  t=14s (original footage)  -> "Original: vizm"   [attribution readable]
  t=18s, t=20s (generated)  -> no attribution line at all
The FrameDrift logo is warped ("ameDrift"/"eframedrift"); hook text is baked in and
duplicated. This violates the attribution invariant (L6 frame watermark) for the
whole generated tail.
=> ARCHITECTURE RULE: extend the SOURCE clip BEFORE compositing, then composite
   overlays across the full extended timeline. Never extend a finished render.

## §B seamless-loop-inspector: BLOCKED by a real incompatibility with our renders.
Fails on every retained render at final-frame extraction:
  anime  "Could not extract frame at 16.768s"  (task 59mt1ztk2s57nm5g9jnk1gp2bt, $0.00)
  gaming "Could not extract frame at 18.516s"  (2 tasks, both $0.00; window size irrelevant)
ROOT CAUSE (reproduced locally, not inferred):
  format duration = 16.810s   <- container (max across streams, incl. AAC tail)
  video stream    = 16.767s   <- 503 frames @ 30fps
  app seeks (format_duration - epsilon) = 16.768s, which is PAST the last video frame.
  ffmpeg fails at that timestamp too, in both seek modes. `-sseof` succeeds.
  Our AAC audio tail runs ~43ms longer than video. Harmless to platforms; fatal to
  any tool that derives a seek target from format duration.
WORKAROUND (untested): remux so format duration == video stream duration, then re-run.
On the PixVerse re-encode (durations aligned) it did NOT fail — it ran 6m59s without
completing and was cancelled at $0.00. Unpriced app, pathological runtime, no result.
=> §B.1 is NOT usable today. §B.2 (subtitle-forge) not yet tested.

## §C stills path: MEASURED, real billed runs, and it is the cheap path.
| app | billed/image | warm latency | output dims | note |
|---|---|---|---|---|
| pruna/flux-2-klein-4b | **$0.001** | **3.6s** (cold 5.5s) | 768x1360 | 9:16 OK but UNDER 1080x1920 — raise output_megapixels |
| openai/gpt-image-2 (quality=medium) | **$0.0412** | **35.8s** (cold 38.5s) | 1024x1536 | billed 3.1x its published "medium $0.0132" |
At 7 stills x 5 niches/day (35 images):
  flux-2-klein-4b : $0.035/day, ~25s per niche
  gpt-image-2     : $1.44/day,  ~4.2 min per niche
Both inside <$5/day. flux is 41x cheaper and 10x faster; quality verified good
(clean 9:16 anime key art, no text artifacts). Recommend flux as default, gpt-image-2
only for hero frames if a quality delta justifies 41x.

## §D: MOOT. No provider key needs provisioning. Nothing to sign up for, so the
"terms get read at signup" step does not arise for belt-run apps. The governing
chain still applies to OUTPUT rights and is unread — that remains open.

## BILLING MECHANIC (new, affects every cost reading we take)
A running task places a **$5.00 reservation hold**, which `belt balance` reports as
reduced balance. It is NOT spend. Cancelling released it ($87.75 -> $92.75, final
charge $0.00). Any "spend to date" figure read from `belt balance` while a task is
in flight is overstated by $5.00 per running task. Reconcile against `belt task cost`.

## Catalog-vs-billed discrepancies found (3 so far)
1. flux-3-video headline "$17/sec" vs variable $0.17 — template drops the decimal.
2. gpt-image-2 published medium $0.0132 vs billed $0.0412 (3.1x).
3. estimate accepts documented-impossible combos (duration 8 + 1080p) and quotes them.
