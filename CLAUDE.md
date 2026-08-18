# CLAUDE.md — Gen Lab
# Authoritative context for every Claude Code session in this project.

## MISSION — READ THIS FIRST

Gen Lab is a **video-first viral content automation platform** that tracks trends and
publishes short-form video reels to grow social media channels toward monetisation.

The end goal is a **multi-tenant SaaS** product for micro-influencers and brands.
We are currently in Phase 1: proving the system works on our own 5 channels before
opening to external subscribers.

### The agent we're building (north star, 2026-07-10)

Gen Lab is an **intelligent automated social-media powerhouse**. Seven load-bearing
capabilities drive every architectural choice — feature work that doesn't advance
at least one of these is deprioritised:

1. **Multi-channel content generation** — writes posts for multiple channels
   simultaneously, adds channel/platform-specific uniqueness, adheres to each
   platform's policies.
2. **Trend & content intelligence** — tracks best-performing content per niche +
   trending stories/topics + past-success/failure attribution.
3. **Continuous learning** — self-improves from every publish, engagement, and
   conversion signal (LinUCB bandit + Thompson fallback + RewardShaper +
   cross-niche transfer + doubly-robust reward estimator).
4. **Multi-channel growth** — grows reach, following, and engagement across all
   channels in parallel.
5. **Professional-grade judgment** — the brains of a professional social-media
   manager AND expert content creator (AUTO #1 gate + ensemble decisioning +
   Strategist weekly meta-analysis).
6. **Monetization intelligence** — gets any channel monetised AND picks the best
   products per post to maximise affiliate revenue (108-arm product bandit +
   Bernoulli conversion reward loop closed 2026-07-07).
7. **Engagement automation** — replies to audience questions, likes top comments,
   applies pro-team tactics (hybrid auto/review/discard classifier live across
   all 5 niches).

See `agent-vision-2026-07-10.md` in the working memory for the full rollup of
what has shipped toward each capability.

### What we build

Short-form VIDEO REELS for Instagram, YouTube Shorts, Facebook Reels, TikTok, and X.

**NOT text posts. NOT carousels. NOT static images. NOT slideshows.**
Text posts have zero traction on short-form video platforms. If it is not a reel with
real video footage, it does not ship.

### The five channels

| Channel | Niche | Video Content |
|---|---|---|
| Blackbox Brief | AI news | Creator clips — humans demoing/explaining AI tools |
| CriticalRush | Gaming | Trending gameplay clips, esports highlights, game trailers |
| ClutchWire | Sports | Top sports moments, highlight plays, viral athletic moments |
| SpliceReel | Movies | Trending trailers, viral film clips, box office reaction clips |
| FrameDrift | Anime | Trending fight scenes, viral episode moments, anime clips |

### The video-first pipeline

**THE VIDEO IS THE STARTING POINT.** Content is written around what is already
trending on YouTube — NOT found after a text story.

```
OLD (wrong): Fetch RSS text → try to find video → fail → stuck at DRAFTED forever
NEW (correct): Find trending YouTube clip → write content around it → render → publish
```

Sources:
- CriticalRush: YouTube Data API v3, category 20 (Gaming), mostPopular chart
- ClutchWire: YouTube Data API v3, category 17 (Sports), mostPopular chart
- SpliceReel: YouTube Data API v3, categories 1 + 24 (Film/Entertainment)
- FrameDrift: YouTube keyword search (no native anime category)
- BlackboxBrief: YouTube search for AI creator/explainer content + RSS for context

Google Trends (via pytrends) enriches the YouTube keyword search in real time.

---

## ARCHITECTURE — THREE LAYERS

```
Layer 1 — genlab-core/        SHARED INFRASTRUCTURE (never changes per niche)
Layer 2 — niches/*/strategies/ PLUGGABLE STRATEGIES (abstract interfaces per niche)
Layer 3 — niches/*/config/    NICHE CONFIGURATION (pure YAML)
```

### What belongs where

**genlab-core/** — If two channels would write identical code, it lives here:
- BacklogClient, TrendingVideoFetcher, GoogleTrendsIntel, VideoContentWriter
- text_sanitizer, story_hook_generator, publish_all_platforms logic
- LLM client (Claude Haiku, GPT-Image-1), FFmpeg render utilities
- Engagement Engine, Learning Loop (Thompson / BanditArms)
- Token health manager, Metric collector, All Pydantic schemas
- **Base strategy classes** (BasePlatformAdaptation, BaseWriting, BaseHooks, BaseContentResearch)
- **Platform clients** (Instagram, YouTube, X/Twitter, Facebook, Threads, TikTok)
- **Unified pipeline CLI** (`python -m genlab_core.pipeline --niche <id>`)

**niches/CHANNEL/** — Everything niche-specific:
- config/ (niche.yaml, sources.yaml, scoring.yaml, visuals.yaml, schedule.yaml)
- strategies/ — thin subclasses inheriting from genlab-core base classes
  (CW: `cw_strategies/`, SR: `sr_strategies/`, FD: `fd_strategies/`, BB: `bb_strategies/`)
- run_pipeline.py, prompts/, tests/

### Adding a new channel

1. Create channel dir with `config/niche.yaml` (copy from existing, change niche_id + sources)
2. Write 4 strategy subclasses (~200 lines total): ContentResearch, Writing, Hooks, PlatformAdaptation
3. Register in `genlab_core.pipeline.cli.NICHE_DIR_NAMES`
4. Add to uv workspace in root `pyproject.toml`
5. Run: `python -m genlab_core.pipeline --niche <new_id>`

### Remaining architecture debt

BlackboxBrief legacy platform clients are GONE from `execution/utils/` (R-06 audit
2026-05-22). Publishing flows fully through genlab-core clients. The only sources
connector still in `BlackboxBrief/execution/` is `execution/sources/youtube_connector.py`
(intelligence scripts, not publishing).

Do NOT add new shared code to BlackboxBrief or CriticalRush. Always add to genlab-core.

---

## PIPELINE EXECUTION ORDER

For all non-BB channels (CriticalRush, ClutchWire, SpliceReel, FrameDrift):

```
1. fetch_trending_videos.py   ← YouTube API: find what's viral RIGHT NOW
2. score_and_filter.py        ← View velocity + Google Trends multiplier
3. compose_blueprints.py      ← Build blueprint for top-N clips
4. write_content.py           ← LLM: write hook + captions around the video
5. render_visuals.py          ← FFmpeg: render video with logo overlay
6. human_review               ← Approve at the review dashboard
7. publish_all_platforms.py   ← Publish to IG, YT, FB, X, Threads
8. metric_collector.py        ← Collect engagement after 6h/24h
9. performance_learner.py     ← Update BanditArms for next run
```

For BlackboxBrief (AI news): Stage 1 is RSS fetch + YouTube creator clip search; rest is identical.

---

## STRICT VIDEO REQUIREMENTS

Every rendered video MUST:
- Be 1080×1920 (9:16 portrait, vertical)
- Use the channel's source video (downloaded via yt-dlp)
- Have the channel logo overlaid (visuals.yaml → logo_path, logo_height: 60px)
- Be bt709 color space (not bt470bg) — enforced in FrameCompositor with
  `-colorspace bt709 -color_primaries bt709 -color_trc bt709`
- Be H.264 (libx264), AAC 48kHz stereo — all platforms use the same
  libx264/CRF20/preset=fast spec because the 4 GB Hetzner VPS OOMs on libx265.
  See `genlab_core/media/ffmpeg.py` PLATFORM_SPECS for the source of truth.
- Be 15–60 seconds long

**VMAF gate fail-opens by R-25 design.** `video_gate.py` deliberately doesn't set
`master_path`; a real VMAF gate would need a lossless master of the branded composite
(not produced today). Enforced quality gates are bt709 + 1080×1920 + duration only.

**`platform_encode_specs.yaml` is INACTIVE** (M-2, 2026-06-09). Only
`platform_durations.max_seconds` is read in prod.

Videos MUST NOT:
- Be a text-only motion render (banned — not a reel)
- Be a static image with Ken Burns (banned)
- Be a placeholder or generic compilation used across multiple blueprints
- Be rendered without the channel logo overlay
- Be rendered with whisper word-by-word caption overlay for niches where
  `whisper_sync.enabled = false`. Current state (per QB-FIX-01 F3b verification
  2026-08-06): **ai_creators is the CANARY** with `whisper_sync.enabled = true`
  since 2026-07-22 (the 2026-06-13 `text_optimizer` regression was fixed inline
  at `word_animator.py:21-46` and `render_whisper_captions.py:231` now passes
  `text_type="caption"`). gaming / sports / movies / anime remain disabled.
  Do not extend to other niches without a canary-vs-baseline retention read.

If no source video exists for a story, the blueprint stays at DRAFTED.
Gaming (CriticalRush) requires a verified video clip — no exceptions.

---

## CONTENT QUALITY RULES

### Hooks (≤60 characters, story-specific)
- Must reference something specific from the actual video/story
- No generic templates: "Something big happened", "the community is going wild",
  "players need to see this", "Cinema is back and", "No more excuses"
- No boilerplate suffixes. Deduplication enforced: same hook cannot appear twice in same niche.

### Pre-render quality gate

`genlab_core.rendering.pre_render_quality.check_pre_render_quality(hook, niche_id)`
runs in `base_visual_render._compose_frame` after `hook_text` extraction, BEFORE
compositor. Three rules, short-circuit on first failure:

1. **no_llm_refusal_preamble** — reuses writer's prefix list (`"i need the"`, etc.)
2. **min_length_15** — catches truncation + placeholder leftovers
3. **hook_bare_title** — bare titles like `"Grand Theft Auto V"` with no verb signal

Rejection returns `""` from `_compose_frame`; blueprint stays at DRAFTED with
`story["media"]["render_error"] = "pre_render_quality:{reason}"`.

### Writer thin-context filter

`base_writing._has_writable_context(story)` returns True iff any of
`summary` / `description_snippet` / `description` has ≥40 chars. Below the floor,
`story["_skip_llm"] = True` — prevents `"I need the Story Summary..."` refusals from
ever reaching the pre-render gate.

### Writer wire — attribution propagation

`base_writing._story_to_video_dict` maps story→writer input; `_write_story_llm`
propagates `content["source_attribution"]` to `story["content"]`; StoryStore persists
all 11 attribution fields (channel_name, channel_id, video_id, source_url, etc.) to
`stories.extra` JSONB. Pin: `test_base_writing_source_attribution_wire.py`. Full
regression history: `[[session-2026-07-13-audit-followup-writer-wire]]`.

### Captions
- Instagram: 150-200 chars + 3-5 hashtags + CTA (must end with CTA)
- Twitter: ≤280 chars, NO external links in tweet body (links in first reply only)
- YouTube: question format title, ≤40 characters
- Facebook: 200-300 chars, engaging question, no external links

### HTML/formatting
- Strip all HTML tags (`<cite>`, `<p>`, `<strong>` etc.) before writing to SharePoint
- `text_sanitizer.sanitize_for_graph_api()` must be called on all string fields

### Content variants (Layer 3 — foundation shipped 2026-07-17)

Every blueprint has `variant_type` (structural: single_clip / series_part /
split_screen / storytime / watch_till_end / question_reveal) + `variant_payload`
(per-variant JSONB). Source of truth: `genlab_core/variant_types.py`. Only
`single_clip` implemented today; other variants ship per session in
`[[variant-architecture-roadmap]]`. Every existing blueprint retroactively
defaults to `single_clip` + `{}` (migration `43c4084cf927`). Unknown variant
strings fall back to `single_clip` with WARNING (rule #17 sibling).

---

## PUBLISHING RULES

- 1 reel per channel per day, hard cap
- Publish window: 06:30 UTC (12:00 IST)
- Daily cap enforced at RENDER time (not just publish time)
- All platforms attempted in parallel (ThreadPoolExecutor)
- When a platform is skipped due to missing credentials: LOG a WARNING, write
  SKIPPED record to Publishing_Analytics — never silent-fail
- Facebook post survival check at 24h: if post removed by Meta, mark REMOVED_BY_META
- YouTube titles: question format, ≤40 chars
- **SKIP_APPROVAL_GATE is REMOVED** (Sprint 62). Dashboard approval is the real gate.
  Auto-scheduling on approval is niche-aware (checks slot collisions per niche).

## ATTRIBUTION DEFENSE STACK

**Full reference**: `docs/attribution-defense-stack.md`. **Ops runbook**:
`docs/RUNBOOK-retroactive-credit-ops.md`. Update BOTH docs whenever file:line refs shift.

**Invariant**: every reel MUST carry `"🎬 Original: @{creator} — {url}"` OR
`"Footage: {url}"` in the caption AND (bonus) burned into the video frame as a watermark.

### 6-layer defense-in-depth

| Layer | File | Bypass flag |
|---|---|---|
| L1 fetcher gate | `media/trending_video_fetcher.py:494` | `GENLAB_ATTRIBUTION_LAYER1_ALLOW_MISSING` |
| L2 persist gate + `_credit` | `pipeline/stages/push_to_backlog.py:1922` | `GENLAB_ATTRIBUTION_LAYER2_ALLOW_MISSING` |
| L3 policy gate | `compliance/copyright_safety.py:201` | `GENLAB_ATTRIBUTION_LAYER3_ENFORCE=1` |
| L4 publisher validation × 6 clients | `platforms/{fb,ig,yt,threads,x_twitter}.py`, `publishing/tiktok_client.py` | `GENLAB_ATTRIBUTION_LAYER4_BLOCK=1` |
| L5 metric + monitor | `dashboard/server/core/attribution_health.py`, `monitoring/attribution_health_monitor.py` | (thresholds 100/99) |
| L6 frame watermark | `media/frame_compositor.py:553` (`_build_watermark_filter`) | (skipped when `source_credit=""`) |

L4 validator recognises CAPTION markers only. L5 tolerance is tiered:
`attribution_health_monitor.timer` at 99%; `post_deploy_verify.sh` check #8 at 80%.

Class-of-bug this stack teaches: `[[class-of-bug-metric-proxies-mask-audience-facing-failures]]`.
Verify + flip runbook: `scripts/verify_writer_wire_and_flip_l4.sh` (guarded L4 flip);
`scripts/retro_credit_uncredited_posts.py` (state at `/opt/genlab/.runtime/retro_credit_state.json`,
owner MUST be `genlab:genlab` per rule #15).

---

## CREDENTIAL ARCHITECTURE (Sprint 62)

- Root `GenLab/.env` — shared credentials + all per-niche prefixed vars
- Per-niche `.env` — that channel's own tokens (belt + suspenders)
- `niche_credentials.py` resolves `{PREFIX}_{KEY}` per niche, never falls back cross-channel
- FB tokens are permanent EAA Page Tokens (expires_at=0) via the project's Meta app
- **Never run env consolidation AFTER token provisioning** — stale values overwrite fresh ones

## PIPELINE SCHEDULE

Pipelines run via systemd timers on Hetzner VPS. Each channel has its own schedule.
Publisher runs after all pipelines complete. Insights collector runs twice daily.
See `deploy/systemd-phase2/` for the full unit + timer inventory.

## DEDUP ARCHITECTURE (Sprint 62)

Three layers prevent duplicates:
1. **video_id dedup** in PushToBacklog — same clip never creates two blueprints
2. **DailyCapEnforcer** with niche_id — per-channel caps, not global
3. **PUBLISHED skip** — PushToBacklog won't overwrite PUBLISHED/VISUAL_READY blueprints

## CONTENT RELEVANCE (Sprint 63)

`RelevanceFilter` in `genlab_core.media.relevance_filter` scores video candidates
against niche-specific keyword lists AFTER YouTube fetch, BEFORE pipeline processing.

- Each niche has `content_filter:` in `config/sources.yaml` with `positive_keywords`,
  `negative_keywords`, and `relevance_threshold`
- Hard-rejects on negative keywords (score=0.0). Anime rejects: MMA, UFC, boxing, wrestling
- Anime threshold 0.35 (strictest — no native YouTube category)
- Rejected videos logged in run_report for debugging

## VIDEO QUALITY PIPELINE

Per-platform transcode via `PLATFORM_SPECS` in `genlab_core.media.ffmpeg`. Production
uses libx264/CRF20/preset=fast for **all 5 platforms** (YT, IG, TikTok, FB, X) because
the 4 GB Hetzner VPS OOMs on libx265. `ffmpeg.py:182-258` is the source of truth.

## CIRCUIT BREAKERS (Sprint 63)

`genlab_core.http.circuit_breaker.CircuitBreaker` — CLOSED → OPEN → HALF_OPEN states.
Pre-configured: SHAREPOINT_CB, META_API_CB, YOUTUBE_CB, ANTHROPIC_CB, TWITTER_CB.
`@resilient` decorator combines retry + circuit breaker. Wired into: BacklogClient,
TrendingVideoFetcher, PersonaEngine, FetchInsights. On open: graceful degradation.

## LEARNING LOOP (Sprint 63 — 100% complete)

Full feedback loop: publish → metrics → reward → bandit update.

- **FetchInsights** queries Publishing_Analytics (not current-run context)
- **MetricCollector** has 6 platform fetchers (YT, IG, FB, X, TikTok, Threads)
- **20 insight schedules** (5 niches × 4 windows: 6h, 24h, 48h, 168h)
- **LinUCB contextual bandit** with 12D features — see `learning/linucb.py:244-262`
  for canonical vector: weekday, hour, source_type, duration, velocity, relevance,
  hook_length, niche, has_affiliate, caption_length, hashtag_count, composite_score
- Cold-start Thompson Sampling fallback (<50 observations per arm)
- **RewardShaper** with monetisation-aware threshold proximity boosting

## ENGAGEMENT ENGINE (Sprint 63 — 100% complete)

Hybrid auto-reply system:
- **auto** (conf ≥0.85, tox <0.15, safe pattern, <100 chars) → post immediately
- **review** (conf ≥0.5, tox <0.3) → queue for dashboard approval
- **discard** (low conf or high tox) → log and drop

Reply lives in platform clients (`platforms/instagram.py:post_reply`, etc.), not in
separate engagement clients. Pollers: YouTube (30min — conserves 10K daily quota),
Twitter (15min), Threads (10min) via `run_engagement_poller.py`. FB + IG via Meta
webhooks (`webhook_receiver.py`). Detoxify toxicity gate, Dramatiq priority queues,
lognormal jitter.

## SAAS TOOLS (Sprint 63)

- `uv run python -m genlab_core.tools.create_niche --niche-id X --brand-name Y --accent-color Z --output-dir /path`
- `uv run python -m genlab_core.tools.validate_configs --niche-dir /path`
- Canonical niche_id is `ai_creators` (not `ai_news`). `ai_news` kept as backward-compat alias.

## AUTO-APPROVAL GATE (AUTO #1 + #2)

**AUTO #1 = decide/measure/visualize** trilogy. `auto_approval_gate.evaluate(blueprint)`
returns `AutoApprovalDecision`. Five gates: `has_video`, `has_hook`, `qc_passed`,
`composite_score ≥ 0.3`, `virality_score ≥ 0.05`. Preview surface:
`GET /api/v1/blueprints/<id>/auto-approval-preview`. Dashboard `AutoApprovalBadge`.
Calibration writes to `auto_approval_calibration` Postgres table per operator click;
readiness = ≥30 samples AND ≥90% agreement.

**AUTO #2 = enforcement worker.** SHIPPED 2026-07-06. Runs as `genlab-auto-approver.timer`
every 30 min. Kill switches: (a) `GENLAB_AUTO_APPROVE_DISABLED=1`, (b)
`touch /opt/genlab/.runtime/auto_approve_kill_switch`, (c) `systemctl stop` the timer.

**Live 2026-08-15 state** (was "ai_creators ONLY" pre-Phase-4.C — that note was
stale by weeks): all 5 niches have `auto_publish.enabled: true` + `rollout_pct:
1.0`. Current min_confidence per niche (daily-tuned by Phase 5.A calibration
tuner, threshold floor lowered 15→5 samples on 2026-08-15 `c0a3a806`):
ai_creators 0.745, sports 0.794, gaming 0.85, movies 0.85, anime 0.85 (pending
+0.06 operator-review suggestion). Threshold tuning history + gaming enrollment
revert lesson: `[[session-2026-07-17-batch-fix-deeper-cuts]]` (rule #22).

Rollout ladder: Week 1 `0.1` → Week 2 `0.25` → Week 3 `0.5` → Week 4 `1.0`. Dice
is deterministic per blueprint (`sha256(record_id) % 10000 / 10000`).

### AUTO #2 diagnostic + observability (2026-07-24 sprint)

Three surfaces added to make the ratchet debuggable + auto-tunable:

* **`gate_examinations` table** — every gate evaluation persists blueprint_id,
  niche_id, approved, confidence, passed_checks + failed_checks JSONB, and
  extra JSONB carrying raw composite_score + virality_score. Log call is
  fail-open, wired in `auto_approver.py` after strategy layer.
* **`GET /api/v1/auto-approval/gate-examinations`** — aggregates by niche with
  per-check rejection tally, top failing check, PERCENTILE_CONT distribution
  over rejected-set scores, and threshold_suggestion (p25 - 0.001 + would-
  unlock-count + weekly-estimate + confidence label high/medium/low based on n).
* **`GET /api/v1/auto-approval/outcome-readiness`** — parallel signal to
  operator-agreement calibration. Computes fraction of auto-approved
  blueprints whose `MAX(reward_48h)` across platforms cleared 0.05 threshold.
  Different from operator-agreement because it doesn't need operator clicks —
  unblocks the ratchet when the operator hasn't reviewed recently. READ-ONLY;
  consumer wire deferred until operator eyeballs the numbers for ≥1 week.
* **Mission Control cards**: `GateExaminationsCard` (top failing check +
  threshold tune suggestion per niche); outcome-readiness surfaces as a
  sub-badge on the existing `AutoApprovalCalibrationCard`.

Detection heuristic for gate stuck-loops: query
`SELECT niche_id, top_failing_check, threshold_suggestion FROM ...` on
the endpoint — reveals what to tune without journal grep.

## INTELLIGENCE ENGINES (2026-07-01→02 sprint)

Seven research-doc interventions shipped end-to-end + visualized on Mission Control.
Every engine is flag-gated OFF by default — runners write artifacts, cards render them,
operator validates before flipping the flag.

| # | Intervention | Runner cadence | Consumer flag | Status |
|---|---|---|---|---|
| 2 | Cross-niche hierarchical Bayes transfer | Weekly Mon 05:30 UTC | `GENLAB_CROSS_NICHE_TRANSFER_ENABLED` | Fully wired (2026-07-23 audit): runner writes priors weekly; consumer at `arm_loader.py:281` calls `get_transferred_prior()` on fresh style/transform arm creation |
| 5 | Trend anticipation (search/creator/social/news signals) | Daily 03:30 UTC | `GENLAB_TREND_ANTICIPATION_ENABLED` | Card ready-gate; pytrends dep fixed 2026-07-14 |
| 6 | Ensemble decision-making | Per-blueprint | `GENLAB_ENSEMBLE_DECISION_ENABLED` | Surfaces "worth_your_look" when components disagree |
| 7 | Doubly-robust reward estimator | Monthly 1st @ 04:30 UTC | `GENLAB_COUNTERFACTUAL_REPLAY_ENABLED` | Ridge model on LinUCB context ⊕ arm one-hot |
| 9 | Cyclical time context (v2) | Persist-side live | `GENLAB_TEMPORAL_CONTEXT_ENABLED` | 15-D vector w/ sin/cos time; LinUCB stays 13-D |
| 10 | Percentile-relative reward targets | Wired: `metric_collector.py`, `late_reward.py` | (already active) | — |
| A.2 | Top-creator upload watcher | 4× daily via `genlab-watch-top-creators.timer` | `GENLAB_TOP_CREATOR_PRIORS_ENABLED` (consumer, off) | Fetcher active 2026-07-14; consumer awaits 2wk correlation maturity |

Strategist — weekly LLM meta-analysis. Runner: `scripts/run_strategist.py`, Sundays
02:00 UTC. Card: `StrategistReportCard`. Per-proposal Accept/Reject/Submit review.

Full engine details, source paths, and per-intervention motivations:
`[[intelligence-state-audit-2026-07-16]]`.

## MISSION CONTROL CARD LINEUP

`StrategistReportCard`, `TrendAnticipationCard`, `TrendAnticipationAccuracyCard`,
`CrossNichePriorsCard`, `CounterfactualReplayCard`, `EnsembleBadge`,
`AutoExperimentsCard` (verdict + queue depth), `GateExaminationsCard` (top
failing check + threshold tune suggestion). Every card uses the "active vs
observation only" flag-state badge pattern.

## OBSERVABILITY (Sprint 63)

- **structlog** wraps stdlib logging — JSON in production, console in dev
- `PipelineMetrics` auto-records per-stage timing in `metrics.jsonl` per run
- Alerting thresholds in `genlab-core/config/alerting.yaml`
- 40 integration smoke tests (`pytest -m integration`)

---

## KEY INFRASTRUCTURE

### PostgreSQL (primary data store — Sprint 65)

Local PostgreSQL database `genlab` with psycopg3 (`psycopg[binary,pool]>=3.2`).
- **ConnectionPool** from `psycopg_pool` (replaces psycopg2 ThreadedConnectionPool)
- **dict_row** factory (replaces RealDictCursor)
- **Pipeline mode** for batch_create (30-50% faster bulk inserts)
- 55 indexes across all tables
- Alembic migrations in `genlab-core/migrations/`
- `GENLAB_USE_POSTGRES=true` + `DATABASE_URL` in `.env`
- RLS niche isolation via `SET LOCAL app.niche_id`

### SharePoint Lists (LEGACY — kept as fallback, not actively used)

Site + list IDs are in `.env` and `config/`. Migration to Postgres complete; SharePoint
paths remain wired as a fallback only. Do not add new writes here.

### YouTube API
- YOUTUBE_API_KEY = Data API v3 key (search + videos.list)
- Category 20 = Gaming, 17 = Sports, 1 = Film, 24 = Entertainment, 25 = News
- Anime: keyword search only (no native category)
- Quota: 10,000 units/day — search.list costs 100 units, videos.list costs 1 unit
- Max 50 search calls per day across all channels

### External Services
- LLM: Anthropic Claude Haiku (content writing), OpenAI GPT-Image-1 (visuals)
- TTS: ElevenLabs → OpenAI TTS → Edge-TTS → gTTS (cascade)
- Video: yt-dlp (download), FFmpeg (render), short-video-maker Docker (port 3123)
- Trends: pytrends (Google Trends unofficial wrapper)

---

## CODING STANDARDS

- Python 3.12+, strict typing, Pydantic v2 schemas
- All new shared code goes in genlab-core/src/genlab_core/
- Read existing patterns before writing new code (read backlog_client.py before
  writing a new client; read trending_video_fetcher.py before writing a new fetcher)
- Every new file needs tests in tests/ using unittest.mock for external calls
- Conventional commits: feat(scope), fix(scope), refactor(scope)
- Never put values in code that belong in config YAML
- All pipeline stages accept and return typed Pydantic models
- Log what every stage is doing, which path it took, and why

### Pre-commit workflow

`.pre-commit-config.yaml` pins the exact ruff version CI runs (v0.15.14 as of
2026-06-23). Activate once after clone: `pre-commit install` + `pre-commit run -a`.
Every commit then runs: `ruff check --fix` (I001 import-order + safe rules) +
`ruff format` + whitespace/EOF/YAML/private-key/gitleaks checks.

Non-negotiable because Edit-into-import-block breaks I001 order regularly. When
Editing imports: either run `ruff check --fix <file>` manually or rely on the hook.

### Test patterns for lazy-imported dependencies

1. **Patch the source module, not the importing module.** Lazy imports inside a
   function don't create attributes on the importing module — they look up bindings
   in the source's namespace at call time:
   ```python
   patch("genlab_core.http.backlog_client.BacklogClient")      # ✓
   patch("server.api.bandit_hour_posteriors.BacklogClient")    # ✗ AttributeError
   ```

2. **Autouse-fixture-stub expensive lazy-loaded DB calls.** Without a default stub,
   every test pays the 5s `connect_timeout` (22-test suite jumps from ~1s to ~85s).
   Pattern: `@pytest.fixture(autouse=True)` monkeypatching the source module getter
   to return `[]`; individual tests that assert behavior re-patch inside the test body.

---

## WHAT NOT TO DO

1. **Never add text-only renders** — not now, not as a fallback, not for any reason
2. **Never add new shared code to BlackboxBrief or CriticalRush** — use genlab-core
3. **Never modify core pipeline base classes** without explicit discussion
4. **Never hardcode credentials** — all secrets in .env files, never in code or YAML
5. **Never silent-fail** a platform publish — log warnings and write SKIPPED records
6. **Never use template strings** for captions ("Something big happened with [TITLE]")
7. **Never write a hook longer than 60 characters**
8. **Never create a blueprint for a scheduled future game** (sports: Final/In Progress only)
9. **Never ingest non-anime content into FrameDrift** — require anime keyword match
10. **Never publish more than 1 reel per channel per day**
11. **Never re-add `source_channel_id IS NOT NULL` as a Layer 5 attribution
    signal** — proxy for the wrong invariant. See attribution-defense-stack docs.
12. **Never lower `_HEALTHY_PCT` below 100 or `_CAUTION_PCT` below 99** in
    `attribution_health.py` — post-2026-07-13 tightening reflects that even
    1-in-20 uncredited is a real audience-facing failure.
13. **Never wire a new platform client without adding a Layer 4 call** —
    behavioral pin tests in `test_caption_validation.py` catch this.
14. **Never flip `LAYER4_BLOCK` or `LAYER3_ENFORCE` without a 24h
    observability window afterward** — in-flight blueprints could hard-fail.
15. **Never `chown root:root` state files that systemd services read/write**
    — the retro-credit state file lost 6h of progress overnight to this exact
    class-of-bug (silent PermissionError under fail-open error handler).
16. **Never treat "env var timestamp" as evidence of "token healthy"** —
    always live-probe the API. `check_meta_token` is the reference pattern;
    `check_threads` (PR #782) + `check_tiktok` (PR #788) applied it.
17. **Never elevate silent ImportError to fail-open without a WARNING log** —
    pytrends was a dormant no-op for months because `except ImportError:
    return None` had DEBUG-level logging. Elevate to WARNING minimum.
18. **Never store retro-credit success in memory only** — the
    `/opt/genlab/.runtime/retro_credit_state.json` file is the durable
    idempotency layer. Meta's write API is cache-lagged; read-back checks
    aren't reliable idempotency.
19. **Never swallow calibration/observability writes at DEBUG level** —
    `review_server.py:1443` masked 17 days of `calibration_logger`
    failures (2026-06-29 → 2026-07-16) because the fail-open handler
    was `logger.debug(...)`. Auto-approver ratchet requires FRESH
    samples — silent-dead writes stall the ratchet indefinitely.
    Use `logger.warning(..., exc_info=True)` minimum. Same class as rule #17.
20. **Never write to a secrets file without flock on a sidecar `.lock`** —
    `.threads_tokens.json` and Twitter quota state both use
    `fcntl.flock(LOCK_EX)` on a sidecar lock to serialise concurrent
    systemd-timer + manual-operator invocations. Without it, parallel writers
    race on the `.tmp` path and one write silently loses.
21. **Never leave a weekly timer at `Persistent=false`** — the strategist timer
    was `Persistent=false` for ~5 weeks so any Sunday when the VPS was down
    silently skipped the meta-analysis until next Sunday. `Persistent=true`
    catches up missed fires on next boot. Reserve `Persistent=false` for
    frequent timers (hourly / every-N-minutes) where a catch-up thundering
    herd would be worse than a skip.
22. **Never treat "agreement %" alone as calibration signal — always look at
    the confusion matrix (TP/TN/FP/FN)**. 2026-07-17 lesson: moved auto-approver
    enrollment from ai_creators (real 92.1%) to gaming (real 53.4%, 47 FN)
    based on a broken query comparing `operator_action = 'approve'` against
    actual DB values of `'approved'`. Reverted 20 min later. **Detection**:
    always SELECT DISTINCT the operator_action column FIRST to see the real
    value set. Never trust a single-number agreement metric without the breakdown.
23. **The 4-platform focus (YT / FB / IG / Threads) is the north star** —
    TikTok and X/Twitter are explicitly out of scope per 2026-07-17 operator
    directive. Auto-approver enforcement, content variety experiments,
    monetization funnel work, and reliability fixes prioritize those 4.
    Historical Twitter+TikTok infrastructure stays in tree for when scope
    re-opens; no new features build against them.
24. **Growth targets are 100K followers, millions of views/week, and $1M/month
    affiliate revenue per channel** — codified from 2026-07-17 operator strategy
    call. Currently at ~0.1-1% of these numbers. Any code change that doesn't
    move one of these three needles is deprioritised.
    See `[[strategic-analysis-2026-07-17-growth-targets]]` for full gap-sizing +
    4-layer upgrade roadmap.
25. **Never use `urllib.request` without an explicit User-Agent header** —
    WAF-fronted APIs (Cuelinks V3 confirmed 2026-07-17) fingerprint the default
    `Python-urllib/X.Y` as a bot and return HTTP 403. Set an identifiable UA
    like `GenLab/1.0 (+https://github.com/...)`. Debug pattern: `curl -v` the
    same URL — if curl 200 + python 403, it's WAF-UA.
    See `[[class-of-bug-waf-blocks-default-python-urllib-user-agent]]`.
26. **Scripts invoked by systemd MUST distinguish "hard error" (exit non-zero)
    from "partial success" or "no work available" (exit 0 + WARN log)** —
    hit 7 times in one week (2026-07-14 → 2026-07-21): nightly_schedule
    (2 iterations), publisher timeout, shared_ingestion timeout,
    outbound_reply_engine, check_affiliate_links, scrape_affiliate_revenue,
    post_deploy_verify version.env path. Common shape: script inherits CLI
    exit-code semantics ("exit 1 if anything failed") but systemd treats
    any non-zero as `Result: exit-code` → OnFailure fires
    `genlab-service-failure-alert` → `service_down` CRITICAL every timer
    fire. False alarms obscure real signals. Rule: **exit 0 unless a
    genuine incident requires operator paging**. Broken URLs / empty
    queues / missing deps / partial completion are DATA-side signals
    that operator sees via stdout logs OR dashboard, NOT via systemd
    exit code. Detection heuristic:
    `sudo journalctl --since '24 hours ago' | grep 'code=exited, status=[1-9]' | grep -oE 'genlab-.+\.service' | sort -u`.
    Any service showing up daily = rule #26 candidate. Pin tests that
    grep the source for exit-code shape prevent regression.
27. **Never trust `SET set_config('app.niche_id', X)` + RLS policy alone
    for niche isolation — always inject explicit `AND niche_id = %s`
    into the WHERE clause** (belt-and-suspenders). The `genlab` Postgres
    role has `Bypass RLS` attribute in prod, so every table-level RLS
    policy is silently no-op'd at query time. Discovered 2026-07-24 via
    the `gate_examinations` diagnostic: same gaming blueprint attributed
    to `ai_creators` + `sports` niches for weeks. Auto-approver had been
    gate-evaluating cross-niche blueprints under wrong policy. Fixed in
    `PostgresBackend.find/update/delete` (commits `11014425` + `99defd0a`).
    See `[[class-of-bug-rls-bypass-role-attribute-defeats-tenant-isolation]]`
    for the detection heuristic. Sibling but distinct from the "34 psycopg
    bypass sites" — that class bypasses `pg_connect` entirely; THIS class
    correctly uses `pg_connect` but the role bypass makes it moot.
28. **Every DB column added via alembic MUST be added to
    `PROMOTED_COLUMNS[table]` in `genlab_core/storage/postgres.py`** —
    otherwise `PostgresBackend._split_fields()` silently routes writes
    into the `extra` JSONB column, leaving the dedicated column NULL.
    Every downstream `WHERE column = X` filter misses those rows.
    Discovered 2026-07-24: `action_taken_source` (23 rows silent),
    `hook_classifier_score` (119 rows silent), `variant_type` +
    `variant_payload` (default-populated but write path broken). Schema
    pin in `test_promoted_columns_vs_db_schema.py` runs against prod DB
    when `GENLAB_SCHEMA_PIN_DSN` is set — catches this at CI time.
    See `[[class-of-bug-column-in-db-not-in-promoted-columns]]`. Backfill
    tool: `scripts/backfill_column_from_extra.py`.
29. **Env flag flip on VPS MUST be preceded by VPS-HEAD == origin-HEAD
    verify** — otherwise the flag is set but the code that READS it
    isn't deployed → silent no-op. Hit 2026-08-18 tonight: 3 canary
    flags flipped between 12:19-12:24 IST, VPS still at pre-shipping
    commit `279efdb1` for ~2h while I claimed "canary LIVE". Every
    pipeline fire in that window ran old code that ignored the flags.
    Correct sequence: `git push origin main` → `ssh vps "cd /opt/genlab
    && git pull"` → `ssh vps "git rev-parse HEAD"` verify matches
    origin → then flip flag → then restart service. GenLab does NOT
    auto-pull (deploy tooling is `deploy/scripts/deploy.sh` scp-based,
    kernel-level `git pull` is manual). Detection: any end-of-session
    audit MUST include the HEAD-drift check as first assertion.
    See `[[class-of-bug-flag-flip-without-code-deploy-verify]]`.
30. **Model/asset registry pattern is the canonical way to add
    inference.sh app diversity** — 2026-08-18 crystallised this
    across `media/hook_thumbnail_models.py` (image) and
    `media/pruna_video_client_models.py` (video). Shape: dataclass
    `ImageModel(model_id, belt_app, build_input, cost_per_X_usd)`
    + `_REGISTRY: tuple[ImageModel, ...]` + `pick_model(seed_str,
    niche_id)` deterministic hash + `multi_model_enabled()` env
    flag gate + `extract_url(output)` shape-tolerant helper. Adding
    a 7th model family costs <1h because none of subprocess handling,
    cost telemetry, fail-open cascade, or canary flag scaffolding
    needs re-designing. Every future integration should follow this
    template — see `session-2026-08-18-fifteen-commit-inference-sh-arc.md`
    for the 6 modules that share this shape.

---

## SAAS ARCHITECTURE CONSIDERATIONS

Every decision should consider multi-tenancy:
- Use niche_id on all writes (tenant isolation)
- No hardcoded account IDs — read from publishing.yaml per niche
- Config-driven: new brand = new YAML config, not new code
- Future: PostgreSQL RLS is niche-blind today (34 psycopg bypass sites per
  `[[deeper-cuts-audit-2026-07-16]]`); tenant-level isolation is a Phase 2 blocker

---

## CHANNEL ACCENT COLORS & LOGOS

| Channel | niche_id | Accent Color | Logo |
|---|---|---|---|
| Blackbox Brief | ai_creators | #00D4FF | BlackboxBrief/assets/logos/blackbox_brief.png |
| CriticalRush | gaming | #f97316 | CriticalRush/niches/gaming/assets/criticalrush_logo.png |
| ClutchWire | sports | #FF2040 | ClutchWire/assets/logos/ClutchWire-Logo.png |
| SpliceReel | movies | #C9A84C | SpliceReel/assets/logos/SpliceReel-Logo.png |
| FrameDrift | anime | #7B3FE4 | FrameDrift/assets/logos/FrameDrift-Logo.png |

FrameDrift is ANIME (not fashion — that was a legacy description bug fixed in Sprint 47).
