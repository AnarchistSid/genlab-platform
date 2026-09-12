# CAP-01 §0 — cost and terms
**2026-09-11 ~23:40 IST · $0.00 spent · balance $89.05 (verified, not carried)**

Status: **COST half complete. TERMS half BLOCKED — see §B.**
Per §0's own rule ("no batch runs until it exists"), this file exists but
records a blocker rather than a clearance. No batch has run.

## A — Cost (MEASURED via `belt app pricing`, no billed runs)

Method note: `belt app pricing` and `belt app estimate` return billing data
**without executing**, so the cost half was obtained for $0.00 rather than by
§0's run-once-and-read-`task_get` method. That method is still required for the
six unpriced apps below.

| app | published pricing | price variable | §0 method needed? |
|---|---|---|---|
| elevenlabs/voice-design | $0.10/design | per_design $0.1000 | no |
| elevenlabs/forced-alignment | $0.48/hr audio | per_hour $0.4800 | no |
| mirage/video-captions | — | rate_per_minute $0.1500 | no |
| elevenlabs/music | $0.15/min | per_minute $0.1500 | no |
| minimax/music-3-0 | $0.15/track | partner_per_request $0.1500 | no |
| elevenlabs/sound-effects | **$0.12/min** | **per_generation $0.1800** | **see anomaly 1** |
| bytedance/seedance-2-0-mini | $0.0035/K tok (text), $0.0021/K (visual) | per_1k_tokens_no_video | yes — token→video unclear |
| runway/gen-4-turbo | **$0.05/sec** | partner_per_second $0.0500 | no |
| inworld/voice-design | none published | — | **yes** |
| anarchistsid/loudness-normalize | none published | — | **yes** |
| plimsoll/subtitle-forge | none published | — | **yes** |
| mirage/text-overlays | none published | — | **yes** |
| anarchistsid/reel-spec-check | none published | — | **yes** |
| anarchistsid/shot-density-check | none published | — | **yes** |
| anarchistsid/chart-broll-renderer | none published | — | **yes** |
| anarchistsid/bt709-metadata-retag | none published | — | **yes** |

### Anomaly 1 — sound-effects headline disagrees with billed variable
Headline says **$0.12/min**; the price variable billed is
**per_generation $0.1800**. These are different units *and* different numbers.
§3 asks for 12 hits per niche × 5 niches = 60 generations → **$10.80** at the
variable, versus an unknowable figure at the headline. This is the second-largest
line in the sprint and it cannot be predicted from the headline. Treat
per_generation as authoritative and confirm on the first billed call.

### Anomaly 2 — runway/gen-4-turbo is affordable for the test, not for production
§4's test scope (3 reels × 5–7 clips × 3–5 s = 15–35 s generated per reel) costs
**$0.75–1.75/reel**, so ~$2.25–5.25 for the three test reels. Fine inside $40.

Projected to production it is a different object: 5 niches × 1 reel/day ×
~$1.25 = **$6.25/day ≈ $187/month** for one app — against the standing
`<$5/day` target in `.claude/rules/optimization.md`. seedance-2-0-mini is
cheaper but token-priced with no published per-second figure, so the comparison
§4 asks for ("if it exceeds the per-reel budget line, the cheaper model is the
default") cannot be made until seedance is probed once.

### Projected sprint total (INFERRED from the above, ±)
§1 ~$0.50 · §2 ~$0.25 · §3 ~$15.30 (60 SFX + 30 music tracks) · §4 ~$5.25 ·
§5 unpriced. **~$21–26 of the $40 ring-fence**, with §3 the dominant line.

## B — Terms: BLOCKED, and the gate cannot be satisfied as written

`belt app get <app>` returns exactly: description, version, pricing, input
schema, output schema, run/batch/estimate/sample commands. **There is no
licence, commercial-use, ownership, attribution, or rights field on the record
for any app.** Verified against all five rights-critical apps
(elevenlabs/music, minimax/music-3-0, elevenlabs/sound-effects,
inworld/voice-design, elevenlabs/voice-design) — grep for
licen|commercial|terms|copyright|ownership|rights|attribution returned nothing
on every one.

Consequences, stated rather than worked around:

* **§3's gate is unsatisfiable from the platform.** It reads "licence terms from
  §0 permit commercial multi-platform use, or the kit is `eval_only` until they
  do." Since §0 cannot establish terms, **the audio kit is `eval_only` by
  default and stays there** — not as a finding about the music, but because the
  evidence the gate requires does not exist at the source the gate names.
* **§1's voice-ownership question is likewise unanswerable here.** Whether a
  *designed* voice is ownable, exclusive, or resellable under SaaS is not on the
  app record. This matters more than the music: a per-niche voice becomes brand
  identity, and discovering later that it is non-exclusive or non-transferable
  would strand five channels' recognisable sound.

This is the same shape as the briefing-collector and drift-auditor bugs already
in the register: **a gate configured against a source that cannot answer it.**
The gate would have "passed" only by someone reading absence as permission.

Terms must come from the upstream providers' own ToS (ElevenLabs, MiniMax,
Inworld) plus inference.sh's platform terms, which is an operator/legal read,
not a CLI read. Filed as a blocker, not attempted.

## C — What is unblocked right now
§1 voice *design and synthesis* (eval_only, listen-verdict pending anyway),
§2 captions entirely (no rights question — alignment and styling of our own
script), §5 hook variants, §6 gate wiring as a branch. §3 proceeds only as
`eval_only` with no path to production until §B is resolved. §4's rights posture
is separate and already constrained by its own "official key art / no episode
footage" rule.

---

# Part 2 — Terms rulings + measured run data (CAP-01b, 2026-09-11)

## Terms (operator ruling, recorded verbatim in intent)
* **inference.sh:** customer owns input and output; commercial use incl. sale and
  publication granted, subject to Model Terms (inference.sh/terms, Feb 2026).
* **ElevenLabs via belt (paid channel):** output owned by customer, commercial use
  in videos/apps permitted indefinitely. **Eleven Music needs an additional licence
  for advertising, film/TV, games and enterprise distribution. Voice exclusivity is
  NOT guaranteed.**
* **§3 audio kit:** cleared for the five owned channels on YT/IG/FB/Threads/X.
  **NOT cleared for SaaS** — flagged `owned_channels_only` in config. SFX and voice
  cleared for both.
* **§1 voices:** cleared for owned channels; non-exclusivity accepted. Design prompt
  + provider ID recorded as the brand asset. SaaS gets per-customer design or
  consented clone, never a shared house voice → **T-33**.
* **Inworld and MiniMax terms: NOT READ.** Their outputs stay `eval_only`.
  ElevenLabs is the production provider for voice, SFX and beds.
* Not legal advice; summaries of published terms. Counsel review before SaaS.

## §0 cost, now MEASURED against real billing

| app | published | measured | note |
|---|---|---|---|
| elevenlabs/voice-design | $0.10/design | **$0.10/design exactly** | $89.05→$88.55 over 5 designs |
| anarchistsid/* (self-owned) | none | **$0.0000** | free to run; 15-file batch cost nothing |
| inworld/voice-design | none | **≤$0.005** | 1 of 5 completed; below 2dp resolution |

**Spent this session: $0.50. Balance $88.55 of the $40 fence.**

## Operational constraints discovered by running (all MEASURED)

1. **ElevenLabs caps at 2 concurrent requests.** A 5-wide batch returned
   `429 concurrent_limit_exceeded` on 2 of 5. Serial retry succeeded. **This
   governs every later EL batch — §3's 25 SFX generations and all music must
   run ≤2 at a time or they will partially fail.**
2. **Inworld rate-limits harder.** A 5-wide batch returned 429 on three and 503
   on one; only 1 of 5 completed. Needs serialization plus retry/backoff.
3. **Inworld caps `design_prompt` at 250 chars; ElevenLabs has no such limit.**
   The same prompt text cannot be used for both — rejected at submit, unbilled.
   Two provider-specific prompt sets are now maintained.
4. **`belt app run` exits 0 on a FAILED task.** The body carried
   `status_text: failed` while the shell saw success. **Never trust the exit code;
   parse `status_text`.** Same shape as rule #26, inverted — here a failure
   presents as success.
5. **Cloud apps cannot read local paths.** `media: /Users/...` returned
   "Input file missing". Pass a public URL, or upload first.
6. **A cost probe on a FAILED call reads $0.0000** — and looks exactly like a
   free app. My first normalize probe reported "$0.0000 per call" from a run that
   never did the work. **A cost reading is only valid when paired with
   `status_text == completed`.** T-20 at the billing layer.
7. **Rule #25 applies to the inference.sh CDN.** `urllib.request` got HTTP 403 on
   `cloud.inference.sh` preview URLs; `curl` with an explicit UA worked. Default
   `Python-urllib/3.14` is fingerprinted.
8. **`loudness-normalize`'s description ("Video in, video out") is narrower than
   its schema**, which reads "Audio or video file". It accepts MP3. Checking the
   schema rather than the prose is what made §1 possible.

## §1 result (ElevenLabs — production candidate)
5 designs × 3 previews = **15 candidates**, all normalized to −14 LUFS streaming
(two-pass, free). 13/15 `on_target`; `ai_creators_cand3` (−15.55) and
`gaming_cand3` (−15.78) undershot >1 LU and need a second pass if chosen.
Independent check: local ffmpeg ebur128 read −18.9 where the app read −18.99 —
the two measurement paths agree.

Retention: `~/cap-01/retention/voices_el/` (raw) and `voices_el_norm/`
(normalized, 9.7 MB). Listen sheet: `CAP-01_listen_sheet.md`. **HUMAN-PENDING.**

## §1 result (Inworld — eval_only)
1 of 5 designs completed (gaming, 3 previews) before rate limits. Remaining four
pending a serialized retry. Blocked from production by unread terms regardless.

---

# Part 3 — Inworld terms (VOICE-02 §1). Read 2026-09-12.

**Why this was urgent and not a precondition:** `infsh_inworld` is the LIVE
production TTS head — FIX-T01 measured it as the actual provider on every niche —
so these terms have been governing published output for weeks while recorded as
NOT READ. This closes a live gap, not a future one.

## The governing chain

Two documents, and the order matters.

**inference.sh Terms** — verbatim:
> "you own and will continue to own all rights, title, and interest, including
> all intellectual property rights, in and to your Customer Input and Output
> Content."
> "inference shell hereby grants you all right, title, and interest, if any, in
> and to Output Content, including your use of Output Content for commercial
> purposes such as sale or publication, **subject to any applicable Model
> Terms**."
> "Certain AI models and Third-Party Services … may be subject to additional
> license terms, restrictions, or usage requirements imposed by their respective
> providers ("Model Terms")."
> "**If there is a conflict between these Terms and applicable Model Terms, the
> more restrictive provision applies.**"

So inference.sh grants commercial use but defers to Inworld wherever Inworld is
stricter. Inworld is the governing document for anything inference.sh does not
settle.

**Inworld Terms of Service** — verbatim:
> "Subject to your compliance with our Terms, we assign to you all of our right,
> title, and interest in Outputs."
> "you grant Inworld AI a non-exclusive, worldwide license to use, reproduce,
> distribute, modify, and create derivative works from **Materials** to operate,
> maintain, and improve the Services"

## Point by point

| question | finding | source standing |
|---|---|---|
| Commercial use on owned channels | **Clear enough to proceed.** Ownership of Outputs is *assigned* by the ToS, and no clause restricts commercial use. | ToS verbatim (ownership); commercial-use affirmation only on product pages |
| Ownership of a designed voice | **Assigned** under the Outputs clause | ToS verbatim |
| **Exclusivity of a published voice** | **NOT ADDRESSED.** No guarantee anywhere. Marketing distinguishes an "exclusive brand voice" as a *separate* offering, which implies the default is non-exclusive. | absent from ToS |
| SaaS / redistribution limits | **NOT ADDRESSED by Inworld.** inference.sh lists redistribution as a *possible* Model-Terms restriction; Inworld states none. | inference.sh verbatim; Inworld silent |
| Attribution / disclosure | **NOT ADDRESSED** in any document read | absent |

## Stated without resolving favourably

* The affirmative "yours to use … including for commercial applications" appears
  on Inworld's **product pages, not in the ToS**. The ToS supports it by
  assigning ownership and imposing no restriction, but the direct permission is
  marketing copy, and the page itself says to "review the latest terms". Recorded
  as clear-by-ownership, not as clear-by-explicit-grant.
* **Exclusivity is unaddressed, so assume none** — the same position taken for
  ElevenLabs ("voice exclusivity is not guaranteed"). A designed voice may be
  reproducible by anyone using the same prompt and seed; our own reproducibility
  test proves that is exactly how the mechanism behaves.
* The Inworld license over "Materials" covers *inputs*, not Outputs, which are
  assigned. Materials is not defined in the extract read; if it were construed to
  include Outputs it would conflict with the assignment clause. Flagged, not
  resolved.

## Material caveat — research preview

Inworld **voice design** is "in research preview": *"The API, schema, and
supported parameters may change as the feature matures."* Building five channels'
permanent brand identity on a preview feature is a real risk: the voices are
reproducible from `(prompt, seed)` only while that API behaves as it does today.
`voice_designs.yaml` mitigates but does not remove it.

## RULING

**Commercial use on owned channels: CLEAR. `eval_only` is LIFTED for Inworld TTS
output on the five owned channels** (YouTube, Instagram, Facebook, Threads, X).
Ownership is assigned by the ToS and nothing restricts publication — and this
retroactively covers the weeks of production output already published through
`infsh_inworld`. **No problem found for live production.**

**SaaS phase: NOT cleared, same posture as ElevenLabs.** Exclusivity and
redistribution are unaddressed, and inference.sh's "more restrictive provision
applies" means silence cannot be read as permission at that scale. Flag
`owned_channels_only` stands for Inworld exactly as for ElevenLabs music.

Not legal advice; a summary of published terms read on 2026-09-12. Counsel review
before the SaaS phase, and re-read when voice design leaves research preview.
