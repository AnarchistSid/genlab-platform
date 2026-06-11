# Gen Lab — System Research & Assessment

> **Living document.** This is the single source of truth for grounded findings about
> how the Gen Lab system is *actually* built (as opposed to how the docs describe it).
> It is meant to be appended to over time, not rewritten.

_Last updated: 2026-06-11_

---

## How to use this doc

- **Reference sections (§1–§4)** describe the current understood state. Update them in place
  when something materially changes; note the date inline if it matters.
- **Risk register (§5)** is a table with stable IDs (`R-01`…). Change `Status`/`Severity`
  rather than deleting rows, so history is preserved.
- **Findings log (§6)** is append-only, newest first. Each entry is dated and scoped.
  Put new research here first; promote durable conclusions up into §1–§4.
- **Open questions (§7)** is the backlog of things we haven't verified yet.
- **Confidence:** findings below come from reading the repo checkout, not the running
  Hetzner host. Items needing host-side confirmation are tagged **[verify on host]**.

---

## 1. Architecture overview

Gen Lab is a **video-first viral-content automation platform** that publishes one short-form
reel per channel per day across 5 channels, with a human approval gate. Three layers:

```
Layer 1  genlab-core/            shared engine (never per-niche)      51.6K LOC, 216 modules
Layer 2  <Channel>/*_strategies/ thin niche strategy subclasses
Layer 3  <Channel>/config/*.yaml pure per-niche configuration
```

**Data flow as actually wired:**

```
YouTube/RSS/Reddit/Trends → fetch_trending_videos → relevance_filter → score
  → write_content (Claude Haiku) → render (FFmpeg + logo overlay + VMAF gate)
  → push_to_backlog (Postgres) → DASHBOARD HUMAN APPROVAL  ← the real publish gate
  → publish_all_platforms (IG/YT/X/FB/Threads via ThreadPool) → Publishing_Analytics
  → metric_collector (6h/24h/48h/168h) → reward_shaper → bandit update → next selection
                                                       ↘ engagement: webhook → Dramatiq → reply
```

**Orchestration reality:** the real multi-niche entry point is
`genlab-core/src/genlab_core/pipeline/cli.py` (`NICHE_DIR_NAMES`, ~L52), **not**
`CriticalRush/core/pipeline_runner.py` (which maps only `{"gaming": …}`). The "CriticalRush
orchestrates all niches" claim in its CLAUDE.md is stale (see §6, doc-drift).

**Scale (verified 2026-05-22):** ~3,313 collected tests total — genlab-core 2,435
(40 integration deselected), criticalrush 282, blackbox-brief 183, framedrift 143,
clutchwire 136, splicereel 134.

---

## 2. Subsystem reality check

### 2.1 genlab-core engine — **Production**
- **Pipeline runner** (`pipeline/pipeline_runner.py`, 516 L) is the strongest part. Stages
  are genuinely config-driven: `_load_stages` (~L438) imports fully-qualified classes via
  importlib. Real safety: per-niche `flock` mutex (`_NicheLock`, ~L57), cross-niche leak
  guard that aborts before DB writes (`_FOREIGN_PREFIX_MAP`, ~L366–436), `parallel_group`
  batching. **Caveat:** stage protocol is duck-typed (`execute(context)->dict`), no ABC →
  a malformed stage only fails at runtime.
- **Platform clients** — real publishers: instagram (541 L), youtube (729 L), x_twitter
  (541 L, tweepy OAuth1.0a), facebook (514 L), threads (606 L). `tiktok.py` (31 L) is a
  **stub** gated on `TIKTOK_AUDIT_APPROVED` (full impl sits unused in
  `publishing/tiktok_client.py`). `platforms/dispatcher.py` (39 L) is **dead code** — the
  live path is `publish_all_platforms.py`'s own ThreadPool + `get_client()` registry.
- **Media** — real FFmpeg: `media/ffmpeg.py` (621 L, per-platform `PLATFORM_SPECS` tuned
  for the 4GB VPS), `frame_compositor.py` (779 L, real `filter_complex`),
  `trending_video_fetcher.py` (1,294 L, YouTube Data API v3 + RSS + circuit breaker).
  **Red flag:** VMAF gate is **fail-open** when no master exists
  (`validate_videos.py:~173`) — videos can skip the gate.
- **HTTP/infra** — `circuit_breaker.py` (318 L, real CLOSED/OPEN/HALF_OPEN + `@resilient`);
  `backlog_client.py` (1,449 L) has a real Postgres-only mode (~L281).
- **Publishing infra** — `daily_cap.py` niche-scoped (~L168); `niche_credentials.py` blocks
  cross-channel fallback (~L44), BB falls through to globals (~L60).
- **Writing** — `video_content_writer.py` (584 L) is a real Anthropic call with
  anti-generic-hook prompt rules. `strategies/interfaces.py` = 7 real ABCs.

### 2.2 The 5 channels

| Channel | niche_id | Strategy weight | Tests | Sourcing | Maturity |
|---|---|---|---|---|---|
| CriticalRush (gaming) | `gaming` | ~8.4K LOC bespoke 14-stage pipeline | 282 | YT trending + IGN/Kotaku RSS + IGDB + Reddit + Twitch | **Production** |
| BlackboxBrief (AI) | `ai_creators` | ~2.8K LOC (real fetch+TF-IDF clustering) | 183 | YT creator search + multi-source RSS/API + Reddit | **Production** (only confirmed live publishes) |
| SpliceReel (movies) | `movies` | 1.4K LOC (film-lifecycle scoring) | 134 | YT trending + TMDB + OMDb enrich | **Working MVP** |
| FrameDrift (anime) | `anime` | 1.2K LOC (trend-cycle scoring) | 143 | YT trending + ANN/Crunchyroll/MAL RSS | **Working MVP** |
| ClutchWire (sports) | `sports` | 1.0K LOC, near-empty subclasses | 136 | YT trending + ScoreBat + ESPN API | **Thin scaffold** |

- **Gaming** is the architectural outlier — bespoke stages under `niches/gaming/stages/`
  (`render_gaming_video.py` 970 L), predates the base-class refactor, never adopted it.
- **BlackboxBrief** is the only channel with confirmed live publishes; richest pipeline
  (adds RelevanceGate, AffiliateMatch, Whisper captions, FetchInsights/PerformanceLearner).
- **SpliceReel/FrameDrift** are working MVPs with genuine niche logic.
- **ClutchWire** is mostly thin pass-throughs riding on shared stages — no sports-specific
  intelligence (no analogue to SpliceReel's lifecycle scoring).

### 2.3 Learning loop — **Thompson correct-but-starved; LinUCB correct-and-BROKEN; classifier inert**
- The bandit loop genuinely turns and is scheduled: context stored at publish
  (`publish_all_platforms.py:~1048`), collected daily (`collect_feedback.py:57`), reward via
  `RewardShaper` at 48h (`reward_shaper.py:~171`), posteriors updated
  (`metric_collector.py:~1214–1357`), and **consumed at selection**
  (`push_to_backlog.py:~624–646`, ±20% priority shift) — but **only when `arm.n_obs ≥ 5`**.
- **VERIFIED CORRECT (wave 2):** reward is bounded [0,1] and distinguishes "no data" (empty
  dict, no update) from genuine-zero engagement; bandit math is textbook and **single-fires**
  (only the 48h window updates — no double-count across 6h/24h/48h/168h); persistence
  round-trips losslessly (float64 A/b via JSON). Dimension is uniformly **12-D** at every site.
- **BUG — LinUCB feature mismatch (R-18):** `build_content_context` is called at *store* time
  with the **blueprint** dict (`publish_all:~1052`), which has no `content` sub-dict and no
  persisted `composite_score`, so dims 5,6,9,11 (relevance, hook_length, caption_length,
  trending) are trained as 0/0.5; but at *predict* time it's called with the **story** dict
  (`push_to_backlog:~430`) where those dims have real values. The model is fit in one feature
  region and queried in another → 4 of 12 dims are out-of-distribution. Masked today (the
  `n_obs≥5` gate is months away) but it will nudge **wrongly** once data accumulates — worse
  than absent.
- **Binding constraint:** at 1 reel/channel/day with 48h lag, accumulating data takes months;
  today it's mostly Thompson + a thin nudge. Note: `MIN_OBS_FOR_LINUCB=50` (`linucb.py:~34`)
  is **dead config** — selection only enforces `n_obs≥5`.
- **Hook classifier is ML-theater:** xgboost installed, but `genlab-core/models/` has only
  `.gitkeep` → every `score_hook()` returns neutral 0.5 (`hook_classifier.py:~120`) → the
  ±25% ranking multiplier collapses to ×1.0. Trainer skips below `MIN_EXAMPLES=50`. **[verify on host]** whether a model was trained on Hetzner.

### 2.4 Engagement engine — **Live, but webhook-only**
- Real LLM replies (`persona_engine.py:~86–106`, Claude Haiku via `ANTHROPIC_CB`), real
  posting through the same platform clients (`comment_processor._post_reply:~624`).
- Gates (`classify_reply_action:~62`): discard if tox ≥ 0.3 or conf < 0.5; auto only if
  conf ≥ 0.85 AND tox < 0.15 AND safe-pattern AND <100 chars; else human review. Detoxify
  is real (`toxicity_gate.py:~41`). **Soft spot:** `confidence` is a hand-rolled heuristic,
  not a model output.
- Production wiring real: `genlab-webhook.service` → Dramatiq actors
  (`tasks.py:~72–110`) → `genlab-engagement-worker.service`.
- **Gap:** the YT/X/Threads **poller is only in legacy `deploy/systemd/`, not
  `deploy/systemd-phase2/`** → engagement is effectively **Meta-webhook-only (FB/IG)** in
  prod. **[verify on host]**

### 2.5 Dashboard — **Real & load-bearing**
- 152 TS/TSX files, 37 server route modules, `review_server.py` (1,922 L). Real views,
  React 19 + Vite + react-query + zustand + recharts.
- **Approval gate is server-enforced:** `publishing_queue.py:~333` (`approve()` assigns a
  collision-free slot under a Postgres advisory lock); publisher's
  `gatekeeper.py:~49` hard-rejects non-approved blueprints. **Caveat:** express-lane bypass
  for CRITICAL/HIGH urgency skips approval (`gatekeeper.py:~52`).
- **Stub:** in-process `core/scheduler.py` jobs are no-ops ("would run…", ~L36–66) — real
  cron runs via systemd, not the dashboard.
- **Frontend untested in CI:** only 5 vitest util files, **no vitest CI job**.

### 2.6 Storage — **Postgres is primary; RLS dormant**
- Real psycopg3 backend (`storage/postgres.py`, 747 L); `backlog_client.py:~281` goes
  Postgres-only when `GENLAB_USE_POSTGRES=true`. 11 Alembic migrations → 13 tables,
  45 indexes, 12 RLS policies.
- **RLS is schema-real but runtime-bypassed:** `get/update/delete` hardcode
  `set_config('app.niche_id','')` (admin mode) (`postgres.py:~443,546,580`); niche filtering
  is done client-side in Python. The DB-level tenant boundary is dormant scaffolding.

### 2.7 Credentials & multi-tenancy — **Single-operator solid; not SaaS-ready**
- `niche_credentials.py` (189 L) clean `{PREFIX}_{KEY}` resolution with a real cross-channel
  guard. But tenancy is hardcoded: closed `frozenset` of 6 niche_ids, brand prefixes in
  code, single basic-auth, RLS in admin-bypass. External-tenant SaaS = substantial
  re-architecture, not config.

### 2.8 Ops / deploy / CI / observability — **Weakest link**
- **Deploy:** Hetzner CX23, `/opt/genlab`, **manual scp (not git pull)** per `deploy/DEPLOY.md`;
  host `.git` lags origin; **no CD, no transactional rollback**. Already caused a cross-brand
  mispublish (cluster-A incident, 2026-05-18).
- **Schedules:** `deploy/systemd-phase2/` (44 files) timers — gaming 04:00, anime 06:00,
  movies 08:00, sports 10:00, ai 02:30 UTC; publisher 06:35 + 10:30 UTC. **DEPLOY.md's IST
  table contradicts these timers** (triage hazard).
- **Mac launchd plists must stay unloaded** (loading → DB split-brain; host-attribution
  alert exists because of the 2026-04-29 12-blueprint loss).
- **Observability is the critical gap:** `monitoring/health_monitor.py` (1,285 L) is genuinely
  good detection (WARP outage, split-brain, posterior drift, stuck publishing) **but is never
  scheduled** (no timer/cron), writes alerts to a `pipeline_alerts` table **nothing reads**,
  and there is **no push delivery** anywhere (`slack_webhook_url` is stored but never POSTed).
  Net: logging + on-demand diagnostics, **not monitoring**. Nobody is paged when a channel
  goes dark.
- **CI:** two overlapping/partly-conflicting workflows (`ci.yml`, `test.yml`), Python 3.13
  only, integration + storage tests deselected, **frontend never built/tested**, **no deploy
  gate** (green CI ≠ shipped).
- **Disk/quota:** the most mature ops area — `quota_daemon` (60s poll, two-pass eviction,
  protects published + recent runs). But `disk_quota.yaml` 4×30GB is *disk*; the 4GB is
  *RAM* — **memory pressure on a 4GB box is the real risk** (only an unscheduled `check_swap`
  watches it).

### 2.9 Monetization layer — **Well-built & platform-compliant, but blind and Amazon-only**
(~18 modules — far more complete than the docs imply, but the revenue loop is a no-op.)
- **Caption compliance is a genuine strength:** `cta_engine.inject_cta` correctly keeps links
  out of the X/FB body and into the first comment/reply, IG CTA before hashtags, dedupes
  disclosures — no shadowban-risk injection found.
- **Attribution is structurally broken (R-23):** published links are raw Amazon UTM URLs, not
  the `/links/go` redirect, so 100% of in-feed clicks are invisible → CTA bandit trains
  all-failure, reward bonus dead, revenue dashboard shows zeros.
- **Only direct Amazon links work (R-31):** all other networks are `NotImplementedError`;
  PA-API gated off (empty keys); Cuelinks strips the tag (0 commission). Matching is a weak
  1-keyword + evergreen-fallback machine (anime clip → generic "Figure Collection"; the AI
  channel's evergreen is a 0%-commission claude.ai link).
- **No revenue readout (R-32):** the only ingestion is an uninstalled Playwright scraper.
- **Verdict:** can earn Amazon commission *today* on 4 channels with compliant captions, but
  is **flying blind** — one wire (route links through the existing redirect server) flips it
  from "blind" to "instrumented," and everything downstream (bandit, reward, dashboard) is
  already waiting on that.

### 2.10 Render pipeline — **Geometry/codec correct; quality gate & logo guarantee broken**
- **VERIFIED correct:** the primary `FrameCompositor.compose()` path enforces 1080×1920,
  bt709, yuv420p, AAC 48k stereo, faststart on every encode; the VMAF re-encode loop is
  bounded (single re-encode, min CRF 12).
- **Broken:** VMAF reference is the raw source clip not a render master (R-25); logo invariant
  is violable/unverified (R-26); no max-duration trim and TTS unwired (R-39); the FFV1
  `render_master`/`transcode_for_platforms` tree is dead code with an arg-order bug;
  `smart_crop` is inert (opencv absent → always center-crop). Gaming compilation audio uses
  `amix` (overlapping commentary) rather than sequenced concat.

---

## 3. Maturity scorecard

| Subsystem | Verdict |
|---|---|
| genlab-core engine | Production |
| Channels: gaming, BlackboxBrief | Production |
| Channels: movies, anime | Working MVP |
| Channel: sports | Thin scaffold |
| Learning — bandit | Closed-loop but data-starved |
| Learning — hook classifier | ML theater (inert 0.5) |
| Engagement engine | Live, webhook-only (pollers undeployed) |
| Dashboard | Real & load-bearing (no FE tests in CI) |
| Storage (Postgres) | Real backend, but **write-path is protocol-violating (R-45)**, 4 tables unmigrated (R-48), DSL parse failures (R-49) — primary path fragile [verify on host] |
| RLS / multi-tenancy | Dormant scaffolding; **write-side isolation structurally absent (no `WITH CHECK`)** — see §9 |
| Stage-handoff integrity | Core loop sound (hook_style/arm_id verified); **seam bugs**: virality_score dropped (R-19), X affiliate name mismatch (R-46), render-validation unenforced (R-47) |
| Content quality / gates | Strong happy-path prompts; **enforcement gates leaky** — sentence-case/CTA-last/≤60/HTML-strip unenforced, fallback hooks generic (R-50/51/52/53) |
| Security (deps + surface) | No secrets / no code-exec (verified); but **17 py CVEs incl. lxml XXE + pillow RCE, webhook injection, timing-unsafe auth** (R-59/60/61) |
| Observability / alerting | **Detect→alert chain broken at every link** — monitor unscheduled (R-01), alerts table unread + units don't alert + no push sink (R-67), dark channel reports "healthy" (R-65), metrics.jsonl dead (R-66) |
| Test infrastructure | 56% engine coverage; **Postgres path + integration run nowhere in CI**, suite segfaults on the CI Python 3.13, a test enshrines R-45 (R-64); no frontend tests in CI |
| Architecture governance | 3-layer design sound internally (no cycles), but **import-linter is broken + unenforced (R-69)**; VisualRender/Scoring base classes missing → ~750L dup (R-70); bus-factor 1 (R-71) |
| Frontend / approval UI | Real & resilient (error boundaries, socket cleanup), but **no CI gate (57 eslint errors, R-72)** + **double-submit holes** on the review cards / FocusMode keyboard (R-73, client face of R-22) |
| Onboarding / reproducibility | A by-the-book fresh clone **can't reach a working run** — README omits the Postgres env pair, `.env.example` is 57 vars short, WARP/Redis/Node undocumented (R-74); fresh DB missing 7+ tables (R-48) |
| Engagement replies | Idempotency + outbound-toxicity solid, but **auto-tier is broken/unreachable**, replies have no post context, no brand-rule enforcement, stale banned-phrase persona (R-75/76/77) — review-only by accident today |
| Blueprint state machine | Crash-recoverable core, but **raw-SQL archive can hit scheduled posts (R-79)**, demotion guard vocabulary-incomplete (R-80), 3 orphan classes accumulate (R-81); documented ladder half-fiction |
| Render pipeline | Geometry/codec correct; **quality gate broken, logo guarantee violable** |
| Publish reliability | Works single-host serial; **double-publish & partial-state risks** (R-21/R-24/R-29) |
| Cost / quota control | Real YT-quota minimization; spend unmeasured but **audit-estimated ~$0.29/day, 6–40× under budget** (R-27) |
| Monetization | Compliant & built, but **unmeasured & Amazon-only** (R-23/R-31/R-32) |
| Ops: deploy / CI / monitoring | Weakest link |
| Dependency / version currency | Mostly current, but **3 active deprecations** (X v1.1 media R-42, dead Gemini ID R-43, Meta v21 + ~June-2026 metric retirement R-44) + missed cost features (no prompt caching / Batch — U-01/02). Full sweep → §8 |

---

## 4. Cross-cutting truths

1. **The system is designed to fail silently, and nothing watches it.** Many individually-
   reasonable graceful-degradation paths (fetcher `return []` on circuit-open, VMAF
   fail-open, SKIPPED records instead of crashes) compound so a channel can produce zero
   output without an error — and there is no scheduled monitor or alert delivery. This has
   already caused a 5-day silent WARP outage and ~40 days of broken engagement.
2. **"Self-learning" is aspiration, not reality, today.** Plumbing is real and scheduled,
   but the bandit is data-starved (months to maturity) and the hook classifier is inert.
   Content is currently driven by hand-tuned heuristics + prompts, not ML.
3. **It's a single-operator control plane, not a multi-tenant SaaS.** Hardcoded niche set,
   brand-prefixed `.env`, single basic-auth, RLS bypassed. Phase 1 → SaaS is real work.
4. **Several documented invariants aren't actually enforced.** The most load-bearing product
   rules are all leaky in code: "1 reel/channel/day" (cap is really 2/platform, no render-time
   enforcement, slot lock non-functional — R-09/R-10/R-22), "dashboard approval is the real
   gate" (express-lane auto-publishes — R-08), "VMAF≥85" (wrong reference / disabled — R-25),
   "every reel has the logo" (portrait/missing-file paths skip it — R-26), and "no double
   posting" (retry pass + non-atomic claim — R-21/R-24). When you reason from CLAUDE.md you
   assume protections the code doesn't provide.
5. **The whole money path is unmeasured — the system can't see cost OR revenue.** Spend has no
   accounting (R-27, three dead trackers, <$5/day unobservable) and in-feed affiliate clicks
   are never logged (R-23, links bypass the redirect), so revenue is zero-by-construction in
   the data even if links convert. Combined with truth #1 (no alerting), the business is
   operating blind on its two most important quantities: is it losing money, and is it making
   any? Both are currently unanswerable from the system itself. **(Refined wave 5: the *cost*
   half is now answered — measured at ~$0.29/day expected, 6–40× under the $5 budget; the bill is
   small even though the system can't see it. The *revenue* half remains zero-by-construction.)**
6. **It's a single-maintainer system whose fragility concentrates on its most important path.**
   595/601 commits are one author's; fixes outnumber features 1.55:1; and the empirical churn lens
   (wave 5) independently rediscovered the audit's hotspots — the **publish→persist→monetize→learn
   spine** (`publish_all_platforms`/`push_to_backlog`/`metric_collector`/`backlog_client`) is at once
   the most-churned, most-fixed, most-god-sized, least-tested (R-08/R-22 have zero tests; the Postgres
   path runs nowhere in CI), and highest-risk part of the codebase. The guardrails meant to protect it
   are themselves inert: the test net doesn't cover it (R-64), the layer-linter is broken (R-69), the
   monitor is unscheduled (R-01/R-67), and a test actively enshrines a bug (R-45/R-64). New work should
   harden this spine with a contract-test net before adding features.

---

## 5. Risk register

**Fix-first priority** (highest leverage across all 41, balancing severity × likelihood ×
effort):
1. **R-01** — schedule the health monitor + wire alert delivery. *Nothing else matters if you
   can't see failures.* Half a day; unblocks visibility of most other risks.
2. **R-23** — route published affiliate links through `/links/go`. One wire; flips
   monetization from "blind" to "instrumented" and revives the CTA bandit + reward loop.
3. **R-21 + R-24** — stop double-publish: conditional claim `UPDATE … WHERE
   status='VISUAL_READY'` + a "did this post land" check before retry. Real money/reputation.
4. **R-41** — re-enable the "scheduled posts are sacred" guard on the Postgres path
   (wrap the proxy / route through `update_blueprint_status`). Cheap; prevents lost work.
5. **R-08** — don't derive auto-publish urgency from untrusted fetched text; gate the
   express-lane behind a human flag or require approval regardless.
6. **R-09/R-10/R-22** — make the 1/day cap real: set caps to 1, add render-time enforcement,
   fix the dashboard slot lock, reconcile the double publish windows.
7. **R-27 + R-25** — record LLM `usage` into one tracker (make <$5/day observable) and fix the
   VMAF reference (or drop the gate). Both are "the gate exists but measures nothing."
8. **R-42** — X video uploads call the v1.1 `media/upload` endpoint that was retired 2025-06-09;
   X reels are likely already failing. Verify on host, then move to v2 `/2/media/upload`.
9. **R-44** — Meta FB/IG reach/impression metrics retire **~June 2026**; the insight collectors
   will silently zero out and starve the reward loop. Audit insight fields before the deadline.

**Wave-4 additions (2026-05-23) that jump the queue:**
10. ~~**R-45** — verify on host first.~~ **RESOLVED 2026-05-23: benign.** Prod is on Postgres, but
    the live write paths handle the str return (`push_to_backlog.py:902` isinstance guard; `:1211`
    ignores the return) and the crashing `record["id"]` helpers have no live callers. Downgraded
    CRITICAL→LOW; no longer urgent. The CRITICAL "nothing publishes" was overstated.
11. **R-59** — bump the untrusted-input CVEs (lxml XXE on RSS, pillow RCE on thumbnails, starlette
    on the webhook). One `uv lock --upgrade` of 4-5 packages; closes real exploitable paths.
12. **R-47** — gate `VISUAL_READY` on `video_validation.valid`. One conditional; stops VMAF/spec
    failures (and the banned text-render edge) from auto-scheduling.

| ID | Severity | Status | Risk | Pointer / fix |
|---|---|---|---|---|
| R-01 | CRITICAL | Done | Health monitoring effectively off — best detection code unscheduled, alerts written to an unread table, no push delivery. Nobody paged on a dark channel. | Add `genlab-health-monitor.timer` (hourly) + wire stored `slack_webhook_url` to actually POST. `monitoring/health_monitor.py` _(landed: `0c19a64`)_ |
| R-02 | HIGH | Done | Manual scp deploy, no rollback, host `.git` stale; green CI doesn't gate/trigger deploy. Caused a cross-brand mispublish. | `deploy/DEPLOY.md`; rsync+checksum verify deploy.sh + git-history rollback.sh + 7 phase-2 unit files (the audit's "minimal CD" half — CI-triggered deploy — deliberately deferred) _(landed: `46c119f`)_ |
| R-03 | HIGH | Done | 4GB RAM running ffmpeg + yt-dlp + Postgres + Dramatiq + Detoxify/PyTorch; memory pressure watched only by unscheduled `check_swap`. OOM in publish window silently kills a day's reels. | schedule swap/mem check; consider larger box or staggering _(landed: `9ce03cb`)_ |
| R-04 | MEDIUM | Done | CI doesn't gate what ships: FE untested, single Python version, integration+storage deselected, two divergent workflows. | FE coverage shipped earlier (U-18/R-72); storage shipped earlier (R-48/R-64). This session: gated 40 integration smoke tests + matrixed test-core on Py 3.12/3.13 + removed duplicate lint. Required-status-checks branch protection still off (settings-level decision deferred) _(landed: `ddf6e5a`)_ |
| R-05 | MEDIUM | Done | Engagement pollers (YT/X/Threads) not in `systemd-phase2` → effectively FB/IG-only. **[verify on host]** | host-verify confirmed the poller IS currently active (hand-installed from phase 1) but absent from the phase-2 deploy bundle; added `genlab-engagement-poller.service` to `systemd-phase2/` with AGENT_ROOT + ENGAGEMENT_DISPATCH=dramatiq pinned by regression tests _(landed: `22423ec`)_ |
| R-06 | MEDIUM | Done | Documentation drift as triage hazard (see §6). | reconciled the tracked surface (README "6 platform publishing" → 5, DEPLOY.md schedule rebuilt from live `OnCalendar=` UTC values) and the gitignored CLAUDE.md/security.md locally. 6 regression pins (3 hard for README+DEPLOY, 3 local-only-skip-in-CI for the gitignored docs) _(landed: `bb7512c`)_ |
| R-07 | LOW | Done | VMAF gate fail-open with no master; tiktok stub; dead `dispatcher.py`; duck-typed stage protocol. | VMAF fail-open paths split (infra-skip INFO vs log-unreadable ERROR) + `vmaf_skipped` flag/counter propagated to `run_stats`+`run_report.json`; `Stage(Protocol)` added with runtime_checkable + load-time isinstance gate in `_load_stages` (fail-fast on config typos vs mid-run AttributeError); `dispatcher.py` verified already deleted; tiktok stub verified gated behind `TIKTOK_AUDIT_APPROVED` env flag _(landed: `fee58e9`)_ |
| R-08 | HIGH | Done | **Express-lane approval bypass is externally escalatable.** `ExpressLane` (wired into ALL 5 niches) classifies urgency by regex on fetched `title`+`summary` (`express_lane.py:~160–224`); CRITICAL/HIGH skips the human approval gate (`gatekeeper.py:~52–67`). A crafted YouTube/RSS title ("breaking", "$1B", "launches") can auto-publish to all channels with zero review — breaks the core "approval is the real gate" invariant. | gate express-lane behind an allowlist or require approval regardless of urgency; don't derive urgency from untrusted text _(landed: `68f455d`)_ |
| R-09 | HIGH | Done | **The "1 reel/channel/day" cap is not enforced as documented.** Real cap is `platform_caps.yaml` = **2/platform/day** (tiktok 1); only BB declares `daily_post_cap:1`. CLAUDE's render-time cap is **unimplemented** (zero render-stage enforcement); `DailyCapEnforcer.can_publish` **fails open** on unconfigured platforms (`daily_cap.py:~88–92`). | set caps to 1; implement render-time cap; fail closed _(landed: `ef7d72a`)_ |
| R-10 | HIGH | Done | gaming/sports/movies `schedule.yaml` each declare **two publish windows (06:30 + 14:00 UTC)**, contradicting the 1/day rule and the single window; combined with R-09 this is the most direct duplicate-publish hazard. Publisher timer (06:35/10:30) matches no YAML window — schedule YAML is decorative. | reconcile to one window; make timer the single source _(landed: `6a25efb`)_ |
| R-11 | HIGH | Done | **Silent zero-output cascade.** 0 yt-dlp downloads logs at **INFO** and produces 0 blueprints (`download_top_videos.py:~715`); empty fetch (`trending_video_fetcher` `return []` on circuit-open, ~436/805) **escapes** the zero-blueprint SLO in `run_report` (which only fires when `len(stories)>0`). A channel goes dark with no error. | raise zero-download/zero-fetch to ERROR; trigger SLO on empty stories _(landed: `826aef3`)_ |
| R-12 | HIGH | Done | `relevance_gate.py:~37–56` is **totally fail-open** (missing niche_root / sources.yaml / empty content_filter / empty positive_keywords → all stories pass) and `relevance_filter.py:~65–67` returns score 1.0 on empty keywords. QCGates does not backstop relevance → off-niche/cross-contaminated reels ship silently. | fail closed on missing relevance config _(landed: `826aef3`)_ |
| R-13 | MEDIUM/HIGH | Done | **Anime relevance_threshold is 0.20, not the documented 0.35** (`FrameDrift/sources.yaml:~323`) — anime is currently the *loosest* filter (tied with gaming), the opposite of the "anime strictest" design. Raises off-brand publish risk. | restore 0.35 _(landed: `9de0e16`)_ |
| R-14 | MEDIUM | Done | **Meta webhook signature verification is fail-open**: gated on `if _APP_SECRET:` (`webhook_receiver.py:~60`, `engagement/webhook.py:~84`). If `META_APP_SECRET` is unset, both POST handlers accept unsigned events → attacker can inject fake comment events, triggering LLM replies / Anthropic spend. | reject POST when `_APP_SECRET` is empty _(landed: `3c4696b`)_ |
| R-15 | MEDIUM | Done | TikTok is listed enabled in gaming `publishing.yaml` (`platforms.enabled`) and in all 4 non-BB `niche.yaml → platforms_enabled`, contradicting the TikTok-disabled rule. Only the publisher CLI's hardcoded default platform list (no tiktok) prevents a live mis-publish; a `--platforms` override breaches it. Two competing enablement sources (`niche.yaml` vs `publishing.yaml`, field `x` vs `x_twitter`). | single source of truth; remove tiktok from configs _(landed: `a099d7d`)_ |
| R-16 | LOW | Done | `tags` reach the LLM without `check_for_injection` (`base_writing.py:~145–149` omits tags from the loop; tags interpolated at `video_content_writer.py:~329`). Output filter is a backstop; tags capped 60 chars. | add tags to the injection-check loop _(landed: `9de0e16`)_ |
| R-17 | LOW | Done | Committed SharePoint List GUIDs (`ClutchWire`/`CriticalRush` `config/lists_config.yaml`) and Meta App/Page/IG IDs in `docs/` break the CLAUDE placeholder convention. Not secrets (SharePoint is legacy), but unnecessary disclosure. | placeholder-ize or note as intentional _(landed: `4f940b7`)_ |
| R-18 | HIGH | Done | **LinUCB trains and predicts on different feature vectors.** `build_content_context` gets the blueprint dict at store time (dims 5/6/9/11 → 0/0.5) but the story dict at predict time (real values) — 4 of 12 dims out-of-distribution. Once `n_obs≥5` accumulates, the contextual bandit nudges *wrongly*. Currently masked by data-starvation. | build context from one canonical dict at both sites (persist the full 12-vector at push time, as the updater already trusts `linucb_context`) _(landed: `43c3dff`)_ |
| R-19 | MEDIUM | Done | **Never-run integration tests are bit-rotted AND may reveal a real bug.** CI always deselects them (`addopts="-m 'not integration'"`, fires even on direct invocation). Force-run: 2/10 fail — `ViralityScoring.execute()` reads/writes `context["stories"]` but the test asserts `result["blueprints"]` carries `virality_score`. **CONFIRMED (wave 4, 2026-05-23): `virality_score` never reaches the blueprint** — two root causes: (a) **no stage ever writes `context["blueprints"]`** (every stage reads/writes `context["stories"]`; `PipelineContext.blueprints` is vestigial, `context.py:51`), and (b) `ViralityScoring` writes `bp["virality_score"]` onto the story (`virality_scoring.py:136`) but `PushToBacklog`'s blueprint `fields` dict (`push_to_backlog.py:1055-1107`) never copies it → computed every run, discarded before persistence, never influences `priority_score`. Also pathologically slow (~43s/test). | `virality_score` + `virality_features` (JSON-encoded) copied into the blueprint `fields` dict at the create branch in `push_to_backlog.py`. Integration tests gated in CI via R-04 (with the 2 virality assertions deselected pending R-19 — remove the deselects in a follow-up after this lands) _(landed: `9873219`)_ |
| R-20 | LOW | Done | Dead-code cluster (~1,960 L, zero live refs): `platforms/dispatcher.py` (+its test), gaming `write_gaming_content_legacy.py` (535) + `adapt_gaming_content_legacy.py` (291), `CriticalRush/tests/_legacy/*.py` (8 files, 1,097, never collected — wrong filename prefix). | delete _(landed: `506d67e`)_ |
| R-21 | CRITICAL | Done | **Double-publish via the retry pass.** `run_publish` re-publishes any `FAILED` platform on the last 50 PUBLISHED blueprints (`publish_all_platforms.py:~1083–1256`); the publisher fires at **06:35 AND 10:30** (`genlab-publisher.timer`). A TRANSIENT failure that actually landed (lost response after the post succeeded — `error_classifier.py:~72`) is retried with **no platform-side idempotency** → the same reel posts twice to a live channel. | conditional retry only after a "did this post land" check; add idempotency keys _(landed: `d078f3a`)_ |
| R-22 | CRITICAL | Done | **Dashboard slot-assignment lock is non-functional.** `_advisory_lock` opens its *own* connection and takes a txn-scoped `pg_advisory_xact_lock`, but the read-modify-write runs on a *different* (Graph-sync) connection (`publishing_queue.py:~46–75,366–378`); the lock key is `hash(niche_id)` (per-process randomized → workers never contend); and it fails open. Two approvals can assign the same slot → breaks 1/day. Zero tests. | take the lock on the writing connection, stable digest key, fail closed _(landed: `d078f3a`)_ |
| R-23 | CRITICAL | Done | **In-feed affiliate links bypass click tracking → monetization is unmeasured.** Published captions/comments carry raw Amazon UTM URLs, not the `/links/go/<slug>` redirect (`cta_engine.py:~236`, `publish_all_platforms.py:~347`); the only click logger is the passive link-in-bio page. So `affiliate_clicks` is empty, `_update_cta_bandit_from_clicks` trains **every CTA variant as a failure** (`metric_collector.py:~944`), and the reward affiliate bonus never fires (`reward_shaper.py:~294`). | route published links through `/links/go/<slug>?bp=<id>` _(landed: `0c19a64`)_ |
| R-24 | HIGH | Done | **Cross-process/host double-publish.** Publisher claims its blueprint with a bare `UPDATE status='PUBLISHING'` — **no `WHERE status='VISUAL_READY'` guard, no row lock** (`publish_all_platforms.py:~760–835`); locks live in `/tmp` (host-local). Two publishers (cross-host, or a stray Mac launchd) both select the top blueprint and both publish. Split-brain is detected (`health_monitor.py:~1054`) but never *prevented*. | conditional claim `UPDATE … WHERE status='VISUAL_READY' RETURNING id`; DB advisory lock keyed on niche_id _(landed: `d078f3a`)_ |
| R-25 | HIGH | Done | **VMAF gate validates against the wrong reference.** `master_path` is set to the raw downloaded clip (`video_gate.py:~194`), so `check_vmaf` diffs a branded 1080×1920 reel against an unbranded 1920×1080 source (`validate_videos.py:~171`) → score is meaningless, triggers a wasted CRF-12 re-encode. Gaming disables VMAF entirely and self-compares (`render_gaming_video.py:~379`). The "VMAF≥85" invariant is unenforced platform-wide. | produce a true 1080×1920 lossless master to diff against, or drop the gate _(landed: `e6a0a07`)_ |
| R-26 | HIGH | Done | **Logo-overlay invariant is violable & unverified.** Portrait (9:16) sources render with **zero branding by design** (`frame_compositor.py:~545`); a missing logo file silently degrades to text-only branding on landscape/square (`:~478`) yet still returns success → VISUAL_READY. No post-render check that a logo pixel exists. | verify logo composited post-render; add logo to portrait path _(landed: `bd03477`)_ |
| R-27 | MEDIUM | Done | **<$5/day cost rule is unobservable — but spend is actually small (refined wave 5).** Estimated **~$0.12–0.85/day (expected ~$0.29), 6–40× under budget** (the live path is ~1 LLM call/story producing hook+captions in one response; scoring/adaptation/affiliate-matching are non-LLM; the "3 calls/hook" loop is short-circuited off the live path; OpenAI/Gemini router tiers are dead config; TTS≈$0 on the free cascade). The blindness is precise: `get_accumulator()` defaults to `None`, `set_accumulator()` is never called, and the 3 direct callers bypass `AnthropicLLMClient` — so usage recording is a permanent no-op. Not urgent (downgraded HIGH→MEDIUM), but still record it before SaaS scales it linearly. Original: three cost trackers exist and are **all dead** (`cost_accumulator`/`cost_tracker` never wired; the writer records no `usage`); dashboard cost fields are never populated; model-router budget downgrades never fire (`budget_ratio` never computed from spend). The system is structurally blind to cost. | accumulator installed in `pipeline_runner.run()` (right after `set_current_context`) and reset in the existing `finally`; `RunReport` reads `get_accumulator()` and writes a `cost` key carrying `total_usd`/`by_category`/`budget_remaining_pct`/`entry_count`; budget overrun surfaces via existing `slo_violations` channel with niche-level `error_budgets.daily_cost_usd` override _(landed: `d703906`)_ |
| R-28 | HIGH | Done | **No global YouTube quota ceiling.** Quota tracking is a per-process, log-only dict reset each run (`trending_video_fetcher.py:~70,1106`); the 5 channels run as 5 processes, so "max 50 searches/day across channels" is unenforced. Today usage is low by construction (RSS-first, search-gated ~700u/day — a real strength), but a config flip or RSS outage forcing keyword-search could silently blow 10k. | shared persistent daily counter _(landed: `18287ab`)_ |
| R-29 | HIGH | Done | **Orphaned-thread / partial-publish states.** IG container poll defaults to 480s + a 30s retry → ~990s, exceeding the 600s `future.result(timeout=600)` which does NOT cancel the thread (`instagram.py:~32,370`, `publish_all_platforms.py:~868`) → the orphaned thread can post *after* the publisher recorded FAILED. Crash recovery sets the whole blueprint PUBLISHED if any platform published (masks partial) or resets to VISUAL_READY (re-publishes succeeded ones) (`:~678–730`). | bound IG poll under the executor timeout; per-platform recovery _(landed: `c9affe8`)_ |
| R-30 | HIGH | Done | **YouTube cross-channel guard is dead code.** `verify_channel()` exists and `expected_channel_id` is resolved + passed to the constructor, but `publish()` never calls it (`youtube.py:~194–233,239`). The one code-level guard against publishing to the wrong YT channel — after the real 2026-05-18 cross-brand incident — is unwired. IG/FB/Threads have none. | call `verify_channel()` before upload _(landed: `3c4696b`)_ |
| R-31 | HIGH | Partly-corrected | **Affiliate networks are mostly stubs.** EarnKaro/Impact/ShareASale/CJ raise `NotImplementedError` (`network_registry.py:~102–138`); PA-API signing is a stub with empty keys → dynamic matching gated off; Cuelinks wrapping strips the Amazon tag → 0 commission. Only **direct Amazon Associates** links work today. Matching is weak (1-keyword hit + evergreen fallback). | sub-item **c** (matching): static-catalog match threshold raised from `>0` to `>=2` keyword hits (mirrors the seasonal threshold); 1-hit candidates fall through to the calibrated evergreen path with a visibility log. sub-item **b** (Cuelinks tag-strip): all 33 Cuelinks URLs in `affiliate_catalog.example.yaml` now carry `?tag=${AMAZON_IN_AFFILIATE_TAG}` inside the embedded Amazon URL; regression pin scans the YAML for any link wrapping Amazon without `tag=`. sub-item **a** (real per-network integrations EarnKaro/Impact/ShareASale/CJ + PA-API signing) **deferred** — multi-day per-network work needing operator credentials + signing + error mapping _(landed: `c49a6b1` + `a7bd949`)_ |
| R-32 | HIGH | Done | **No working revenue readout.** The only revenue ingestion is a Playwright dashboard-scraper (`scripts/scrape_affiliate_revenue.py`) and Playwright isn't installed; `record_revenue()` is otherwise never called → `affiliate_revenue` is empty. Even with R-23 fixed, earnings are unmeasured. | use Amazon report API or fix the scraper; make click-through the proxy KPI _(landed: `821bb19`)_ |
| R-33 | MEDIUM | Done | **FB 24h survival check (`REMOVED_BY_META`) is not implemented** — zero occurrences in `genlab-core/src`; the sprint-47 "DELETED status + alert" claim is stale doc-drift. Meta-removed reels are never detected. | implement or strike the claim _(landed: `ecef7c5`)_ |
| R-34 | MEDIUM | Done | Perceptual `HashStore` is a non-atomic whole-file JSON write with no lock (`video_hasher.py:~136`); concurrent writes / crash mid-dump corrupt it → `load()` throws → perceptual dedup silently disabled (duplicates re-ship). XOR-combining frame hashes is also lossy (false negatives). | atomic write+rename; lock; per-frame hash list _(landed: `4409938`)_ |
| R-35 | MEDIUM | Done | No proactive token-health on the publish path (`check_token_health` exists, never called by `run_publish` or any phase-2 timer); Threads refresh fires only lazily inside `publish()` → a channel dark >10 days lets the 60-day token expire unrefreshed. Expiry surfaces only as a per-platform SKIP (compounds R-01). | add a token-health timer _(landed: `d707eee`)_ |
| R-36 | MEDIUM | Done | Partial-failure status is misleadingly binary: 1/5 success → `PUBLISHED` (`publish_all_platforms.py:~948`); the `publish_partial` dashboard event fires only on *terminal* failures. A channel looks healthy while 4 platforms silently didn't post. | surface per-platform partial state _(landed: `a32ef74`)_ |
| R-37 | MEDIUM | Done | `TrendingVideoFetcher` (the hot path for 4 channels) **bypasses the disk cache** — re-queries YouTube every run (cross-run caching absent), contra optimization.md. Only BB's fetch and Google Trends use a cache. | wrap fetches in the 6h disk cache _(landed: `514b3b6`)_ |
| R-38 | MEDIUM | Done | Monetization progress is split across two stores: SharePoint `GenLab_MonetisationProgress` (writer has **no phase-2 timer** → likely unscheduled) which the dashboard *reads*, vs Postgres `monetisationprogress` (live via `genlab-audience-collector.timer`). Dashboard may show zeros while Postgres has data. | unify on Postgres; point dashboard at it _(landed: `5d2a8a1`)_ |
| R-39 | MEDIUM | Done | Render-time spec gaps: no max-duration trim (>60s reels produced then silently dropped downstream as `too_long`); silent/no-audio sources render then get dropped (`no_audio_stream`); the TTS cascade is **never wired into the render path** (the documented ElevenLabs→gTTS audio guarantee is doc-only). | All three sub-items closed. sub-items **a**+**b**: both `too_long` and `no_audio_stream` joined the `ValidateVideos._can_fix` fixable set — `_fix` adds `-t SPEC.max_duration` (output-trim, hook-preserving) and/or `-f lavfi -i anullsrc` (silent stereo bed at 48kHz + `-shortest` so silence never extends past the visual); one ffmpeg pass handles both. sub-item **c** closed via the audit's OR-alternative ("wire TTS or document video-first source-audio policy"): `docs/audio-policy.md` is the canonical policy doc; render path NEVER synthesizes audio (silent-mux > TTS-over-silent because the latter would sound AI-generated and off-brand); TTS cascade is correctly scoped to the audio stage + Whisper caption alignment, not render; `genlab_core.tts.__init__` and `validate_videos.py` docstrings point at the policy with 4 regression-pin tests so a future refactor can't quietly delete the policy reference _(landed: `2f77012` + `75207e5`)_ |
| R-40 | MEDIUM | Done | `PUBLISH_FAILED` revive (publisher, `:~732`) can race a fresh `PushToBacklog` re-create for the same event → two live blueprints; the revive strips `candidate_id` (`:~1196`) so the UNIQUE constraint can't catch it. Also: the `candidate_id` UNIQUE constraint is the real dup backstop, but only for *identical* candidate_id — a different video_id for the same trending event slips through the (lock-free) video_id/Jaccard pre-checks. | Both `recover_publish_failed` (crash_recovery) and the PushToBacklog revive now route the status flip through R-24's `claim_status(expected, new)` atomic primitive. A racing writer harmlessly loses the claim; revived rows can no longer briefly coexist with their replacement. The "event-key dedup" secondary recommendation (catching different `video_id`s of the same trending event) **deferred** — needs an `event_key` column + fetcher change to populate it; flagged as a follow-up _(landed: `a8c13c7`)_ |
| R-41 | HIGH | Done | **"Scheduled posts are sacred" is UNENFORCED in production (verified 2026-05-22).** BOTH guard layers are off on the Postgres-primary path: (1) `ScheduleGuardedProxy` is never applied — the Postgres branch sets `self.blueprints = PostgresTableProxy(...)` raw (`backlog_client.py:330`) and `return`s (`:338`) before the wrap at `:392` (legacy SharePoint only); (2) the method-level guard `update_blueprint_status`→`assert_not_scheduled` (`:717,531`) is called by **no production code** — `publish_all_platforms.py` (`:708/715/722/831/900/1209`) and `push_to_backlog.py` (`:1199`) write status via `blueprints.update(...)` directly. So a re-run/publisher can demote or overwrite a `scheduled_for` blueprint — a non-negotiable `cleanup_safety.md` rule. Regressed silently when Postgres became primary (Sprint 65). | wrap the Postgres `blueprints` proxy in `ScheduleGuardedProxy`; route status writes through `update_blueprint_status` _(landed: `699cd5d`)_ |
| R-42 | HIGH | Done | **X/Twitter video upload uses a retired API (verified 2026-05-22).** `_get_api_v1()` builds a `tweepy.API` v1.1 client (`x_twitter.py:109`) and `:275` calls `api_v1.media_upload(...)`; the v1.1 `media/upload` endpoint was **sunset 2025-06-09** (~11 months ago). tweepy 4.16 (locked) exposes no v2 `Client.media_upload`, so every video tweet's media step likely errors → X reels SKIPPED/FAILED, masked by the silent-SKIP cascade (R-11/R-36). OAuth1.0a tweet creation (`create_tweet`) is unaffected. | migrate to v2 `POST /2/media/upload` (OAuth1.0a still valid; needs `media.write`) or a tweepy version exposing v2 media. **[verify on host]** whether X video publishes are currently failing _(landed: `5b5e6f9`)_ |
| R-43 | MEDIUM | Done | **Dead Gemini model ID (verified 2026-05-22).** The `video_analysis` tier is pinned to `gemini-2.5-flash-preview-04-17` (`llm/router.py:72`, `model_routing.yaml:41`), a preview **deprecated 2025-07-15** → the endpoint 404s. Latent if the tier is rarely exercised; a hard error the moment it is. | all three sites bumped to GA `gemini-2.5-flash`: `model_routing.yaml:41` + `llm/router.py:72` (`9de0e16`), `llm/video_analyzer.py:83` (`ba98fd1` / #123). Exhaustive workspace grep confirms zero `gemini-*-preview-*` references remain (verified 2026-06-11). The row was Open in the doc only — code was already complete; this row catches up _(landed: `9de0e16` + `ba98fd1`)_ |
| R-44 | HIGH | Done | **Meta Graph API aging + imminent metric retirement.** All FB/IG calls pin `v21.0` across ~20 sites (client defaults `facebook.py:66`, `instagram.py:59`) — 4 majors behind v25.0 (no hard sunset yet, ~12mo out). **More urgent:** legacy FB/IG reach & impression metrics retire **~June 2026**; the insight collectors (`metric_collector.py:378+`, `fetch_insights.py:226+`) request these fields and will **silently zero out**, starving the learning-loop reward inputs (compounds R-23/R-27 + bandit data-starvation). | centralize the version in one constant + bump to v25.0; audit/replace retiring insight fields before June 2026 _(landed: `f5c2b3a`)_ |
| R-45 | LOW | Done | **Postgres backend's return type doesn't match the StorageBackend protocol annotation (verified 2026-05-23; downgraded CRITICAL→LOW after host-path tracing).** `PostgresBackend.create`/`batch_create` return a bare `str`/`list[str]` (`postgres.py:406,429,592,620`); the protocol annotates `dict`. The `backlog_client.py` helper methods that do `record["id"]` (`create_story:570`, `create_blueprint:702`, `batch_create_*:617/803`, `log_publish_result:1095`, …) WOULD `TypeError` on Postgres — **but none are called by live pipeline code (grep: no external callers).** **RESOLVED: prod IS on Postgres (`GENLAB_USE_POSTGRES=true`) and does NOT crash** — the live writes handle the str return explicitly: `push_to_backlog.py:902` does `if isinstance(record, str): {"id": record}` for stories, and `:1211` ignores the blueprint `create()` return. So this is a protocol-annotation mismatch + latent-buggy *dead* helpers, not a publishing outage; the wave-1/4 CRITICAL "nothing publishes" was overstated. (Corrects R-64: `test_postgres_phase2:80`'s `uuid.UUID(record_id)` tests the real intended contract — a UUID string — **not** a bug.) | protocol now honestly annotates `CreateResult = str \| dict[str, Any]` (both backends are correct, neither was lying); new `id_from_create_result()` helper centralizes the str-vs-dict extraction (was scattered as inline isinstance dances); 10 parity tests (8 unconditional helper-level + mock + protocol-shape + 2 SKIPPED live-Postgres). Existing isinstance defensive sites and dead helpers left in place — the protocol annotation no longer lies, which was R-45's core complaint _(landed: `ce0163f`)_ |
| R-46 | HIGH | Done | **X affiliate first-comment never posts (name mismatch).** `publish_all_platforms.py:513` reads `twitter_first_comment` only when `platform == "x_twitter"`, but the default platform list uses `"twitter"` (`:1299`) and sibling branches handle both names (`:385` `in ("twitter","x_twitter")`, `:559` `=="twitter"`) — the asymmetry means the affiliate self-reply URL (the entire X monetization payload) is dropped. `cta_engine.py:305` writes it; `:511` Facebook's equivalent name matches and works. Compounds R-23. | normalize the platform name before the comparison, or `platform in ("twitter","x_twitter")` at `:513`. **[verify on host]** X posts for a missing first-comment _(landed: `9de0e16`)_ |
| R-47 | HIGH | Done | **Render quality-gate result is never enforced at persistence.** `validate_videos.py:104-146` writes `media["video_validation"]={"valid":…}` (incl. `vmaf_below_threshold`, un-fixable `wrong_dimensions`, `no_audio_stream`), but `push_to_backlog.py` gates only on `if rendered_path:` (`:1024,1101,1148`) and never reads `["video_validation"]["valid"]` — the only reader is `run_report.py` (stats). A video that FAILS VMAF/spec still gets `VISUAL_READY` + auto-schedule. Distinct from R-25 (wrong reference): even the check that *does* run is ignored. | treat `rendered_path` as publishable only when `video_validation.valid` is True; else keep DRAFTED _(landed: `9de0e16`)_ |
| R-48 | HIGH | Done | **≥7 code-referenced tables have no Alembic migration (verified; wave 6 widened 4→7+).** The original 4 (`monetisationprogress`/`affiliate_clicks`/`ab_tests`/`audience_snapshots`) **plus** `affiliate_revenue`/`product_embeddings`/`preference_data`/`dashboard_events` (all 0 CREATE) and `content_pool` (only a hand-run `.sql`, not Alembic) are in `_VALID_TABLES`/`PROMOTED_COLUMNS` (`postgres.py:136-141,282-307`) and queried (`metric_collector.py:822,935`) but **no Alembic revision CREATEs them** (grep: 0). A fresh `alembic upgrade head` yields a DB where reads error (swallowed at `metric_collector.py:829`) → monetisation boost / audience / CTA-click paths silently dead. `env.py:26` `target_metadata=None` disables autogenerate so the drift is invisible. | add migrations creating + RLS-enabling all 4; assert table existence at startup _(landed: `823e6e7`)_ |
| R-49 | HIGH | Done | **`formula_to_sql` parse failures silently break CTA bandit + publish dedup on Postgres.** The DSL regex matches only `{f}='v'` with no spaces (`formula_sql.py:166`); the CTA-bandit queries use `{task_id} = '…'` *with* spaces (`metric_collector.py:918,937`) → pass through unparsed → invalid SQL → caught by `try/except` → CTA bandit never updates. Separately, queries filter on `analytics_id`/`task_id` which live only in `extra` JSONB, not as columns (`backlog_client.py:1076`) → "column does not exist" → `log_publish_result` dedup `find` errors → every publish takes the create branch → duplicate `publishing_analytics` rows. | tolerate whitespace + add a spaced-`=` branch (or retire the formula DSL for structured filters); promote `analytics_id`/`candidate_id` to columns or query `extra->>` _(landed: `1583dee`)_ |
| R-50 | HIGH | Done | **Sentence-case is enforced nowhere in code, and BB's config actively demands lowercase.** No normalizer exists (grep: only prompt strings + one fallback `.capitalize()` at `video_content_writer.py:452`); the rule is only "requested" in prompts. `BlackboxBrief/config/writing.yaml:28-45` (all-lowercase caption examples + "Lowercase where natural") contradicts the shared prompt's "❌ all-lowercase reads as shitpost" (`video_content_writer.py:256-270`), and BB's `extra_instructions` are appended LAST (`:317`, higher salience) → the flagship production AI channel reliably emits lowercase captions. | add a `to_sentence_case()` post-pass on hook + caption fields; delete BB's lowercase examples _(landed: `51a252b`)_ |
| R-51 | HIGH | Done | **Template-fallback hooks ship all-lowercase and dodge the banned-phrase list.** When `ANTHROPIC_API_KEY` is unset / the LLM fails, gaming ships `templates.yaml:8-26` formulas ("the {game} community is eating rn", "wait... {game} really did this??") — all-lowercase, and `_is_banned` (`base_hooks.py:202-210`) uses exact-substring so they evade the banned "the community is going wild"; `HookValidator` has no all-lowercase rule. Degraded-mode output is exactly the generic AI-tell content the system claims to ban. | rewrite formulas in sentence case; add an all-lowercase check + evasive variants to the banned list _(landed: `51a252b`)_ |
| R-52 | MEDIUM | Done | **The 9-rule HookValidator is dead on the primary (LLM) path + can't enforce ≤60 chars.** `base_hooks.py:312-321` `continue`s for `written_by=="llm"` so `validator.validate()` (`:328`) runs only on template hooks; `HookValidator` has no ≤60 rule (`_TITLE_LIMITS["instagram"]=2200`, `hook_validator.py:163`) — ≤60 is only ad-hoc truncation (`video_content_writer.py:352`, `llm_hook_generator.py:396`) producing "…"-suffixed hooks rather than regeneration. | run the validator on LLM hooks too; add a hard ≤60 rule; regenerate on failure _(landed: `51a252b`)_ |
| R-53 | MEDIUM | Done | **Caption platform-rule enforcement gaps (3).** IG caption assembly is `[body, cta, hashtags]` so it ends with hashtags, not the CTA — violates "must end with CTA" (`video_content_writer.py:461-467`; `caption_ends_with_cta` flag never read); only the *upper* length bound is enforced (no IG-150 / FB-200 floor, no FB trailing-`?` check, `:457-520`); LLM output is never HTML-stripped before render/Postgres (`push_to_backlog.py:1061-1078` store hook/caption raw; the Graph sanitizer is bypassed on the PG path). | reorder CTA last; add floor + FB-question validation; `strip_html_tags` on all output fields _(landed: `6cc255c`)_ |
| R-54 | HIGH | Done | **No global render/encode concurrency lock → the realized OOM mechanism behind R-03.** The pipeline mutex is per-niche only (`pipeline_runner.py:57-107`, docstring: "different niches … run in parallel"); no machine-wide encode semaphore exists (grep: none). Overlapping launchd schedules run N concurrent FFmpeg + VMAF passes on a 4GB box (code already notes x265 OOMs). Also: the H.264 `tee` runs up to 5 x264 encoders in one process (`ffmpeg.py:494-532`); VMAF temp-log path collides across runs (`video_validator.py:43`, keyed only by platform). | add a cross-process `render.global.lock` around render+VMAF; stagger schedules; serial transcode on ≤4GB; PID/run-id the VMAF log _(landed: `9ce03cb`)_ |
| R-55 | HIGH | Done | **YouTube quota is unsafe across the 5 niche processes.** Upload ledger `_save()` is a non-atomic whole-file `write_text` with a per-process lock, but the file is shared by all niches (`youtube_quota.py:168-183`, `platforms/youtube.py:103`) → last-writer-wins undercount → `uploadLimitExceeded` (a known daily failure). Fetch-side quota is in-process, reset every run (`trending_video_fetcher.py:70-86`) → no aggregated daily ceiling across channels (extends R-28). | atomic write + `flock` around the upload ledger RMW (as `comment_processor.py:263` already does); route fetch counts through the persistent tracker _(landed: `4409938`)_ |
| R-56 | MEDIUM | Done | **`transcode_for_platforms_sync` swaps args positionally.** Sync wrapper `(master, output_dir, platforms)` calls the async `(master, platforms, output_dir)` (`ffmpeg.py:603-621`) → `output_dir` binds to `platforms`. Latent (the gaming path bypasses this entrypoint via FrameCompositor) but a live bug for any caller of the sync wrapper. | fix arg order; add a call-site test _(landed: `4409938`)_ |
| R-57 | MEDIUM | Done | **Disk quota daemon omits the production channel.** `disk_quota.yaml` (both copies) enumerates only criticalrush/clutchwire/splicereel/framedrift — **no `blackboxbrief`/`ai_creators`** agent, so BB's run dir (the only confirmed-live channel) is unmanaged by `quota_daemon`; configured quota totals 4×30GB=120GB on one box. Also `scan_runs` walks each run tree 3× per 60s poll (`disk_quota.py:291-296`). | add a BB agent stanza; right-size quota to actual disk; single-pass `os.walk` _(landed: `18dd344`)_ |
| R-58 | MEDIUM | Done | **Content_Memory dedup loads the entire never-purged history into RAM and re-tokenizes per check.** `check_duplicate`/`find_similar` iterate `_load_posts()` → `proxy.all()` with no limit (`scripts/content_memory.py:487-523,147-184`) and re-tokenize both sides per post; CLAUDE mandates "DO NOT PURGE" → unbounded O(n) RAM+CPU that grows forever on the 4GB box. | 3 layered defenses against the scan-everything class: `_load_posts()` pre-tokenizes once per cache TTL (`_token_set`); `check_duplicate`/`find_similar` take a `window_days=90` recency cap with `_MAX_DEDUP_POSTS=5000` hard ceiling; exact-hash short-circuit returns similarity 1.0 without any Jaccard. Server-side hash filter (SharePoint `$filter` push-down) explicitly noted as deferred — needs Content_Memory list schema change _(landed: `525da99`)_ |
| R-59 | HIGH | Done | **Known-CVE dependencies on untrusted-input paths.** `pip-audit`: 17 vulns / 11 used packages — **lxml 6.0.2 XXE** (RSS/feed XML reads local files, PYSEC-2026-87→6.1.0), **starlette 0.52.1 host/path injection** (the FastAPI webhook, →1.0.1), **pillow 12.1.1 PSD memory-corruption/RCE + decompression-bomb DoS** (processes external thumbnails, →12.2.0), urllib3 redirect-leak (→2.7.0), idna ReDoS, cryptography name-constraint bypass, mako/pygments. `npm audit`: 5 (vite dev-server arbitrary file read GHSA-p9ff-h696-f583 — dev-only). Cross-ref U-06/U-08/U-24. | bump lxml/starlette/pillow/urllib3/cryptography first (untrusted-input sinks); then the dev-chain _(landed: `9de0e16`)_ |
| R-60 | MEDIUM | Done | **Webhook `media_id` formula injection.** `engagement/webhook.py:45-46` interpolates the attacker-controlled Meta `media_id` raw into `SEARCH('{media_id}', {post_id})`, bypassing the `_esc()` (`graph_proxy.py:39`) used everywhere else in `backlog_client.py`. | `_esc(media_id)` + validate it's a numeric Meta ID _(landed: `d707eee`)_ |
| R-61 | MEDIUM | Done | **Timing-unsafe Basic-Auth comparison.** `review_server.py:302` uses `==` for user/pass, while the form-login path (`:341-342`) and CSRF (`:494`) correctly use `hmac.compare_digest`. Only the Basic-Auth fallback is inconsistent. | `hmac.compare_digest` _(landed: `9de0e16`)_ |
| R-62 | LOW | Done | **Bounded SSRF via `is_direct_video_url` catch-all.** The final pattern `https?://[^\s"']+\.(?:mp4\|webm)` (`video_sourcer.py:56`) matches any host; the URL flows from externally-controlled story fields (Reddit `fallback_url`) into yt-dlp (`download_top_videos.py:123,372`). Mitigated only when WARP/`YT_DLP_PROXY` forces external egress. | `_KNOWN_VIDEO_CDN_HOST_SUFFIXES` allowlist gates the catch-all; host extracted via `urlsplit().hostname` (not naive `endswith`) to defeat the userinfo-spoof (`http://evil.com@target.com/x.mp4`) class of bypass. 5 regression pins cover private IPs, userinfo spoof, and the userinfo-with-allowed-host edge _(landed: `6d559b0`)_ |
| R-63 | MEDIUM | Done | **Postgres schema-hygiene bundle.** Zero CHECK constraints anywhere (status free-text `TEXT`, scores unbounded — a typo'd status silently breaks the state machine); `stories.score` column never written + its partial index `WHERE status='NEW'` matches 0 rows (stories are created `INTAKE`) (`postgres.py:185`, `backlog_client.py:557`); standalone `.sql` files run outside Alembic create RLS-less `content_pool`/`affiliate_clicks` + duplicate-index drift (`migrations/*.sql`); `migrate_table.py:59` `ON CONFLICT (arm_id)` is broken after migration i9 dropped that unique key; two exact-duplicate blueprint indexes (`idx_bp_niche_status`/`idx_bp_status_niche`); the universal `ORDER BY created_at DESC` is unindexed on 12/13 tables; `fk_bp_story` validates without `NOT VALID` (locks on large tables). | add CHECK constraints + status enum; fold `.sql` into Alembic with RLS; fix the ON CONFLICT key; drop dup/dead indexes; add `(status, created_at DESC)` _(landed: `5aad535`)_ |
| R-64 | MEDIUM | Done | **Test suite segfaults on the CI Python + the primary store is untested in CI (verified 2026-05-23).** `conftest.py:27` guards the Detoxify/libtorch OpenMP segfault only on `>=3.14`, but CI runs **3.13** → `test_reply_dispatch_live` loads the real model and SIGSEGVs the engine job (crash report: `libtorch_cpu`→`libomp` in `layer_norm`). Separately: `tests/storage/` + `tests/integration/` are CI-deselected and all 50 `test_postgres_*` tests **skip** without `POSTGRES_PASSWORD` → the Postgres write-path runs **nowhere** in CI (engine coverage **56%**, `postgres.py` effectively ~20%). Worst: `test_postgres_phase2:80` does `uuid.UUID(record_id)`, **enshrining the R-45 bare-string return** → a correct R-45 fix breaks that test. | extend the skip to `>=3.13` or set `OMP_NUM_THREADS=1` in CI; add a CI Postgres service; add a backend return-contract parity test + fix the enshrining test _(landed: `9de0e16`)_ |
| R-65 | CRITICAL | Done | **A fully dark channel reports `status="success"` (verified 2026-05-23).** `run_report.py:99-108`: a clean total-fetch-wipeout (0 stories, 0 errors) matches neither `has_errors and not stories` nor `zero_blueprint_cascade` (which requires `len(stories)>0`, `:91`) → falls through to `else → "success"`; the dashboard then shows the niche `health:"healthy"`. With R-11 (zero-download at INFO) + absent alerting (R-01/R-67), a channel can fetch nothing and miss its daily post with **zero signal**. | treat `blueprints_pushed==0` as `failed` regardless of story count — a video-first pipeline producing 0 blueprints is always a failure _(landed: `9de0e16`)_ |
| R-66 | HIGH | Done | **`PipelineMetrics` / `metrics.jsonl` is dead code (verified 2026-05-23).** `StageRunnerFactory(...)` is built with **no `metrics=` arg** (`pipeline_runner.py:277`) → `_metrics` is always `None`, `.flush()` is never called, and nothing reads `metrics.jsonl`. The CLAUDE.md "auto-records per-stage timing in metrics.jsonl" claim is inert — no per-stage timing observability exists beyond the in-memory `run_report`. | instantiate `PipelineMetrics(niche_id, run_id)`, thread into `StageRunnerFactory`, `.flush()` in `finally` (~15 lines); or strike the claim _(landed: `9de0e16`)_ |
| R-67 | HIGH | Done | **The detect→alert chain is broken at every link (extends R-01).** `pipeline_alerts` is write-only — the dashboard `/alerts/*` API never queries it; `/alerts/system` (`missed_today`, the one query that detects a dark channel) is **orphaned** in the frontend (`client.ts:392`, no hook/view); **24/25** `alerting.yaml` thresholds have no consumer; all batch units are `Type=oneshot` with **no `Restart=` / `OnFailure=`** (a crashed pipeline waits until tomorrow's tick); and **no push sink (slack/telegram/smtp) exists anywhere**. | wire `/alerts/pipeline`←`pipeline_alerts` + a banner; consume `/alerts/system`; add `Restart=on-failure` + `OnFailure=genlab-alert@%n`; add one `notify()` webhook called from health_monitor + daily-verify _(landed: `18dd344`)_ |
| R-68 | MEDIUM | Done | **JSON logging is off in prod + journald not run-id correlated.** `GENLAB_LOG_JSON` is set in zero units/.env → prod runs the console (plain) renderer despite the "JSON in production" docstring (`observability/logging.py:13`); `bind_contextvars(run_id=…)` is never called, so the journald/console stream carries only `current_stage`, not `run_id` (only the per-niche JSONL handler is run-correlated). | set `GENLAB_LOG_JSON=true` in the units (or default-on when not a TTY); `bind_contextvars(run_id=…)` at run start _(landed: `4617a5c`)_ |
| R-69 | HIGH | Done | **Layer-boundary enforcement is fictional (verified 2026-05-23).** `.importlinter:14` lists a `genlab_core.models` layer that **doesn't exist** (no `src/genlab_core/models/`; models live in `platforms/models.py`/`auth/models.py`) → the single contract **crashes at startup** before checking anything; it's **not in CI/pre-commit**; and it covers <20% of the 33 packages (pipeline/publishing/platforms/learning/engagement/storage… ungoverned). Real upward violations exist: `genlab_core` imports BlackboxBrief's `execution.*` (`render_whisper_captions.py:274`, `rendering/word_animator.py:24`, try/except fallback). The "import-linter enforces boundaries" claim (both CLAUDE.md) is unbacked. | fix the `models` layer name, expand contracts to real packages, add `lint-imports` to CI; finish the `execution.*` extraction _(landed: `6c17d75`)_ |
| R-70 | MEDIUM | Partly-corrected | **Missing strategy base classes → ~750L duplication + 826L dead gaming legacy.** No `BaseVisualRenderStrategy`/`BaseScoringStrategy` in `genlab_core/strategies/` (only Writing/Hooks/ContentResearch/PlatformAdaptation) → `visual_render.py` (~258/248/270L in SR/CW/FD) + the per-channel scoring strategies reimplement an identical method set (~750L copy-paste), contradicting the "~50 lines of niche overrides" claim. Gaming's `write_gaming_content_legacy.py` (535L) + `adapt_gaming_content_legacy.py` (291L) remain in-tree, referenced only by `tests/_legacy/` (extends R-20). | **Part 1 done** (`27dc509`): 826 LOC of gaming legacy strategies deleted (R-20's `506d67e` claimed they were gone but they weren't — this commit makes that claim honest); workspace-wide regex pin on import statements catches re-introduction. **Part 2 design phase done** (`dd4c73b`): empirical measurement found audit overstated — actual divergence is 55% (visual_render) and 70% (scoring), parallel-evolved not copy-paste. `docs/r70-part2-design-phase.md` sequences 5-PR extraction with measured-divergence baseline, method inventory per channel, explicit non-decisions deferred to pilot. **Part 2 PR 1 done** (`fd1ef6c`): `BaseVisualRenderStrategy(VisualRenderStrategy)` shipped to `genlab_core/strategies/base_visual_render.py` — one concrete method (`_get_whisper_config`, verified byte-identical across SR/CW/FD before extraction) + 6 abstract for the pilot to fill in. No channel migration yet; the base is inert until PR 2 (SR pilot). 7 pins cover abstract-contract integrity + `_get_whisper_config` semantics + `isinstance(x, VisualRenderStrategy)` preservation. Sequence steps PR 2-5 (SR pilot, CW+FD migration, `_compose_frame` extraction, scoring base split) remain as named follow-ups _(landed: `27dc509` + `dd4c73b` + `fd1ef6c`)_ |
| R-71 | MEDIUM | Done | **Bus-factor 1 + fragility on the publish→persist→monetize→learn spine.** 595/601 commits by one author (no second reviewer); fix:feat = 245:158 (1.55:1); the most-churned/most-fixed files are exactly the highest-risk ones — `push_to_backlog.py` (36 commits/25 fixes), `publish_all_platforms.py` (25/19), `metric_collector.py` (26, most-active recently) — all god-sized, carrying open CRITICAL/HIGH risks, and the two CRITICAL concurrency/bypass paths (R-08, R-22) have **zero tests**. `affiliate_matcher.py` (17/12 fixes) is the "patched-but-never-converged" signature on the project's KPI. | "zero tests" claim was stale at re-read time: R-08 covered by `test_critical_urgency_no_longer_bypasses_approval` (#116), R-22 by `test_lock_key_is_stable_sha256_not_salted_hash` (#118). This session shipped the contract-test net layer the audit asked for — 3 cross-call invariants on `process_pending_task`: 5-invocation idempotency proves `bandit_updater.call_count == 1`; no fire at 168h (48h is single source of truth); no fire at 6h early-stop (the Bug F regression pin). "Decompose the spine" half remains workflow discipline + tracked under R-70 part 2 _(landed: `bc7ca79`)_ |
| R-72 | HIGH | Done | **Frontend has zero CI gate.** `tsc -b`/`eslint`/`vitest` never run on push (`test.yml:50` runs only pytest); `npx eslint .` = **57 errors** (incl. `react-hooks/set-state-in-effect`×15, purity×3). This is the root cause that lets every frontend defect ship silently — incl. ≥6 more orphaned `client.ts` methods beyond R-67 (a whole unwired `tokenHealth` UI) and a dead `pipeline_state_update` socket listener. `tsc --noEmit` does pass, but `as`-casts mask real server-contract mismatches. | add a `frontend` CI job (`npm ci && build + lint + test`); triage the 57 errors _(landed: `16f281f`)_ |
| R-73 | HIGH | Done | **Approval-UI double-submit (client face of R-22).** `ContentCard` approve/reject/schedule buttons have **no disabled/pending state** (`ContentCard.tsx:266-292`; `approveAndSchedule.isPending` computed but unused) and FocusMode's **keyboard** path bypasses the `isPending` guard the buttons have (`focus-mode.tsx:549-565`) → double-approve on the same blueprint id. The quick-assign dialog also skips the slot-collision warning the drag path has (`schedule-board.tsx:222`). FocusMode/ReviewActions/PublishingQueue are correctly guarded — ContentReviewView is the hole. | thread `isPending` to the buttons + guard the keydown; share occupancy with the assign dialog _(landed: `2178d46`)_ |
| R-74 | HIGH | Done | **Documented setup is unrunnable + reproducibility gaps.** The README quick-start migrates Postgres then runs the pipeline but **never sets `GENLAB_USE_POSTGRES=true` + `DATABASE_URL`** → `BacklogClient.__init__` falls through and raises demanding decommissioned SharePoint creds (`backlog_client.py:281,351`). `.env.example` (a tracked **136-line** file — *correcting wave-1's "empty" claim*) is **57 vars short** of what the code reads (incl. `DATABASE_URL`=60 reads, and `YOUTUBE_API_KEY` which the README tells you to set). Hard non-pip prereqs undocumented: WARP SOCKS proxy (downloads), Redis (engagement), Node ≥20.19 (dashboard). `yt-dlp` is declared only in CriticalRush, not genlab-core which owns the download code. | document the PG env pair + prereqs; sync `.env.example` from the `os.getenv` set; `!.env.example` in gitignore; move yt-dlp dep _(landed: `18dd344`)_ |
| R-75 | HIGH | Done | **Engagement auto-reply is broken-and-off-brand when reachable.** `classify_reply_action` is fed the **inbound** comment's toxicity, not the generated reply's (`comment_processor.py:506,517`); the `auto` tier is near-unreachable because `_is_safe_reply` requires a 6-prefix/emoji-only <100-char string while the persona is told to be specific (`:43-59` vs `persona_engine.py:67`) → today it's effectively review-only; the reply prompt enforces **none** of the brand rules (sentence-case R-50, banned phrases R-51), and the **package `personas/gaming.yaml:13-16` still ships banned-phrase voice** ("INSANE", "absolutely godlike fr") as style examples. `confidence` is a 3-rule heuristic on input shape, unrelated to reply quality. The moment auto-posting is reachable, off-brand replies ship. | score the outbound reply for routing; add brand rules + a banned-phrase post-filter; sync/delete the stale gaming persona _(landed: `431a652`)_ |
| R-76 | HIGH | Done | **Engagement replies have no post context.** Every dispatcher hardcodes `"post_context": ""` (`run_engagement_poller.py:89`, `webhook.py:130`) and `generate_reply` adds the topic line only `if post_context:` (`persona_engine.py:91`) → the model never knows what the clip was about → generic/hallucinated replies to "who is that?"/"what game?", affecting even the review-queued replies humans see today. | populate `post_context` from the blueprint/Publishing_Analytics by `post_id` (the webhook already resolves niche from it) _(landed: `b387408`)_ |
| R-77 | MEDIUM | Done | **Engagement safety/decision gaps (bundle).** Rate-limiter starts **full** (burst = full hourly quota, `rate_limiter.py:23`→`token_bucket.py:46`) → a fresh worker can fire a whole hour's replies at once; unknown platforms fail-open. The **dashboard approve path re-posts an edited reply with no toxicity/brand re-check** + a missing `context_id` (`api/engagement.py:252-284`). Spam gate is URL+regex only and `topics_to_engage` is dead config → it earnestly replies to "first!"/emoji. Inbound toxicity checks only the `toxicity` dim at 0.7, ignoring threat/insult/severe (`toxicity_gate.py:55`). | empty-start the bucket + fail-closed; re-gate at approve time; relevance pre-filter; check all toxicity dims inbound _(landed: `78c4753`)_ |
| R-78 | LOW | Done | **`[automated reply]` suffix on every public reply.** Appended to both auto + review replies (`comment_processor.py:91-101,543,563`) — undermines the authentic-CM persona ("never mention you are AI") and isn't counted against the platform char budget. | product decision: drop it, or once-per-thread + budget for it _(landed: `d078f3a`)_ |
| R-79 | HIGH | Done | **`check_missing_media` can auto-archive scheduled posts, bypassing the guard (cleanup_safety.md violation).** Its VISUAL_READY selection has **no `scheduled_for IS NULL` filter** (`health_monitor.py:447`, unlike the sibling `archive_orphan_drafts` `:681`) and it archives via **raw `UPDATE … SET status='ARCHIVED'`** (`:513`) bypassing `ScheduleGuardedProxy`. Since `push_to_backlog` sets `scheduled_for` on every render, the normal VISUAL_READY row is scheduled → a transient mount/symlink miss past the mass-safety gate archives a scheduled post. | add `AND scheduled_for IS NULL` or route through the guarded client _(landed: `9de0e16`)_ |
| R-80 | HIGH | Done | **Demotion/schedule guard is blind to operational statuses (extends R-41).** `_is_demotion`/`assert_not_scheduled` use `STATUS_ORDER.index()` and return False on `ValueError` (`backlog_client.py:188,525`); `PUBLISHING`/`PUBLISH_FAILED`/`FAILED` are **absent from STATUS_ORDER** → the stuck-publishing recovery resetting a scheduled `PUBLISHED`→`VISUAL_READY`/`PUBLISH_FAILED` is never classed as a demotion → R-41's protection is doubly defeated. Half of STATUS_ORDER (VALIDATED/INTEL_READY/RESEARCHED/ANALYZED) is unreachable dead vocabulary — the documented ladder is fictional (real: create→DRAFTED\|VISUAL_READY→PUBLISHING→PUBLISHED/PUBLISH_FAILED→ARCHIVED). | use an explicit allowed-transition table — **NOT** a linear STATUS_ORDER tweak (error states aren't rankable: e.g. PUBLISH_FAILED→VISUAL_READY recovery would falsely read as a demotion). **Do together with R-41** (wiring the guard onto the Postgres path) in Phase 4; the guard is dormant until then, so this is latent (determined 2026-05-23) _(landed: `699cd5d`)_ |
| R-81 | LOW | Partly-corrected | **State-machine orphan accumulation (rejected sub-claim CORRECTED 2026-05-23).** The "rejected blueprints stay VISUAL_READY" claim is **WRONG** — `_execute_review_action` already sets `status="ARCHIVED"` on reject (`review_server.py:900`); SM-7 read the action-name map (`blueprints.py:518`), not the persistence. Remaining (low-urgency) gaps: DRAFTED-with-video orphans accumulate (`archive_orphan_drafts` only archives empty-`video_id`, `health_monitor.py:680`; the population grew slightly now that R-47 keeps validation-failed renders DRAFTED), INTAKE stories have no cleanup path, and `SCHEDULED` is a phantom status. | **DRAFTED-with-video half DONE** (`acd4a4f`): `archive_orphan_drafts` now runs a second UPDATE for `video_id IS NOT NULL` rows at the stricter 14-day age (twice the no-video case, reflecting that more pipeline effort was invested) with `auto_archived_failed_video` action stamp; both branches preserve `scheduled_for IS NULL` (pinned). **INTAKE-story cleanup DONE** (`bbc128a`): `archive_orphan_intake_stories` inspects the blueprint-reference graph (`update_story_status` is never called in live code, so status alone can't tell 'never used' from 'rotation done') — archives at >30d when every referencing blueprint is in the terminal-only set {`PUBLISHED`, `ARCHIVED`}; the cleanup_safety.md "scheduled posts sacred" rule applies transitively via the `VISUAL_READY` non-terminal protection; explicit regression pin asserts `VISUAL_READY`/`DRAFTED`/`PUBLISHING`/`PUBLISH_FAILED` MUST NOT appear in the terminal set. **Phantom-`SCHEDULED`-status prune still deferred** — needs an R-80-style explicit-transition-table redesign rather than a status-list edit _(landed: `acd4a4f` + `bbc128a`)_ |
| R-82 | HIGH | Done | **Publish gate enforces no window + tz ambiguity.** `gatekeeper._schedule_gate` allows publish iff `scheduled_for <= now` (`gatekeeper.py:81-101`) — **no "today/in-window" check** → a stale **past-day** blueprint stays eligible forever (feeds the double-publish triad). A naive `scheduled_for` is read as **UTC** by the gatekeeper but **IST** by `scheduling.is_due()` (`scheduling.py:12,31`, dead code but a +5:30 latent trap). Masked today (pipeline writes tz-aware UTC). | gate on `scheduled_for.date()==today_utc` + a window; single tz authority; reject naive datetimes _(landed: `d707eee`)_ |
| R-83 | MEDIUM | Done | **Schedule doc-drift + day-boundary/DST inconsistency.** DEPLOY.md's ai_creators row is **off by 10h** (says 18:00 IST; real `02:30 UTC`=08:00 IST) and **hides the publisher's 2nd daily run** (06:35 **and** 10:30 UTC); `schedule.yaml` windows + `publish_window_utc` are **dead config** (no readers); the cap day is UTC while YouTube quota resets Pacific (unreconciled); systemd is UTC-safe but the Mac plists use wall-clock + **6 hardcoded `+5:30` offsets**; the slot-collision query misses PUBLISHING/SCHEDULED/PUBLISH_FAILED (compounds R-22). **The live double-publish triad (2 publisher runs × 2/platform fail-open cap R-09 × idempotency-free retry R-21) is the single biggest timing risk**, firing every day the 10:30 timer runs. | collapse to one publisher run (or 2nd=retry-only); reconcile docs/timers; UTC end-to-end + ZoneInfo _(landed: `b7c65a7`)_ |

---

## 6. Findings log (append-only, newest first)


### 2026-06-11 — Session sweep #2: 13 R-IDs closed via 15-PR squash-merge stack

Second focused remediation arc on 2026-06-11 (the morning's monetization /
Meta-API / publish-safety sweep — entry below — was sprint #1). Hit every
remaining open R-ID that fit a single-PR shape; explicitly deferred the
multi-PR refactors.

**Full-close R-IDs (10):**
* R-02 (HIGH) — `deploy.sh` (rsync + md5 checksum verify + audit log,
  absolute-path/`..` reject), `rollback.sh` (git-history extract, hands off
  to deploy.sh's verify), and 7 essential-daemon unit files moved into
  `systemd-phase2/` (`dashboard`, `db-maintenance`, `metric-collector`,
  `token-refresh`). Deploy gate (CI-triggered CD) deferred. (`46c119f`)
* R-04 (MEDIUM) — new `integration` job runs the 40 hermetic smoke tests
  (2 deselected pending R-19); `test-core` now matrices Py 3.12 + 3.13;
  duplicate `lint` job removed from `test.yml`. (`ddf6e5a`)
* R-05 (MEDIUM) — `genlab-engagement-poller.service` added to
  `systemd-phase2/` with AGENT_ROOT + ENGAGEMENT_DISPATCH=dramatiq pinned
  by 3 regression tests; host-verify confirmed the poller IS currently
  active but absent from the deploy bundle. (`22423ec`)
* R-06 (MEDIUM) — tracked surface (README 5-platform count + DEPLOY.md
  schedule rebuilt from live `OnCalendar=` UTC values + grep one-liner
  for self-verify) + local CLAUDE.md/security.md fixes; 6 pins (3 hard,
  3 skip-in-CI). (`bb7512c`)
* R-07 (LOW) — VMAF skip paths split (INFO for infra/libvmaf-missing,
  ERROR for log-unreadable — the May 2026 silent-failure mode); `Stage`
  protocol + load-time isinstance gate in `_load_stages`;
  `dispatcher.py` verified already deleted; tiktok stub verified env-gated.
  (`fee58e9`)
* R-19 (MEDIUM) — `virality_score` + JSON-encoded `virality_features`
  now copied into the blueprint `fields` dict at the create branch in
  `push_to_backlog.py`. The 2 integration tests CI-deselected via R-04
  for the moment until the rebase happens. (`9873219`)
* R-27 (MEDIUM) — `CostAccumulator` installed in `pipeline_runner.run()`
  via `set_accumulator(...)` right after `set_current_context`; `RunReport`
  stamps `cost: {total_usd, by_category, budget_remaining_pct,
  entry_count}` into `run_report.json`; budget overrun appended to
  `slo_violations`; niche-level `error_budgets.daily_cost_usd` override.
  (`d703906`)
* R-40 (MEDIUM) — `recover_publish_failed` AND PushToBacklog revive both
  route their status flips through R-24's `claim_status(expected, new)`
  atomic primitive; a racing writer harmlessly loses the claim. Event-key
  dedup (different `video_id` for the same trending event) noted as
  deferred follow-up. (`a8c13c7`)
* R-58 (MEDIUM) — `_load_posts()` pre-tokenizes per cache TTL (one-shot
  amortization); `check_duplicate`/`find_similar` take `window_days=90`
  recency cap + `_MAX_DEDUP_POSTS=5000` hard ceiling + exact-hash
  short-circuit. Server-side SharePoint `$filter` push-down deferred.
  (`525da99`)
* R-62 (LOW) — `_KNOWN_VIDEO_CDN_HOST_SUFFIXES` allowlist on the catch-all
  `.mp4|.webm` regex; host extracted via `urlsplit().hostname` (not naive
  `endswith`) to defeat the userinfo-spoof bypass. (`6d559b0`)
* R-71 (MEDIUM) — contract-test net for `process_pending_task` cross-call
  idempotency: 5-invocation invariant proves `bandit_updater.call_count
  == 1`; no fire at 168h (48h = single source of bandit truth); no fire
  at 6h early-stop (the Bug F regression pin). R-08/R-22 "zero tests"
  claim was already stale by re-read time. (`bc7ca79`)

**Partly-corrected (3 — explicitly multi-PR by nature):**
* R-31 (HIGH) — sub-items **b** (Cuelinks Amazon-tag preservation in the
  example catalog — all 33 URLs fixed + pin) and **c** (matching threshold
  raised from `>0` to `>=2` hits, mirroring seasonal) shipped. Sub-item
  **a** (real EarnKaro / Impact / ShareASale / CJ / PA-API integrations)
  deferred as per-network multi-day work. (`c49a6b1` + `a7bd949`)
* R-39 (MEDIUM) — sub-items **a** (`too_long` autofix via
  `-t SPEC.max_duration`) and **b** (`no_audio_stream` autofix via
  `anullsrc` audio bed + `-map 1:v:0 -map 0:a -shortest`) shipped in one
  ffmpeg pass. Sub-item **c** (TTS cascade into composit-stage render)
  deferred as multi-stage refactor. (`2f77012`)
* R-70 (MEDIUM) — **part 1**: 826 LOC of gaming legacy strategies
  deleted; R-20's `506d67e` claimed they were gone but they weren't.
  **Part 2**: `Base{VisualRender,Scoring}Strategy` extraction across
  SR/CW/FD/_template is a multi-PR refactor and remains deferred.
  (`27dc509`)

**Merge order:** all 15 PRs landed in PR-number sequence (#128 → #142)
via `gh pr merge --squash --delete-branch`. One conflict between
#130 (R-07) and #135 (R-27) on `test_generic_pipeline_runner.py` — both
branches appended a new test class at the same module-bottom anchor with
no overlap; resolved with a strip-markers + run-both-pin verification.

**What's left truly open in the register after this sweep:**
* R-31 (a) — 4 per-network affiliate integrations + PA-API signing
* R-39 (c) — TTS cascade wired into the composit render path
* R-43 — Gemini model bump to GA (preview model deprecated; latent)
* R-45 — Verified-benign protocol annotation honesty (low-pri)
* R-70 (part 2) — `Base{VisualRender,Scoring}Strategy` extraction
* R-81 — DRAFTED-with-video orphan cleanup widening (low-pri)


### 2026-06-11 — Session sweep: 11 R-IDs closed across 4 clusters

Single-session focused remediation across monetization, Meta-API, and
publish-safety clusters. 12 PRs merged + deployed + verified on prod.

**Monetization (4 PRs, R-32/R-33/R-38 closed):**
* R-32 — click-proxy revenue aggregator (#107) + daily cron timer
  (#108). 80-day backfill landed ₹1,155.79 across 124 clicks.
* R-38 — dashboard repointed at live Postgres `monetisationprogress`
  (#109). AI Creators FB followers threshold finally visible: 10073/10000.
* R-33 — FB 24h post-survival check (#110/#111/#112/#113). Conservative
  classifier with sanity rate-guard after a 360-row false-positive
  incident during the prod backfill — the rate-guard fired correctly,
  rollback was a single SQL UPDATE.

**Meta API (2 PRs, R-44 closed):**
* Part 1 — centralised `META_GRAPH_API_VERSION` constant + bumped
  v21.0 → v22.0 across 12 files (#114).
* Part 2 — metric deprecation observability: static registry +
  consecutive-zero detector wired into 3 hot-path insight calls (#115).

**Publish safety (4 PRs, R-08/R-09/R-10/R-15 closed):**
* R-08 — removed the express-lane approval bypass (#116). The bypass
  was the most direct externally-escalatable hazard: a crafted RSS
  title could auto-publish to all 5 channels.
* R-15 — dropped `tiktok` from `platforms_enabled` in 4 niches +
  gaming `publishing.yaml` (#117). 6 invariant tests pin the rule.
* R-09 — daily caps 2→1 + fail-CLOSED on unconfigured platforms
  (#118). Bundled-config invariant test pins all caps == 1.
* R-10 — dropped 14:00 publish window from gaming/sports/movies
  `schedule.yaml` (#119). 15 invariant tests parametrized across all
  5 niches.

**Doc-reality reconciliation (this PR):**
Earlier v3 classifier missed R-11 and R-12 — commit `826aef3` (PR #16)
addressed both but kept the R-IDs only in body sub-bullets, not on the
fix-prefix subject line. Flipped to `Done` here.

**Net status:** 24 R-IDs remain Open.

**Tooling lesson:** the v3 reconciliation classifier was too narrow
(required R-ID on the fix-prefix line itself). A v4 should also walk
each commit's body for lines like `R-NN — <description>` (the
two-em-dash bullet convention used in `826aef3`) — they're load-bearing
signal that R-NN was the SCOPE, not just a cross-reference.

### 2026-06-09 — Risk-register reconciliation against git reality

Discovered that the doc and git had drifted significantly: of 81 R-IDs marked
`Open`, 38 were actually landed on `main` and 9 sat on
unmerged `fix/*` branches. Walked every commit since the audit started,
filtered to `fix/feat/refactor/perf/config`-prefixed subjects mentioning an
R-ID, and updated the Status column accordingly. Pointer column now carries
the landing SHA (Done rows) or branch name (Branch-ready rows) inline.

**Net deltas:** 38 `Open → Done`, 9 `Open → Branch-ready`.

**Branch-ready (waiting for merge):** R-21, R-22, R-46, R-47, R-59, R-65, R-66, R-78, R-79

**Still Open with no work found (next-tier triage candidates):** R-02
(deploy/rollback), R-04..R-07, R-08..R-13, R-15..R-17, R-19, R-20, R-27
(cost observability — partly addressed via U-03 landing), R-31..R-33
(monetization stack), R-36, R-38..R-40, R-43 (Gemini model ID), R-44
(Meta API v25 + retiring metrics), R-58, R-61, R-62, R-64, R-68, R-70..R-72.

**U-table (upgrade register, separate schema):** landed — U-03 (Anthropic
usage recording, the cross-link unblocking R-27), U-08 (sweep upgrade), U-18
(hygiene), U-19 (supply-chain). Branch-ready: U-24 (Python CVE batch). The
upgrade register's Effort column makes inline status notation awkward; check
git for U-XX commit messages when planning.

**Method note for future reconciliations:** running
`git log main --since=1y --pretty=%s | grep -E '^(fix|feat|refactor)\b.*R-NN'`
beats `Status: Open` cells whenever they conflict — the commits are
authoritative, the table cells are aspirational.

**Follow-up (same day):** v1 pass over-counted `Branch-ready`. Squash-merge
commit subjects use compact `R-NN/M/M` notation (e.g. PR #13's "fixes
(R-24/21/22/78)") and squash bodies list `* fix(...): ... (R-NN)` for each
bundled sub-fix; v1 only matched `\bR-NN\b` on the top-level subject and
missed bundled siblings. A v2 pass with body-walking + slash-list parsing
flipped 9 more `Branch-ready` → `Done`: R-21, R-22, R-46, R-47, R-59, R-65,
R-66, R-78, R-79 (all in `d078f3a` or `9de0e16`). Net `Branch-ready` count
is now **0** — every fix-branch that previously appeared waiting was
actually already squash-merged. **Final tally:** Open 34, Done 47,
Verified-benign 1, Partly-corrected 1.


### 2026-05-23 — Deep-dive wave 6: the operational surfaces (frontend, onboarding, replies, state, scheduling)
Five parallel agents on the user-facing/operational surfaces the engine-focused waves skipped.
Produced **R-72…R-83**, widened R-48 (4→7+ tables), and a correction to wave 1. Flagship claims
re-verified (.env.example exists, `check_missing_media` lacks the scheduled guard, the missing-table set).

- **Correction to wave 1 (security):** `.env.example` is **not** empty/absent — it's a tracked
  136-line file (force-added past `.gitignore`). The real problem is *incompleteness* (R-74).
- **Frontend (R-72/R-73):** the approval gate's UI is mostly trustworthy (error boundaries, correct
  socket cleanup, hierarchical query keys, FocusMode/PublishingQueue guard `isPending`) — but
  ContentReviewView's cards and FocusMode's keyboard path can **double-submit** (the client face of
  R-22), and there's **no frontend CI** at all (tsc/eslint/vitest unrun; 57 eslint errors), which is
  why ≥6 more orphaned `client.ts` methods (incl. an unwired tokenHealth UI) + a dead socket listener
  went unnoticed — extends R-67.
- **Onboarding (R-74):** a by-the-book fresh clone gets through `uv sync` + `alembic upgrade head`
  then **dies in `BacklogClient.__init__`** because the README never sets the Postgres env pair, and
  `.env.example` is 57 vars short. Hard prereqs (WARP/Redis/Node) are undocumented. The working config
  lives only in the maintainer's local `.env` — bus-factor-1 (R-71) made concrete.
- **Engagement replies (R-75/R-76/R-77/R-78):** the verdict is stark — **the system effectively does
  not auto-reply today** (the `auto` tier is near-unreachable), so safety holds only because a human is
  in the loop *for the wrong reason*. When auto is "fixed" it would post replies with **no idea what the
  post was about** (R-76), in a voice with **no brand-rule enforcement** + a stale banned-phrase gaming
  persona (R-75), routed on the **wrong (inbound) toxicity** + an input-shape "confidence" (R-75). Good
  positives: idempotency is solid (fcntl lock, mark-after-post), outbound toxicity fails *closed* and
  checks all dims, injection-checked upstream, and LLM-fail returns *no* reply (no generic fallback).
- **State machine (R-79/R-80/R-81 + R-24/R-29/R-33/R-40/R-47 confirmed):** the publish loop is
  genuinely crash-recoverable (in-process + cron stuck-publishing recovery; PUBLISH_FAILED self-heals at
  24h) — but the lifecycle is leaky at the edges: a **raw-SQL archive can hit scheduled posts** (R-79,
  a live cleanup_safety violation), the **demotion guard is vocabulary-incomplete** (R-80, defeats R-41),
  and three orphan classes accumulate with no exit (R-81). The documented status ladder is half-fiction.
- **Scheduling (R-82/R-83 + R-10 widened):** **four conflicting schedule representations** (systemd
  timers = the real one; `schedule.yaml`, `publishing.yaml`, DEPLOY.md, CLAUDE.md all disagree). The
  publish gate enforces no *window* (only "not future", R-82) and DEPLOY.md is off by 10h on ai_creators
  + hides the 2nd publisher run (R-83). **The single biggest live timing risk is the double-publish
  triad** — 2 publisher runs/day × 2-per-platform fail-open cap (R-09) × idempotency-free retry (R-21) —
  which fires *every day the 10:30 timer runs*, independent of any tz math. Positives: timers are
  `Persistent=true` (no missed-timer skips) and the scheduling-critical code uses `datetime.now(UTC)`
  (no naive-now); the tz drift bombs (R-82/R-83) are armed but dormant.

### 2026-05-23 — Deep-dive wave 5: measurement (coverage, churn, observability, coupling, cost)
Five parallel agents that **ran tooling and measured**, rather than reading code — the lenses prior
waves couldn't apply. Produced **R-64…R-71**, refined R-27, added cross-cutting truth #6. Four
flagship claims re-verified against the repo (import-linter crash, conftest 3.14-only guard,
run_report 0-stories→success, StageRunnerFactory no `metrics=`).

**A. Test coverage (ran `pytest --cov`).** Engine **56%** (20,510 stmts, 9,000 missed), 2294 pass /
0 fail once a **SIGSEGV** was neutralized — the suite crashes on **Python 3.13** (the CI version)
because `conftest.py:27` guards the Detoxify/libtorch+OpenMP segfault only on `>=3.14` (**R-64**).
The Postgres write-path runs **nowhere in CI** (storage+integration deselected; 50 `test_postgres_*`
skip without a DB) and `test_postgres_phase2:80` **enshrines the R-45 bare-string bug** (a correct fix
breaks the test). Lowest coverage on load-bearing code: `relevance_gate` 0% (R-12), `formula_sql` 0%
in CI (R-49), `video_validator` 13%, `health_monitor` 35%, `publish_all_platforms` 55%. **Risk×coverage
verdict:** nearly every high-severity risk (R-08/R-21/R-24/R-45/R-47/R-49/R-54) sits on
missing/absent-branch lines → *every one of those fixes lands in untested code* (write the test first).

**B. Git churn / fragility (ran `git log`).** 601 commits over ~10 weeks; **bus-factor 1** (595/601 one
author); **fix:feat = 245:158 (1.55:1)** — heavily remediated. Churn hotspots = the audit's risk
hotspots: `push_to_backlog.py` (36 commits, 25 fixes/69%), `publish_all_platforms.py` (25/19=76%),
`metric_collector.py` (26, most-active in the last 30d), `affiliate_matcher.py` (17/12=71% — "patched
but never converged" on the KPI). God-modules were **born large** in the Sprint 64/65 import (W12=247
commits), not grown — and are monotonic-accretion (`publish_all_platforms` +1549/−203). Cold-dark
corners (`express_lane`/`relevance_gate`/cost-trackers/`formula_sql`/`youtube_quota`) are low-churn
*because they're broken-and-ignored*, not stable — the most dangerous quadrant. → cross-cutting truth #6,
**R-71**.

**C. Observability (operator's-eye trace).** New: **R-65** (a fully dark channel reports
`status="success"`/`health:"healthy"` — verified), **R-66** (`metrics.jsonl` dead — `StageRunnerFactory`
gets no `metrics=`), **R-67** (detect→alert broken at every link: `pipeline_alerts` write-only,
`/alerts/system` orphaned, 24/25 thresholds unconsumed, oneshot units have no `Restart=`/`OnFailure=`,
no push sink anywhere), **R-68** (JSON logging off in prod; journald not run-id correlated). **The 3am
scenario:** a channel fetches 0 clips → cascades INFO logs → run_report `success` → publisher logs one
INFO and exits 0 → dashboard shows "healthy" → **operator sees nothing**, finds out days later from the
platform. Single highest-leverage fix: schedule `health_monitor` (a timer) + one `notify()` webhook —
activates ~14 already-written detectors at once. **Positives:** per-niche JSONL logs are run-id
correlated; flock is reboot-safe; daemons have `Restart=always`; health_monitor's *logic* is genuinely
good (it's just unscheduled + unread).

**D. Architecture / coupling.** **R-69** (import-linter contract crashes on a non-existent
`genlab_core.models` layer, not in CI, covers <20% of 33 packages → boundary enforcement is fictional;
+ real `genlab_core`→`execution.*` upward imports). **R-70** (no `BaseVisualRender`/`BaseScoring` base
classes → ~750L duplicated across SR/CW/FD, contradicting "~50 lines/strategy"; + 826L dead gaming
`_legacy` stages). **Positives:** no package-level cycles; clean `interfaces/` direction; the public API
is what channels actually import; one module-level SCC (`backlog_client↔storage.factory↔sharepoint`)
is broken by inline imports. Highest fan-in hubs: `strategies` (39), `niche_credentials` (31),
`backlog_client` (28).

**E. Cost model (estimated $/day from the code + web pricing).** **The headline correction to R-27:**
spend is **~$0.12–0.85/day, expected ~$0.29 — 6–40× UNDER the $5 budget.** The live write path is **one
LLM call per story** (hook+6 captions in one JSON response), not the feared 3-calls/hook loop (that's
short-circuited off the live path). Content writing is ~80% of spend; **BB's single Sonnet-4.6 channel
≈ the other 4 Haiku channels combined.** The OpenAI BULK/NANO + Gemini router tiers are **dead config**
($0); TTS≈$0 (free Edge/gTTS cascade unless ElevenLabs key set, and `audio_path` is unused anyway —
refines R-39). U-01 prompt caching saves ~25–30% of the LLM bill (~$0.06–0.09/day — tiny now, but scales
linearly with SaaS tenants). So R-27's *blindness* is real but its *urgency* is low → downgraded to MEDIUM.

### 2026-05-23 — Deep-dive wave 4: handoffs, content quality, performance, schema, SaaS, deep security
Six parallel agents took the dimensions the subsystem reviews structurally miss. Produced
**R-45…R-63** + the new **§9 SaaS gap analysis** + U-24/U-25. Three flagship findings were
re-verified against the repo before promotion (S-01, CD-1, S-07). Detail by stream:

**1. Stage-handoff contract integrity.** The pipeline's only mutation channel is
`context["stories"]`; `context["blueprints"]` is **vestigial** — no stage ever writes it. New seam
bugs: **CD-1/R-46** (X affiliate first-comment dropped by `twitter`≠`x_twitter`), **CD-2/R-47**
(render-validation result never enforced at persist), and **R-19 CONFIRMED** (`virality_score`
computed every run, never copied into the blueprint). Lower-sev (findings-log only): **CD-3/CD-4**
— the shared `GenerateAudio` (`generate_audio.py:114`) and gaming `commentary_audio_path` outputs
are *doubly dead* (audio never built — `_build_script` reads top-level `bp["hook"]` but content
lives in `story["content"]["hook"]` → empty script → skipped — and never muxed; both also run in
`post_render`, after the render that would consume them; extends R-39); **CD-5** affiliate
`utm_content` is empty (blueprint_id unset at `inject_cta` time, `cta_engine.py:164`); **CD-6**
`x_twitter.first_reply` dropped at push. **Verified-correct handoffs (positives):** `hook_style`
and `arm_id` round-trip fully (write→persist→publish→48h reward credit); the affiliate field copy;
ExpressLane urgency; the `content["hook"]` key is consistent across writing/hooks/QC/virality/push.
R-18's blast radius is **larger** than stated (dims 5/6/9/10/11 diverge, not 4) but same root cause.

**2. Content quality & prompt engineering.** Verdict: **the happy-path prompt is genuinely strong**
(anti-"X just Y" regex backstop, good/bad exemplars, per-niche voice + banned phrases + few-shot —
a real strength), but **enforcement leans on LLM goodwill, not deterministic post-checks** — exactly
backwards from "Determinism > Intelligence." New: **R-50** (sentence-case unenforced + BB demands
lowercase), **R-51** (fallback hooks lowercase + evade banned list), **R-52** (HookValidator dead on
LLM path, no ≤60 gate), **R-53** (CTA-not-last, no length floor / FB-question, no HTML-strip).
Lower-sev: **CQ-05** banned-phrase naive substring → false-positives reject good hooks ("this could
change the meta") *and* false-negatives pass bare "insane"; **CQ-09** dead duplicated
`content_prompts.yaml` in the 3 stub channels (contains banned phrases); **CQ-10** BB
`hook_formulas.yaml` is a banned-content minefield kept dormant only by wiring; **CQ-11** hook dedup
is correct but *late* (LLM spends tokens, then push discards collisions — cost waste). Strongest
niches: movies/anime/sports (clean sentence-case configs); weakest: **BlackboxBrief** (lowercase
self-contradiction on the flagship) and **gaming** (worst fallback hooks). **CQ-08 reconciled with
stream 6:** the engagement reply path *is* injection-checked upstream (`comment_processor.py:389`),
so persona_engine's lack of its own check is defense-in-depth (LOW), not an open hole.

**3. Performance / memory / scale.** New: **R-54/P-01** (no global render lock = the OOM mechanism
behind R-03), **R-55/P-05+P-06** (YouTube quota cross-process unsafe), **R-56/P-04** (transcode
arg-swap), **R-57/P-10** (quota daemon omits BB; 120GB on one box), **R-58/P-08** (Content_Memory
unbounded in-RAM). Lower-sev: **P-02** publish ThreadPool unbounded + co-resident-with-render
unguarded; **P-03** FFV1 master + concurrent x264 tee buffers; **P-07** VMAF temp-log path collision
+ uncapped threads; **P-09** DedupEngine dense n×n matrix (fine now, cliff at scale); **P-11**
`scan_runs` 3× tree-walk per 60s. **First thing that breaks at 10× channels:** RAM (no render lock),
then YouTube quota, then DB connections (~10 procs × pool-max-10 ≈ 100 vs default `max_connections`).
**Verified-OK (positives):** video downloads/uploads are *streamed* not buffered; quota_daemon two-pass
eviction protects published + scheduled runs (honours "sacred" rule); the *upload* quota ledger is a
real persistent Pacific-reset hard-stop (only its atomicity is the R-55 flaw); smart_crop reads frames
incrementally; publisher processes niches sequentially.

**4. Postgres schema / migrations / queries.** Headline = **R-45** (protocol contract violation —
verified). Plus **R-48** (4 tables unmigrated — verified), **R-49** (formula DSL parse failures →
CTA bandit + publish dedup silently broken), **R-63** (CHECK-less schema, dead `stories.score` index,
`.sql` outside Alembic with RLS-less tables, broken `ON CONFLICT`, dup indexes, unindexed universal
sort). **S-10** extends R-22 (advisory lock on wrong connection, salted-`hash()` key). Connection
pools are per-`BacklogClient`-instance (not global) at min2/max10 with no env sizing. **Verified-OK:**
`SET LOCAL app.niche_id` is correctly transaction-scoped where used; `content_pool` claim uses
`FOR UPDATE SKIP LOCKED` (the one race-safe primitive); batch_create pipeline mode is correct;
TIMESTAMPTZ + sensible UNIQUE business keys throughout.

**5. SaaS / multi-tenancy readiness → see new §9.** Core fact: **there is no tenant/billing/auth/
credential-vault primitive anywhere** (repo-wide grep for tenant/billing/stripe/encrypt/vault = 0);
`niche_id` does double duty as content-category AND tenant-key, but only the category half is real.
Every isolation control is fail-open / admin-bypass. The path is real (engine is genuinely
config-driven, RLS schema exists) but item 1 (data isolation) is a **security fix that must land
before any second org's data enters the DB**.

**6. Deep security + dependency scan.** Ran the scanners the prior pass didn't: **R-59** (17 py CVEs
incl. lxml XXE / pillow RCE / starlette; 5 npm dev-chain), **R-60** (webhook `media_id` formula
injection), **R-61** (timing-unsafe Basic-Auth), **R-62** (bounded yt-dlp SSRF). **Verified-clean
(positives, recorded so we don't re-chase):** no committed secrets (`.env*`/`token.json`/
`credentials.json` gitignored, none tracked); **no** unsafe deserialization or dynamic code-execution
primitives anywhere (verified: no unsafe-load, eval, exec, or shell-spawning subprocess); Meta rule
intact (no `graph.instagram.com`, no `ig_refresh_token`); the **primary feed-text→LLM path IS
injection-guarded** (`base_writing.py:129-174`) and so is the engagement comment path
(`comment_processor.py:389`); `geo_link_resolver` URLs are operator-config, not attacker-influenced
(the wave-1 concern is real but not live SSRF); `/links/*` public routes are solid (domain-allowlisted
redirects, parameterized SQL, escaped HTML); `serve_media` path-traversal is guarded; Flask has a 16MB
body cap + security headers.

### 2026-05-22 — Upgrade & version-currency sweep (5 parallel streams)
Method: 5 parallel agents researched independent upgrade dimensions (Python deps, frontend/Node,
runtime+CI tooling, external API versions, AI/ML models+prompt stack), web-verifying "latest" as
of today. Full opportunity table promoted to **§8 (U-01…U-23)**; active breakage/deadlines
promoted to **§5 as R-42…R-44**. Headlines:
- **Three active deprecations, not just stale pins:** X video upload still calls the v1.1
  `media/upload` retired 2025-06-09 (R-42, verified `x_twitter.py:109,275`); the Gemini
  `video_analysis` tier points at a preview ID deprecated 2025-07-15 (R-43, verified
  `router.py:72`/`model_routing.yaml:41`); Meta Graph is pinned `v21.0` (4 majors behind) **and**
  legacy FB/IG reach/impression metrics retire ~June 2026, which will silently zero the insight
  collectors that feed the reward loop (R-44).
- **Biggest cost win is missed feature adoption, not a version bump:** zero prompt caching
  anywhere in genlab-core (verified — no `cache_control`), no Batch API, and `usage` recorded at
  only 1 of ~6 Anthropic call sites — directly compounding R-27 (cost unmeasured). U-01/02/03.
- **Mostly current otherwise:** Claude model IDs are correct (Haiku 4.5 / Sonnet 4.6, not legacy);
  yt-dlp, psycopg3, pydantic, ruff (0.15.14, 1 day old), most deps at/near latest. The frontend's
  only real work is three coordinated majors (Vite 8 / TS 6 / ESLint 10), de-risked poorly by the
  no-vitest-CI gap (R-04).
- **One upgrade trap:** gunicorn 26 removes the eventlet worker the dashboard relies on
  (`review_server_wrapper.sh:48`) — pin `<26` until the worker is migrated to gthread (U-07).
- **Footprint:** detoxify→torch is the heaviest runtime cost on the 4GB box; a lighter Albert
  (`original-small`) or ONNX (`speedtoxify`) path exists (U-04, compounds R-03).
- **New doc-drift (→ R-06):** the "Prefect flows" claim is aspirational — `metric_collector.py`
  uses no-op stub `flow`/`task` decorators (L23-29) and Prefect is in no pyproject/uv.lock;
  `ffmpeg-python` is not a dependency (FFmpeg is subprocess-only); `cost_accumulator.py` lists
  stale model IDs (`claude-sonnet-4-5-20250514`, `claude-opus-4-6` — Opus is never used).

### 2026-05-22 — Deep-dive wave 3: render, publish, concurrency, cost, monetization
Five exhaustive verified investigations. Produced R-21…R-40. Detail (incl. items not in the
register and the positives):

**G. Render correctness.** → §2.10, R-25/R-26/R-39. Also: gaming has no effective quality gate
(VMAF off + a colorspace check that can't fail since compose always writes bt709); the FFV1
master/transcode tree is dead with an arg-order bug + a broken tee `-vf`; gaming compilation
audio `amix` overlaps commentary. **Positive:** bt709/yuv420p/AAC48k/1080×1920 genuinely
enforced on the primary path; VMAF re-encode is bounded.

**H. Publish reliability.** → R-21/R-24/R-29/R-30/R-33/R-35/R-36. Also: X media-upload 429 isn't
caught by the rate-limit guard → a text-only tweet may post (violates video-first). **Verified
correct:** same-run double-select is prevented (PidLock + VISUAL_READY-only selection); YT
quota gate fails gracefully (next-day retry); the EAA Meta token is never refreshed (security
compliant); X thread path refuses partial-retry.

**I. Concurrency / dedup integrity.** → R-21/R-22/R-24/R-34/R-40. Also: advisory-lock fail-open;
quota-daemon protected-path set fails open on DB error (brief evictable window). **Verified
correct & notable:** the per-niche `flock` is crash-safe (OS-released); the `candidate_id`
UNIQUE constraint (no `ON CONFLICT` → raises+skip) is the real duplicate backstop; and
`content_pool` claiming uses `SELECT … FOR UPDATE SKIP LOCKED` in one txn — **the one
genuinely race-safe dedup primitive in the system**. The `scheduled_for` "sacred" guard
(`ScheduleGuardedProxy` + `assert_not_scheduled`) is real and layered — **but** only blocks
demotion/clearing (sideways overwrites allowed) and **must be confirmed to wrap the Postgres
`blueprints` proxy** (→ §7); if it doesn't, the guard is bypassed on the primary path.

**J. Cost / quota.** → R-27/R-28/R-37. Also at exhaustion YT 403s spam until the breaker trips
then `return []` (feeds R-11). **Verified correct & positive:** the Sprint-64 search
minimization (RSS-first, `search.list` gated behind <3-candidates) keeps worst-case ~700u/day
≪ 10k; the "888-error retry storm" is **fixed** — rate-limit `Retry` is raised at step 4
*before* the Anthropic call at step 5, so retries never re-spend the LLM; `@resilient` +
circuit breaker + token bucket are all bounded. TTS ElevenLabs-first has no cost cap but the
TTS stage is unwired (and would `TypeError` if it ran). GPT-Image essentially unused.

**K. Monetization.** → §2.9, R-23/R-31/R-32/R-38. Also: monetization-aware reward shaping
(`reward_shaper.get_adjusted_weights`) is mathematically sound but inert (channels far from
thresholds). **Positive:** caption platform-rule compliance is the most production-ready part
of the layer; catalog link-health checks run daily (but only protect the catalog, not
already-published captions).

### 2026-05-22 — Deep-dive wave 2: learning correctness, test quality, dead code / doc drift

**D. Learning-loop correctness verification.** Folded into §2.3 + R-18. Headline: the bandit
is mathematically sound and single-firing, but the **LinUCB contextual layer is broken by a
store-vs-predict feature mismatch** (R-18) — it will mislead once it has data. Thompson
(today's dominant signal) is correct, just starved. Reward/persistence/update math all
VERIFIED-CORRECT. `MIN_OBS_FOR_LINUCB=50` is dead config (selection gate is 5).

**E. Test-quality audit.** Verdict: **MIXED** — real protection on hot paths, pockets of
theater, one rotted safety net.
- *Genuinely good:* publish dispatch (`test_publish_all_platforms.py` — real `run_publish`,
  behavioral asserts), FFmpeg/VMAF (`test_ffmpeg.py`, `test_video_quality_pipeline.py`),
  and **excellent** cross-niche guard regression tests anchored to the real 2026-05-18
  cluster-A leak (`test_cross_niche_guards.py`).
- *Critical-path coverage GAPS:* the **express-lane approval bypass (R-08) has zero tests**;
  `relevance_gate.py` fail-open (R-12) has zero tests; `validate_videos.py` no-master VMAF
  fail-open (R-07) untested; the dashboard collision-free slot assignment + advisory lock
  (`publishing_queue.py:~333`) untested. Note: `test_daily_cap.py::test_unknown_platform_fails_open`
  **codifies** the R-09 fail-open as intended behaviour.
- *Theater (isolated):* `test_instagram.py::test_publish_video_reel` asserts only attribute
  existence; `test_sandbox_runner.py` asserts only `isinstance(bool)`. Exception, not the rule.
- *Non-hermetic:* `CriticalRush/tests/test_opensandbox_smoke.py:~31` fires a real
  `requests.get` at import/skipif time (network round-trip even when skipped). Otherwise clean
  (no time/random equality asserts; ffmpeg refs are mocked argv assertions).
- *Integration tests (R-19):* always deselected and **2/10 currently FAIL** on a real
  `stories`-vs-`blueprints` contract drift in `ViralityScoring`; ~43s/test.
- **Positive:** zero bare blanket-except swallows; LinUCB seeded `default_rng(42)`; lru_cache
  discipline good in `test_ffmpeg.py`.

**F. Dead code / doc drift inventory.**
- *Dead (R-20):* `platforms/dispatcher.py`, gaming `write_gaming_content_legacy.py` (535) +
  `adapt_gaming_content_legacy.py` (291), `CriticalRush/tests/_legacy/*.py` (8 files, 1,097,
  never collected), `publishing/cdn_upload.py::LocalCDNUpload` (export-only).
- *Markers:* **zero FIXME/HACK/XXX in the whole codebase** (a real strength); only 8 TODOs,
  mostly feature-gated (PA-API/affiliate networks unconfigured) + a dashboard "stopgap JSON
  file store" for pending replies (`api/engagement.py:~152`) and a "Sprint 59 TODO" comment in
  `render_gaming_video.py:258`.
- *Confirmed NOT duplicates:* `ffmpeg.py` vs `ffmpeg_utils.py` (specs/binaries vs filter
  builders — both load-bearing); `platforms/threads.py` vs `publishing/threads_client.py`
  (intentional adapter).
- *Doc drift (consolidated → R-06), claim → reality:*
  - root CLAUDE.md "BB legacy clients in `execution/utils/`" → **gone** (only
    `config_loader/contracts/error_events/html_scraper/playwright_fetcher` remain;
    `youtube_connector.py` is under `execution/sources/`).
  - root CLAUDE.md "~10,300 lines" → **51,603**. "Learning/Engagement 100% complete" →
    misleading (classifier inert, bandit starved+miscalibrated, pollers undeployed).
  - genlab-core CLAUDE.md "`strategies.py` … 6 interfaces" → it's a **package**; `interfaces.py`
    has **7** ABCs. Lists phantom `platforms/postiz` (removed). Omits ~14 real top-level dirs
    (`auth/cost/intelligence/llm/monetization/monitoring/observability/rendering/scoring/
    storage/utils/video/…`).
  - CriticalRush CLAUDE.md "orchestrates all 5 niches / `NICHE_ROOTS`" → code maps only
    `{"gaming":…}`; real orchestrator is `genlab_core/pipeline/cli.py`. Cites dead legacy stage
    class names; uses `ai_news` (now alias-only).
  - dashboard CLAUDE.md names only 2 niches; `core/scheduler.py` is an undocumented 240-L no-op.
  - README/ARCHITECTURE "6 platforms (… TikTok)" → functionally **5** (TikTok stub);
    Layer-2 `<Channel>/strategies/` → actual dirs are `*_strategies/`.
  - `security.md` points to `genlab_core.utils.text_sanitizer` for injection checks → they live
    in `cache/text_sanitizer.py` (the `utils` one is Graph-API Unicode only) — a misdirecting
    pointer for a security rule.

### 2026-05-22 — Deep-dive wave 1: silent-failure, config consistency, security
Three exhaustive verified investigations. Produced R-08…R-17 above. Detail:

**A. Silent-failure / fail-open catalog** (every path that can yield zero/degraded output
without raising). The system-wide amplifier is R-01 (alerts undelivered), so even the
WARNING-level detections below are effectively silent in prod.
- *Empty-return-on-error:* `download_top_videos.py:~715` (0 downloads → INFO log → 0
  blueprints — the realized WARP/SABR zero-out, **most dangerous**); `trending_video_fetcher.py`
  empty-list return on circuit-open/zero-candidate (~436/805/358) → empty `stories` **escapes**
  `run_report`'s zero-blueprint SLO (it requires `stories>0`); `video_sourcer.py` BB-fallback
  search returns empty (~383/447/527/590).
- *Fail-open gates:* `validate_videos.py:~172` VMAF passes at DEBUG when no master; `:~86`
  missing rendered_path silently "skipped"; `video_gate.py:~167,173` ffprobe-fail → text-only
  clips pass (re-introduces the banned text render); `relevance_gate.py:~37–56` totally
  fail-open; `relevance_filter.py:~65–67` empty keywords → 1.0; `qc_gates.py` is
  **non-blocking** (failures only apply a −0.3 score penalty, never drop), dedup swallowed at
  DEBUG (~67).
- *Silent default substitution:* `engagement/webhook.py:~49` niche defaults to `ai_creators`;
  `youtube.py:~270` unknown niche → category "28". (Publish path is **safe**: `_validate_niche`
  raises on empty/unknown.)
- *Swallowed exceptions:* `base_writing.py:~385–399` LLM-fail → template fallback → per-story
  silent loss; all-fail → 0 blueprints at INFO/WARNING. **Good:** no bare blanket-except
  swallow anywhere in genlab-core or channels (verified).
- *SKIPPED records:* `publish_all_platforms.py:~930` correctly records SKIPPED + WARNs per
  CLAUDE (good); outbound Detoxify fails **closed** (good).

**B. Config consistency (5-channel diff).** PASS: video_gate/fallback (all `require`/`false`),
visuals (accents match CLAUDE table, logos resolve, 1080×1920), source allowlists present.
FAILS:
- **Daily cap (R-09):** real cap = `platform_caps.yaml` 2/platform (tiktok 1); only BB sets
  `daily_post_cap:1`; render-time cap unimplemented; `DailyCapEnforcer` fails open.
- **Publish windows (R-10):** gaming/sports/movies declare 06:30 **and** 14:00; publisher
  timer is 06:35/10:30 → YAML windows decorative.
- **Anime threshold (R-13):** 0.20 not 0.35. (Anime negative-keyword hard-rejects MMA/UFC/
  boxing/wrestling ARE present — good.)
- **TikTok (R-15):** listed enabled in gaming `publishing.yaml` + all 4 non-BB
  `platforms_enabled`; two competing enablement sources (`x` vs `x_twitter`).
- *Minor:* anime `composite min_score` 0.15 vs 0.25 elsewhere; `sentence_case` exists only in
  code/prompts, not any config (drift vs the documented decision); banned generic-phrase lists
  are hardcoded, not in YAML.

**C. Security audit.** CLEAN (no action): no committed secrets (`.env*` gitignored,
`.env.example` empty); **Meta rule fully compliant** (all `graph.facebook.com/v21.0`, zero
`graph.instagram.com`, zero `ig_refresh_token`); no shell-spawning subprocess calls, dynamic
code execution, or unsafe deserialization; YAML loaded safely; FFmpeg/yt-dlp use argv lists
(no shell injection); SQL parameterized with field/table allowlists; dashboard auth stronger
than documented (signed sessions, HMAC CSRF, 5/min login rate-limit). ISSUES: R-08
(express-lane bypass, **top security finding**), R-14 (webhook fail-open), R-16 (tags injection
gap), R-17 (committed GUIDs/IDs); RLS dormant confirmed (`postgres.py` hardcodes the empty
`app.niche_id` admin setting at ~443/546/580) — fine for Phase 1, blocking for SaaS. Minor:
Basic-Auth path uses direct string compare not constant-time (~302).

### 2026-05-22 — Comprehensive system audit (5 parallel subsystem reviews)
Method: 5 parallel agents read the repo checkout (engine, channels, learning+engagement,
dashboard+storage+credentials, ops). Conclusions seeded §1–§5 above. Highlights:
- Engine is genuinely production-grade (51.6K LOC, ~3.3K tests); docs **undersell LOC ~5×**
  (CLAUDE.md still says "~10,300 lines").
- **Doc drift identified (R-06):** (a) CriticalRush CLAUDE.md "orchestrates all niches" is
  stale — code maps only gaming; real orchestrator is `genlab_core/pipeline/cli.py`.
  (b) "BB legacy clients in `execution/utils/`" is stale — only `execution/sources/
  youtube_connector.py` remains. (c) `DEPLOY.md` schedule table contradicts the
  `systemd-phase2` timers.
- Two real reliability gaps surfaced: health monitor unscheduled + no delivery (R-01);
  engagement pollers undeployed (R-05).

### 2026-05-22 — CI repair + dependency sweep (context, not a finding)
Brought all CI green (CI + Test & Lint + CodeQL on `e2154ff`) and merged 11 dependency PRs.
Two issues worth remembering surfaced during this:
- A dependabot squash merge can land as a **no-op** if the conflict resolution commit doesn't
  actually contain the change (setup-uv #3 silently stayed at v5; re-applied via #11).
- `TestSelectBestNetwork` was **non-hermetic** — `select_best_network` calls
  `geo_link_resolver._is_url_healthy()` which fires a live HTTP GET, so the test passed/failed
  on network luck. Now mocked. (Other tests calling network-touching helpers may have the
  same latent issue — see §7.)

---

## 7. Open questions / next research

- **[verify on host]** Are the engagement pollers (YT/X/Threads) actually running? Or is prod
  FB/IG-webhook-only?
- **[verify on host]** Has any hook-classifier model been trained on Hetzner (`models/` is
  empty in the repo)? What are the real per-arm `n_obs` counts in the `bandit_arms` table?
- ~~Other non-hermetic tests?~~ **RESOLVED (2026-05-22):** largely clean — only
  `CriticalRush/tests/test_opensandbox_smoke.py` fires a real `requests.get` at skipif time;
  ffmpeg/network refs elsewhere are mocked. No time/random equality asserts.
- **Does `virality_score` actually reach the published blueprint?** (R-19) The integration
  test failure suggests `ViralityScoring` writes to `context["stories"]` but downstream may
  expect it on blueprints. Trace stories→blueprint conversion in `push_to_backlog` to confirm
  the score isn't dropped. **[verify]**
- ~~Does `ScheduleGuardedProxy` wrap the Postgres `blueprints` proxy?~~ **RESOLVED
  (2026-05-22): NO — and the method-level guard isn't called either → R-41.** Both layers of
  "scheduled posts are sacred" are off on the production path.
- Has the cross-host double-publish (R-24) or retry-pass double-publish (R-21) ever actually
  fired in `Publishing_Analytics` (two SUCCESS rows, same blueprint+platform+day)? **[verify on
  host]** — would confirm whether these are latent or realized.
- What is actual peak RAM during the publish window on the 4GB box? (informs R-03)
- ~~Is the express-lane approval bypass gaming-only?~~ **RESOLVED (2026-05-22):** wired into
  ALL 5 niches, urgency derived from regex on untrusted fetched text → R-08.
- Should ClutchWire get real sports-scoring intelligence, or remain a low-effort 5th channel?
- **Intent vs regression?** Is the cap of 2/platform (R-09) deliberate, or drift from the
  documented 1/day? Are the second publish windows at 14:00 (R-10) intentional? Is anime's
  0.20 relevance threshold (R-13) a deliberate loosening or a regression from 0.35?
- Is the express-lane *meant* to exist at all? If yes, what's the legitimate use case, and
  can it be gated by a human-set flag instead of text regex?
- Does any production code path read `niche.yaml → platforms_enabled` (which lists tiktok), or
  is the hardcoded publisher default the only enablement source? (R-15 blast radius)
- **[verify on host]** Are X video publishes currently failing on the retired v1.1 `media/upload`
  (R-42)? Check `Publishing_Analytics` for X FAILED/SKIPPED on video posts since ~2025-06.
- **[verify on host]** Is the Gemini `video_analysis` tier (R-43) ever actually invoked, or is it
  dead-but-never-called? Determines whether R-43 is active or merely latent.
- Which exact FB/IG insight fields do `metric_collector.py`/`fetch_insights.py` request, and which
  are in the ~June-2026 reach/impression retirement (R-44)? Audit the field lists before the date.
- **[verify on host] — the highest-priority unknown:** is `GENLAB_USE_POSTGRES=true` in prod, and
  are story/blueprint writes actually succeeding (R-45)? If yes, the protocol return-contract
  violation should be crashing `create_story`/`create_blueprint` — so either there's a mask we
  haven't found, or prod is silently still on the SharePoint backend (which would make "Postgres is
  primary" doc-drift). Resolve this first; it changes the severity of R-45/R-48/R-49.
- **[verify on host]** Are X posts actually missing the affiliate first-comment (R-46)? Check a
  recent X post's replies vs the blueprint's `twitter_first_comment`.
- Has a fresh `alembic upgrade head` ever been run cleanly, or does prod's DB only exist because of
  the hand-run `.sql` files (R-48/R-63)? Determines whether the 4 missing tables exist in prod.
- **[verify on host]** Real per-arm `n_obs` in `bandit_arms` + whether the CTA-bandit update has
  *ever* succeeded (R-49 says its query errors on Postgres) — would confirm the monetization learning
  loop is dead, not just starved.
- **[verify on host]** Does the CI engine job actually pass on 3.13, or is it silently segfaulting /
  green-because-the-live-test-is-skipped (R-64)? Check a recent Actions run's engine-test step.
- Is the deploy box's Python 3.13 or 3.14? (3.14 dodges the Detoxify segfault via the conftest guard
  but the engagement worker loads the model in prod regardless — does it segfault on the host?) (R-64)
- Given cost is ~$0.29/day (R-27), is wiring usage-recording worth it *now*, or defer until SaaS makes
  it linear? (Argues for prioritizing R-65/R-67 observability over R-27 cost instrumentation.)
- Would adding `BaseVisualRenderStrategy`/`BaseScoringStrategy` (R-70) be better done now or folded into
  the eventual SaaS config-as-data refactor (§9)? The ~750L dup is also where per-niche bugs diverge.
- **[verify on host]** Is the publisher's 10:30 UTC run actually publishing *second* blueprints (the
  double-publish triad, R-83), or is the daily cap holding? Check `publishing_analytics` for two
  SUCCESS rows same niche+day across the 06:35 and 10:30 windows.
- **[verify on host]** Are engagement auto-replies effectively never firing (R-75)? Check how many
  replies have `action=auto` vs `review` in the pending/engagement store — if ~0 auto, the automation
  is inert and everything is human-gated.
- Is the stale package `personas/gaming.yaml` (banned-phrase voice) actually loaded in prod, or does
  the per-channel `config/persona.yaml` always win (R-75)? Depends on the `_load_persona` fallback path.
- Should the publisher be collapsed to one run now (kills the R-83 triad cheaply), or does the 10:30
  run serve a real retry purpose that needs preserving as retry-only?

---

## 8. Upgrade register (append-only; U-NN stable IDs)

Version-currency + modernization sweep (5 parallel streams, 2026-05-22). Distinct from §5: these
are **opportunities / maintenance**, not active risks — except where a row cross-references a risk.
"Latest" web-verified as of 2026-05-22 (knowledge-cutoff-independent). Active *breakage/deadlines*
this sweep surfaced were promoted into §5 as **R-42** (X media), **R-43** (Gemini ID), **R-44**
(Meta Graph). Effort: S = trivial/hours, M = a day-ish, L = multi-day.

**Do-first (highest value/effort):** U-01 prompt caching, U-06 requests CVE, U-16 ruff pre-commit
drift, U-03 usage recording (unblocks R-27), U-07 gunicorn pin (a trap, not optional).

| ID | Class | Effort | Item | Pointer |
|---|---|---|---|---|
| U-01 | COST-WIN | M | **Adopt Anthropic prompt caching.** No `cache_control` anywhere in genlab-core (verified). Mark the shared system prompt + few-shot ephemeral → ~90% input-token discount + faster TTFT. Highest leverage on the high-volume reply path and the 3-calls-per-hook loop. Cross-ref R-27. | `engagement/persona_engine.py:99`, `writing/llm_hook_generator.py:386,522`, `llm/router.py:178`, `writing/llm_client.py:55` |
| U-02 | COST-WIN | M | **Anthropic Batch API for daily content/hooks** (50% off). 1 reel/channel/day rendered before the 06:30 UTC window = non-realtime → ideal batch workload. | content-writing + hook stages |
| U-03 | UPGRADE | S | **Record `response.usage` (incl. cache tokens) at every Anthropic call site** — only `llm_client.complete()` does today. Directly unblocks the R-27 "cost unmeasured" gap. | `router.py:178`, `persona_engine.py:99`, `llm_hook_generator.py:386,522` |
| U-04 | COST-WIN | S | **Detoxify `original` → `original-small`** (Albert backbone, same Jigsaw categories, lighter torch/RAM on the 4GB box — compounds R-03). `speedtoxify` (ONNX) is an even lighter spike. | `engagement/toxicity_gate.py:41` |
| U-05 | UPGRADE | S | OpenAI TTS `tts-1-hd` → `gpt-4o-mini-tts`; ElevenLabs legacy `client.generate()` → `text_to_speech.convert()` (survives a future SDK major). | `tts/providers.py:198,129` |
| U-06 | SECURITY | S | **`requests` → 2.34.2** (CVE-2026-25645). Constraint already permits; `uv lock --upgrade-package requests`. Low practical impact (no `extract_zipped_paths` use) but free hygiene. | root + per-pkg `pyproject.toml` |
| U-07 | TRAP | S | **Pin `gunicorn<26`** — v26 removes the eventlet worker the dashboard runs (`--worker-class eventlet`). Migrate to `gthread` (psycopg is sync → clean swap; also retires the discouraged eventlet dep) *before* any v26 move. | `dashboard/runbooks/review_server_wrapper.sh:48` |
| U-08 | SAFE-MINOR | S | One `uv lock --upgrade` sweep: openai 2.26→2.38, elevenlabs 2.38→2.45, anthropic 0.102→0.104, pydantic 2.12→2.13, redis 7.3→7.4, fastapi 0.135→0.136, sqlalchemy (transitive), google-api-python-client. **Avoid** sqlalchemy 2.1 / redis 8.0 (betas). | `pyproject.toml` + `uv.lock` |
| U-09 | OPERATIONAL | S (recurring) | **yt-dlp routine refresh.** On latest (2026.3.17) today, but it's the single most likely silent-break point for 4 channels' fetch — treat `uv lock --upgrade-package yt-dlp` as monthly maintenance, not one-time. | `CriticalRush` dep |
| U-10 | MAINT-RISK | M | **pytrends is archived** (last release Apr 2023, repo archived). Evaluate `pytrends-modern` / `trendspyg` before Google Trends next breaks the FrameDrift + intel path. | `FrameDrift` dep; `intel/google_trends.py` |
| U-11 | SAFE-MINOR | S | Frontend minor batch: react/react-dom 19.2.4→.6, tailwind + `@tailwindcss/vite` 4.2.1→4.3.0 (lockstep), framer-motion, react-router-dom, zod, immer, jsdom, lucide-react 0.575→1.0, react-query-devtools→5.100.x. No security advisories. | `dashboard/frontend/package.json` |
| U-12 | PREREQ | S | **Pin a Node baseline** (`engines.node` + `.nvmrc` @ Node 22 LTS). None today (no engines / .nvmrc / CI node). Prerequisite for U-13. | `dashboard/frontend` |
| U-13 | MAJOR | M | ESLint 9→10 (+ `@eslint/js`, `typescript-eslint`). Flat config already used → low migration; main work = JSX ref-tracking `no-unused-vars` change. Node floor `^20.19‖^22.13‖>=24`. | frontend devDeps |
| U-14 | MAJOR | M-L | Vite 7→8 (Rolldown) + `@vitejs/plugin-react` 5→6 (lockstep). Config renames (`rollupOptions`→`rolldownOptions`, auto-compat layer); main risk = React Compiler interaction. Validate via `npm run build`. | frontend |
| U-15 | MAJOR | L | TypeScript 5.9→6 (do **last**). Strict-by-default + `baseUrl`/`moduleResolution:classic` removals; `ts5to6` codemod + `ignoreDeprecations:"6.0"` escape hatch. Needs a build gate CI lacks (see U-18 + no-vitest-CI gap). | frontend `tsconfig*` |
| U-16 | TRIVIAL | S | **Bump pre-commit ruff hook `v0.11.12` → `0.15.14`** to match CI. The 2026 style guide landed in ruff 0.15.0 → local `pre-commit` and CI `ruff format --check` disagree today. Ruff itself already latest. | `.pre-commit-config.yaml` |
| U-17 | POLICY | S | **Python version policy.** Floor `requires-python>=3.12` but CI only runs 3.13; 3.14 is GA. Either add a 3.12+3.13(+3.14) matrix to prove the floor, or raise the floor to 3.13 + bump ruff `target-version` / mypy. | all `pyproject.toml`, CI |
| U-18 | HYGIENE | M | **Consolidate `ci.yml`+`test.yml`** (divergent uv pin 0.10.x vs unpinned, Python none vs 3.13, Postgres `genlab` vs `genlab_test`); add the missing frontend `npm run build` gate (Dockerfile already builds it). Reframes R-04 as an upgrade. | `.github/workflows/` |
| U-19 | SUPPLY-CHAIN | S | **Pin floating Docker tags:** `ghcr.io/astral-sh/uv:latest`, `gyoridavid/short-video-maker:latest-tiny`. | `dashboard/Dockerfile`; BB short-video-maker compose |
| U-20 | SAFE | S | uv 0.10.9→0.11.11; `astral-sh/setup-uv` v7→v8. | CI + workspace |
| U-21 | INFO | S | mypy configured (`[tool.mypy]`) but **never run in CI** + unpinned → type-checking unenforced. Add a CI step if desired. (overlaps R-04) | `.github/workflows/` |
| U-22 | LOW | S | Verify `actions/cache@v5` tag resolves (web shows v4 as latest documented major); else pin v4. | `ci.yml` |
| U-23 | OPTIONAL | M | Optional majors, both current pins still supported: Postgres 16→18; Node 22→24 (Active LTS). Defer unless doing a sweep. | compose + Dockerfile |
| U-24 | SECURITY | S | **Python CVE remediation batch (from `pip-audit`, wave 4 / R-59).** lxml 6.0.2→6.1.0 (XXE), starlette 0.52.1→1.0.1 (host injection), pillow 12.1.1→12.2.0 (RCE/DoS), urllib3→2.7.0, idna→3.15, cryptography→46.0.7, mako→1.3.12, pygments→2.20.0, requests→2.34.2 (subsumes U-06), pytest→9.0.3. torch 2.10.0 has no fix (local-only). Lead with the untrusted-input four (lxml/starlette/pillow/urllib3). | `uv lock --upgrade` + per-pkg pins |
| U-25 | SECURITY | S | **Frontend dev-chain vuln bumps (`npm audit`, R-59).** vite (dev-server arbitrary file read GHSA-p9ff-h696-f583 — dev-only, lands with the U-14 Vite major), postcss, ws, engine.io-client, brace-expansion. None reach the prod static bundle, but bump the lockfile. | `dashboard/frontend` lockfile |

---

## 9. SaaS / multi-tenancy readiness (wave 4, 2026-05-23)

The MISSION is Phase 1 → multi-tenant SaaS for external micro-influencers/brands. **Core structural
fact:** there is **no tenant abstraction anywhere** — a repo-wide grep for `tenant`/`billing`/
`subscription`/`stripe`/`encrypt`/`fernet`/`vault`/`budget`/`spend` in `genlab-core/src` returns
**zero hits**. `niche_id` is doing double duty as content-category AND tenant-key, but only the
category half is real; the isolation half is dormant scaffolding. The engine *is* genuinely
config-driven and the RLS schema exists, so this is a build-out, not a rewrite — but **every
isolation control is currently fail-open / admin-bypass**, and item 1 below is a security fix that
must land before any second org's data shares the database.

### Gap table

| Dimension | Today (file:line) | Required for external tenants | Effort | Blocking? |
|---|---|---|---|---|
| Tenant model | `niche_id` ∈ closed `frozenset` in ≥5 places (`publish_all_platforms.py:66`, `pipeline/cli.py:38`, prefix maps `pipeline_runner.py:366`, `push_to_backlog.py:131`, `niche_credentials.py:21`); no tenant table in 11 migrations | `tenants` table; `tenant_id` orthogonal to `niche_id` (a tenant may run *gaming* too); replace frozensets/maps with a DB lookup | L | **Yes** |
| Data isolation (read) | RLS bypassed: policy treats `app.niche_id IN ('','all')` as admin-sees-all; filtering done by interpolating `{niche_id}='x'` into a formula (`backlog_client.py:208-223`); `find` sets the GUC only from a kwarg defaulting to `""` | per-request `SET LOCAL app.niche_id=<tenant>` from auth context; drop the `''`/`'all'` escape (fail-closed); client formula filter becomes belt-and-suspenders | L | **Yes** |
| Data isolation (write) | `get/update/delete` hardcode admin `''` (`postgres.py:443,546,580`); `create/batch_create` set no GUC; **RLS policies have no `WITH CHECK`** → a tenant can write another tenant's `niche_id` | thread GUC from context in all 6 methods; add `WITH CHECK (niche_id=current_setting(...))` to all 12 policies | M | **Yes** |
| AuthN/AuthZ | single shared `REVIEW_AUTH_USER/PASS` (`review_server.py:246`); session = one bool, no user/role/tenant | users table + roles + per-tenant sessions; session `tenant_id` feeds the isolation GUC | L | **Yes** |
| Credentials per tenant | brand-prefixed env only (`niche_credentials.py:42-71`), hardcoded prefix map, no DB store, no encryption; adding a tenant = edit `.env` + redeploy | encrypted DB-backed per-tenant cred store + per-platform "connect your account" OAuth flows | XL | **Yes** |
| Config as data | configs are files on disk (`niche_loader.py:58-69`); N tenants = N folders committed + a deploy | DB/API-driven config; the YAML schema is a clean contract, just needs a non-FS backing store | L | **Yes** |
| Onboarding | `tools/create_niche` copies a template dir then lists 4 **manual** code/registry/env edits (`create_niche.py:124-128`) | self-serve signup→connect→configure→run, no engineer, no deploy | XL | **Yes** |
| Billing / metering | none (zero code) | plan tiers, usage metering, Stripe, enforcement | L | No (gates revenue) |
| Per-tenant quota | `DailyCapEnforcer` is content-cap not fair-use; one **global** `YOUTUBE_API_KEY` shared by all (`trending_video_fetcher.py:27,1099`) | per-tenant quota buckets / keys; per-tenant LLM+render cost ceilings (ties R-27/R-28) | M | No (one tenant degrades others) |

### Path to multi-tenant (dependency order)
1. **Fix data isolation first** (M) — the only item that's a *correctness/security bug today*: add `WITH CHECK` to all policies, drive `app.niche_id` from a request ContextVar in all 6 `PostgresBackend` methods, remove the `''`/`'all'` fail-open. Do this *before* a 2nd org's data enters the DB.
2. Tenant model (L) → 3. AuthN/AuthZ (L) → 4. Config-as-data (L) → 5. Per-tenant credentials + OAuth (XL) → 6. Self-serve onboarding (XL) → 7. Billing + per-tenant quota (L+M).

### SaaS-blocking isolation risks (latent today — single owner — but live the moment tenant #2 exists)
- **SR-A (Critical):** `get/update/delete` run in admin mode ignoring tenant (`postgres.py:443,546,580`) → tenant B can read/modify/delete tenant A's record by id.
- **SR-B (Critical):** RLS policies have only `USING`, no `WITH CHECK` → write-side cross-tenant isolation is structurally absent.
- **SR-C (High):** `create/batch_create` never set the niche GUC → created rows' tenancy depends entirely on the Python caller.
- **SR-D (High):** isolation enforced by SQL string interpolation (`backlog_client.py:208-223`); any read path that forgets the `niche_id` kwarg defaults to `""` → returns all tenants' rows (fail-open default).
- **SR-E (Medium):** shared global YouTube key = cross-tenant DoS (ties R-28).
- **SR-F (Medium):** single dashboard credential = the approval gate is tenant-blind.