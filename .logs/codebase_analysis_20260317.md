# GenLab Codebase Analysis
**Date:** 2026-03-17
**Scope:** Comprehensive audit of every top-level directory and root file

---

## Table of Contents

1. [Root-Level Overview](#1-root-level-overview)
2. [genlab-core/ (Shared Library)](#2-genlab-core)
3. [Content Scraper/ (Blackbox Brief)](#3-content-scraper)
4. [CriticalRush/ (Gaming)](#4-criticalrush)
5. [ClutchWire/ (Sports)](#5-clutchwire)
6. [SpliceReel/ (Movies)](#6-splicereel)
7. [FrameDrift/ (Anime)](#7-framedrift)
8. [dashboard/ (Operations Dashboard)](#8-dashboard)
9. [scripts/ (Shared Utility Scripts)](#9-scripts)
10. [OpenSandbox/ (Sandboxing)](#10-opensandbox)
11. [docs/ (Documentation)](#11-docs)
12. [docker/ (Docker Configs)](#12-docker)
13. [Hidden/Config Directories](#13-hidden-config-directories)
14. [Root-Level Files](#14-root-level-files)
15. [Architecture Debt & Issues Summary](#15-architecture-debt-issues-summary)

---

## 1. Root-Level Overview

The GenLab workspace is a **uv workspace** (defined in `/pyproject.toml`) containing 7 member packages: `genlab-core`, `dashboard`, `Content Scraper`, `CriticalRush`, `ClutchWire`, `SpliceReel`, `FrameDrift`. A single `uv.lock` at root provides a unified lockfile.

### Submodule Structure

Five directories are git submodules with their own `.git/`: `Content Scraper`, `CriticalRush`, `ClutchWire`, `SpliceReel`, `FrameDrift`, `scripts`, and `genlab-core`. The parent GenLab repo tracks them as submodules.

---

## 2. genlab-core/

**Purpose:** Shared infrastructure library imported by all 5 niche channels. v0.1.0, hatchling build, src-layout.

**Lines of code:** ~36,100 (190 source files)
**Test files:** 146 test files (~695 tests passing)

### Directory Structure (3 levels)

```
genlab-core/
  .importlinter             # Layer boundary enforcement
  pyproject.toml            # hatchling build, optional deps groups
  config/                   # Shared YAML configs
    alerting.yaml           # Threshold definitions
    disk_quota.yaml         # Disk usage quotas (symlinked from CS)
    lists_config.yaml       # SharePoint list IDs (symlinked from CS)
    monetisation_targets.yaml
    platform_caps.yaml      # Daily post caps per platform (symlinked from CS)
    platform_encode_specs.yaml  # Per-platform encode overrides
    storage.yaml
    storage_backends.yaml
  runbooks/                 # 25 launchd plist files
    com.genlab.engagement-poller.plist
    com.genlab.metric-collector.plist
    com.genlab.quota-monitor.plist
    com.genlab.engagement.poller.{youtube,twitter}.{sports,movies,anime,ai-news}.plist
    com.genlab.fetch-insights-{niche}-{48h,168h}.plist
    com.genlab.{niche}-publisher.plist
  src/genlab_core/
    __init__.py             # Lazy loader for 20 submodules
    analytics/              # YouTube Analytics API v2 client
    auth/                   # Auth models (Pydantic)
    cache/                  # stable_ids, text_sanitizer, disk_cache
    context.py              # PipelineContext dataclass
    cost/                   # model_router (LiteLLM cost routing)
    engagement/             # 8-step reply pipeline + platform clients
      comment_processor.py  # Core reply pipeline
      persona_engine.py     # Claude Haiku reply generation
      persona_schema.py     # Persona YAML schema
      platform_clients/     # youtube_reply, instagram_reply, facebook_reply, twitter_reply, threads_reply
      poller.py             # YouTube/X/FB comment polling
      rate_limiter.py       # Token bucket per platform
      spam_filter.py        # Spam detection
      tasks.py              # Dramatiq actors
      timing.py             # Jitter + scheduling
      toxicity_gate.py      # Detoxify local model
      webhook.py            # FastAPI webhook receiver
    exceptions.py           # Custom exceptions
    feedback/               # hook_analyzer
    http/                   # backlog_client, async_bridge, retry, graph_proxy, circuit_breaker
    intel/                  # google_trends, mlb_fetcher, reddit_fetcher
    intelligence/           # budget_guard, cost_accumulator, dedup_engine, hook_validator, otel_exporter, score_normalizer
    interfaces/             # TTS interface protocol
    learning/               # 14 modules: arm_loader, config_updater, config_writer, hook_classifier, hook_features, hook_training_data, linucb, meta_prior, metric_collector, pending_feedback_store, pending_feedback_task, reward_shaper, config_update_flow
    media/                  # 14 modules: audio_probe, download_top_videos, egress_policies, ffmpeg.py, ffmpeg_utils.py, frame_compositor, quota_manager, relevance_filter, sandbox_runner, smart_crop, trending_video_fetcher, video_compositor, video_sourcer, video_validator, whisper_timing
    models/                 # Pydantic models
    monitoring/             # check_token_health, monetisation_tracker, run_monetisation_tracker, token_health, youtube_quota
    niche_loader.py         # Niche config loading from YAML
    observability/          # structlog logging, metrics_writer (JSONL per-stage timing)
    orchestration/          # dashboard_routes, deployment_manager, models, prefect_api, socketio_poller, task_factory
    pipeline/               # pipeline_runner, stage_runner, log_streamer
      stages/               # 16 shared stages: express_lane, fetch_anime_promos, fetch_insights, fetch_scorebat, fetch_steam_trailers, fetch_tmdb_trailers, fetch_twitch_clips, generate_audio, performance_learner, push_to_backlog, qc_gates, render_text_overlays, render_whisper_captions, run_report, validate_videos, video_gate, virality_scoring
    platforms/              # 13 modules: dispatcher, engagement/ (engine, _worker), facebook, gatekeeper, instagram, models, postiz, protocols, registry, rules, threads, tiktok, x_twitter, youtube
    publishing/             # 8 modules: analytics_recorder, cdn_upload, daily_cap, niche_credentials, publish_all_platforms, scheduling, threads_client, tiktok_client
    ratelimit/              # token_bucket, domain_limiter
    rendering/              # overlay_compositor, video_renderer, word_animator
    scoring/                # composite_scorer
    scripts/                # run_fetch_insights.py CLI
    settings.py             # Pydantic settings, env loading, _PROJECT_ROOT
    storage/                # 8 modules: disk_quota, factory, formula_sql, migrate_table, postgres, protocol, quota_daemon, sharepoint
    strategies.py           # 6 abstract strategy classes (ContentResearch, Scoring, Writing, Hooks, VisualRender, PlatformAdaptation)
    testing/                # Test utilities
    tools/                  # safe_push, credential_check, create_niche, validate_configs
    tts/                    # TTSCascade + 4 providers (ElevenLabs, OpenAI, Edge, gTTS)
    utils/                  # env.py, text_sanitizer.py
    video/                  # standards.py (video quality constants)
    writing/                # llm_client, llm_hook_generator, video_content_writer
  tests/                    # 146 test files across 20 subdirectories
```

### Key Modules

| Module | Purpose | Status |
|--------|---------|--------|
| `pipeline/pipeline_runner.py` | Generic niche-agnostic pipeline orchestrator | Active, core |
| `pipeline/stage_runner.py` | Stage execution with timing, parallel groups, sandbox support | Active, core |
| `http/backlog_client.py` | SharePoint MS Graph CRUD for Blueprints/Stories/Analytics | Active, core |
| `media/trending_video_fetcher.py` | YouTube Data API v3 trending clip fetcher | Active, Sprint 48 |
| `media/video_compositor.py` | FFmpeg sandwich/pillarbox video rendering | Active, core |
| `media/frame_compositor.py` | 3-mode frame layout (landscape/portrait/square) | Active, Sprint 62 |
| `publishing/publish_all_platforms.py` | Canonical multi-platform publisher (526 LOC) | Active, Sprint 62 |
| `publishing/niche_credentials.py` | Per-niche credential resolution, cross-publish guard | Active, Sprint 62 |
| `publishing/daily_cap.py` | DailyCapEnforcer per-niche | Active |
| `engagement/comment_processor.py` | 8-step automated reply pipeline | Active, Sprint 17 |
| `learning/linucb.py` | LinUCB contextual bandit (6D features) | Active, Sprint 63 |
| `learning/metric_collector.py` | Prefect flow for 4-window feedback collection | Active |
| `storage/postgres.py` | PostgreSQL storage backend (Phase 0-1 scaffolding) | New, Sprint 63 |
| `platforms/postiz.py` | Postiz social scheduler client (shadow evaluation) | Inactive, under eval |

### Issues Found

1. **`utils/text_sanitizer.py` duplication**: Both `genlab_core/utils/text_sanitizer.py` AND `genlab_core/cache/text_sanitizer.py` exist. The `cache` version appears to be the canonical one; the `utils` version may be a leftover.
2. **`orchestration/` package large surface**: 6 modules (dashboard_routes, deployment_manager, models, prefect_api, socketio_poller, task_factory) -- some may be underutilized stubs from Prefect integration work.
3. **PostgreSQL storage** (`storage/postgres.py`, `storage/sharepoint.py`, `storage/factory.py`) is Phase 0-1 scaffolding; the production system still uses SharePoint exclusively.
4. **`platforms/postiz.py`**: Postiz integration is disabled/under shadow evaluation (review date 2026-04-07). Dead code in production.

---

## 3. Content Scraper/ (Blackbox Brief)

**Purpose:** Blackbox Brief (AI creators) niche channel. The oldest and largest channel. Also houses legacy shared infrastructure that other channels historically depended on (known architecture debt).

**Lines of code:** ~55,000 (111 execution files)
**Test files:** 86 test files (~1,397 tests passing)

### Directory Structure (2 levels)

```
Content Scraper/
  .env, .env.template       # Per-niche credentials
  run_pipeline.py            # BB pipeline entry point
  pyproject.toml             # uv workspace member
  CLAUDE.md                  # 500+ line project instructions
  core/                      # BB-specific business logic
    __init__.py
  bb_strategies/             # 11 files: strategy wrappers for unified pipeline
    __init__.py, compose.py, content_research.py, hooks.py, platform_adaptation.py,
    push_backlog.py, qc_gates.py, scoring.py, visual_render.py, write_post_content.py, writing.py
  config/                    # 31 YAML configs (BB-specific + symlinks to genlab-core)
    niche.yaml, sources.yaml, scoring_weights.yaml, templates.yaml, visuals.yaml,
    publishing.yaml, schedule.yaml, persona.yaml, content_prompts.yaml, ...
    disk_quota.yaml -> ../../genlab-core/config/disk_quota.yaml (symlink)
    lists_config.yaml -> ../../genlab-core/config/lists_config.yaml (symlink)
    platform_caps.yaml -> ../../genlab-core/config/platform_caps.yaml (symlink)
  execution/                 # ~95 pipeline scripts (the bulk of BB logic)
    fetch_ai_creators.py     # RSS + YouTube creator clip search
    compose_blueprints.py    # Story x template -> candidates
    generate_content.py      # LLM content writing
    generate_hooks.py        # Formula-driven hook generation
    render_visuals.py        # VideoCompositor + FFmpeg rendering
    render_text_overlays.py  # Text overlay burning (3,800+ LOC, largest file)
    publish_all_platforms.py # Legacy multi-platform publisher (2,407 LOC)
    push_to_backlog.py       # SharePoint upsert
    check_token_health.py    # Pre-flight token checks
    stages/                  # 2 render pipeline stages
    sources/                 # 6 source connectors (X, YouTube, HF Spaces, Civitai, Playwright, registry)
    ugc/                     # 7 UGC source adapters (reddit, pexels, pixabay, youtube, unsplash, gallery, submission)
    utils/                   # 24 utility modules
    archive/                 # 1 file: assemble_reel.py (DEPRECATED, Ken Burns assembler)
  directives/                # SOPs (01-10): operational procedures
  schemas/                   # 11 JSON schemas (contracts for pipeline data)
  templates/                 # Slide templates + fonts
  inspo_library/             # Controlled Instagram pattern dataset
  assets/                    # logos/, music/, people/ (brand assets)
  runbooks/                  # 5 plist files + 7 shell scripts (cron, publisher, orchestrator)
  tests/                     # 86 test files + fixtures/ + golden/
  setup/                     # generate_launchd_plists.py
  docker/                    # short-video-maker Docker config
  configs/                   # (appears to be separate from config/ -- may be stale)
```

### Key Files

| File | LOC | Purpose | Status |
|------|-----|---------|--------|
| `execution/render_text_overlays.py` | ~3,800 | Text overlay rendering + whisper captions | Active, largest file |
| `execution/publish_all_platforms.py` | ~2,400 | Legacy BB publisher (predates canonical) | Active but duplicative |
| `execution/generate_hooks.py` | ~1,200 | Formula-driven hook generation with scoring | Active |
| `execution/compose_blueprints.py` | ~1,000 | Story x template blueprint composition | Active |
| `execution/fetch_ai_creators.py` | ~900 | RSS + YouTube source fetching | Active |

### Shim Files (Backward-Compat Wrappers -> genlab-core)

| CS File | Points To |
|---------|-----------|
| `execution/utils/niche_credentials.py` | `genlab_core.publishing.niche_credentials` |
| `execution/utils/scheduling.py` | `genlab_core.publishing.scheduling` |

### Issues Found

1. **MAJOR: Dual publisher**: `execution/publish_all_platforms.py` (2,407 LOC) coexists with `genlab-core/src/genlab_core/publishing/publish_all_platforms.py` (526 LOC). The CS version is the legacy BB-specific publisher; the genlab-core version is the canonical one. Both are active. This is the **single largest source of duplication** in the codebase.

2. **Architecture debt (documented)**: Content Scraper houses shared infrastructure that other channels depend on. The `execution/` directory contains modules that are imported by CriticalRush/dashboard via sys.path manipulation. Migration to genlab-core is partially complete (shims exist) but many modules remain in CS.

3. **`configs/` vs `config/`**: Two config directories exist. `config/` is the active one; `configs/` purpose is unclear (may be stale).

4. **Deprecated files still present**:
   - `execution/archive/assemble_reel.py` (Ken Burns assembler, explicitly deprecated)
   - `execution/utils/background_animator.py` (deprecated, Ken Burns + gradient)
   - `execution/qc_claims_validator.py` (deprecated, logic moved to run_qc_gates.py)
   - `execution/publish_twitter.py` (deprecated per docstring, prefer publish_all_platforms.py)

5. **`.tmp/` scripts**: 16+ one-off Python scripts in `.tmp/` (airtable_audit.py, batch_visual_ready.py, etc.) -- ephemeral but not cleaned up.

6. **Large execution directory**: 95+ Python files in `execution/` with unclear separation between active pipeline scripts and historical utilities.

---

## 4. CriticalRush/ (Gaming)

**Purpose:** Gaming niche pipeline runner. Houses gaming-specific stages, tools, learning system, and the multi-niche pipeline orchestrator `NICHE_ROOTS`.

**Lines of code:** ~11,950 (60 source files)
**Test files:** 42 test files

### Directory Structure (3 levels)

```
CriticalRush/
  .env, .env.example         # Gaming credentials
  .engagement_replied.jsonl   # Engagement reply log
  pyproject.toml              # uv workspace member
  CLAUDE.md
  core/
    pipeline_runner.py        # Gaming-only pipeline runner (NICHE_ROOTS = {"gaming": self})
  niches/
    _template/                # New niche scaffolding template
      config/                 # Template YAML configs
      stages/                 # publish_content.py template
      strategies/             # content_research.py, scoring.py templates
    ai_creators/              # BB flow wrappers (thin)
      flows/
        ai_creators_flow.py   # BB Prefect flow definition
    gaming/
      assets/                 # Gaming brand assets
      config/                 # 17 YAML configs (niche, sources, visuals, publishing, scoring_weights, etc.)
      flows/                  # gaming_flow.py, spike_detector_flow.py (Prefect)
      hooks/                  # hook_validator.py (gaming-specific)
      learning/               # 4 files: bandit.py, bandit_store.py, feedback_collector.py, hook_predictor.py
      media/                  # Gaming-specific FFmpeg (ffmpeg_gaming.py referenced in CLAUDE.md)
      models/                 # Gaming data models
      schemas/                # Gaming JSON schemas
      stages/                 # 13 gaming pipeline stages
        fetch_gaming_stories.py, filter_gaming_stories.py, score_gaming_clips.py,
        enrich_with_igdb.py, extract_gaming_media.py, write_gaming_content.py,
        adapt_gaming_content.py, render_text_overlays.py, generate_gaming_audio.py,
        push_to_backlog.py, publish_gaming_content.py, learn_performance.py,
        write_run_report.py
      tools/                  # 11 gaming tools
        audio_analyzer.py, ass_subtitle_generator.py, bandit_optimizer.py,
        caption_generator.py, chat_excitement_scorer.py, clip_sourcer.py,
        compilation_planner.py, crispy_scorer.py, igdb_client.py,
        platform_bandit_manager.py, schema_validator.py, video_hasher.py,
        _twitch_auth.py
  tools/
    publishing/               # cdn_upload.py, threads_client.py, tiktok_client.py
  scripts/                    # test_api_connections.py, test_igdb_connection.py, test_steam_connection.py, test_twitch_connection.py, collect_feedback.py, test_content_generation.py
  setup/                      # create_launchd_plist.py
  runbooks/                   # 7 plist files (spike-detector, feedback, prefect, publisher, token-refresh, cleanup)
  tests/                      # 42 test files across gaming/, orchestration/, publishing/, pipeline/
  config/                     # (appears to be CR-level config, separate from niches/gaming/config/)
```

### Key Files

| File | Purpose | Status |
|------|---------|--------|
| `core/pipeline_runner.py` | Gaming-only pipeline entry point | Active |
| `niches/gaming/stages/score_gaming_clips.py` | Gaming clip scoring (NOT score_gaming_stories.py) | Active |
| `niches/gaming/learning/bandit.py` | Thompson Sampling bandit for gaming | Active |
| `niches/gaming/tools/igdb_client.py` | IGDB game database client | Active |
| `niches/gaming/tools/clip_sourcer.py` | Video clip discovery + download | Active |
| `niches/gaming/flows/gaming_flow.py` | Prefect flow definition for gaming | Active |
| `tools/publishing/threads_client.py` | Threads publishing client | Active |
| `tools/publishing/tiktok_client.py` | TikTok publishing client | Active |

### Issues Found

1. **`tools/publishing/` duplication**: `threads_client.py` and `tiktok_client.py` exist both in `CriticalRush/tools/publishing/` AND `genlab-core/src/genlab_core/publishing/`. The genlab-core versions are canonical; the CR versions may be stale.

2. **`niches/ai_creators/`**: Contains a thin Prefect flow wrapper for BB. This cross-niche reference (gaming runner knowing about ai_creators) is documented architecture debt.

3. **`niches/_template/`**: Niche scaffolding template. Useful but only contains partial strategy stubs (2 files) vs the 6 strategies defined in genlab-core.

4. **`core/pipeline_runner.py` NICHE_ROOTS**: Only maps `gaming`, despite the comment in CLAUDE.md referencing all 5 niches. The multi-niche orchestration has been deprecated in favor of per-channel `run_pipeline.py` entry points.

5. **Prefect flows** (`gaming_flow.py`, `spike_detector_flow.py`, `ai_creators_flow.py`): May be partially unused if Prefect server is not running consistently.

---

## 5. ClutchWire/ (Sports)

**Purpose:** Sports niche channel. Follows the standardized niche structure.

**Test files:** 14 test files (88 tests passing)

### Directory Structure

```
ClutchWire/
  __init__.py, .env, .gitignore
  run_pipeline.py             # Thin wrapper around GenericPipelineRunner
  pyproject.toml, CLAUDE.md, README.md
  config/                     # 13 YAML configs
    niche.yaml, sources.yaml, visuals.yaml, publishing.yaml, schedule.yaml,
    scoring_weights.yaml, templates.yaml, writing.yaml, content_prompts.yaml,
    lists_config.yaml, monetization.yaml, persona.yaml, platform_algorithms.yaml
  cw_strategies/              # 10 strategy modules
    __init__.py, content_research.py, fetch_sports_news.py, hooks.py,
    platform_adaptation.py, publishing.py, push_to_backlog.py, scoring.py,
    visual_render.py, writing.py
  hooks/                      # __init__.py (hook utilities)
  assets/                     # logos/ (ClutchWire-Logo.png), ClutchWire-Cover.png
  runbooks/                   # 2 plists + daily_intel.sh
  tests/                      # 14 test files
  .logs/, .tmp/               # Runtime artifacts
```

### Key Characteristics

- **Clean structure**: Follows the standard niche pattern precisely
- `run_pipeline.py` is 68 lines -- thin wrapper around `GenericPipelineRunner`
- Strategy classes prefixed `cw_strategies/` to prevent sys.modules collisions
- `video_gate: require` enforced in config

### Issues Found

1. **No `film_lifecycle.py` equivalent**: Unlike SpliceReel's `film_lifecycle.py`, ClutchWire has no game lifecycle tracking (e.g., tournament stages, season status). May be a feature gap.
2. **`hooks/` directory**: Contains only `__init__.py` -- may be a stub or the hook logic lives entirely in `cw_strategies/hooks.py`.

---

## 6. SpliceReel/ (Movies)

**Purpose:** Movies/film niche channel. Has OMDb enrichment unique to this niche.

**Test files:** 16 test files (96 tests passing)

### Directory Structure

```
SpliceReel/
  __init__.py, .env, .gitignore
  run_pipeline.py             # Thin wrapper
  pyproject.toml, CLAUDE.md, README.md
  config/                     # 13 YAML configs (same pattern as ClutchWire)
  sr_strategies/              # 11 strategy modules
    __init__.py, content_research.py, fetch_film_news.py, film_lifecycle.py,
    hooks.py, omdb_client.py, platform_adaptation.py, publishing.py,
    push_to_backlog.py, scoring.py, visual_render.py, writing.py
  hooks/                      # __init__.py
  assets/                     # logos/ (SpliceReel-Logo.png), SpliceReel-Cover.png
  runbooks/                   # 2 plists + daily_intel.sh
  tests/                      # 16 test files
  .logs/, .tmp/               # Runtime artifacts
  .venv/                      # Local venv (Python 3.14, mostly empty)
```

### Key Characteristics

- `omdb_client.py`: Unique to SpliceReel -- selective OMDb enrichment for items with score >= 0.45
- `film_lifecycle.py`: Tracks movie release stages (pre-release, theatrical, streaming, etc.)
- Has its own `.venv/` (mostly empty, uses workspace venv)

### Issues Found

1. **Stale `.venv/`**: SpliceReel has a local `.venv/` directory that appears to be an empty Python 3.14 virtualenv. Should use workspace venv exclusively.
2. **`hooks/` directory**: Same pattern as ClutchWire -- only `__init__.py`.

---

## 7. FrameDrift/ (Anime)

**Purpose:** Anime niche channel. RSS-primary with Google Trends integration.

**Test files:** 15 test files (108 tests passing)

### Directory Structure

```
FrameDrift/
  __init__.py, .env, .gitignore
  run_pipeline.py             # Thin wrapper
  pyproject.toml, CLAUDE.md, README.md
  config/                     # 13 YAML configs
    sources.yaml              # RSS sources + google_trends.stub_mode flag
  fd_strategies/              # 11 strategy modules
    __init__.py, content_research.py, fetch_anime_news.py, hooks.py,
    platform_adaptation.py, publishing.py, push_to_backlog.py, scoring.py,
    trend_cycle.py, visual_render.py, writing.py
  hooks/                      # __init__.py
  assets/                     # logos/ (FrameDrift-Logo.png), FrameDrift-Cover.png
  runbooks/                   # 2 plists + daily_intel.sh
  tests/                      # 15 test files
  .logs/, .tmp/               # Runtime artifacts (includes test_video_dl/ with test media)
  .venv/                      # Local venv (Python 3.14, mostly empty)
```

### Key Characteristics

- `trend_cycle.py`: Anime-specific trend detection (seasonal cycles, series hype curves)
- `fetch_anime_news.py`: RSS primary + YouTube keyword search (no native anime YouTube category)
- Google Trends integration activated but behind `stub_mode` flag in sources.yaml
- `.tmp/test_video_dl/`: Contains test video download artifacts

### Issues Found

1. **Stale `.venv/`**: Same issue as SpliceReel -- has local empty venv.
2. **`google_trends.stub_mode: true`**: Still in stub mode. Real pytrends queries not activated.

---

## 8. dashboard/ (Operations Dashboard)

**Purpose:** Shared operations dashboard serving all 5 niches. React 19 frontend + Flask backend.

**Test files:** 20 test files

### Directory Structure (3 levels)

```
dashboard/
  pyproject.toml              # uv workspace member, depends on genlab-core
  CLAUDE.md
  .gitignore
  configs/
    niches_registry.yaml      # All 5 niches registered
  data/
    scheduler.db              # SQLite scheduler state
  frontend/                   # React 19 + TypeScript + Vite
    package.json, vite.config.ts, tsconfig.json
    dist/                     # Built frontend (42+ asset files)
    public/                   # PWA manifest, service worker, icons
    worker/                   # Cloudflare Worker (edge proxy)
    src/
      api/                    # client.ts, socket.ts, query-keys.ts, types.ts
      views/                  # 12+ view components
        analytics/, blueprints.tsx, channel-health/, focus-review.tsx,
        mission-control/, monetisation/, pipeline/, publishing-queue/,
        runs.tsx, schedule.tsx, settings.tsx, stories.tsx
      components/
        blueprints/           # 7 components (card, detail, comparison, inline editor, platform preview, review actions, version diff)
        charts/               # 14 chart components (audience growth, engagement, KPI, monetization, etc.)
        layout/               # 9 layout components (shell, sidebar, command palette, notification center, etc.)
        review/               # 3 focus review components
        schedule/             # 6 scheduling components (calendar, drag-card, assign slot, etc.)
        shared/               # 5 shared components (data-table, filter-bar, status-badge, empty-state, carousel-viewer)
        ui/                   # 12 shadcn/ui primitives
      hooks/                  # 22 custom React hooks
      stores/                 # 4 Zustand stores (command-palette, niche, notification, selection)
      niches/                 # registry.ts + 3 detail views
      design-system/          # chart-tokens.ts, tokens.css
      lib/                    # 7 utility modules
      shell/                  # command-registry.ts
  server/                     # Flask + Flask-SocketIO
    review_server.py          # Main app (1,605 LOC)
    api/                      # 22 REST API route modules
      analytics.py, blueprints.py, config_routes.py, engagement.py, health.py,
      learning.py, legal.py, monetisation.py, niches.py, overview.py, pipeline.py,
      platform_posts.py, publishing_queue.py, runway.py, schedule.py, scheduler.py,
      stories.py, token_health.py, webhook_receiver.py, youtube_quota.py
    core/                     # 4 core modules
      graph_sync.py, publishing_queue.py, responses.py, scheduler.py
    middleware/                # auth.py (Basic Auth)
  runbooks/
    review_server_wrapper.sh  # Gunicorn launcher for launchd
  tests/                      # 20 test files
  .hypothesis/, .venv/
```

### Key Characteristics

- **Full-stack**: React 19 frontend with 12 views, 14 chart types, 22 hooks
- **22 API routes**: Comprehensive REST API under `/api/v1/`
- **Real-time**: WebSocket via Flask-SocketIO for live pipeline updates
- **PWA**: Service worker + manifest for offline/installable app
- **Cloudflare Worker**: Edge proxy in `frontend/worker/`

### Issues Found

1. **`review_server.py` at 1,605 LOC**: This is the main Flask app. It's large and monolithic. Route registration, auth, static serving, and WebSocket handling are all in one file. The `api/` modules help but the server file itself is oversized.

2. **2 pre-existing test failures**: `TestMediaRoute` tests documented as failing.

3. **Dual `analytics.tsx`**: Both `views/analytics.tsx` and `views/analytics/Analytics.tsx` exist. The former may be a re-export or stale.

4. **`data/scheduler.db`**: SQLite database committed to the repo. Should be gitignored.

---

## 9. scripts/ (Shared Utility Scripts)

**Purpose:** Shared intelligence and maintenance scripts serving all niches.

### Files (27 files, non-git)

```
scripts/
  .gitignore
  # Intelligence Scripts
  intelligence_hub.py         # Central intelligence aggregator (KNOWN_NICHES)
  morning_briefing.py         # Daily briefing generator
  social_analytics.py         # Social media analytics (BB credentials only)
  trend_signals.py            # Google Trends signal aggregator
  viral_detector.py           # Virality prediction
  posting_optimizer.py        # Optimal posting time calculator
  content_memory.py           # Content deduplication memory
  token_health.py             # Cross-niche token health checker

  # Maintenance Scripts
  backfill_analytics_niche_id.py
  backfill_content_memory_niche_id.py
  backfill_publishing_analytics_niche_id.py
  backfill_publishing_analytics.py
  bandit_analysis.py
  bulk_approve_backlog.py
  clean_cite_tags.py
  migrate_niche_ids.py
  purge_stale_data.py
  push_rerenders_to_backlog.py
  remediate_visual_ready.py
  rerender_all_reels.py
  rerender_existing.py
  seed_bandit_arms.py
  youtube_audit.py

  # Shell Scripts
  launch_wrapper.sh           # Shared launchd wrapper
  publish.sh                  # Publish trigger
  test_publish_cycle.sh       # E2E publish test
  verify_daily_cycle.sh       # Daily pipeline verification

  # Tests
  tests/
    test_intelligence_hub.py
    test_launch_wrapper.py
```

### Issues Found

1. **Only 2 test files**: scripts/ has 27 scripts but only 2 test files. Most maintenance scripts are untested.
2. **social_analytics.py**: Still uses Blackbox Brief credentials only -- per-niche analytics need separate platform accounts (documented blocker).
3. **Multiple backfill scripts**: 4 `backfill_*.py` scripts suggest historical data migration was done incrementally. These are likely one-time-use scripts that could be archived.
4. **`migrate_niche_ids.py`**: One-time migration script (ai_news -> ai_creators, fashion -> anime). Already executed. Dead code.

---

## 10. OpenSandbox/ (Sandboxing)

**Purpose:** Third-party open-source sandbox infrastructure for secure code execution. Used for FFmpeg sandboxing (Phases 0-2 complete).

### Directory Structure (2 levels)

```
OpenSandbox/
  AGENTS.md, CODE_OF_CONDUCT.md, LICENSE, README.md
  .pre-commit-config.yaml
  .github/                    # CI workflows (14 workflow files)
  components/                 # egress, execd, ingress, internal
  docs/                       # VitePress docs site
  examples/                   # 18 example configurations
  kubernetes/                 # K8s APIs, charts, controllers
  sandboxes/                  # code-interpreter sandbox
  scripts/                    # spec-doc generation
  sdks/                       # code-interpreter, mcp, sandbox SDKs
  server/                     # Sandbox server (src/, tests/)
  specs/                      # OpenSandbox specifications
  tests/                      # Multi-language tests (csharp, java, javascript, python)
  oseps/                      # Proposal documents
```

### Key Characteristics

- **External dependency**: This is a cloned open-source project, not GenLab code
- **Phases 0-2 complete**: Setup, SandboxedFFmpegRunner, egress policies done
- **Phases 3-5 pending**: Full sandboxing of render pipeline, network isolation, monitoring
- Excluded from ruff linting in root `pyproject.toml`

### Issues Found

1. **Entire open-source repo checked in**: Rather than as a submodule or dependency, the full OpenSandbox source is in the repo. This adds significant code size without being GenLab code.

---

## 11. docs/ (Documentation)

**Purpose:** Planning documents, sprint reports, and design specs.

### Files (37 documents)

```
docs/
  meta-app-review-submission.md   # Meta app review documentation
  plans/                          # 5 plan documents
    2026-03-09-dashboard-extraction-{design,plan}.md
    2026-03-11-sprint-30-report.md
    2026-03-11-twitter-cost-decision.md
    2026-03-13-whisper-synced-captions-{design,impl}.md
  superpowers/                    # 24 documents
    audit-v4-2026-03-14.md
    sprint-45-report.md
    sprint-47-report.md
    plans/                        # 10 plan documents
      2026-03-13-platform-consolidation.md
      2026-03-15-dashboard-archived-status.md
      2026-03-15-sprint-61-content-quality.md
      2026-03-16-env-consolidation.md
      2026-03-16-video-frame-layout-redesign.md
      2026-03-17-postgresql-rls-phase0-1.md
      2026-03-17-ws{1-5}-*.md     # Sprint 63 workstream plans
      PROMPT_DEFINITIVE_AUDIT.md
    specs/                        # 14 design specs
      2026-03-13-platform-consolidation-design.md
      2026-03-15-dashboard-archived-status-design.md
      2026-03-17-postgresql-rls-migration-design.md
      2026-03-17-r{1-6}-*-design.md  # Robustness specs
      2026-03-17-sprint3-canonical-publisher-design.md
      2026-03-17-ws{1-5}-*-design.md  # Sprint 63 workstream specs
```

### Issues Found

1. **docs outside channel dirs**: Some docs are also in `Content Scraper/docs/` and `CriticalRush/docs/`. No clear separation between platform-wide and channel-specific documentation.

---

## 12. docker/ (Docker Configs)

**Purpose:** Docker configurations for third-party services.

### Files

```
docker/
  .env.docker.example          # Docker env template
  .gitignore
  postiz/
    docker-compose.yaml        # Postiz social scheduler
    fly.toml                   # Fly.io deployment config
    FLY_DEPLOY.md              # Deployment instructions
    SHADOW_EVAL_REVIEW.txt     # Shadow evaluation checklist (review date: 2026-04-07)
    start.sh                   # Container start script
```

### Key Characteristics

- Only contains Postiz configuration (social scheduling tool under shadow evaluation)
- Shadow eval review date: 2026-04-07

### Issues Found

1. **Postiz under evaluation**: Not in production use. The entire docker/ directory serves a single experimental integration.

---

## 13. Hidden/Config Directories

### `.claude/`
```
.claude/
  launch.json                  # Launch configuration
  settings.local.json          # Local settings (modified)
  rules/                       # 4 rule files
    cleanup_safety.md          # Scheduled post protection rules
    content_policy.md          # Content generation rules
    optimization.md            # Context management + cost control
    security.md                # Prompt injection, credentials, Meta API rules
  worktrees/                   # Claude Code worktree state (skip per instructions)
```

### `.logs/` (58 files)
Active runtime log directory. Contains:
- **Engagement poller logs**: 10 files (per-niche, per-platform: youtube/twitter x 5 niches)
- **Fetch insights logs**: 20 files (per-niche, per-window: 24h/48h x 5 niches, stdout + stderr)
- **Service logs**: metric_collector, quota_monitor, review_server, review_tunnel, engagement_webhook, engagement_worker
- **Pipeline run logs**: 7 files (per-niche run logs from 2026-03-16)
- **Audit logs**: 8 files from 2026-03-16/17 audits
- **Daily verify log**: 1 file

### `.tmp/` (50+ items)
Ephemeral artifacts directory. Contains:
- **Pipeline logs**: 5 JSONL files (per-niche pipeline logs)
- **Cache**: HTTP response cache
- **Run artifacts**: `runs/` (49 subdirectories), `rerender_downloads/`
- **Test artifacts**: Video frames, layout checks, centered previews
- **Dashboard audit**: Audit screenshots and reports

### `.media/` (5 subdirectories)
Per-niche media storage:
```
.media/
  ai_creators/    # BB rendered media (has content)
  anime/          # Empty
  gaming/         # Empty
  movies/         # Empty
  sports/         # Empty
```

### `.serena/`
Serena MCP server configuration (project.yml, project.local.yml, cache/, memories/).

### `.hypothesis/`
Hypothesis test framework constants cache.

### `.playwright-mcp/`
Playwright MCP server logs/state.

### `.pytest_cache/`
Pytest cache.

### `.ruff_cache/`
Ruff linter cache.

### `logs/` (root-level, empty)
Empty directory -- likely superseded by `.logs/`.

---

## 14. Root-Level Files

| File | Purpose | Status |
|------|---------|--------|
| `pyproject.toml` | uv workspace definition (7 members) + ruff config | Active |
| `uv.lock` | Unified lockfile for all workspace members | Active |
| `CLAUDE.md` | Master project instructions (16,587 bytes) | Active |
| `.env` | Root credentials (all per-niche prefixed vars) | Active (gitignored) |
| `.env.example` | Credential template | Active |
| `.gitignore` | Ignore patterns for Python, Node, secrets, temp | Active |
| `firebase-debug.log` | Firebase debug output (256KB) | Should be gitignored |
| `CLEANUP_PLAN.sh` | Disk reclamation script (~66GB target) | Utility, not committed |
| `Blackbox Brief Cover.png` | BB brand cover image (2MB) | Stale -- should be in CS/assets/ |
| `blackbox.brief logo.png` | BB brand logo (437KB) | Stale -- should be in CS/assets/ |
| `ClutchWire-Cover.png` | CW brand cover (1.8MB) | Stale -- should be in CW/assets/ |
| `ClutchWire-Logo.png` | CW brand logo (1.9MB) | Stale -- should be in CW/assets/ |
| `CriticalRush Banner.png` | CR brand banner (1.3MB) | Stale -- should be in CR/assets/ |
| `CriticalRush Logo.png` | CR brand logo (1.3MB) | Stale -- should be in CR/assets/ |
| `FrameDrift-Cover.png` | FD brand cover (2.3MB) | Stale -- should be in FD/assets/ |
| `FrameDrift-Logo.png` | FD brand logo (1.4MB) | Stale -- should be in FD/assets/ |
| `SpliceReel-Cover.png` | SR brand cover | Stale -- should be in SR/assets/ |
| `SpliceReel-Logo.png` | SR brand logo | Stale -- should be in SR/assets/ |
| `blueprints-*.png` (5 files) | Dashboard debugging screenshots | Temporary, should be cleaned |
| `dashboard_*.png` (12 files) | Dashboard screenshot documentation | Temporary, should be cleaned |

### Issues Found

1. **27 PNG files at root**: Brand logos, covers, and debugging screenshots pollute the root directory. Logos should be in their respective `assets/` directories. Screenshots should be in `.tmp/` or deleted.
2. **`firebase-debug.log`**: 256KB log file tracked by git despite `.gitignore` having a rule for it. Needs `git rm --cached`.

---

## 15. Architecture Debt & Issues Summary

### Critical Issues

| # | Issue | Severity | Location | Impact |
|---|-------|----------|----------|--------|
| 1 | **Dual publisher** | HIGH | CS `execution/publish_all_platforms.py` (2,407 LOC) vs genlab-core `publishing/publish_all_platforms.py` (526 LOC) | Two active codepaths for the same function. Bug fixes must be applied in both places. |
| 2 | **Content Scraper as shared infra** | HIGH | CS `execution/` imported by dashboard via sys.path | Violates 3-layer architecture. Dashboard depends on CS for token_health checks. |
| 3 | **Root-level image pollution** | MEDIUM | 27 PNG files at GenLab root | 15+ MB of brand assets that belong in channel `assets/` dirs |

### Duplication

| Module | CS Version | genlab-core Version | Status |
|--------|-----------|-------------------|--------|
| `niche_credentials.py` | Shim (10 LOC) | Canonical (119 LOC) | Migrated |
| `scheduling.py` | Shim (2 LOC) | Canonical | Migrated |
| `publish_all_platforms.py` | Active (2,407 LOC) | Canonical (526 LOC) | **NOT MIGRATED** |
| `threads_client.py` | CR `tools/publishing/` | genlab-core `publishing/` | Potentially duplicated |
| `tiktok_client.py` | CR `tools/publishing/` | genlab-core `publishing/` | Potentially duplicated |

### Dead/Deprecated Code

| File | Location | Reason |
|------|----------|--------|
| `assemble_reel.py` | CS `execution/archive/` | Explicitly deprecated (Ken Burns assembler) |
| `background_animator.py` | CS `execution/utils/` | Deprecated (Ken Burns + gradient, video-only pipeline) |
| `qc_claims_validator.py` | CS `execution/` | Logic moved to `run_qc_gates.py` |
| `publish_twitter.py` | CS `execution/` | Deprecated per docstring, prefer publish_all_platforms |
| `migrate_niche_ids.py` | `scripts/` | One-time migration script, already executed |
| `backfill_*.py` (4 files) | `scripts/` | One-time backfill scripts |
| `platforms/postiz.py` | genlab-core | Under shadow evaluation, disabled in production |
| `.tmp/` one-off scripts (16) | CS `.tmp/` | Ad-hoc audit/fix scripts never cleaned up |

### Missing Tests

| Package | Source Files | Test Files | Coverage Gap |
|---------|-------------|------------|--------------|
| scripts/ | 25 | 2 | Very low coverage |
| CS execution/ | 95 | 86 | Some coverage but many execution scripts untested |
| Dashboard frontend | 80+ TSX | 0 | No frontend tests |

### Stale/Orphaned Items

1. **`configs/` vs `config/`** in Content Scraper: Two config directories
2. **`logs/`** at GenLab root: Empty directory, superseded by `.logs/`
3. **Local `.venv/` in SpliceReel and FrameDrift**: Empty virtualenvs when workspace venv is used
4. **`_reference/`**: Contains `AI-Youtube-Shorts-Generator` and `Socioboard-5.0` (reference material, excluded from ruff)
5. **`firebase-debug.log`**: Tracked despite gitignore rule

### Architecture Patterns

1. **Successful**: genlab-core as shared library with lazy loading, abstract strategies, niche-agnostic pipeline runner
2. **Successful**: Per-niche strategy packages with unique prefixes (cw_, sr_, fd_, bb_) preventing sys.modules collisions
3. **Successful**: Config-driven YAML architecture with symlinks for shared configs
4. **In Progress**: PostgreSQL migration (Phase 0-1 scaffolding in `storage/`) alongside production SharePoint
5. **In Progress**: Canonical publisher migration from CS to genlab-core
6. **Technical Debt**: OpenSandbox checked in as full source rather than submodule/dependency

### Size Summary

| Package | Source LOC | Test Files | Config Files | Total Files |
|---------|-----------|------------|--------------|-------------|
| genlab-core | 36,107 | 146 | 8 YAML | ~350 |
| Content Scraper | 55,033 | 86 | 31 YAML | ~300 |
| CriticalRush | 11,954 | 42 | 17 YAML | ~120 |
| ClutchWire | ~3,000 | 14 | 13 YAML | ~60 |
| SpliceReel | ~3,500 | 16 | 13 YAML | ~65 |
| FrameDrift | ~3,500 | 15 | 13 YAML | ~65 |
| Dashboard (server) | ~5,000 | 20 | 1 YAML | ~40 |
| Dashboard (frontend) | ~8,000 | 0 | - | ~100 |
| scripts | ~5,000 | 2 | 0 | ~30 |

**Total estimated codebase size: ~131,000 LOC** (excluding OpenSandbox, tests, and generated files)

---

*Analysis generated 2026-03-17 by Claude Opus 4.6*
