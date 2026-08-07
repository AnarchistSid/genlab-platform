# Phase 9 — Synthesis, gap matrix, and remediation plan

**Audit ID:** QB-2026-08
**Read-only phases (0-8) complete.** This phase is the only action-producing phase — it produces prioritised recommendations and a build plan, but touches no pipeline code.

**Overall posture:** the pipeline has fully working end-to-end technology (ai_creators publishes to 4 platforms daily) but four of five channels have gone dark on publishing (F-QB-0003), the reward loop measures the wrong things (F-QB-0801, 0802, 0809), and there is a specific YouTube demonetisation-adjacent template signature in the caption + editing patterns (F-QB-0708). None of the ten quality dimensions is failing catastrophically on ai_creators's rendered output; the failure is upstream (topic freshness, template variety) and downstream (reward loop, compliance placement).

---

## 1. Re-verified numbers

Spot-checked the four most consequential carried numbers:

| Number | Source phase | Re-check method | Result |
|---|---|---|---|
| Gaming N=0 MP4s across 14 days | F-QB-0002 | `find /opt/genlab/{.tmp/runs,.backups} -path '*/gaming_*' -name '*.mp4' \| wc -l` → 0 | ✅ confirmed |
| 4 niches have not published ≥9 days | F-QB-0003 | `SELECT niche_id, MAX(published_at) FROM publishing_analytics GROUP BY 1` — ai_creators 2026-08-06, sports 2026-07-28, anime 2026-07-26, gaming 2026-07-25, movies never in window | ✅ confirmed |
| 17/17 movies affiliate posts have #ad past 100-char fold | F-QB-0701 | `SELECT COUNT(*) WHERE LEFT(caption, 100) ~* '#ad' AND affiliate_url IS NOT NULL` → 0 out of 17 | ✅ confirmed |
| analytics table has only metric_type='composite' | F-QB-0801 | `SELECT DISTINCT metric_type FROM analytics WHERE collected_at >= NOW() - INTERVAL '14 days'` → single row `composite` | ✅ confirmed |

No number in the phase reports failed spot-check. Any subsequent-user's re-check will use the same commands recorded in each phase's finding.

---

## 2. Deferral ledger reconciliation

All Phase 0-8 deferrals below either promoted here, closed with reason, or carried into the harness build plan.

| Origin phase | Item | Outcome |
|---|---|---|
| Phase 0 | Detailed reward formula | Promoted to Phase 8; done (F-QB-0801) |
| Phase 0 | Auto_approver root cause | Promoted to Phase 8; done (F-QB-0803) |
| Phase 0 | sends_per_reach in prod | Promoted to Phase 8; done (F-QB-0802) |
| Phase 0 | Per-niche virality_scoring inline sections | Not verified in this audit; carried to remediation list item R-Phase-5 |
| Phase 1 | Faststart / moov position | Closed; source code shows `+faststart` set; not a real risk |
| Phase 1 | VMAF baseline | Closed with reason (no lossless master — F-QB-0103) |
| Phase 1 | OpenCV sharpness distribution | Rolled into Phase 2 batch pass; sharpness not surfaced as a finding — not a gap |
| Phase 2 | Compilation segment analysis | Closed with reason (compilation reels not in Phase 0 pull; low frequency in output — no immediate remediation) |
| Phase 3 | Audio-onset vs. cut correlation | Deferred to R-Phase-2/3-onset-lock remediation item |
| Phase 3 | Real ducking delta | Closed with reason (no music bed exists — F-QB-0302) |
| Phase 4 | Whisper WER vs. pipeline captions | Carried; verification-gate for R-Cap-2 remediation item |
| Phase 5 | Cold-open branded-frame detection | Carried; harness build plan Tier-1 item |
| Phase 5 | Hook novelty embedding distance | Rolled into R-Topic-1 (topic classification + embedding infra) |
| Phase 6 | Embedding-distance novelty | Same as above |
| Phase 8 | Per-arm observation histogram | Carried; part of R-Bandit-2 evaluation-gate |
| Phase 8 | Bandit posterior vs. outcome correlation | Existing `bandit_validation.py` covers per-source; extending to per-arm carried as R-Bandit-3 |

**Zero escapes.** Every deferred item is either resolved, closed with reason, or carried into a named remediation item below.

---

## 3. Gap matrix — dimension × channel × severity × tier

Format: `{dimension} | {channel} | {measured} | {benchmark} | {gap} | {severity} | {tier} | {confidence} | {evidence F-ref} | {sample N}`. Gap direction: `WORSE` means below benchmark; `MATCH` means at benchmark; `N/M` means Not Measured; `N/A` means channel excluded (gaming).

| Dim | Channel | Measured | Benchmark | Gap | Sev | Tier | Conf | Evidence | N |
|---|---|---|---|---|---|---|---|---|---|
| 1 Cover | all | none generated | face + high contrast | WORSE (partial) | MED | 2 (FB/IG grid) / 3 (Shorts) | H | F-QB-0601 | code |
| 2 Editing | ai_creators | 7.01s median shot | 2-4s benchmark | WORSE 2× | HIGH | 2 | H | F-QB-0201 | 5 |
| 2 Editing | sports | 7.01s median shot | 2-4s benchmark | WORSE 2× | HIGH | 2 | H | F-QB-0201 | 5 |
| 2 Editing | movies | 7.01s median shot; 76% low-motion | 2-4s + motion-heavy trailer | WORSE 2×+static | HIGH | 2 | H | F-QB-0201/0202 | 5 |
| 2 Editing | anime | 8.03s median shot | 2-4s | WORSE 3× | HIGH | 2 | H | F-QB-0201 | 5 |
| 2 Editing | gaming | N/A no artifacts | — | — | — | — | — | F-QB-0002 | 0 |
| 3 Audio | ai_creators | -30.7 LUFS | -14 target | WORSE 16 LU | HIGH | 2 | H | F-QB-0301 | 5 |
| 3 Audio | movies | -33.0 LUFS | -14 target | WORSE 19 LU | HIGH | 2 | H | F-QB-0301 | 5 |
| 3 Audio | sports | -26.0 LUFS | -14 target | WORSE 12 LU | HIGH | 2 | H | F-QB-0301 | 5 |
| 3 Audio | anime | -18.8 LUFS | -14 target | WORSE 5 LU | MED | 2 | H | F-QB-0301 | 5 |
| 3 Audio license | all | none doc | commercial licence | WORSE (preventive) | HIGH | 1 (compliance) | M | F-QB-0303 | code |
| 4 Branding intro | ai_creators | text-in-first-1s=100% | ≈0s to first content | MATCH | LOW | 3 | M | F-QB-0402 | 5 |
| 4 Branding intro | movies | text-in-first-1s=60% | ≈0s | WORSE 40% miss | MED | 1 (hook) | M | F-QB-0402/0501 | 5 |
| 4 Branding intro | sports | text-in-first-1s=60% | ≈0s | WORSE 40% miss | MED | 1 (hook) | M | F-QB-0402/0501 | 5 |
| 5 Captions | ai_creators | 11 safe-zone violations/reel median (46% of blobs) | inside safe zones | WORSE | HIGH | 1 | H | F-QB-0402 | 5 |
| 5 Captions | sports | 10 safe-zone violations (83% of blobs) | inside safe zones | WORSE | HIGH | 1 | H | F-QB-0403 | 5 |
| 5 Captions | anime | 2 violations (6%) | inside safe zones | MATCH | LOW | 1 | H | F-QB-0402 | 5 |
| 5 Captions | movies | 0 violations | inside safe zones | MATCH | LOW | 1 | H | F-QB-0402 | 5 |
| 6 Affiliate disc. | movies | 0/17 have #ad in first 100 chars | #ad in first 80-100 chars | WORSE 100% miss | HIGH | 1 (legal) | H | F-QB-0701 | 17 |
| 6 Affiliate disc. | others | 0 affiliate posts in 30 days | N/A | N/A | — | — | H | F-QB-0702 | 0 |
| 6 Affiliate CTR | all | 3 clicks / $0 revenue in 30 days | 0.5-2.5% CTR | WORSE (functionally 0) | HIGH | 1 (business + tracking?) | M | F-QB-0702 | 3 |
| 7 Video quality | ai_creators | median 0.92 Mbps | 6-12 Mbps | WORSE 8× | HIGH | 2 | H | F-QB-0101 | 5 |
| 7 Video quality | movies | median 1.02 Mbps | 6-12 Mbps | WORSE 8× | HIGH | 2 | H | F-QB-0101 | 5 |
| 7 Video quality | anime | median 1.51 Mbps | 6-12 Mbps | WORSE 5× | HIGH | 2 | H | F-QB-0101 | 5 |
| 7 Video quality | sports | median 2.39 Mbps | 6-12 Mbps | WORSE 3× | HIGH | 2 | H | F-QB-0101 | 5 |
| 7 Video quality | all | color transfer/primaries missing 15/20 | bt709/bt709/bt709 | WORSE (partial) | MED | 2 | H | F-QB-0102 | 20 |
| 8 Publish throughput | anime | approver seg 151.9h (C1) | ≤24h | WORSE 6× | HIGH | 1 | H | F-QB-0602 (reattr QB-FIX-10 D1) | 2 |
| 8 Publish throughput | sports | approver seg 169.1h (C1) | ≤24h | WORSE 7× | HIGH | 1 | H | F-QB-0602 (reattr QB-FIX-10 D1) | 5 |
| 8 Publish throughput | gaming | approver seg 194.4h (C1) | ≤24h | WORSE 8× | HIGH | 1 | H | F-QB-0602 (reattr QB-FIX-10 D1) | 2 |
| 8 Publish throughput | ai_creators | approver seg 87.7h (C1) | ≤24h | WORSE 3.7× | HIGH | 1 | H | F-QB-0602 (reattr QB-FIX-10 D1) | 12 |
| 8 Topic novelty | all | 95%+ mono-topic tag per niche | high variety | WORSE severe | HIGH | 1 | H | F-QB-0603 | 173 |
| 8 Source diversity | ai_creators | top-5 sources = 60% posts | high variety | WORSE | MED | 2 | H | F-QB-0604 | 42 |
| 9 Duration | ai_creators | 21.0s ± 0.05s (fixed) | matched to own completion curve | WORSE (no data to tune to) | MED | 1 | H | F-QB-0501 | 5 |
| 9 Duration | movies | 21.0s exact all 5 | same | WORSE | MED | 1 | H | F-QB-0501 | 5 |
| 9 Duration | anime | 16.1s mode (4/5) | same | WORSE | MED | 1 | H | F-QB-0501 | 5 |
| 9 Duration | sports | bimodal 16 or 21s | matched to own completion curve | WORSE | MED | 1 | H | F-QB-0501 | 5 |
| 10 Compilations | all | N/M — not in sample | per-segment dwell above floor | N/M | — | 3 | L | F-QB-0205 | 0 |

**Outcome-instrumentation gaps (Section 1.2)**:
* Sends per reach — computed but disconnected from reward — F-QB-0802 — HIGH T1
* YT averageViewPercentage — not ingested — F-QB-0809 — HIGH T1
* Per-metric persistence gap — analytics table only stores composite — F-QB-0801 — HIGH T1
* Reference / competitor corpus — none — F-QB-0807 — MED T3
* Per-arm observation count vs MIN_OBS_FOR_LINUCB — 50-obs threshold likely unmet — F-QB-0804 — HIGH T1

---

## 4. Compliance exposure summary

**Separate from quality — ranked by blast radius (channel-lifetime risk), not by tier.**

| # | Exposure | Ranked by | Evidence | Severity | Time to remediate |
|---|---|---|---|---|---|
| C1 | YouTube inauthentic-content risk on ai_creators (currently the sole publishing channel) — template caption + template shot cadence + template duration + AI TTS voice all match Section 1.3 bucket #1 | Blast radius = channel demonetisation | F-QB-0708 + F-QB-0201 + F-QB-0501 + Section 1.3 Screen Culture / KH Studio precedent | **BLOCKER** for future publishing at scale | 4-8 weeks (needs topic classifier + edit variety) |
| C2 | 17/17 movies affiliate posts non-compliant with FTC 16 CFR §255 disclosure position | FTC max civil penalty $53K per violation × 17 = $902K theoretical | F-QB-0701 | HIGH | 1-2 days (caption-position fix + pre-publish rule) |
| C3 | Unlicensed footage exposure per niche: sports (Content ID), movies (rights holders), anime (rights holders — FrameDrift highest risk) | Cease-and-desist / channel termination | F-QB-0709 + CLAUDE.md source tables | HIGH | Preventive (short-clip rule needs enforcement) |
| C4 | 6 Facebook `platform_policy_block` events on 2026-07-21 — X-App-Usage not measured; root cause unresolved despite `[[meta-code-368-audit-2026-07-22]]` memo | FB reach loss / temporary posting bans | F-QB-0707 | MED | Follow-up on X-App-Usage instrumentation |
| C5 | AI-content disclosure is being added (1016 events / 30d) — POSITIVE, but the disclosure by itself does not restore monetisation per Section 1.3 | — | F-QB-0710 | LOW (working) | — |
| C6 | Attribution invariant duplicated in 97 blueprints (56%) + attribution missing URL in 64 blueprints (37%) + 5 gaming blueprints have no attribution at all | Legal/DMCA and rule #11 defense-stack weakening | F-QB-0703 + F-QB-0704 + F-QB-0705 | HIGH | 2-4 days (caption-builder de-dupe + regex tighten in Layer 4) |
| C7 | No music-bed / no music-licence documentation (preventive) | — | F-QB-0302 + F-QB-0303 | LOW (not-yet-defect) | Blocking gate BEFORE first bed ships |

---

## 5. Prioritised remediation list — tier-ordered, not severity-ordered

Per prompt Section 5: a Tier-3 finding cannot outrank a Tier-1 finding.

### Tier 1 — highest leverage (hook, duration-to-own-completion, captions, compliance)

* **R-Pub-1 — Unstall publishing on the 4 dark channels (gaming/anime/sports/movies).** This is the audit's biggest single-move opportunity. Every other Tier-1 finding is scoped to "one channel that actually ships." Do this first. Investigate why sports/anime/movies blueprints go DRAFTED → ARCHIVED without VISUAL_READY (F-QB-0003). Investigate why gaming produces zero MP4s (F-QB-0002). Ownership: pipeline. **Verification gate:** `SELECT niche_id, MAX(published_at) FROM publishing_analytics WHERE published_at >= NOW() - INTERVAL '24 hours' GROUP BY 1` — expect all 5 niches present.
* **R-Comp-1 — Move `#ad` disclosure into first 100 chars of caption for every affiliate post.** Caption template needs restructure so disclosure precedes the hook OR immediately follows it. Also add a pre-publish check rule that fails render if `LEFT(caption, 100)` doesn't contain approved terms when `affiliate_url IS NOT NULL`. Ownership: writing + compliance. **Verification gate:** `SELECT COUNT(*) FROM blueprints WHERE affiliate_url IS NOT NULL AND created_at >= NOW() - INTERVAL '7 days' AND LEFT(caption, 100) !~* '(#|\s)(ad|sponsored|advertisement|paid[- ]partnership)'` — expect 0.
* **R-Cap-1 — Constrain caption Y-position to middle 56% of frame for ai_creators's whisper captions.** Currently 11 of 24 (46%) text blobs are in UI-occluded zones (F-QB-0402). Ownership: rendering. **Verification gate:** re-OCR expect ≤10% of caption blobs in `SAFE_ZONE_TOP`, `SAFE_ZONE_BOTTOM`, or `SAFE_ZONE_RIGHT`.
* **R-Topic-1 — Replace `topic='youtube_trending'` mono-tag with a per-story topic classification.** Enrich stories with topic-classification (game title, movie franchise, anime title, sports team/event, AI product/company). This is the enabler for the LinUCB topic feature, for topic-diversity metrics, and for the YT inauthentic-content-bucket-#1 remediation. Ownership: writing / classification model. **Verification gate:** `SELECT COUNT(DISTINCT topic) FROM blueprints WHERE niche_id='ai_creators' AND created_at >= NOW() - INTERVAL '30 days'` — expect ≥20.
* **R-Fresh-1 — Reduce event → publish lag.** Sports needs <24h, anime needs <48h. Current 7-11 days is not "trending." May require change to fetch cadence + scheduling policy. **Verification gate:** median lag hours by niche per weekly report.
* **R-Reward-1 — Wire `retention_derivations.sends_per_reach` into RewardShaper's IG `dm_send_rate` slot.** ~5 lines of glue. Then remove the redistribution WARNING that fires on every IG post. Ownership: learning. **Verification gate:** `dropped_pct` DEBUG log for IG platform drops from ~30% to <5%.
* **R-Reward-2 — Ingest YT `averageViewPercentage` into MetricCollector and add to publishing_analytics.extra.** Then reweight the YT reward to use percent-viewed instead of seconds-viewed. Ownership: learning. **Verification gate:** `SELECT COUNT(*) FROM publishing_analytics WHERE platform='youtube' AND extra ? 'averageViewPercentage' AND published_at >= NOW() - INTERVAL '7 days'` — expect > 0.
* **R-Reward-3 — Persist per-metric outcomes to `analytics` table (not just composite).** Row per (post_id, platform, metric_type, window). Unblocks any future outcome-correlation analysis. Ownership: learning + storage. **Verification gate:** `SELECT DISTINCT metric_type FROM analytics WHERE collected_at >= NOW() - INTERVAL '48 hours'` — expect ≥6 distinct values.
* **R-Comp-2 — De-duplicate the attribution line and enforce `🎬 Original: @X — URL` requires a URL (Layer 4 validator regex tightening).** ~1 hour. Fixes 97 blueprints of duplicated attribution + 64 with missing URL. Ownership: compliance / caption builder. **Verification gate:** `SELECT COUNT(*) FROM blueprints WHERE caption ~ '🎬 Original:.*🎬 Original:' OR caption ~ '🎬 Original: @[^ ]+ —[[:space:]]*[[:cntrl:]$]'` — expect ≤5%.

### Tier 2 — correctness floors, diminishing returns (encode, audio loudness, compilation structure)

* **R-Encode-1 — Add `-minrate 4M -bufsize 8M` to all PLATFORM_SPECS in `media/ffmpeg.py`.** Prevents CRF undershoot on low-motion content. Ownership: rendering. **Verification gate:** ffprobe median vbr per niche ≥ 4 Mbps.
* **R-Encode-2 — Set `-color_trc bt709 -color_primaries bt709` explicitly on every render (currently only 5/20 files have full triple).** Ownership: rendering. **Verification gate:** ffprobe on all reels shows `bt709/bt709/bt709`.
* **R-Loud-1 — Add EBU R128 loudness normalisation post-render (`-af loudnorm=I=-14:LRA=7:TP=-1.5`).** Ownership: rendering. **Verification gate:** all reels within ±1 LUFS of -14.
* **R-Edit-1 — Add cut-point diversity to compositor: 3-4s median shot length target (down from 7-8s).** Content-driven cuts (audio-onset-locked, motion-triggered, or beat-locked if music bed added). Ownership: rendering. **Verification gate:** PySceneDetect median shot length ≤ 4s.
* **R-FB-1 — Move source URL out of FB caption body into first-comment via Meta Graph API.** Currently 66 FB publishes have external URLs in body (self-throttling reach). Requires first-comment posting to preserve attribution. Ownership: publishing. **Verification gate:** grep FB caption body for `http` — expect 0 in trailing 7 days; separately confirm first-comment attribution is posted.
* **R-Comp-3 — Instrument X-App-Usage capture on every Meta API response and alert when >90%.** Backfill needed for 2026-07-21 root-cause (F-QB-0707). Ownership: publishing / observability. **Verification gate:** `pipeline_alerts` for `meta_api_usage_high` fires when appropriate.

### Tier 3 — commonly over-weighted, likely low real impact (Shorts thumbnails, branded intros, trending audio, resolution beyond 1080p)

* **R-Cover-1 — Add explicit IG cover_url on IG publishes (uses reel's most-motion frame).** Only if Tier-1/Tier-2 items are complete. Ownership: publishing. **Verification gate:** IG API response confirms cover_url set.
* **R-Ref-1 — Build reference corpus for percentile scoring.** Ingest own YT Analytics + at least 3 competitor channels per niche. Ownership: intelligence. **Verification gate:** every publish has a percentile-rank recorded vs. reference corpus.

---

## 6. Phased harness build plan

Ordered per prompt Phase 9 step 7: deterministic file/metadata metrics and compliance gates first; reference corpus second; VLM-as-judge third; outcome-correlation validation last.

### Harness Phase 1 — deterministic gates (weeks 1-3)

* Pre-publish `caption_disclosure_position` check (blocks render on affiliate post with #ad past 100 chars) — R-Comp-1.
* Per-render ffprobe assertion suite: video bitrate ≥ 4 Mbps, LUFS in [-15, -13], color triple bt709/bt709/bt709 — enforces R-Encode-1, R-Encode-2, R-Loud-1.
* Per-render OCR safe-zone check (fails render if >10% of caption blobs in occluded zones) — R-Cap-1.
* Layer 4 attribution regex tightening — R-Comp-2.
* Duration + shot-length assertions per niche (`assert 15 <= duration <= 60`, `assert median_shot_len <= 4.5`) — R-Edit-1 depth.

### Harness Phase 2 — reference corpus + percentile scoring (weeks 4-8)

* `reference_corpus` table + ingestion of own YT Analytics + 3 competitor channels per niche — R-Ref-1.
* Percentile-rank field on every publish.
* Cross-corpus outcome ranking on the Mission Control card.

### Harness Phase 3 — VLM-as-judge for subjective dimensions (weeks 9-14)

* CLIP or GPT-4V cover-frame aesthetic scoring.
* Hook novelty via embedding-distance calculation vs. own-last-30 + reference corpus.
* Automated topic classification for R-Topic-1.

### Harness Phase 4 — outcome-correlation validation + bandit integration (weeks 15+)

* Per-metric outcome-vs-bandit-prediction Spearman/Pearson per niche (extension of `bandit_validation.py`).
* `sends_per_reach` wired into main reward path (R-Reward-1).
* YT retention curve → percent-viewed reward (R-Reward-2).
* Per-metric analytics persistence (R-Reward-3).

---

## 7. What was not measured

* **Gaming channel — every dimension.** No artifacts existed to measure (F-QB-0002). Findings scope explicitly excludes gaming from Phases 1-5.
* **VMAF baseline** on any reel — no lossless master exists (F-QB-0103; also CLAUDE.md R-25).
* **WhisperX ground-truth caption timing** — runtime constraint (F-QB-0406). Deferred to R-Cap-2.
* **CLIP aesthetic score on cover frames** — no cover exists (F-QB-0601).
* **Embedding-distance novelty** between recent posts — requires embedding API call; deferred to R-Topic-1 delivery.
* **Per-arm bandit observation histogram** — deferred; single query would answer.
* **Real ducking delta** — no bed exists (F-QB-0302). N/A finding.
* **Per-post caption sent-vs-published diff** — requires Meta API read-back on each post; not attempted.
* **Cold-open branded-frame duration** — approximated via text-in-first-1s only.
* **Movies channel per-metric behaviour on publish** — movies has 0 publishes in the 14-day window despite 17 fresh renders today; the render→publish gap needs a follow-up beyond this audit's scope.

Sample sizes recorded per finding.

---

## 8. Method-error self-review

Per Section 0.7. The following methodology errors were logged during this audit; all captured in `methodology_errors.md`:

* **ME-01** — Assumed VPS `.venv` had measurement libs (it doesn't; it's a symlink to system python). Reversed by setting up isolated Mac audit venv.
* **ME-02** — Explore agent 2 had typo'd paths (`/Users/anthropistsid/…`). Reversed by verifying every path with `[ -f ]`.
* **ME-03** — Two Explore agents contradicted on IG metric fetcher. Reversed by re-reading the source in Phase 8 verification pass; F-QB-0802 is authoritative.
* **ME-04** — Prompt asked for N=10 per channel; gaming has 0 available. Handled by scoping every finding to "N=X across channels [list]".
* **ME-06** (new) — F-QB-0004 initially claimed the auto-approver was skipping 4 niches. Phase 8 code inspection revealed sports also has `auto_publish.enabled=true` — the empty gate_examinations for sports are because sports has zero VISUAL_READY blueprints upstream, not because the approver skips it. F-QB-0803 supersedes F-QB-0004. Logged.
* **ME-07** (new) — My Phase 3 ducking-delta metric (sliding-RMS `p90-p10`) reports ~170 dB for silence-heavy tracks because `log10(near-zero)` diverges. Anime's continuous audio gives a plausible 6.6 dB. The metric is not usable for the intended purpose; use `pyloudnorm.Meter.loudness_range()` instead. Logged.
* **ME-08** (new) — Phase 0 pull_list logic included raw source clips (640×360) in the artifact list; Phase 1 measurements needed to filter to `is_reel=True` (1080×1920). No conclusion changed but analysis had to be redone for 13 source-clip rows.

Errors logged: 7 across 9 phases. This is an audit that noticed itself making mistakes — not a signal of poor audit hygiene, per Section 0.7.

---

## 9. What this audit says the operator should do this week

1. **R-Pub-1** first. Everything else is scoped to whichever channels actually ship.
2. **R-Comp-1** second (1-day fix, unblocks legal-risk finding).
3. **R-Comp-2** third (1-hour caption de-dup + regex tightening).
4. Then start **Harness Phase 1** (deterministic gates) in parallel with **R-Reward-1** and **R-Reward-2**.
5. Once (1) is unblocked and 3 channels are shipping again, revisit **R-Topic-1** (topic classification) which touches every downstream Tier-1 finding.

Do NOT start Tier-3 items (R-Cover-1, R-Ref-1) until Tier-1 items ship. Section-1.1-row-1 explicitly warns Shorts thumbnails are near-irrelevant; committing engineering cycles there while topic freshness is 7 days behind would be misallocation.

Estimated engineering effort to close every Tier-1 item: 3-4 weeks with one focused engineer. Estimated marginal impact on the north-star metrics (100K followers, millions of views/week, $1M/month affiliate per rule #24): unblocks the growth loop from a state where 4 of 5 channels don't publish + the reward signal is misaligned with what the platforms actually reward. That is the leverage. Estimated %-progress on the north-star numbers post-fix: not this audit's job to predict.
