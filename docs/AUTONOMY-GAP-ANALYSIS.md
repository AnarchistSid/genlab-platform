# Autonomy Gap Analysis — 2026-06-12

**North star** ([[project-autonomous-agent-vision]]): an AI agent autonomously running 5 social channels at scale, getting smarter over time, requiring no manual approval.

This document is the **exhaustive current-state snapshot** mapped against that vision, with file:line citations and prod-DB measurements taken 2026-06-12 22:00 IST. Refresh by re-running the queries in the appendix.

---

## Executive summary

| Dimension | Built | Active | Empirical health | Distance to autonomy |
| --- | --- | --- | --- | --- |
| **Reliability** | 17 systemd timers + 5 always-on services + health_monitor + crash_recovery | Yes | 90 critical `download_failure` alerts in 30d, **only 15 auto-fix successes**; warp_down 12 events, 0 auto-fix successes | ⚠️ Brittle — agent can't run unattended >24h |
| **Visibility** | 17 dashboard views + 27 API endpoints + structlog JSON + pipeline_alerts + CriticalAlertsBanner (PR #174) + error_message (PR #173) | Yes | 0 off-dashboard notification sinks (no Slack/SMTP/webhook). dashboard_events records only system events, NOT operator approve/reject | ⚠️ Operator must be looking at the dashboard |
| **Quality** | content_filter per niche + NicheClassifier + content_pool + FrameCompositor + VMAF gate + banned-hook patterns | Partial | **All 5 niches have ZERO negative_keywords**. Portrait layout draws logo but NOT name/handle (`frame_compositor.py:563-595`). 23% of blueprints flow through content_pool; 77% via direct fetch | 🔴 Cross-niche contamination 3.8% egregious + brand inconsistency |
| **Learning** | 5 loops built: content-type bandit, style bandit, LinUCB, hook classifier, reward shaper. Hook trainer timer weekly Sunday 10:30. | Partial | Untouched arms (α=β=1): ai_creators 3/9, anime 5/9, gaming 2/9, **movies 6/9, sports 5/9**. Classifier trained 2026-05-20 on n=53-68 examples per niche (tiny). config_updates table empty — write-back not deployed | 🔴 Most arms inert; classifier under-trained; agent isn't getting smarter |
| **Trust** | Approval gate (`gatekeeper.py:72`): pure `action_taken=='approved'` binary check. SKIP_APPROVAL_GATE removed Sprint 62 per R-08 | n/a — by design | 223 approvals + 173 rejections in 60d = ~6.5 operator decisions/day. Zero references to `confidence_threshold` / `auto_publish` / `confidence_score` in code | 🚨 No path defined to lift the gate |

**Single-sentence summary**: the architecture for autonomy is mostly built but mostly inert. The agent has the infrastructure (timers, learning loops, classifiers, content_pool, dashboard) but **the loops aren't training, the cross-niche routing is bypassed 77% of the time, brand standards aren't enforced on portrait layout, and there's no code path that lifts the approval gate**.

---

## Dimension 1 — Reliability

**Goal**: pipeline runs unattended, day after day, without operator intervention.

### What's built

**23 systemd timers** on the prod Hetzner box (`46.224.237.56`):

| Timer | Schedule | Service |
| --- | --- | --- |
| `genlab-shared-ingestion.timer` | Daily 10:30 IST | Cross-niche source fetch + classify + route to content_pool |
| `genlab-pipeline-ai.timer` | Daily 08:00 IST | ai_creators pipeline |
| `genlab-pipeline-gaming.timer` | Daily 09:30 IST | gaming pipeline |
| `genlab-pipeline-anime.timer` | Daily 11:30 IST | anime pipeline |
| `genlab-pipeline-movies.timer` | Daily 13:30 IST | movies pipeline |
| `genlab-pipeline-sports.timer` | Daily 15:30 IST | sports pipeline |
| `genlab-publisher.timer` | Daily 12:05 IST | publish_all_platforms loop |
| `genlab-insights-collector.timer` | Daily 12:15 IST | Fetch 6h/24h/48h/168h analytics |
| `genlab-health-monitor.timer` | Every ~30min | `health_monitor.py` writes alerts to pipeline_alerts |
| `genlab-yt-session-warm.timer` | Every ~90min | Refresh YouTube cookies/session |
| `genlab-spike-detector.timer` | Every ~5min | Viral content detection |
| `genlab-cleanup.timer` | Daily 06:30 IST | Disk + run-artifact cleanup |
| `genlab-db-maintenance.timer` | Daily 14:15 IST | Postgres VACUUM/REINDEX |
| `genlab-pg-backup.timer` | Daily 06:30 IST | Postgres backup |
| `genlab-token-refresh.timer` | Daily 07:30 IST | Refresh platform OAuth tokens |
| `genlab-hook-trainer.timer` | Weekly Sun 10:30 IST | Train hook classifier |
| `genlab-config-updater.timer` | Weekly Mon | Write back bandit → YAML |
| `genlab-affiliate-link-check.timer` | Daily 09:15 IST | Verify affiliate URL liveness |
| `genlab-affiliate-scraper.timer` | Daily 17:30 IST | Update affiliate catalogue |
| `genlab-proxy-revenue.timer` | Daily 09:30 IST | Pull affiliate revenue stats |
| `genlab-feedback-collector.timer` | Daily 19:00 IST | Pending feedback rollup |
| `genlab-audience-collector.timer` | Daily 20:00 IST | Follower-count snapshots |
| `genlab-daily-verify.timer` | Daily 22:00 IST | End-of-day sanity check |
| `genlab-morning-briefing.timer` | Daily 08:15 IST | Daily intel briefing |
| `genlab-viral-detector.timer` | Every ~2h | Viral content velocity scan |
| `genlab-fb-survival-check.timer` | Daily 11:30 IST | Verify FB posts haven't been removed |
| `genlab-metric-collector.timer` | Every ~1h | Metric collection |

**Always-on services**: `genlab-dashboard.service`, `genlab-engagement-poller.service`, `genlab-engagement-worker.service`, `genlab-quota-monitor.service`, `genlab-webhook.service`.

**Crash recovery**:
- `genlab_core.publishing.crash_recovery.recover_stuck_publishing` — reclaims PUBLISHING blueprints stuck >2h
- `genlab_core.publishing.crash_recovery.recover_publish_failed` — atomic PUBLISH_FAILED → VISUAL_READY retry (R-40)
- `assert_not_scheduled` (PR #170, R-81) — blocks demotion of scheduled blueprints

**Health monitor + auto-fix** (`genlab_core/monitoring/health_monitor.py`):
- `check_warp_health` (line 977) — checks `warp-svc` active + port 40000 listening
- `check_download_failures` — fires when 3+ consecutive runs produce 0 downloads
- `check_qc_collapse` — fires when QC pass rate stays at 0%
- `check_zero_blueprints` — fires when N consecutive runs produce 0 blueprints
- `check_git_drift` — uncommitted changes on prod (2026-05-17 audit signature)

### What's empirically broken

**Pipeline_alerts last 30 days** (`SELECT check_name, severity, COUNT, unresolved, auto_fix_success FROM pipeline_alerts GROUP BY ...`):

| Alert | Total | Unresolved | Auto-fix tried | Auto-fix succeeded |
| --- | --- | --- | --- | --- |
| qc_collapse (critical) | 90 | 4 | 0 | 0 |
| download_failure (critical) | 90 | 4 | 90 | **15** ← only 17% |
| zero_blueprints (critical) | 29 | 3 | 0 | 0 |
| warp_down (critical) | 12 | 0 | 12 | **0** ← never resolved itself |
| missing_media_mass (critical) | 10 | 1 | 0 | 0 |
| service_down (critical) | 9 | 0 | 9 | 0 |
| missing_media (critical) | 1 | 0 | 1 | 0 |
| content_gap (warning) | 74 | 4 | 0 | 0 |
| bandit_posterior_drift (warning) | 60 | 0 | 0 | 0 |
| bandit_stale (warning) | 18 | 4 | 0 | 0 |
| orphan_drafts_archived (warning) | 11 | 0 | 11 | 0 |
| stuck_publishing (warning) | 4 | 0 | 4 | 0 |

**The warp_down line is the smoking gun**: 12 critical fires in 30d, 12 auto-fix attempts, **0 successes**. Auto-fix message is literally `"not attempted (would need warp-cli mode/port reconfig)"` (health_monitor.py:1044) — by design no auto-fix exists for this. Today (2026-06-12) the wedge persisted 20 days because of this gap.

**Today's hotfixes on prod (NOT in repo / will revert on next deploy)**:
1. `/usr/local/bin/yt-dlp` wrapper script (since `yt_dlp` module was installed but no console-script binary)
2. `/opt/genlab/.youtube_cookies.txt` chowned to `genlab:genlab` (was root:root, blocked write-back)
3. `/opt/genlab/genlab-core/src/genlab_core/pipeline/stages/push_to_backlog.py` patched per PR #175

### Gaps to autonomy

1. **WARP auto-recovery escalation**: `systemctl restart warp-svc` is logged as the suggested fix but the wedge requires a full system `reboot` (verified today). Auto-fix should escalate: try restart → if port 40000 still not LISTEN after 30s → reboot. Code lives in `health_monitor.check_warp_health`.
2. **Today's hotfixes baked into setup**: `deploy/` doesn't have idempotent steps for the yt-dlp wrapper or cookies ownership. They'll regress on the next deploy.
3. **BacklogClient Postgres path missing Tier 2 stores**: `backlog_client.py:404` early-returns before constructing `_stories`/`_blueprints`/`_assets`/etc. Delegator methods at lines 678-703 throw AttributeError on Postgres (production). PR #175 hot-fixed one delegator call site; the architectural gap remains.
4. **Stale-schedule cutoff strands approved blueprints**: `gatekeeper.py:113` enforces `_SCHEDULE_STALE_AFTER = 18h`. When approval happens >18h before scheduled_for, blueprint becomes permanently un-publishable (today's gaming case: 5 VISUAL_READY all stale-rejected).
5. **Daily warp wedge mechanism unknown**: needs investigation. Likely Cloudflare WARP client bug or systemd unit lifecycle issue. Until root-caused, only reboot recovers.

---

## Dimension 2 — Visibility

**Goal**: every failure is obvious and the fix is obvious. Operator (and eventually the agent itself) sees what's happening in real-time.

### What's built

**Dashboard views** (`dashboard/frontend/src/views/`): mission-control, analytics, content (review), engagement, focus-review, health, learning, monetisation, pipeline, publishing-queue, schedule, runs, stories, blueprints, channel-health, settings.

**API endpoints** (`dashboard/server/api/`): 27 route modules covering alerts, analytics, audience, blueprints, config_routes, engagement, events, health, learning, legal, links, metrics, monetisation, niches, overview, pipeline, platform_posts, publishing_queue, revenue, runway, schedule, scheduler, stories, token_health, trends, webhook_receiver, youtube_quota.

**Alert surfaces**:
- `pipeline_alerts` table (208 rows in 14d) — populated by health_monitor and other check_* functions
- `/api/v1/alerts/critical` (new PR #174) — returns unresolved CRITICAL rows
- `CriticalAlertsBanner` on Mission Control — rendered above PublishingAlertBanner, dedup'd by `(niche_id, check_name)`, click-to-expand with verbatim remediation
- `dashboard_events` table — 431 pipeline_complete + 244 publish_success + 66 publish_partial in 30d
- `error_message` on blueprints (PR #173) — structured `render:<bucket>:<detail>` reason

**Logging**: structlog JSON in production (R-68 enforced 2026-05-22), `bind_contextvars(run_id=...)` per run.

### What's missing

**Off-dashboard notification sinks**: greps for `slack|webhook|sendgrid|smtp|mailgun|telegram|pagerduty` in `genlab-core/` + `dashboard/` return only the Meta webhook **receiver** (incoming) and credential_check stubs. **No outbound notification mechanism exists** — operator must actively look at the dashboard. R-01 / R-67 audit items explicitly deferred this.

**Operator-action audit trail**: `dashboard_events.event_type` in the last 30d only contains `pipeline_complete`, `publish_success`, `publish_partial`. **No `approval_approve`, `approval_reject`, `schedule_set` events captured**. The action_taken column on `blueprints` is the only record of operator decisions. This means we can't easily compute "operator approval velocity" or "operator reject reasons" for training the hook classifier.

**"Did we publish today?" SLO surface**: the owner stated SLO is 1 reel/channel/day at 12:00 noon IST. The dashboard has Mission Control but **no widget that says "today's 5 reels: 0 published, 1 VISUAL_READY awaiting approval, 4 DRAFTED stuck"**. The SLO is checkable in DB but not surfaced.

**Per-blueprint reasoning explainer**: the agent picks a blueprint to publish via `blueprint_selector.select_blueprint()` (gatekeeper-eligible + top priority_score). There's no UI surface showing **why this blueprint was selected vs alternatives** — which signals fired, what predicted engagement was, which arm the bandit picked. Without this, the operator can't build trust in the agent's choices.

### Gaps to autonomy

1. **SLO surface on Mission Control**: "Today's publish status" per-channel widget. Reads `SELECT niche_id, COUNT(*) FILTER (WHERE status='PUBLISHED' AND DATE(updated_at)=CURRENT_DATE) FROM blueprints GROUP BY niche_id`.
2. **Operator-action events**: emit `dashboard_event(event_type='approval_approve', entity_id=blueprint_id, niche_id=..., body=jsonb_of_factors)` on every approve/reject/schedule click. Build the training dataset for the autonomy-readiness classifier.
3. **Push notification sink**: Slack / SMS / email / webhook. Wired to CRITICAL alerts (R-01 / R-67 closeout).
4. **Per-blueprint "Why this content" explainer**: Each blueprint card shows the signals: `priority_score=0.74 (bandit_boost=1.12, virality_score=0.42, hook_classifier=0.61) | sources=youtube_trending+content_pool | classifier=movies@0.63 ai_creators@0.15`.

---

## Dimension 3 — Quality

**Goal**: content shipped is on-brand and on-niche. Logo, name, hook positioning standards apply per layout. Cross-niche contamination ≈ 0%.

### What's built

**Per-niche content_filter** (`config/sources.yaml`):
- `BlackboxBrief`: threshold=0.30, **0 negative_keywords**
- `ClutchWire`: threshold=0.25, **0 negative_keywords**
- `SpliceReel`: threshold=0.25, **0 negative_keywords**
- `FrameDrift`: threshold=0.35, **0 negative_keywords**
- `CriticalRush`: threshold=0.20, **0 negative_keywords**

**NicheClassifier** (`genlab_core/intelligence/niche_classifier.py:78`):
- Multi-label cross-niche scoring
- Score = keyword_score (max 0.6, min-2-hits gate) + source_affinity_bonus (+0.15) + youtube_category_bonus (+0.15)
- Negative keywords are hard reject (score=0.0) — but never configured

**content_pool routing** (`shared_ingestion.py:670` insert; `trending_video_fetcher.py:1117` read):
- shared_ingestion runs daily 10:30 IST → fetches from 716 sources in `genlab-core/config/shared_sources.yaml`
- Classifies each story across all 5 niches, writes to `content_pool` with `niche_scores`, `routed_niches`, `routing_reason`
- Last run (2026-06-12 10:46): 1207 classified → 619 routed (≥1 niche). Routing breakdown: gaming 232, sports 215, movies 166, ai_creators 59, anime 48.

**FrameCompositor branding** (`frame_compositor.py`):
- Layout A — Landscape (line 526): logo + channel_name + handle via `_build_branding_filters` (line 484-523)
- Layout B — Portrait (line 563): logo only via inline drawbox+overlay (line 583-585). **DOES NOT call `_build_branding_filters` — no name, no handle drawn**
- Layout C — Square (line 600): logo + channel_name + handle via `_build_branding_filters`

**Cross-niche forbidden source guards** (`push_to_backlog.py:136`):
- `_FORBIDDEN_SOURCE_PREFIXES` per niche — blocks tmdb_/scorebat/twitch_/etc. by source prefix
- Doesn't catch `youtube_trending` (each niche's own primary feed)

### What's empirically broken

**Cross-niche contamination (60d data)**:
- 682 blueprints with video_id
- 130 of those have a content_pool match
- **96.2%** of matched are "legitimate multi-route" (blueprint's niche IS in `routed_niches`)
- **3.8%** (5 blueprints) are truly egregious (niche NOT in `routed_niches`)
- **2 cross-niche-contaminated blueprints actually PUBLISHED** to social ("Google Just Sold Out" → ai_creators when content_pool said sports; "Lakers Game" → sports when pool said gaming)
- Direct fetch produces 77% of total blueprints; content_pool only 23%

**Source distribution last 30d** (raw counts):
- `youtube_trending` 317
- `espn_news` 66
- `scorebat` 24
- `twitch_trending` 20
- `steam_spike` 18
- everything else <10 each

So `youtube_trending` (the direct-fetch source) dominates. Pool-routed content (sources `youtube_content`, `youtube_channel`, `rss`) is far smaller share. This means **77% of decisions skip the cross-niche classifier**.

**Portrait layout missing branding** (verified today): user's screenshot showed a 1080×1920 portrait reel of "How to NOT add CGI NATURE in movies" with logo top-left but **no channel name or handle text anywhere**. Confirmed via `frame_compositor.py:563-595` — Layout B writes only the logo drawbox/overlay, never the drawtext for safe_name + safe_handle.

### Gaps to autonomy

1. **Add negative_keywords across all 5 niches' content_filter**: catches the obvious cross-niche misroutes (movies VFX → ai_creators; AI tutorials in gaming; etc). Empirically informed by the 5 egregious cases observed.
2. **Smart-dedup in trending_video_fetcher.execute()** (this session's recommendation): after direct fetch, query content_pool. If pool says routed to other niche, skip. Eliminates the 3.8% egregious case.
3. **Portrait branding parity** (`frame_compositor.py:_build_cmd_portrait`): add `_build_branding_filters` call with portrait-appropriate coordinates so name + handle render on portrait reels too.
4. **The classifier itself has errors**: 3 of the 5 egregious cases were actually content_pool MISCLASSIFYING (e.g., Sinners (movie) routed to anime). Need a classifier-improvement loop — possibly with LLM judgment as a periodic auditor.
5. **YouTube category 28 (Sci&Tech) is single-affinity to ai_creators**: this is the source of the CGI/movies → ai_creators case. Either widen its affinity to include movies (since film VFX content trends here) or use a richer classifier that doesn't trust YouTube category alone.

---

## Dimension 4 — Learning

**Goal**: the agent gets continuously smarter. Bandits update from reward signal. Classifier improves from approval/reject history. Affiliate revenue grows over time.

### What's built

**bandit_arms table** (per-niche × per-arm):
- Each niche has 9 arms (content-type × hook-style combinations)
- `alpha`, `beta` (Thompson Sampling Beta posterior)
- `n_plays`, `linucb_state` (contextual bandit state)
- Updated by `genlab_core.learning.metric_collector` per reward signal

**Hook classifier**:
- Per-niche XGBoost model at `/opt/genlab/genlab-core/models/hook_classifier_{niche}.json`
- 5 niches × ~85 KB model files exist (verified on prod 2026-06-12)
- 8 hand-crafted features (word_count, has_question, has_number, emoji_count, has_superlative, starts_with_you, avg_word_length, unique_word_ratio)
- Training data from `pending_feedback` table; trainer is `genlab_core.learning.hook_training_data`
- `genlab-hook-trainer.timer` fires weekly Sunday 10:30 IST

**Reward shaper** (`genlab_core/learning/reward_shaper.py`):
- Monetisation-aware threshold-proximity reward computation
- Per-platform metric normalization

**LinUCB contextual bandit** (`genlab_core/learning/linucb.py`):
- 6D feature vector per arm: (day, hour, source, duration, velocity, relevance)
- Cold-start fallback to Thompson Sampling when n_obs < 50

**Config write-back** (`genlab_core/learning/config_writer.py` + `config_update_flow.py`):
- Bandit posterior → YAML config (schedule_slots ±2h, template ratios)
- `genlab-config-updater.timer` fires weekly Monday

### What's empirically broken

**Untouched arms per niche** (`α=1 AND β=1` = never updated):
- ai_creators: 3/9 untouched
- anime: 5/9 untouched
- gaming: 2/9 untouched
- **movies: 6/9 untouched**
- **sports: 5/9 untouched**
- Average: ~50% of arms have never received a reward update

**Bandit last_update**:
- ai_creators: 2026-05-30
- anime: 2026-05-28
- gaming: 2026-06-10 (most recent — only niche actively publishing)
- movies: 2026-05-28
- sports: 2026-06-01

**Hook classifier training data sizes** (from `.meta.json`):
- ai_creators: n=53, pos_rate=26%, trained 2026-05-20
- anime: n=60, pos_rate=25%, trained 2026-05-20
- gaming: n=54, pos_rate=26%, trained 2026-05-20
- movies: n=59, pos_rate=25%, trained 2026-05-20
- sports: n=68, pos_rate=26%, trained 2026-05-20

These are tiny training sets. The trainer next fires 2026-06-14 10:30. Since 2026-05-20 (last training) → 2026-06-14 (next), only ~23 days of operator-feedback data has accumulated, and given 4/5 niches dark for 22 of those days, the new training set will be barely larger.

**Attribution chain** (where reward signal is supposed to flow):
- `publishing_analytics` columns: views, likes, comments, shares, saves, metrics_fetched, blueprint_id, error_message — schema is right
- pending_feedback table: 4-window collection (6h, 24h, 48h, 168h)
- Per [[video-sourcing-quality-overhaul]] 2026-05-26 finding: "**41% of rewards exactly 0; pending_feedback.post_id ≠ analytics.post_id → corr only testable n=4/7**". Attribution is mostly broken.
- `config_updates` table is **empty** (0 rows) — the config write-back trainer has never produced an update. Either timer isn't firing or write-back logic isn't completing.

### Gaps to autonomy

1. **Fix attribution chain end-to-end**: ensure `pending_feedback.post_id` format matches `analytics.post_id` format (post-2026-05-26 work claimed to fix this but config_updates emptiness suggests something is still broken).
2. **Deploy the config write-back trainer**: per memory, it's "in `systemd-phase2/`" — never deployed. Verify deployment + observe `config_updates` table populating.
3. **Grow training data deliberately**: with 4/5 niches restored today, the next 30 days should produce 5× the prior training data. Hook trainer running next 2026-06-14.
4. **Add richer hook features**: current 8 hand-crafted regex features predict performance weakly. Consider transformer embedding of hook text + per-niche fine-tuning.
5. **Bandit cold-start strategy for dormant arms**: 6/9 movies arms have α=β=1. Either remove them (if they're never picked) or warm-start them from meta-prior (`genlab_core.learning.meta_prior`).
6. **Per-platform learning loops**: per 2026-05-26 analysis, platform-fit is niche-specific (sports→YouTube, ai/movies→Instagram, gaming/anime→Facebook+Instagram). Currently single-arm-per-niche bandit doesn't capture this. Need per-platform arms.
7. **Affiliate revenue learning loop**: PA-API SigV4 shipped (PR #168) but the 4 per-network adapters (EarnKaro/Impact/ShareASale/CJ) are credential-blocked. Without per-network revenue data, affiliate optimization can't actually learn.

---

## Dimension 5 — Trust

**Goal**: the operator trusts the agent's decisions enough to lift the approval gate. The agent demonstrates that trust via track record, confidence scoring, and explanations.

### What's built

**Approval gate** (`genlab_core/platforms/gatekeeper.py:58-74`):

```python
def _approval_gate(self, bp: dict, platform: str) -> GateResult:
    """Audit R-08 — the legacy express-lane bypass let a blueprint
    through whenever urgency was CRITICAL or HIGH. That urgency was
    derived from a regex over attacker-controllable text (RSS/YouTube/
    Reddit). The gate is now strict: only dashboard approval passes."""
    if bp.get("action_taken") == "approved":
        return GateResult(allowed=True, reason="approved", gate_name="approval_gate")
    return GateResult(allowed=False, reason="Not approved", gate_name="approval_gate")
```

**Approval data captured**: `blueprints.action_taken`, `blueprints.reviewed_at`. Recorded by dashboard's approve/reject/schedule endpoints in `dashboard/server/api/blueprints.py`.

**Operator decisions last 60d**:
- 223 approvals
- 173 rejections
- 103 auto_archived_orphan (system-side)
- 13 auto_archived_missing_media (system-side)
- 5 auto_archived_stale_publish_failed
- 2 manual archived
- 1 user_flagged_cross_niche_leak (the operator HAS noticed cross-niche contamination before today)

This is ~6.5 operator decisions/day across all 5 niches. Approval rate ~56% (223 / 396 explicit decisions).

### What's missing

**Zero infrastructure for confidence-based auto-publish**:
- Grep across `genlab-core/src/` for `confidence_score|auto_publish|skip_approval|confidence_threshold|auto_approve` → **no matches**
- No code path exists that could decide "this blueprint is confident enough to publish without approval"

**Zero infrastructure for graduated autonomy rollout**:
- No flag for "auto-publish enabled for niche X content-type Y"
- No track-record measurement ("if we had auto-approved with policy P, what would have happened?")
- No emergency-freeze kill switch separate from the per-blueprint approve/reject

**Zero confidence scoring on the agent's choices**:
- `priority_score` is computed but it's a bandit-boost-modulated reach prediction, not a confidence-of-correctness signal
- No "predicted engagement" or "predicted approval probability" surfaced to the dashboard

### Gaps to autonomy

This is the dimension with the LARGEST gap. Closing it requires:

1. **Confidence score per blueprint**: compute and persist as a column. Inputs: hook_classifier prediction, content_pool niche_scores match strength, bandit posterior confidence, historical conversion rate for the (niche, source, content_type) tuple, anomaly signals (negative_keyword hits, brand-safety flags).
2. **"Shadow mode" auto-publish**: agent decides every day "if auto-publish were on, this is the blueprint I'd ship". Recorded as a `dashboard_event` of type `shadow_auto_publish_decision`. Compared with operator's actual approval. Measure: agreement rate over rolling 30d window.
3. **Graduated rollout policy**: per (niche, content_type, layout) tuple, allow auto-publish only if (a) shadow-mode agreement rate ≥ 95% over last 30d, (b) historical conversion ≥ 60%, (c) classifier confidence ≥ threshold (per-niche-tunable).
4. **Auto-publish freeze switch**: single config flag `AUTO_PUBLISH_ENABLED=false` env or `system_state.auto_publish_frozen=true` row that any operator + alert can flip instantly.
5. **Track-record evaluation**: dashboard view showing "auto-publish track record" — what was auto-published last week, how it performed, what predictions were right/wrong.

---

## Cross-dimension dependencies

Some fixes unlock multiple dimensions:

| If you ship... | It unblocks... |
| --- | --- |
| **Smart-dedup in trending_video_fetcher** | Quality (eliminates egregious contamination), Learning (cleaner training data), Trust (basis for "this content was correctly routed" confidence) |
| **Operator-action events on dashboard_events** | Visibility (audit trail), Learning (richer training data for hook classifier), Trust (shadow-mode evaluation requires comparison ground truth) |
| **Off-dashboard notification sink** | Reliability (operator notified within minutes not weeks), Visibility (closes R-01/R-67), Trust (operator stays in the loop when autonomy partial) |
| **Today's hotfixes baked into deploy** | Reliability (immediate), but also a Quality pre-req (without working renders the whole pipeline can't generate the dataset the learning loops need) |
| **Confidence score per blueprint** | Trust (foundational), Visibility (per-blueprint explainer), Learning (shadow-mode disagreements are gold training data) |

The cleanest causal order:
1. **Reliability + Quality fundamentals first** (so the agent doesn't break + ships clean content)
2. **Visibility second** (so the operator sees decisions + the agent can self-report)
3. **Learning third** (so the agent gets smarter from real data)
4. **Trust fourth** (gated on the first three having matured)

---

## Recommended sequencing — 4 weeks to first auto-publish

### Week 1 — Stop the bleeding

**Track A — Reliability**
- PR: `health_monitor.check_warp_health` auto-fix escalation (restart → wait → reboot if still wedged)
- PR: setup script idempotency for `/usr/local/bin/yt-dlp` wrapper + `chown genlab:genlab` on cookies
- PR: BacklogClient Tier 2 stores constructed on Postgres path (architectural fix for the bug PR #175 surface-fixed)

**Track B — Quality**
- PR: smart-dedup in `trending_video_fetcher.execute()` (cross-niche skip)
- PR: portrait layout draws name + handle (parity with landscape/square)
- PR: negative_keywords across 5 niches' `sources.yaml` (config-only)

### Week 2 — Make the agent show its work

- PR: SLO widget on Mission Control — "today's 5 reels" status
- PR: Operator-action events emitted to `dashboard_events` (approve/reject/schedule)
- PR: Per-blueprint "Why this content" explainer card
- PR: Off-dashboard notification sink (Slack webhook MVP)

### Week 3 — Wake up the learning loops

- PR: Deploy the config write-back trainer (the one in `systemd-phase2/` per memory)
- PR: Fix `pending_feedback.post_id ↔ analytics.post_id` join
- PR: Add transformer-embedding features to hook classifier (in addition to the current 8 regex features)
- PR: Per-platform arms for the bandit (separate ones for IG/YT/FB/Threads)

### Week 4 — Begin lifting the gate

- PR: Compute `confidence_score` per blueprint at push_to_backlog time
- PR: Shadow-mode auto-publish — agent records its decision, no actual publish, compare to operator
- PR: Graduated rollout policy + auto-publish freeze switch
- PR: Track-record evaluation dashboard view

After this campaign: shadow-mode dataset built, the **first content-type × niche combination with ≥95% agreement** can have its auto-publish gate lifted with confidence. The owner picks which one based on track record (likely sports `highlight_play` per 2026-05-26 performance analysis).

---

## Open questions for the owner

These need decisions before I can ship the campaign:

1. **Priority order**: my recommendation is Reliability→Quality→Visibility→Learning→Trust. Is that right or do you want something else first?
2. **Time horizon**: 4-week campaign assumes ~1 PR/day cadence. Is that the right pace?
3. **Scope of "autonomous"**: when the gate lifts, do you want the agent to also choose the publish time (per-platform optimal), or just choose the content and use the existing 12 noon IST window?
4. **Affiliate-revenue loop**: PA-API works; per-network credentials are blocked. Do you want to invest operator time in setting up EarnKaro/Impact/ShareASale/CJ accounts to unblock that learning loop? Without it, affiliate revenue can't autonomously optimize per-product.
5. **Risk tolerance for auto-publish**: do you want shadow-mode to run for ≥30 days before lifting any gate, or are you comfortable lifting after fewer (≥14) days of agreement evidence?

---

## Appendix — How to refresh this snapshot

Run these queries in order to regenerate the empirical sections. Date-range them as needed.

```sql
-- §1 alert frequency
SELECT check_name, severity, COUNT(*) AS total,
       COUNT(*) FILTER (WHERE resolved_at IS NULL) AS unresolved,
       COUNT(*) FILTER (WHERE auto_fix_applied IS NOT NULL) AS auto_fix_tried,
       COUNT(*) FILTER (WHERE auto_fix_applied ~ '(success|ok|connected)') AS auto_fix_success
FROM pipeline_alerts WHERE created_at >= NOW() - INTERVAL '30 days'
GROUP BY check_name, severity ORDER BY severity, total DESC;

-- §3 cross-niche contamination summary
WITH joined AS (
  SELECT b.id, b.niche_id, b.status, cp.routed_niches, cp.claimed_by
  FROM blueprints b JOIN content_pool cp ON cp.video_id = b.video_id
  WHERE b.video_id != '' AND b.created_at >= NOW() - INTERVAL '60 days'
)
SELECT COUNT(*) AS total_matched,
       COUNT(*) FILTER (WHERE niche_id = ANY(routed_niches)) AS multi_route,
       COUNT(*) FILTER (WHERE NOT (niche_id = ANY(routed_niches))) AS egregious,
       COUNT(*) FILTER (WHERE NOT (niche_id = ANY(routed_niches)) AND status IN ('PUBLISHED','PUBLISHING')) AS published_wrong
FROM joined;

-- §3 source distribution
SELECT source, COUNT(*) FROM blueprints
WHERE created_at >= NOW() - INTERVAL '30 days' AND source IS NOT NULL
GROUP BY source ORDER BY count(*) DESC LIMIT 15;

-- §3 content_pool contribution share
SELECT COUNT(*) AS total,
       COUNT(*) FILTER (WHERE cp.id IS NOT NULL) AS in_pool,
       COUNT(*) FILTER (WHERE cp.id IS NULL) AS direct_only
FROM blueprints b LEFT JOIN content_pool cp ON cp.video_id = b.video_id
WHERE b.video_id != '' AND b.created_at >= NOW() - INTERVAL '30 days';

-- §4 bandit health
SELECT niche_id, COUNT(*) AS arms,
       COUNT(*) FILTER (WHERE alpha = 1 AND beta = 1) AS untouched,
       MAX(updated_at) AS last_update
FROM bandit_arms GROUP BY niche_id ORDER BY niche_id;

-- §5 operator decision velocity
SELECT action_taken, COUNT(*),
       MIN(reviewed_at), MAX(reviewed_at)
FROM blueprints WHERE reviewed_at >= NOW() - INTERVAL '60 days'
GROUP BY action_taken ORDER BY count(*) DESC;
```

```bash
# §4 hook classifier model state
ssh root@46.224.237.56 'ls -la /opt/genlab/genlab-core/models/ && cat /opt/genlab/genlab-core/models/*.meta.json'

# §1 systemd timer inventory
ssh root@46.224.237.56 'systemctl list-timers --all --no-pager | grep genlab'
```

---

**Last refreshed**: 2026-06-12 22:00 IST
**Next refresh suggested**: after the Week 1 PRs ship, to validate Reliability + Quality numbers move in the right direction.
