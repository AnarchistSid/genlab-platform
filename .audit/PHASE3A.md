# PHASE 3A — Runtime Reality (Session 1: Steps 0-3)

## Three leading numbers

1. **Stages with no runtime evidence in any channel**: `engagement_reply`
   (unknown — DB not queried this session) + 4 pipeline units that never
   fired (`shadow-reviewer`, `counterfactual-replay`, `spike-detector`,
   `pipeline-ai-creators` — the last is a wrong-named duplicate; real one
   is `pipeline-ai`).
2. **Channels below 80% of publish mandate (30 posts/platform/month)**:
   Threads on 4 of 5 channels (`anime=3, gaming=3, movies=1, sports=8` vs
   mandate 30). Twitter/X on ai_creators only (25). Rule #23 (TikTok+X out
   of scope) explains X. Threads under-delivery is a real gap.
3. **Video-first sample pass rate**: **5/5**. All sampled `_reel.mp4` files
   have >30 scene changes, 16-21s duration, 1080×1920 h264+aac. No static-
   frame or placeholder outputs in sample.

## Section 0 corrections (all applied)

- **0.1**: v2 partition reconciled — moved the 2 phantoms from SET_UNREAD to
  SET_AND_READ (they ARE read via `_ENABLE_ENV_VAR` module constant, F-0042).
  New totals: SET_AND_READ=57, SET_UNREAD=0, UNSET_AND_READ=62, UNSET_UNREAD=0,
  total=union=119. `.audit/phase3/env_buckets_v3.csv`.
- **0.2**: F-0033 reinstated (see below — root cause found).
- **0.3**: LINUCB + THOMPSON bare-name flags are legitimate fallbacks, always
  overridden by per-niche variants. **Real ML dormant count = 5, not 7**:
  `BEDROCK_FINETUNE`, `POLICY_BLOCK_RCA`, `SPLIT_SCREEN_COMPOSITOR`,
  `STORYTIME_COMPOSITOR`, `TEMPORAL_CONTEXT`. F-0041 corrected.

## Stage × channel matrix (evidence from DB, not code reading)

Full CSV: `.audit/phase3/03_stage_channel_matrix.csv` (75 rows).

| Stage | ai_c | gaming | sports | movies | anime | Evidence |
|---|--:|--:|--:|--:|--:|---|
| trending/fetch/score/compose/write/render/qc/gate/push/publish (10 stages) | 24 | 18 | 20 | 10 | 18 | publishing_analytics 7d rows |
| metric_collector | 13 | 14 | 14 | 4 | 13 | pending_feedback created_7d |
| performance_learner + bandit_update | 39 | 46 | 52 | 29 | 32 | bandit_arms moved_7d |
| fetch_insights | y | y | y | y | y | .logs/fetch_insights_<ch>_*h.log |
| engagement_reply | unknown | unknown | unknown | unknown | unknown | not queried |

**5/5 channels have runtime evidence for every measured stage**. No dead
stage confirmed. Empty-column check: all 5 channels active.

## Publish ground truth (Step 2)

Full artifact: `.audit/phase3/03_publish_ground_truth.txt`. Highlights:

- **90 total posts across 5 channels in last 7d** (mandate = 105 = 5 ch × 3
  primary platforms × 7d). Actual/expected = **86%**.
- **Post-ID coverage** (with_id / total per channel×platform, 30d): YouTube
  averages 90%+ across all niches; Instagram 60-80%; Facebook 85-95%;
  Twitter (ai_creators only) 32%. **Twitter is failing more than it
  succeeds** — matches rule #23 (out of scope).
- **Top error classes 30d**: Anthropic `402 Payment Required` (×11) — credit
  exhausted; "No valid media files" (×17 across niches) — 4-month-old
  render artifacts referenced after `.tmp` sweep; Meta `2207082` container
  processing (×8) — anime + movies; YouTube quota hard-stop (×4 anime);
  Layer 4 attribution gate blocking tweet text without credit (×4).

## State machine integrity (Step 3)

- **`PUBLISHED` blueprints with NO `publishing_analytics` row** — CRITICAL
  lies: **25 total** (`gaming`=15, `anime`=3, `movies`=3, `ai_creators`=2,
  `sports`=2). Every downstream metric on these rows is wrong. **F-0046**.
- **Stuck VISUAL_READY > 48h**: 4 rows (1 per non-movies channel), all from
  2026-07-21 — same-day cohort, likely one cohort failure. **F-0047**.
- **VISUAL_READY without `approved`**: 3 rows, all `gaming`. Publisher gate
  never met — auto-approver rollout still at 10% + rule #22 gaming revert.
- **Bandit arms all moved in 30d**: 77/77/74/73/73 across niches. **Learning
  loop IS running** (F-0006 sibling — refutes "decorative" hypothesis).

## RLS runtime evidence — CRITICAL

```
current_user: genlab, rolsuper=t, rolbypassrls=t
20 tables have RLS policies enabled (blueprints, publishing_analytics,
stories, content_memory, ...); ALL are silently bypassed at query time
because the app connects as a BYPASSRLS superuser.
```
This runtime-confirms F-0007 (which was inferred from `\du`). **All 20
"multi-tenant isolation" policies are decorative in production.** Combined
with F-0024 (public 5432) + F-0031 (world-open pg_hba), the RLS story has
no teeth. **F-0048 CRITICAL**.

## F-0033 root cause (open since Phase 2.5)

`genlab-shadow-reviewer.timer` is `enabled` at unit-file level but
`inactive` at runtime — never `systemctl start`-ed. `NEXT=- LAST=-`. Timer
would fire OnCalendar 04:00 UTC daily WITH Persistent=true if activated.
Config is set (`GENLAB_SHADOW_REVIEWER_ENABLED=true`), code reads it
(scripts/run_shadow_reviewer.py), unit exists — only missing:
`sudo systemctl start genlab-shadow-reviewer.timer` on VPS. **F-0033 root
cause established.**

## F-0034 sweep result

180 genlab-* services enumerated. Filtering out template `-@` units and
long-running services: **4 never fired** — `counterfactual-replay`,
`pipeline-ai-creators` (wrong name; real is `pipeline-ai`), `shadow-reviewer`,
`spike-detector`. 92 of 180 have fired successfully in last 7d.

## Blindness list

- **`engagement_reply` stage** — pending_engagement table not queried this
  session; F-0037 cascade test also deferred to session 2.
- **Journal has no output for `genlab-pipeline-<ch>.service`** — cron_wrapper
  logs to `.tmp/logs/{daily,finalize,publish}_<RUN_ID>.log` NOT journal. The
  matrix used DB proxies (publishing_analytics row counts) not per-stage log
  parses. Stage-level success rates unknown without file parsing.
- **`content_pool`, `stories`, per-stage error rates**: not queried this
  session. Session 2 (steps 4-7).
- **VPS `.tmp/logs/`**: empty at time of scan — old daily runs may have been
  cleaned. Cannot reconstruct per-stage historic activity.

## Process

All shells exited before write (`ps aux | grep pytest = 0`). No secret
values in `.audit/`. Read-only queries used `SET default_transaction_read_only`
+ `statement_timeout=30s`.

Session 2 (Steps 4-7) queued.
