# NARR-02 §1 — narration diagnosis. READ-ONLY. 2026-09-13 ~09:00Z.
All findings Measured unless marked.

## HEADLINE: FIX-LLMFB is EXONERATED. The LLM was never called.

## 1a — the deploy correlation: correlated in time, refuted as cause

**Deploy boundary [Measured]:** prod HEAD `e0143ec6`, pulled 2026-09-12 **19:52:37 IST =
14:22Z**. `32b06809` (belt Haiku fallback) IS in prod; `3ee79e54` (docs) is absent.
Every 09-12 pipeline fire (02:30–06:00Z) ran on PRE-deploy code; every 09-13 fire POST.

**Provider attribution, ai_creators [Measured]:**

| run | llm_usd | entry_count | by_model |
|---|---|---|---|
| 09-09 02:55 | **0.0000** | 4 | `{"tts": 0.0041}` |
| 09-10 02:46 | 0.0283 | 11 | tts + claude-sonnet-4-6 + claude-haiku-4-5 |
| 09-11 02:59 | 0.0803 | 32 | tts + sonnet + haiku |
| 09-12 03:02 | 0.1044 | 40 | tts + sonnet + haiku |
| 09-13 02:55 | **0.0000** | 3 | `{"tts": 0.004}` |

On both failure days LLM spend is **exactly $0.0000** and `by_model` carries **only tts**.
**No LLM call was made at all** — not a failed call, not a failover, not an empty response.

**Did anything route to the belt? NO.** Runs since the deploy whose `by_model` mentions
`belt` or the belt haiku slug: **0** (positive control: 7 rows exist in that window).
`call_fallback_chain` never fired. The "empty `openai` belt response with green status"
hypothesis is **refuted** — the belt was never called, so its response shape is irrelevant.

**Was the writer path altered when not failing over?** Not reachable as a question: the
writer made no LLM call on 09-13, so no request payload existed to differ. **Filed as a
finding:** the writer's request payload is not logged anywhere and cannot be reconstructed
from the blueprint — a 09-11 vs 09-13 payload diff is impossible today.

**The failure mode PREDATES the deploy [Measured].** `script_generation_failed` occurred
4× on **09-09**, three days before FIX-LLMFB existed:

| day | outcome | vs deploy |
|---|---|---|
| 09-09 | `script_generation_failed` ×4 | pre |
| 09-10 | **260 chars** | pre |
| 09-11 | **356 chars** + `script_too_long` | pre |
| 09-12 | **359 chars** + `script_too_long` ×3 | pre |
| 09-13 | `script_generation_failed` ×3 | POST |

Note the two distinct modes: on 09-11/09-12 generation **succeeded** (`script_too_long`
means a script existed and was rejected on length — T-08's haircut). On 09-09 and 09-13
generation never happened. The deploy coincides with a recurrence, not an onset.

## ROOT CAUSE [Measured] — the LLM is being SKIPPED upstream of the writer

ai pipeline journal, 09-13 fire (08:11:00 IST):
```
[ai_creators] HookStrategy: generated 0 hooks, skipped 3 (LLM), validated 3, rejected 0
```
**`skipped 3 (LLM)`** — every story had its LLM call skipped. That is the `_skip_llm` path
(`base_writing._has_writable_context`, 40-char floor). It explains `llm_usd = $0.0000`
exactly, and it is upstream of everything FIX-LLMFB touches.

The full chain, each link measured in the same fire:
1. stories arrive without writable context -> `_skip_llm`
2. `HookStrategy: generated 0 hooks, skipped 3 (LLM)`
3. writer emits no `narration_script`
4. `[GenerateAudio] narration enabled for ai_creators but writer emitted empty
   narration_script (reason=script_generation_failed) — degrading to legacy audio path`
5. `[transformation_orchestrator] narration already degraded upstream … using legacy 2-input mix`
6. TTS still runs (`tts tier attempted=infsh_inworld used=infsh_inworld fell_back=False`)
   on the **legacy path**, whose input is the caption

**Step 6 is the link to the 06:20Z finding**: `spoken_text` contains hashtags and a URL
because the legacy audio path speaks the caption when no narration script exists. One
root cause, two symptoms.

### Could not test the context floor from the DB [Measured limitation]
`summary` / `description_snippet` / `description` are **0 chars on all 14 ai_creators
blueprints since 09-09 — including the three that DID produce scripts.** The persisted
columns do not carry the story context the writer saw, so they cannot discriminate
success from failure. The journal can; the blueprint cannot. Same family as "store
inputs, not just outputs" (SWEEP-01 §B).

## 1b — degraded reason, VERBATIM [Measured]
* ai_creators ×3: `degraded=true`, reason = **`script_generation_failed`**
* anime / gaming / movies / sports ×12: `degraded=false`, reason = **empty string**

The four non-canary niches are not degraded because they never attempt narration.
`degraded=false` there means "not attempted", not "succeeded" — a false-green shape.

## 1c — have the four ever had a script? NO. [Measured]
30-day coverage:

| niche | blueprints | with_script | max_chars | first | last |
|---|---|---|---|---|---|
| ai_creators | 60 | **3** | 359 | 2026-09-10 02:46 | 2026-09-12 03:02 |
| anime | 42 | **0** | 0 | — | — |
| gaming | 63 | **0** | 0 | — | — |
| movies | 86 | **0** | 0 | — | — |
| sports | 68 | **0** | 0 | — | — |

**This is an absence on four niches plus a very thin canary on one** — ai_creators produced
3 scripts in its entire 30-day history, all inside a 3-day window. The operator's 1c branch
resolves: **§3 of NARR-02 (restore on all five) is the fix**, not a regression hunt.

## 1d — script vs spoken: NOT COMPARABLE today
`narration_script` is 0 chars on all five niches today, so a byte-for-byte comparison has
no left-hand side. The 3c904e01 divergence (audio contains "Other foldables have existed…"
absent from its 356-char script) therefore stands **unexplained and open** — it cannot be
retested until narration produces a script again. Re-run the comparison on the first fire
after §3.

## Incidental, filed not fixed
`[out#0/mp4] Nothing was written into output file, because at least one of its streams
received no packets` — twice in the 09-13 ai fire (08:15:41, 08:18:46). A render producing
an empty output file. Not investigated here.

## Record
**#218's closure is a measured fact about one blueprint created 2026-09-11. It does not
certify that narration works today — it does not.** Both statements are true and both
stand recorded. The capability was thin when certified (3 of 60) and is now at 0 of 3.

# =====================================================================
# RETRACTION + CORRECTED ROOT CAUSE — INGEST-01 trace, 2026-09-13 ~09:40Z
# =====================================================================

## I RETRACT the root cause recorded above ("the LLM is being SKIPPED upstream").

**It is wrong.** The ordered journal from the same fire disproves it:
```
08:10:30 base_writing: [ai_creators] Using LLM writing with model=claude-sonnet-4-6
08:10:49 base_writing: [ai_creators] WritingStrategy: wrote 3 stories (3 LLM, 0 failed)
08:11:00 base_hooks:   [ai_creators] HookStrategy: generated 0 hooks, skipped 3 (LLM), validated 3, rejected 0
08:11:00 GenerateAudio: writer emitted empty narration_script (reason=script_generation_failed)
```
**The writer called the LLM and succeeded on all three stories.** `_skip_llm` never fired:
fixed-string searches for all three assignment messages return **0 hits** in the window
(control: 626 lines) — "Skipping story with thin context" (`base_writing:1300`, INFO),
"Template writer produced empty caption" (`:395`, WARNING), "LLM returned no platform body
content" (`:1027`, WARNING).

**Two errors of mine combined to produce the wrong answer:**
1. I read `HookStrategy: … skipped 3 (LLM)` as the writer skipping. It is **BBHookStrategy**
   declining to LLM-*generate hooks* — it validated 3 instead — and runs *after* the writer
   has already completed. Right string, wrong stage.
2. I treated `llm_usd = $0.0000` as proof no LLM call happened. **It is a cost-attribution
   failure.** `claude-sonnet-4-6` is named in the log at 08:10:30 while `by_model` for that
   run records `{"tts": 0.004}` only. The spend is real and unrecorded.
   **New finding (HIGH):** `pipeline_run_costs` did not record writer LLM spend on 09-13
   (entry_count 3 vs 40 on 09-12). Any cost/provider analysis off that table for 09-13 is
   invalid — including my own §1a table above, whose $0.0000 column is an artifact.

## CORRECTED ROOT CAUSE [Measured]: narration_script dies in a PROPAGATOR, not at the LLM.

`base_writing.py:866-874` documents this exact failure from 2026-08-19 (NARR-06), verbatim:
> *"the LLM was emitting narration_script (verified via standalone reproduction — response
> JSON contained 350+ chars of legit on-topic commentary), but this cherry-pick propagator
> only copied hook + instagram_caption. narration_script fell on the floor, GenerateAudio
> saw empty content['narration_script'], marked the blueprint narration_degraded=true with
> reason script_generation_failed. Two real-fire recurrences over 2 [days]"*

Today's signature is identical: LLM succeeds, `narration_script` empty,
`reason=script_generation_failed`. This is the memoed class
`[[class-of-bug-pass-through-fields-die-in-explicit-propagators]]` — a 4-gate chain
(writer result -> content dict -> blueprint -> column) where fixing one gate buys false
confidence. `ae76e975` fixed gate 1 on 08-19; the symptom is back.

**Still open:** which gate fails now. Not determined this pass — it needs either the writer
response logged (see below) or a standalone reproduction like NARR-06's.

**Why 09-11/09-12 differ:** those days failed with `script_too_long` — validation rejecting
a script that EXISTED (`validate_narration_script`, `base_writing:680`, retry at `:730`).
That is a different failure entirely. Two modes, two causes:
  * `script_too_long`   -> script produced, rejected on length (T-08 haircut)
  * `script_generation_failed` -> script never reached `content["narration_script"]`

## INGEST-01 §1 — field-flow trace, boundary 1 findings [Measured]

| field | fetch_reddit_clips | fetch_scorebat | trending_video_fetcher (`to_story`) |
|---|---|---|---|
| `summary` | **absent** | `"{competition} highlight"` (~25 chars, **below the 40 floor**) | present via `_writable_summary()` |
| `description_snippet` | **absent** | absent | present |
| `description` | **absent** | absent | absent |
| `view_count` | absent | absent | **present** |
| **`like_count`** | absent | absent | **ABSENT** |

**§5 SEAM CONFIRMED — and it is at the fetcher, not a later boundary.**
`TrendingVideo.to_story()` (`trending_video_fetcher.py:337-395`) carries `view_count` but
**never `like_count`** (grep count = 0). `CompositeScorer` computes
`engagement_score = like_count/view_count / target_like_ratio`; with `like_count` absent it
is 0, so `engagement_factor` pins at its 0.5 floor — precisely the constant measured in
SWEEP-01 §R5. **Fix: add `like_count` to `to_story()`.** One field, one file.

**Reddit supplies none of the three context fields** — so any niche sourcing from
`reddit:*` reaches the writer with no writable context. ScoreBat supplies a sub-floor string.
Only the YouTube path is context-complete, and `_writable_summary()` synthesizes from
title+channel+tags when the description is thin — which is why ai_creators (100%
`youtube_trending` every day) never actually hit the thin-context gate.

**Boundaries 2 and 3 as named in the brief do not exist.** There is no
`dedupe_rank_items.py` and no `compose_blueprints.py`. The real ai_creators stage list
(24 stages, from the runner's own log) is:
ExpressLane · FetchTrendingVideos · FetchRedditClips · BBContentResearchStrategy ·
BBScoringStrategy · RelevanceGate · PreDownloadDedup · DownloadTopVideos · VideoGate ·
BBWritingStrategy · BBHookStrategy · AffiliateMatch · QCGates · ViralityScoring ·
GenerateAudio · BBVisualRenderStrategy · RenderTextOverlays · RenderWhisperCaptions ·
ValidateVideos · BBPlatformAdaptationStrategy · PushToBacklog · FetchInsights ·
PerformanceLearner · RunReport.
(This also re-confirms the 24-stage count retracted-and-restored in SWEEP-01 §3.6.)

## §2 — the predicate, verbatim [Documented]
`base_writing.py:114` `_has_writable_context(story)`: for each of
`("summary", "description_snippet", "description")`, passes if the value clears
`_MIN_WRITABLE_CONTEXT_CHARS = 40` **and** is not URL-dominant (`_is_url_dominant`, added
after fetch_reddit_clips wrote a permalink as `summary` — >40 chars, zero content).
**Known divergence, documented in-file at `:216-227`:** the gate passes on ANY of the three,
but the LLM prompt reads **only `summary`** (`llm_hook_generator.py:400`) — the movies
backlog-starvation bug of 2026-07-22. Gate and consumer still disagree.

## §3 — the skip is NOT silent (correcting the brief's premise)
All three `_skip_llm` sites log: `:395` WARNING, `:1027` WARNING, `:1300` INFO. The INFO
level on the context gate is the weakest, but none is silent. The brief's premise that
`_skip_llm` is a silent degrade does not hold — it simply did not fire today.
**What IS silent** is the propagator drop: `narration_script` vanishing between the LLM
response and `content[...]` produces no log at all; the first sign is GenerateAudio's
WARNING one stage later, which names the symptom, not the cause.

## §6 — filed
The writer's request AND response payloads are unlogged. NARR-06 needed a *standalone
reproduction* to prove the LLM was emitting the field; that cost is being paid twice.
Logging the response JSON keys (not the content) at DEBUG, or a hash plus prompt-template
version, would make the next diff a one-liner.
