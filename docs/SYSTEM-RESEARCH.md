# Gen Lab — System Research & Assessment

> **Living document.** This is the single source of truth for grounded findings about
> how the Gen Lab system is *actually* built (as opposed to how the docs describe it).
> It is meant to be appended to over time, not rewritten.

_Last updated: 2026-05-22_

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
| Storage (Postgres) | Primary, real |
| RLS / multi-tenancy | Dormant scaffolding |
| Render pipeline | Geometry/codec correct; **quality gate broken, logo guarantee violable** |
| Publish reliability | Works single-host serial; **double-publish & partial-state risks** (R-21/R-24/R-29) |
| Cost / quota control | Real YT-quota minimization; **spend entirely unmeasured** (R-27) |
| Monetization | Compliant & built, but **unmeasured & Amazon-only** (R-23/R-31/R-32) |
| Ops: deploy / CI / monitoring | Weakest link |

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
   any? Both are currently unanswerable from the system itself.

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

| ID | Severity | Status | Risk | Pointer / fix |
|---|---|---|---|---|
| R-01 | CRITICAL | Open | Health monitoring effectively off — best detection code unscheduled, alerts written to an unread table, no push delivery. Nobody paged on a dark channel. | Add `genlab-health-monitor.timer` (hourly) + wire stored `slack_webhook_url` to actually POST. `monitoring/health_monitor.py` |
| R-02 | HIGH | Open | Manual scp deploy, no rollback, host `.git` stale; green CI doesn't gate/trigger deploy. Caused a cross-brand mispublish. | `deploy/DEPLOY.md`; consider rsync+checksum verify or minimal CD |
| R-03 | HIGH | Open | 4GB RAM running ffmpeg + yt-dlp + Postgres + Dramatiq + Detoxify/PyTorch; memory pressure watched only by unscheduled `check_swap`. OOM in publish window silently kills a day's reels. | schedule swap/mem check; consider larger box or staggering |
| R-04 | MEDIUM | Open | CI doesn't gate what ships: FE untested, single Python version, integration+storage deselected, two divergent workflows. | add vitest job; consolidate workflows; deploy gate |
| R-05 | MEDIUM | Open | Engagement pollers (YT/X/Threads) not in `systemd-phase2` → effectively FB/IG-only. **[verify on host]** | move poller unit into phase2 if intended |
| R-06 | MEDIUM | Open | Documentation drift as triage hazard (see §6). | reconcile CLAUDE.md / DEPLOY.md |
| R-07 | LOW | Open | VMAF gate fail-open with no master; tiktok stub; dead `dispatcher.py`; duck-typed stage protocol. | tighten gate; delete dead code |
| R-08 | HIGH | Open | **Express-lane approval bypass is externally escalatable.** `ExpressLane` (wired into ALL 5 niches) classifies urgency by regex on fetched `title`+`summary` (`express_lane.py:~160–224`); CRITICAL/HIGH skips the human approval gate (`gatekeeper.py:~52–67`). A crafted YouTube/RSS title ("breaking", "$1B", "launches") can auto-publish to all channels with zero review — breaks the core "approval is the real gate" invariant. | gate express-lane behind an allowlist or require approval regardless of urgency; don't derive urgency from untrusted text |
| R-09 | HIGH | Open | **The "1 reel/channel/day" cap is not enforced as documented.** Real cap is `platform_caps.yaml` = **2/platform/day** (tiktok 1); only BB declares `daily_post_cap:1`. CLAUDE's render-time cap is **unimplemented** (zero render-stage enforcement); `DailyCapEnforcer.can_publish` **fails open** on unconfigured platforms (`daily_cap.py:~88–92`). | set caps to 1; implement render-time cap; fail closed |
| R-10 | HIGH | Open | gaming/sports/movies `schedule.yaml` each declare **two publish windows (06:30 + 14:00 UTC)**, contradicting the 1/day rule and the single window; combined with R-09 this is the most direct duplicate-publish hazard. Publisher timer (06:35/10:30) matches no YAML window — schedule YAML is decorative. | reconcile to one window; make timer the single source |
| R-11 | HIGH | Open | **Silent zero-output cascade.** 0 yt-dlp downloads logs at **INFO** and produces 0 blueprints (`download_top_videos.py:~715`); empty fetch (`trending_video_fetcher` `return []` on circuit-open, ~436/805) **escapes** the zero-blueprint SLO in `run_report` (which only fires when `len(stories)>0`). A channel goes dark with no error. | raise zero-download/zero-fetch to ERROR; trigger SLO on empty stories |
| R-12 | HIGH | Open | `relevance_gate.py:~37–56` is **totally fail-open** (missing niche_root / sources.yaml / empty content_filter / empty positive_keywords → all stories pass) and `relevance_filter.py:~65–67` returns score 1.0 on empty keywords. QCGates does not backstop relevance → off-niche/cross-contaminated reels ship silently. | fail closed on missing relevance config |
| R-13 | MEDIUM/HIGH | Open | **Anime relevance_threshold is 0.20, not the documented 0.35** (`FrameDrift/sources.yaml:~323`) — anime is currently the *loosest* filter (tied with gaming), the opposite of the "anime strictest" design. Raises off-brand publish risk. | restore 0.35 |
| R-14 | MEDIUM | Open | **Meta webhook signature verification is fail-open**: gated on `if _APP_SECRET:` (`webhook_receiver.py:~60`, `engagement/webhook.py:~84`). If `META_APP_SECRET` is unset, both POST handlers accept unsigned events → attacker can inject fake comment events, triggering LLM replies / Anthropic spend. | reject POST when `_APP_SECRET` is empty |
| R-15 | MEDIUM | Open | TikTok is listed enabled in gaming `publishing.yaml` (`platforms.enabled`) and in all 4 non-BB `niche.yaml → platforms_enabled`, contradicting the TikTok-disabled rule. Only the publisher CLI's hardcoded default platform list (no tiktok) prevents a live mis-publish; a `--platforms` override breaches it. Two competing enablement sources (`niche.yaml` vs `publishing.yaml`, field `x` vs `x_twitter`). | single source of truth; remove tiktok from configs |
| R-16 | LOW | Open | `tags` reach the LLM without `check_for_injection` (`base_writing.py:~145–149` omits tags from the loop; tags interpolated at `video_content_writer.py:~329`). Output filter is a backstop; tags capped 60 chars. | add tags to the injection-check loop |
| R-17 | LOW | Open | Committed SharePoint List GUIDs (`ClutchWire`/`CriticalRush` `config/lists_config.yaml`) and Meta App/Page/IG IDs in `docs/` break the CLAUDE placeholder convention. Not secrets (SharePoint is legacy), but unnecessary disclosure. | placeholder-ize or note as intentional |
| R-18 | HIGH | Open | **LinUCB trains and predicts on different feature vectors.** `build_content_context` gets the blueprint dict at store time (dims 5/6/9/11 → 0/0.5) but the story dict at predict time (real values) — 4 of 12 dims out-of-distribution. Once `n_obs≥5` accumulates, the contextual bandit nudges *wrongly*. Currently masked by data-starvation. | build context from one canonical dict at both sites (persist the full 12-vector at push time, as the updater already trusts `linucb_context`) |
| R-19 | MEDIUM | Open | **Never-run integration tests are bit-rotted AND may reveal a real bug.** CI always deselects them (`addopts="-m 'not integration'"`, fires even on direct invocation). Force-run: 2/10 fail — `ViralityScoring.execute()` reads/writes `context["stories"]` but the test asserts `result["blueprints"]` carries `virality_score`. Either the test is stale or the score never reaches the blueprint. Also pathologically slow (~43s/test). | verify whether virality_score actually reaches published blueprints; fix or delete the integration tests; run them somewhere |
| R-20 | LOW | Open | Dead-code cluster (~1,960 L, zero live refs): `platforms/dispatcher.py` (+its test), gaming `write_gaming_content_legacy.py` (535) + `adapt_gaming_content_legacy.py` (291), `CriticalRush/tests/_legacy/*.py` (8 files, 1,097, never collected — wrong filename prefix). | delete |
| R-21 | CRITICAL | Open | **Double-publish via the retry pass.** `run_publish` re-publishes any `FAILED` platform on the last 50 PUBLISHED blueprints (`publish_all_platforms.py:~1083–1256`); the publisher fires at **06:35 AND 10:30** (`genlab-publisher.timer`). A TRANSIENT failure that actually landed (lost response after the post succeeded — `error_classifier.py:~72`) is retried with **no platform-side idempotency** → the same reel posts twice to a live channel. | conditional retry only after a "did this post land" check; add idempotency keys |
| R-22 | CRITICAL | Open | **Dashboard slot-assignment lock is non-functional.** `_advisory_lock` opens its *own* connection and takes a txn-scoped `pg_advisory_xact_lock`, but the read-modify-write runs on a *different* (Graph-sync) connection (`publishing_queue.py:~46–75,366–378`); the lock key is `hash(niche_id)` (per-process randomized → workers never contend); and it fails open. Two approvals can assign the same slot → breaks 1/day. Zero tests. | take the lock on the writing connection, stable digest key, fail closed |
| R-23 | CRITICAL | Open | **In-feed affiliate links bypass click tracking → monetization is unmeasured.** Published captions/comments carry raw Amazon UTM URLs, not the `/links/go/<slug>` redirect (`cta_engine.py:~236`, `publish_all_platforms.py:~347`); the only click logger is the passive link-in-bio page. So `affiliate_clicks` is empty, `_update_cta_bandit_from_clicks` trains **every CTA variant as a failure** (`metric_collector.py:~944`), and the reward affiliate bonus never fires (`reward_shaper.py:~294`). | route published links through `/links/go/<slug>?bp=<id>` |
| R-24 | HIGH | Open | **Cross-process/host double-publish.** Publisher claims its blueprint with a bare `UPDATE status='PUBLISHING'` — **no `WHERE status='VISUAL_READY'` guard, no row lock** (`publish_all_platforms.py:~760–835`); locks live in `/tmp` (host-local). Two publishers (cross-host, or a stray Mac launchd) both select the top blueprint and both publish. Split-brain is detected (`health_monitor.py:~1054`) but never *prevented*. | conditional claim `UPDATE … WHERE status='VISUAL_READY' RETURNING id`; DB advisory lock keyed on niche_id |
| R-25 | HIGH | Open | **VMAF gate validates against the wrong reference.** `master_path` is set to the raw downloaded clip (`video_gate.py:~194`), so `check_vmaf` diffs a branded 1080×1920 reel against an unbranded 1920×1080 source (`validate_videos.py:~171`) → score is meaningless, triggers a wasted CRF-12 re-encode. Gaming disables VMAF entirely and self-compares (`render_gaming_video.py:~379`). The "VMAF≥85" invariant is unenforced platform-wide. | produce a true 1080×1920 lossless master to diff against, or drop the gate |
| R-26 | HIGH | Open | **Logo-overlay invariant is violable & unverified.** Portrait (9:16) sources render with **zero branding by design** (`frame_compositor.py:~545`); a missing logo file silently degrades to text-only branding on landscape/square (`:~478`) yet still returns success → VISUAL_READY. No post-render check that a logo pixel exists. | verify logo composited post-render; add logo to portrait path |
| R-27 | HIGH | Open | **<$5/day cost rule is unobservable.** Three cost trackers exist and are **all dead** (`cost_accumulator`/`cost_tracker` never wired; the writer records no `usage`); dashboard cost fields are never populated; model-router budget downgrades never fire (`budget_ratio` never computed from spend). The system is structurally blind to cost. | record `response.usage` into one tracker; write `estimated_cost_usd` to run_report |
| R-28 | HIGH | Open | **No global YouTube quota ceiling.** Quota tracking is a per-process, log-only dict reset each run (`trending_video_fetcher.py:~70,1106`); the 5 channels run as 5 processes, so "max 50 searches/day across channels" is unenforced. Today usage is low by construction (RSS-first, search-gated ~700u/day — a real strength), but a config flip or RSS outage forcing keyword-search could silently blow 10k. | shared persistent daily counter |
| R-29 | HIGH | Open | **Orphaned-thread / partial-publish states.** IG container poll defaults to 480s + a 30s retry → ~990s, exceeding the 600s `future.result(timeout=600)` which does NOT cancel the thread (`instagram.py:~32,370`, `publish_all_platforms.py:~868`) → the orphaned thread can post *after* the publisher recorded FAILED. Crash recovery sets the whole blueprint PUBLISHED if any platform published (masks partial) or resets to VISUAL_READY (re-publishes succeeded ones) (`:~678–730`). | bound IG poll under the executor timeout; per-platform recovery |
| R-30 | HIGH | Open | **YouTube cross-channel guard is dead code.** `verify_channel()` exists and `expected_channel_id` is resolved + passed to the constructor, but `publish()` never calls it (`youtube.py:~194–233,239`). The one code-level guard against publishing to the wrong YT channel — after the real 2026-05-18 cross-brand incident — is unwired. IG/FB/Threads have none. | call `verify_channel()` before upload |
| R-31 | HIGH | Open | **Affiliate networks are mostly stubs.** EarnKaro/Impact/ShareASale/CJ raise `NotImplementedError` (`network_registry.py:~102–138`); PA-API signing is a stub with empty keys → dynamic matching gated off; Cuelinks wrapping strips the Amazon tag → 0 commission. Only **direct Amazon Associates** links work today. Matching is weak (1-keyword hit + evergreen fallback). | tighten matching; add a real network beyond Amazon |
| R-32 | HIGH | Open | **No working revenue readout.** The only revenue ingestion is a Playwright dashboard-scraper (`scripts/scrape_affiliate_revenue.py`) and Playwright isn't installed; `record_revenue()` is otherwise never called → `affiliate_revenue` is empty. Even with R-23 fixed, earnings are unmeasured. | use Amazon report API or fix the scraper; make click-through the proxy KPI |
| R-33 | MEDIUM | Open | **FB 24h survival check (`REMOVED_BY_META`) is not implemented** — zero occurrences in `genlab-core/src`; the sprint-47 "DELETED status + alert" claim is stale doc-drift. Meta-removed reels are never detected. | implement or strike the claim |
| R-34 | MEDIUM | Open | Perceptual `HashStore` is a non-atomic whole-file JSON write with no lock (`video_hasher.py:~136`); concurrent writes / crash mid-dump corrupt it → `load()` throws → perceptual dedup silently disabled (duplicates re-ship). XOR-combining frame hashes is also lossy (false negatives). | atomic write+rename; lock; per-frame hash list |
| R-35 | MEDIUM | Open | No proactive token-health on the publish path (`check_token_health` exists, never called by `run_publish` or any phase-2 timer); Threads refresh fires only lazily inside `publish()` → a channel dark >10 days lets the 60-day token expire unrefreshed. Expiry surfaces only as a per-platform SKIP (compounds R-01). | add a token-health timer |
| R-36 | MEDIUM | Open | Partial-failure status is misleadingly binary: 1/5 success → `PUBLISHED` (`publish_all_platforms.py:~948`); the `publish_partial` dashboard event fires only on *terminal* failures. A channel looks healthy while 4 platforms silently didn't post. | surface per-platform partial state |
| R-37 | MEDIUM | Open | `TrendingVideoFetcher` (the hot path for 4 channels) **bypasses the disk cache** — re-queries YouTube every run (cross-run caching absent), contra optimization.md. Only BB's fetch and Google Trends use a cache. | wrap fetches in the 6h disk cache |
| R-38 | MEDIUM | Open | Monetization progress is split across two stores: SharePoint `GenLab_MonetisationProgress` (writer has **no phase-2 timer** → likely unscheduled) which the dashboard *reads*, vs Postgres `monetisationprogress` (live via `genlab-audience-collector.timer`). Dashboard may show zeros while Postgres has data. | unify on Postgres; point dashboard at it |
| R-39 | MEDIUM | Open | Render-time spec gaps: no max-duration trim (>60s reels produced then silently dropped downstream as `too_long`); silent/no-audio sources render then get dropped (`no_audio_stream`); the TTS cascade is **never wired into the render path** (the documented ElevenLabs→gTTS audio guarantee is doc-only). | trim at render; wire TTS or document video-first source-audio policy |
| R-40 | MEDIUM | Open | `PUBLISH_FAILED` revive (publisher, `:~732`) can race a fresh `PushToBacklog` re-create for the same event → two live blueprints; the revive strips `candidate_id` (`:~1196`) so the UNIQUE constraint can't catch it. Also: the `candidate_id` UNIQUE constraint is the real dup backstop, but only for *identical* candidate_id — a different video_id for the same trending event slips through the (lock-free) video_id/Jaccard pre-checks. | dedupe on event, not just candidate_id |
| R-41 | HIGH | Open | **"Scheduled posts are sacred" is UNENFORCED in production (verified 2026-05-22).** BOTH guard layers are off on the Postgres-primary path: (1) `ScheduleGuardedProxy` is never applied — the Postgres branch sets `self.blueprints = PostgresTableProxy(...)` raw (`backlog_client.py:330`) and `return`s (`:338`) before the wrap at `:392` (legacy SharePoint only); (2) the method-level guard `update_blueprint_status`→`assert_not_scheduled` (`:717,531`) is called by **no production code** — `publish_all_platforms.py` (`:708/715/722/831/900/1209`) and `push_to_backlog.py` (`:1199`) write status via `blueprints.update(...)` directly. So a re-run/publisher can demote or overwrite a `scheduled_for` blueprint — a non-negotiable `cleanup_safety.md` rule. Regressed silently when Postgres became primary (Sprint 65). | wrap the Postgres `blueprints` proxy in `ScheduleGuardedProxy`; route status writes through `update_blueprint_status` |

---

## 6. Findings log (append-only, newest first)

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
