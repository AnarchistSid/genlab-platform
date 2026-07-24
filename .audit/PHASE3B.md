# PHASE 3B — Learning loop, data layer, cost

## Three leading lines

1. **End-to-end reel trace — COMPLETED with real IDs.** Blueprint
   `a86a541f-608f-4a39-a056-c15cf55bba2d` (anime, arm=`adaptation_news`,
   candidate=`3178cba7...`) traced through all 5 links:
   - **Selection**: bp created 2026-07-16 07:05, arm sampled = `adaptation_news`
   - **Publish**: 3 real platform post_ids — FB `836650...`, IG `18111353...`,
     YT `iWSugODIyg4`, published 2026-07-16 07:03-07:05
   - **Insight**: analytics status `INSIGHTS_168H` (full 7d window closed)
   - **Reward**: 3 `pending_feedback` rows all `complete`, reward_48h ∈
     {0.028, 0.042, 0.000}, updated 2026-07-23 07:24
   - **Bandit update**: `adaptation_news`/anime alpha=5.64 beta=29.09
     updated 2026-07-23 14:34 — **7h after reward closed**
   Loop is closed. Not decorative.

2. **5432 — accepted risk with expiry 2026-07-31**, re-recorded against
   superuser+RCE facts. `rolsuper=t, rolbypassrls=t, rolcreaterole=t,
   rolcreatedb=t, rolreplication=t`. Exposed credential is a Postgres
   SUPERUSER = arbitrary command exec via `COPY TO PROGRAM`. **F-0024
   rewritten** at CRITICAL with 6-day expiry; **F-0049** filed for the
   least-privilege `genlab_app` role remediation (one change closes 3
   findings — F-0024, F-0045, F-0048).

3. **Cost/revenue**: **$13.61/30d** total ($0.063/run), gaming $10.73 alone
   is 79% of spend (needs investigation). Revenue: near-zero affiliate.
   Monthly ratio: **~$14 in / ~$0 out**. **Days-to-Neural-LinUCB**: 1,190
   `pending_feedback` rows over 126 days = ~9.5/day across 5 niches = ~2/day
   per niche. To 1000 obs/niche: **~500 days** at current velocity.

## Section 2 corrections applied

- **2.1** F-0048 cross-ref removed (it doesn't confirm F-0007's linter
  violation; F-0007 stays as its own thing).
- **2.2** "Loop is real" rewritten: **arms are being written; closure now
  verified** via the end-to-end trace above.
- **2.3** ~~Denominator: 3 primary platforms (FB/IG/YT), Threads out-of-scope
  per H5 blocker. **86%** stands.~~ **CORRECTED by F-0061 (Phase 7):** Rule #23
  says Threads is *in* scope; TikTok+X are the excluded platforms. Correct
  denominator is 4-platform. **7-day recompute (Phase 7.1): 86 rows / 140
  expected = 61.4%.** 3-platform 86% claim withdrawn.
- **2.4** IG post_id coverage 30d — **F-0050 filed**:
  movies 37.5%, anime 36%, ai_c 30.8%, sports 28%, gaming 16.7% missing.
  Spot-check deferred (needs live IG session).

## Step 4 — data layer

- **Table sizes**: `dashboard_events` 4969 rows (1.2 MB), `assets` 3109 (1.5 MB),
  `publishing_analytics` 2928 (2.5 MB), `content_pool` 1229 (10.2 MB — biggest),
  `blueprints` 2137 (8.8 MB).
- **Sequential scans**: `pipeline_alerts` 21,957 seq / 297 idx (36M rows read),
  `bandit_arms` 9,601 seq / 5,330 idx. Missing indexes on hot paths. **F-0051**.
- **Unused indexes**: 15 with 0 scans since stats reset (mostly PKs on
  low-traffic tables like `templates`, `email_subscribers`, `preference_data`).
- **Alembic head**: `m9h0i1j2k3l4` — matches this session's shipped migration.

## Step 5 — errors + F-0037/F-0039 status

- **F-0037 cascade test**: still requires staging env — not present. `action:
  test` stands.
- **F-0039 silent-in-prod**: 107 sites, publish-path proportion ~2/3. Runtime
  matrix shows publish path executes daily → **107 × 2/3 ≈ 70 silent sites
  on daily-fire code**. Individual site classification deferred.

## F-0046 root cause

15 gaming orphans clustered **2026-03-17 to 2026-03-24** — all pre-VPS-deploy
(2026-05-17). These are historical local-execution artifacts marked PUBLISHED
before publishing_analytics existed on VPS. Not an ongoing bug. **F-0046
downgraded to LOW** (historical residue, safe to backfill or delete).

## F-0047 root cause

Files EXIST for all 4 stuck-VISUAL_READY visual_paths (verified via
`sudo test -f`). NOT a cleanup-eats-media issue. Root cause not diagnosed
this session — likely the 2026-07-21 cohort is 4 rows the archive-stale-visuals
sweep hasn't caught yet. `action: investigate` — separate root-cause session.

## Blindness list

- **Cost breakdown**: image/TTS/compute all $0.00 in `pipeline_run_costs`.
  Either genuinely not tracked or written elsewhere. Anthropic 402 credit
  exhaustion (×11 in 7d) suggests real LLM spend is higher than the $13 shown.
- **F-0047 root cause** — deferred.
- **IG post_id spot-check** — need live IG session.
- **F-0034 sweep** — per-timer body inspection (was the "success" a real
  execution?) not completed for all 92 firing units. 4 never-fired identified.
- **Cascade tier 2/3/4 (F-0037)** — no staging env, test deferred.

## Recommendation

**Go to Phase 6 (scorecard) next, not Phase 4.** Phase 4's core question was
"is production running code not in git" and Phase 2.6 answered that at the
Python level (in sync, HEAD matches). Config drift is filed as F-0040 and
resolved as low-risk in Phase 2.7 gate check. The stronger drift finding —
supervisor state (shadow-reviewer timer not started) — is captured in F-0033.
Phase 6 has everything it needs: 51 findings, the reel-trace evidence, cost
ratio, and video-first pass rate. Phase 4 would repeat inspection work
Phase 2.6 already did with better methodology.

**All shells exited** (`ps aux | grep pytest = 0`). No secret values in
`.audit/`. Read-only tx used throughout.
