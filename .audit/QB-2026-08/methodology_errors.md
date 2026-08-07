# QB-2026-08 Methodology Error Log

Per prompt Section 0.7: log both prompt-origin errors and execution errors, including reversals. Zero-error audit ⇒ under-scrutinized.

---

## Phase 0

### ME-01 — Execution error — Assumed VPS `.venv` was uv-managed with the measurement stack

* **What happened:** Initial SSH probe assumed `/opt/genlab/.venv/bin/python` was a uv-managed venv that pulled in the full workspace deps (librosa, scenedetect, whisper, etc.).
* **Reality:** `ls -la /opt/genlab/.venv/bin/python*` shows it is a symlink chain to `/usr/bin/python3`. The real workspace stack lives under uv's cache but `uv run` inside `/opt/genlab` also failed to import `scenedetect` (never installed on VPS).
* **Reversal:** Set up isolated audit venv on Mac at `.audit/QB-2026-08/.venv/` with the measurement stack. VPS-side deep measurements are not possible without a pip install; Phase 1+ analyses run on Mac against MP4s pulled from VPS.
* **Detection:** direct `ls -la` on the venv symlink before trusting `python --version` output.

### ME-02 — Execution error — Explore agent 2 returned paths with a typo (`/Users/anthropistsid/…` vs. actual `/Users/anarchistsid/…`)

* **What happened:** Second dispatched Explore agent (DB-schema task) reported paths and also file existence claims for paths that don't exist as-quoted. Additionally reported `rendering/video_gate.py` — the file is at `pipeline/stages/video_gate.py`.
* **Reversal:** Verified every reported path with `[ -f "$p" ] && echo OK || echo MISSING` before accepting into the phase report.
* **Detection heuristic:** any subagent report that quotes file paths must be verified against `find` or `[ -f ]` before use.

### ME-03 — Execution error — Two Explore agents contradicted on IG metric fetcher content

* **What happened:** Agent 1 claimed IG fetcher does NOT capture sends and dm_send_rate ("explicitly omitted for RewardShaper weight redistribution"); agent 2 claimed `total_interactions` IS captured and `sends_per_reach` IS derived via `retention_derivations.py`. Both cannot be true simultaneously.
* **Reversal:** Deferred resolution to Phase 8, where I will read `metrics/instagram.py` and `retention_derivations.py` directly and pin a specific line-and-value answer, not accept a subagent claim.
* **Detection heuristic:** any time two subagents disagree on a factual claim, do not merge or pick a winner; open the source file myself.

### ME-04 — Prompt-origin ambiguity — Section 0.2 says "minimum sample: the 10 most recent rendered videos per channel"

* **What happened:** The prompt asks for "10 most recent rendered videos" but the artifact reality per Phase 0 findings is:
  * ai_creators: ≥10 available ✓
  * movies: ~6 fresh + older; can hit 10 with older 2026-07-22 window
  * anime: 6 fresh + older
  * sports: 0 rendered since 2026-07-28; only 5 blueprints × 4 platform variants
  * **gaming: 0** rendered in the entire retention window
* **How this was handled:** Recorded as F-QB-0002 (BLOCKER) and F-QB-0003 (BLOCKER). Every subsequent-phase finding scoped to "N=X across channels [list]" with gaming explicitly excluded as "cannot verify."

### ME-05 — Prompt-origin issue — Section 1.1 row 1 says thumbnails are near-irrelevant for Shorts

* **What happened:** The prompt correctly warns not to raise a HIGH-severity Shorts thumbnail gap. My Phase 0 F-QB-0009 respected this by grading MEDIUM (FB/IG grid) rather than HIGH.
* **Not an error, logged for traceability.**

### ME-08 — Escape from the deferral ledger — "real ducking delta" closed as N/A on a false premise

* **What happened:** Phase 3's deferral ledger recorded `Real ducking delta — Closed with reason (no music bed exists — F-QB-0302)`. Phase 9's synthesis carried that closure and asserted "zero escapes" in the deferral reconciliation. Both were wrong. The QB-FIX-01 F2 session (2026-08-06) discovered live music-bed mixing via `transformation_orchestrator.music_mood` + `audio_replacer.build_audio_mix_filtergraph` running against 125 real music files across 4 niches. F-QB-0302 is void; the ducking-delta measurement should have been carried forward.
* **How this happened:** Phase 3 grepped `tts/cascade.py`, `base_visual_render`, and `frame_compositor` for music-mixing code and found none. It did not open `media/transformation_orchestrator.py` or `media/audio_replacer.py`. The mixing lives in the transformation layer, which the entire audit had classified as post-render "styling" rather than a semantic content stage. Prompt Section 0.2 explicitly warned against this class of failure: "concluding a capability exists because the code appears to implement it." Here the failure is the mirror: concluding a capability does NOT exist because the code the auditor looked at doesn't implement it. Missing files from a code search is as much a measurement failure as reading the wrong file.
* **Reversal:** F-QB-0302, F-QB-0303, F-QB-0305 rewritten per QB-FIX-01 amended §1. F0 documented the 125-track library at `docs/audio-licensing.md`. Phase 9 gap matrix will be updated during any subsequent audit refresh. The "zero escapes" claim in `phase_9_synthesis.md` §2 is retracted here; the audit had one escape (this one).
* **Detection heuristic:** any audit finding phrased as "no code exists that does X" must include the search commands as evidence, and the search commands must cover EVERY module in the relevant subsystem tree (not just the subsystems the auditor knows about). For pipelines with plugin/transformation-stage architecture, "no X in the render stage" is not the same as "no X anywhere."

---

## Phase 9 + QB-FIX-01

### ME-06 — Execution error — F-QB-0004 initially claimed auto-approver skipped 4 niches

Originally logged inline in `phase_9_synthesis.md` §7. Consolidated here. Phase 8 code inspection revealed sports also has `auto_publish.enabled=true`; the empty `gate_examinations` for sports are because sports has zero VISUAL_READY blueprints upstream, not because the approver skips it. **Reversal:** F-QB-0803 supersedes F-QB-0004. **Detection heuristic:** empty-state signals must be traced to a specific cause (no data vs. no wire vs. no execution) before being logged as a capability gap.

### ME-07 — Execution error — Ducking-delta metric diverges on silence

Originally logged inline in `phase_9_synthesis.md` §7. Consolidated here. Phase 3's sliding-RMS `p90-p10` ducking-delta metric reports ~170 dB for silence-heavy tracks because `log10(near-zero)` diverges. Anime's continuous audio gave a plausible 6.6 dB. The metric is unusable for the intended purpose. **Reversal:** use `pyloudnorm.Meter.loudness_range()` instead. **Detection heuristic:** any dB-domain aggregate must be sanity-checked against silence (`dbfs → -inf`) and clip regions before being trusted as a comparison basis.

### ME-09 — Execution error — Dedup TTL reversal (Reddit fix session)

Referenced in `.audit/QB-FIX-01/reddit_dedup_report.md` as "logged" but never actually written to this file until now. Consolidated. The F3d-1-gate BLOCKED report claimed "URL dedup TTL not enforced — `dedup_keys` queries all active-status blueprints regardless of age" as one of three concurrent failure modes. Deep investigation reversed this: `dedup_keys.py` DOES honour `url_dedup_ttl_days` correctly (verified with real DB queries at prod). The dedup saturation was real but the TTL was not the cause. **Reversal:** the F3d-1-gate report's second bullet point is wrong. **Detection heuristic:** before attributing a pipeline failure to a config knob, query the actual DB rows that would be affected AND read the code path — code inspection alone can mislead when the runtime data doesn't match the code shape you're assuming.

### ME-10 — Execution error — F3d-4 "F0 zero-claims monitor" was reported as wired but has no consumer

Filed as part of QB-FIX-02 V0-c. The F3d-4 report on audio-claim routing recorded that the compliance_events schema exists and `fb_survival_check` writes to it. What it did NOT clearly state: no code path anywhere in the workspace maps a platform's "audio content matched / Content ID hit / partial mute" response INTO a `compliance_events` row with a distinct `event_type` for audio claims. `fb_survival_check` only knows "post removed / not removed" and writes a generic `REMOVED_BY_META` marker. **Consequence:** the F4 batch 1 publishes on 2026-08-07/08 will produce zero telemetry signal if any of the four posts is audio-muted by YouTube Content ID (mute, not remove). QB-FIX-02 §1 correctly downgrades the watch to manual because of this gap. **Reversal:** the F3d-4 "wired but zero claims" framing was misleading; the accurate framing is "no capture path exists AND no observations recorded, so 'zero claims' proves nothing." **Detection heuristic:** any "zero observations from a monitor" claim must include verification that the monitor's write path fires on a synthetic positive case before the zero can be interpreted as absence.

### ME-11 — Execution-pattern error — Gates measure aggregates while defects live in the composition

Filed as part of QB-FIX-02 V0-c. Recurring shape observed twice:

* **R-Encode-1:** F2 encode gate measured "final reel is 1080x1920" and passed. The actual defect (source clip was 640x360 upscaled) required inspecting the SOURCE tier before compositor scaling, not the FINAL output.
* **F3a:** Loudness gate measured "final reel is -14 LUFS" and passed. The actual F3a-2 defect (VO -12 dB / bed -6 dB — bed 6 dB louder than voice) lived in the mix filter graph, not in the final loudness measurement. A -14 LUFS output can be reached with any imbalanced mix — loudnorm normalises the sum, not the ratio.

**Pattern:** when a gate measures an aggregate of a multi-input composition, defects in the input ratios are silently smoothed away. **Detection heuristic:** for any gate that measures a composition output (audio mix, encode chain, layered visual), require an ADDITIONAL gate on each independent input tier, not just the final aggregate. This pattern applies to any downstream capability that could hide upstream defects behind a passing aggregate check.

### ME-16 — Execution-pattern error — A gate on an aggregate cannot localise a defect to a segment (third instance)

Filed as part of QB-FIX-10 D1. Third documented instance of the class-of-bug first named in ME-11.

**Three instances in this audit alone:**

1. **R-Encode-1** measured output bitrate; defect was source clip resolution (640x360 upscaled to 1080x1920 by compositor while output bitrate looked fine).
2. **F3a** measured aggregate integrated loudness at -14 LUFS; defect was internal mix ratio (music bed 6dB louder than source-video audio — voice buried but sum was correctly normalised).
3. **F-QB-0602** measured total event-to-publish lag (111-281h); attributed it to "fetcher cadence." QB-FIX-09 C1 decomposed into fetch / approver / slot segments and found the approver segment dominates 92-100% of the total. The fetchers were never the constraint — the gate could have been satisfied by making fetchers faster (which they already were) without moving the actual metric of concern (freshness at publish).

**Class-of-bug (canonical statement):** any verification gate that measures an end-to-end quantity is structurally unable to localise the defect to a specific segment. A gate can go green while the responsible segment gets worse, if a different segment compensates. Conversely, a gate can go red on a segment that is not the actual constraint.

**Detection heuristic:** for any proposed verification gate on a metric that has recognisable input → transformation → output segments (rendering pipeline stages, publishing pipeline stages, learning-loop stages), the gate MUST decompose the metric into per-segment measurements and target the segment where causal responsibility actually lives. "Total X" gates are structurally incomplete without a segment attribution.

**Applies retroactively:** any Phase 9 verification gate on an end-to-end metric needs review for segment-level decomposition. D1 Step 4 audit for this pass returned zero additional confirmed instances (see D1 report), but the pattern is likely to recur — mark it as a review criterion for future audit design.

**Reversal for F-QB-0602:** verification gate rewritten to target the approver segment only. Original gate (median lag ≤ 24/48/72h) is retired; new gate is `median(scheduled_for - created_at) ≤ 24h` per niche. Fetcher-latency and slot-contention segments are orthogonal and can be measured separately if needed.

### ME-14 — Execution error — Threads timeout size-correlation hypothesis wrong (measurement killed it)

Filed as part of QB-FIX-06 Z1 Step 3, formalized in QB-FIX-07 §5.

**Hypothesis:** sports' `Threads container processing timeout (180s)` on 2026-08-07 correlated with file size — a "large reel = long ingestion = timeout" story.

**Measurement:** sports Sainz reel was 2.22 MB / 18.6s / 0.78 Mbps — **smallest of the three published in that fire window**. Anime Saga of Tanya at 4.97 MB (2× larger) succeeded. Movies INHERIT at 3.98 MB also succeeded.

**Reversal:** timeout is uncorrelated with size. Correct read: legitimately transient Meta infrastructure at that moment. Filed as ME-14 for the record — the hypothesis was plausible and the measurement disproved it, which is the process working.

**Detection heuristic:** for any "timeout" hypothesis, verify size / duration / bitrate correlation before filing a fix. Timeouts have many causes (auth, ingestion queue, downstream service health, adjacent-request contention). Size is the first suspect but not the only one.

### ME-15 — Execution-pattern error — `render_error` persistence gap invalidates F-QB-0606's verification gate

Filed as part of QB-FIX-06 Z1 Step 1, formalized in QB-FIX-07 §5.

**Discovery:** all 4 sports DRAFTED rows had `extra->>'render_error'` = NULL, but the pipeline journal for the Aug 6 Pete Crow-Armstrong row (from QB-FIX-05 Y2) showed the pre-render quality gate rejected it with `hook_title_truncation`. The rejection reason exists at runtime and is discarded before persistence.

**Impact:** F-QB-0606's verification instruction — "check `extra->>'render_error'` on DRAFTED rows to determine whether pre_render_quality rejected them" — is structurally unanswerable. The column is uniformly NULL whether the gate ran, the gate rejected, or the gate is buggy. Any future audit finding that relies on `render_error` persistence for verification hits the same dead end.

**Class-of-bug:** verification gates that rely on runtime signals being persisted. If the persistence path is silently missing, the gate becomes structurally unanswerable regardless of what the runtime does. Detection heuristic: for any "check field X on the row" verification, verify that the write path for X exists AND fires on the branch being verified.

**Reversal:** reclassify from "observability follow-up" (my prior label in QB-FIX-06 Z1) to "invalidates a verification gate." Findings depending on `render_error` need re-verification via journal grep (fragile) or via adding write-side persistence (proper fix, not in this pass's scope).

### ME-13 — Execution error — Mitigation proposed against a mechanism it does not act on

Filed as part of QB-FIX-03 W0. QB-FIX-02 V2 recommended attenuating `source_audio_duck_db` from -6 to -9 dB on sports/movies/anime to "hedge fingerprint exposure on higher-risk niches." **The mechanism does not respond to the intervention.** Audio fingerprinting (YouTube Content ID, AudibleMagic, Meta Rights Manager) matches on signal *content* (spectral features, chroma vectors, MFCC hashes), not signal *level*. A 3 dB attenuation shifts amplitude, not the fingerprint. Section 1.3 of the audit prompt explicitly notes 2026 fingerprinting detects low-volume music beds — the same detector mechanism catches music mixed under speech at any level. What the -9 dB recommendation actually buys: a quieter reel. Zero hedge on the risk it named.

**Class-of-bug:** a control that reads as protective while acting on nothing relevant. Same shape as F0's zero-claims monitor (ME-10) — the write path never fires, so "zero observations" proves nothing about the underlying risk. In both cases the surface looks like defense-in-depth; the substance is theater.

**Reversal:** QB-FIX-03 W0 supersedes V2. Kept -6 uniformly across all five niches (adopted on V1's ground: source audio is the only aural content, no reason to duck). SpliceReel's copyright exposure is real but must be addressed at layers the risk actually responds to — clip length as fraction of source, source-use ratio, presence of original overlay content. Those are W3's scope, not W0.

**Detection heuristic:** for any proposed mitigation, write down the causal chain from control → mechanism → risk reduction, and identify one variable in the mechanism that the control moves. If none can be named, the control is theater. Applies to every future config knob proposed as a risk hedge — verify the knob is on a path the risk actually flows through.

### ME-12 — Execution error — F3b status drift between session recap and detail report

Filed as part of QB-FIX-02 V0-b. My QB-FIX-01 F4 session-end summary listed F3b among "verified live before approval" for the movies + anime F4 batch 1 reels. The authoritative artifact (`F3d_reports.md` §"F3b final status") says F3b is **NOT MEASURED end-to-end** — real gate remains the pending ai_creators whisper canary; movies has `whisper_sync.enabled=false` so its reels cannot exercise the whisper-caption clamp. **Reversal:** `F4_batch1_report.md` now carries an explicit correction section noting F3b is not verified on batch 1 artifacts; F3d_reports is authoritative. **Detection heuristic:** every fix claimed as "verified live" in a session recap must have a specific corresponding measurement command + output line in the persistent report artifact, not just an in-conversation mention. When session recaps and persistent reports drift, treat the persistent report as authoritative and correct the recap.

---
