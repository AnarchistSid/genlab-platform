# Phase 8 — Outcome instrumentation and learning-loop integrity (Section 1.2)

**Scope:** what platform outcomes are ingested, how they aggregate into the reward, whether the reward can converge under current cadence, what guardrails are in place, and whether proxy validation exists.

**Trust posture:** Every code claim below was independently verified by a fresh Explore agent reading the source (quoted excerpts on file at `phase_8_code_reference_verified.md`). Where the earlier Phase 0 code agent contradicted a later one, the fresh pass was authoritative. **F-QB-0004 (Phase 0) is corrected below.**

---

## Findings (12/12)

### F-QB-0801 — HIGH — Per-metric platform outcomes are only persisted via the 5 canonical `publishing_analytics` columns; every deeper metric fetched by MetricCollector is folded into one composite scalar and discarded

* **Measured value:** `SELECT DISTINCT metric_type FROM analytics WHERE collected_at >= NOW() - INTERVAL '14 days';` → single value `composite`. `publishing_analytics` dedicated columns: `views, likes, comments, shares, saves`. No timeseries or per-fetcher-row detail.
* **Verified from code:** `metric_collector.py:1103-1125` passes a single `reward_48h` scalar into `bandit_updater(niche_id, arm, platform, reward_48h, context)`. There is no path that writes per-metric detail (individual `avg_view_duration`, `subscribers_gained`, `dm_send_rate`, `total_interactions`) to any DB table.
* **Impact:** the entire per-post analytical history for the reward loop is: `{views, likes, comments, shares, saves}` + one scalar. If tomorrow the operator asks "did that post's IG sends per reach change over time?" the answer is: not stored. This also caps this audit's ability to answer any per-metric outcome question retrospectively.
* **Confidence:** HIGH.
* **Tier:** 1 (foundation of the learning loop).
* **Verification gate:** add per-metric rows to `analytics` (one row per `{post_id, platform, metric_type, window}`) and query `SELECT DISTINCT metric_type FROM analytics WHERE collected_at >= NOW() - INTERVAL '48 hours'` — expect at least 6 distinct metric_types.

### F-QB-0802 — HIGH — IG "sends per reach" IS derived from `total_interactions - (likes + comments + saves)` but is only consumed by the per-dimension transformation-reward-router, NOT by the primary bandit reward path

* **Verified from code:** `learning/retention_derivations.py:184-201` computes `sends_per_reach = clamp01((total_interactions - likes - comments - saves) / reach)`. Grep shows it is consumed by `metric_collector.py:1461` inside `transformation_reward_router.compute_dimension_reward` (per-dimension attribution surface). The main `RewardShaper.compute_reward()` at `reward_shaper.py:361` uses `BASE_WEIGHTS["instagram"]` which lists `dm_send_rate: 0.25` but IG fetcher intentionally omits `dm_send_rate` from its output — so the `dm_send_rate` weight-slice always falls into weight-redistribution and never contributes a real IG-shares signal to the main reward.
* **Impact:** the piece of the codebase that KNOWS how to compute the top-3 IG ranking factor (sends per reach) is disconnected from the piece that drives the primary bandit reward. The RewardShaper's `dm_send_rate: 0.25` weight is effectively dead code for IG on the main reward path — its 25% share always gets redistributed to `views/saves/shares/follower_gained`. The audit's most important Section-1.2 signal (IG sends/reach) is not actually driving the primary bandit.
* **Confidence:** HIGH.
* **Tier:** 1 (correctness of the reward loop).
* **Verification gate:** either (a) pipe the `retention_derivations.sends_per_reach` value into `metrics["dm_send_rate"]` before RewardShaper.compute_reward is called (~5 lines to bridge), OR (b) delete the `dm_send_rate` weight from BASE_WEIGHTS and re-normalise weights so the redistribution isn't a fixed 25% invisible shift. Then re-query the "IG dm_send present" branch in `reward_shaper.py:408-439` — expect the `dropped_pct` DEBUG log to drop from ~30% to <5% for IG posts.

### F-QB-0803 — HIGH (CORRECTION to F-QB-0004) — Auto-approver `auto_publish.enabled=true` for BOTH ai_creators AND sports; the sports gate examinations are absent because sports blueprints never reach `VISUAL_READY` (blocked upstream at render/DRAFTED), not because the approver skips sports

* **Verified from code + config:**
  * `BlackboxBrief/config/publishing.yaml:130 auto_publish.enabled: true, min_confidence: 0.70, rollout_pct: 1.0`
  * `ClutchWire/config/publishing.yaml:97 auto_publish.enabled: true, min_confidence: 0.70, rollout_pct: 1.0`
  * `CriticalRush/niches/gaming/config/publishing.yaml:121 auto_publish.enabled: false, min_confidence: 0.85, rollout_pct: 0.0`
  * `SpliceReel/config/publishing.yaml:87 auto_publish.enabled: false, min_confidence: 0.85, rollout_pct: 0.0`
  * `FrameDrift/config/publishing.yaml:84 auto_publish.enabled: false, min_confidence: 0.85, rollout_pct: 0.0`
* **Cross-reference to Phase 0 blueprint distribution:** sports has 7 ARCHIVED + 4 DRAFTED in 14d, **no VISUAL_READY**. Since `auto_approver.py:686` queries only VISUAL_READY candidates, the empty result set explains the empty `gate_examinations` for sports.
* **Corrected finding:** the auto-approver code is running for both niches; sports simply produces zero VISUAL_READY candidates because the render or pre-render-quality gate is rejecting them upstream. **This is a different bug** than "gate examiner stalled." The upstream failure is what needs fixing.
* **Also corrects CLAUDE.md:** "Currently enforces on ai_creators ONLY" is now stale — sports is also flag-enabled (rollout_pct: 1.0). If sports render ever comes back, sports will auto-approve at full rollout with no calibration data, since `auto_approval_calibration` rows for sports = 0.
* **Confidence:** HIGH.
* **Verification gate:** trace `sports` in the pipeline logs — expect either "pre_render_quality: rejected" or "compose_blueprint: no video" as the reason for the DRAFTED→ARCHIVED path.

### F-QB-0804 — HIGH — Statistical power: single-arm cold-start requires 50 observations before LinUCB (`MIN_OBS_FOR_LINUCB = 50`). At 1 post/day per channel × ~4 platforms = 4 observations/day per platform, a single arm needs ~12-13 days to leave cold-start. With ~20 arms per channel, per-arm observations approach 0 in any operator-actionable window.

* **Verified from code:** `linucb.py:124 MIN_OBS_FOR_LINUCB = 50`, cold-start branch returns None and picker falls back to Thompson (`linucb.py:332-342`).
* **Sample-count evidence:** `SELECT niche_id, arm_id, n_plays FROM bandit_arms WHERE niche_id='ai_creators' ORDER BY n_plays DESC` needed for full picture; not run in this pass but referenced from prior audit memory that "16-31% share, needs architectural fix" was largely-shipped.
* **Impact:** Section 1.2 in the prompt is explicit that "time-to-significance for a plausible effect size" must be honestly reported. At current cadence with ~10 arms per niche, each arm sees ~1-2 obs/week. Reaching MIN_OBS_FOR_LINUCB per arm takes ~25 weeks per arm. Thompson Sampling (Beta posterior with `α`, `β`) does converge faster than LinUCB but its posterior tightens slowly at these sample sizes too.
* **Confidence:** MEDIUM (arithmetic is solid, but the arm space cardinality per niche isn't tallied in this pass).
* **Verification gate:** `SELECT niche_id, COUNT(*) arms, MIN(n_plays), AVG(n_plays), MAX(n_plays) FROM bandit_arms GROUP BY 1;` — expect max arm-observations < 50 for most niches other than ai_creators.

### F-QB-0805 — MEDIUM — Reward is bounded [0, 1] and `None`-safe. Weight redistribution is instrumented at DEBUG (redistribution ≥15% escalates to WARNING) — this is a well-designed guardrail

* **Verified from code:** `reward_shaper.py:462 return max(0.0, min(1.0, raw_reward))`. Also `compute_reward()` returns `None` when `MonetisationRewardShaper.compute` raises internally (2026-07-14 change, per docstring) — callers explicitly null-check before the bandit update (`late_reward.py:252`, `metric_collector.py:1033`, `backfill_bandit_from_history.py:300`).
* **Guardrails present:** (a) reward clamping [0,1], (b) `MIN_OBS_FOR_LINUCB=50` cold-start Thompson fallback, (c) `_MIN_PROPENSITY = 1e-6` IPS floor, (d) weight-redistribution WARNING at dropped_pct ≥15%, (e) None-safety at all bandit callsites.
* **Guardrails ABSENT (per Section 1 Phase 8 checklist):** (a) no decay-toward-prior term on the bandit posterior, (b) no per-cycle weight-movement cap on `BASE_WEIGHTS`, (c) no held-out never-optimised slice, (d) no per-arm confidence-interval width cap.
* **Impact:** existing guardrails prevent the loud failure modes (posterior poisoning, IPS explosion). Absent guardrails mean two subtler risks remain: (i) an arm that got lucky early can dominate exploitation indefinitely without decay; (ii) any long-tail arm may never be revisited once the top arm's posterior tightens. The Thompson exploration floor is the mitigation, but it depends on the prior — no per-niche prior audit was done.
* **Confidence:** HIGH (measurement of what's present); MEDIUM (impact interpretation).
* **Tier:** 2.

### F-QB-0806 — MEDIUM — Proxy validation IS instrumented via `bandit_validation.py`: Spearman + Pearson correlation between per-source bandit-predicted score and 48h realized viral_score

* **Verified from code:** `learning/bandit_validation.py` computes for each niche over the last N days: Spearman rank correlation between bandit-predicted per-source arm score and realized 48h viral_score; also Pearson and a top-quartile lift.
* **Impact:** answers Phase 8 step-6 in the affirmative — there IS a proxy-validation loop. But it only validates PER-SOURCE arms (not content-features, not hook, not variant). Real IG-shares-per-reach vs. bandit-predicted-per-source-arm is a coarse proxy of what should be validated.
* **Confidence:** HIGH.
* **Verification gate:** run the validation harness and record `spearman` per niche for last 14 days — check it against zero. Any value <0.1 means the per-source arm has no informative posterior yet (which given cadence is expected). Expect this metric to appear on Mission Control as a card.

### F-QB-0807 — HIGH — No reference / competitor corpus exists for percentile scoring; every "composite score" is computed against the channel's own history only

* **Verified from code:** grep for `percentile\|competitor_corpus\|reference_dataset\|benchmark_dataset` in `learning/` returns no such construct. `analytics.metric_type='composite'` rows contain a scalar per (post, platform, window) but no percentile band vs. any external distribution.
* **Impact:** ai_creators's high-composite posts (currently the only channel publishing) are being used to teach the bandit "what worked here." If ai_creators is deep in a low-engagement regime, "good relative to bad" is being rewarded and there is no upper bar. Prompt Section 8 step 7 flagged this as expected — confirmed.
* **Confidence:** HIGH.
* **Tier:** 3 (needs infrastructure investment; delayed until harness Phase 2 build-out).
* **Verification gate:** ingestion of at least one competitor channel's public per-post metrics (via YT Analytics for own channel + YT public data for competitors) into a `reference_corpus` table; percentile rank of every GenLab post recorded per publish.

### F-QB-0808 — MEDIUM — LinUCB context is 13-D and normalised to [0, 1] per feature. Content-only features dominate (5 of 13); no interaction terms or embedding features

* **Verified from code:** `linucb.py:681-780 build_content_context()` — 13 dims: day_of_week, hour_utc, source_type, duration_bucket, view_velocity, relevance_score, hook_length, niche_encoding, has_affiliate, caption_length, hashtag_count, trending_score, content_type_showcase.
* **Impact:** the context vector is well-scoped for a first-generation contextual bandit but it does not include (a) hook embedding, (b) source-creator embedding, (c) platform-of-publish, (d) topic embedding. Adding these would let LinUCB learn cross-feature interactions.
* **Confidence:** HIGH.
* **Tier:** 3 (feature engineering upgrade; not urgent given F-QB-0801 first).

### F-QB-0809 — HIGH — YouTube analytics API request DOES capture `estimatedMinutesWatched, averageViewDuration, subscribersGained, shares` but NOT `averageViewPercentage` (retention curve), NOT `impressions`, NOT `annotationClickThroughRate` — the retention curve is unavailable to the reward loop even though YT exposes it

* **Verified from code:** `learning/metrics/youtube.py` YT Analytics API `metrics=` string includes `views,estimatedMinutesWatched,averageViewDuration,subscribersGained,shares` (per the fresh Explore agent report). Missing: `averageViewPercentage`, `viewerPercentageRetention`, `impressions`, `impressionClickThroughRate`.
* **Impact:** the top-2 Section-1.2 outcome signal for Shorts (average percent viewed, retention curve) is not being ingested from YT Analytics. RewardShaper's `avg_view_duration: 0.3` weight uses seconds-watched, not percent-watched — meaning shorter reels are systematically under-scored relative to their retention effectiveness.
* **Confidence:** HIGH.
* **Tier:** 1.
* **Verification gate:** after adding `averageViewPercentage` to the YT metrics fetcher and to the reward computation, `SELECT COUNT(*) FROM publishing_analytics WHERE platform='youtube' AND extra ? 'averageViewPercentage' AND published_at >= NOW() - INTERVAL '7 days'` should return > 0.

### F-QB-0810 — MEDIUM — Facebook fetcher captures shares AND `post_video_avg_time_watched` — the FB reward path is materially better instrumented than YT for retention

* **Verified from code:** `learning/metrics/facebook.py` grabs `post_video_view_time, post_video_avg_time_watched, post_video_views, post_impressions, post_engaged_users, shares`.
* **Impact:** FB is the platform where the reward loop has the deepest retention signal. Rule #23 explicitly makes FB a north-star platform. This means the FB reward is closer to "measures the right thing" than YT's is.
* **Confidence:** HIGH.
* **Verification gate:** the RewardShaper's FB weights should be checked against realized FB reach growth — if `completion_rate` (0.20 weight) is being driven by `post_video_avg_time_watched / video_length`, that's the same math YT lacks.

### F-QB-0811 — MEDIUM — Threads fetcher captures views, likes, replies, reposts. `follower_gained` and `discovery_share` are in `BASE_WEIGHTS["threads"]` but NOT in the fetcher's output — so 30% of Threads weight (0.15 + 0.15) always gets redistributed

* **Verified from code:** `learning/metrics/threads.py` returns `views, likes, replies, reposts, quotes`. RewardShaper BASE_WEIGHTS has `follower_gained: 0.15, discovery_share: 0.15` for threads.
* **Impact:** same class as F-QB-0802 — 30% of Threads weight is dead. Every Threads reward computation silently redistributes to views/replies/reposts. The `dropped_pct` WARNING will fire on every Threads post since 15% is the trigger and the dropped share is 30%.
* **Confidence:** HIGH.
* **Verification gate:** count of WARNING log lines containing `dropped_pct=` for threads platform in the last 24h — expect near-100% of Threads posts.

### F-QB-0812 — LOW — `PostgresBackend` belt-and-suspenders `AND niche_id = %s` is present in `find/update/delete` when the table has `niche_id` in PROMOTED_COLUMNS, mitigating rule #33 (the `genlab` role's BYPASSRLS attribute)

* **Verified from code:** `postgres.py:738-743` explicitly appends the niche_id filter to the WHERE clause when applicable.
* **Impact:** rule #27 mitigation confirmed. The 34-psycopg-bypass-sites risk from `[[deeper-cuts-audit-2026-07-16]]` is orthogonal — those are code paths that skip `pg_connect` entirely; they don't touch `PostgresBackend`.
* **Confidence:** HIGH.
* **Verification gate:** unit test that constructs a `PostgresBackend.find("publishing_analytics", niche_id="ai_creators")` and asserts the emitted SQL contains `AND niche_id = %s`.

---

## Deferral ledger (Phase 8)

| Item | Reason deferred |
|---|---|
| Per-arm observation-count histogram | Deferred to Phase 9 verification queries |
| Statistical-power calculation with specific effect size | Deferred — needs an effect-size assumption from operator |
| `_reduce_context_for_frozen_model` audit | Not seen in this pass; deferred |
| RLS behaviour under `session_user='genlab'` migration path | Migration-only; not runtime — noted in F-QB-0008 |

## What was not measured

* Actual `bandit_arms.n_plays` distribution (deferred to Phase 9 quick-query).
* Realized `bandit_validation.spearman` per niche over last 14 days (deferred — should be one query).
* Bandit posterior mean values vs. realized outcomes correlation (needs the validation output).

## Corrections logged to methodology_errors.md

* F-QB-0004 (Phase 0) was **partially wrong**: I asserted "auto-approver only runs for ai_creators" based on the empty `gate_examinations` rows. Verified Phase 8 evidence: sports has `auto_publish.enabled=true` and would auto-approve if it had VISUAL_READY candidates. The corrected root-cause is the upstream render/DRAFTED failure, not the gate itself. F-QB-0803 supersedes F-QB-0004.
