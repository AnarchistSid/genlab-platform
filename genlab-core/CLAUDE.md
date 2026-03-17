# CLAUDE.md — genlab-core

Shared library for the Gen Lab video content platform.

## What This Is

`genlab-core` is the shared Python package imported by all 5 niche channels. It owns:
- Pipeline runner + stage protocol
- Platform clients (Instagram, YouTube, X, Facebook, Threads, TikTok)
- Video processing (TrendingVideoFetcher, VideoCompositor, DownloadTopVideos)
- Publishing infrastructure (DailyCapEnforcer, niche_credentials, scheduling)
- Learning system (reward shaper, metric collector, hook classifier, bandit)
- Engagement engine (comment processor, persona, toxicity gate)
- HTTP layer (BacklogClient, async_bridge, retry, graph_proxy)
- Cache + text sanitization + stable IDs

## Layout

```
src/genlab_core/
├── cache/          # stable_ids, text_sanitizer, disk_cache
├── http/           # backlog_client, async_bridge, retry, graph_proxy
├── media/          # trending_video_fetcher, video_compositor, ffmpeg_utils, quota_manager
├── platforms/      # instagram, youtube, x_twitter, facebook, threads, tiktok, postiz
├── publishing/     # daily_cap, niche_credentials, scheduling
├── pipeline/       # pipeline_runner, stage_runner, stages/
├── learning/       # reward_shaper, metric_collector, hook_classifier, config_writer
├── engagement/     # comment_processor, persona_engine, toxicity_gate, platform_clients/
├── analytics/      # youtube_analytics_client
├── intel/          # google_trends
├── ratelimit/      # token_bucket, domain_limiter
├── tools/          # safe_push, credential_check
├── writing/        # video_content_writer
├── strategies.py   # Base strategy classes (6 abstract interfaces)
├── niche_loader.py # Niche config loading
└── settings.py     # Project root, env loading
```

## Key Principles

- **src-layout**: Package lives under `src/genlab_core/`. `_PROJECT_ROOT` uses `.parent` x4.
- **VIDEO-ONLY**: Gen Lab is a video-only platform. No text posts, carousels, static images, or placeholder videos.
- **video_gate: require**: All niche configs must set `fallback_to_text_render: false`.
- **Niche-agnostic**: genlab-core never imports from channel packages. Channels import from genlab-core.
- **import-linter**: `.importlinter` enforces layer boundaries. Interfaces at bottom, services at top.

## Build & Test

```bash
# From GenLab workspace root
~/.local/bin/uv run --package genlab-core pytest genlab-core/tests/ -x
```

## Meta API Rule

All Instagram API calls use `graph.facebook.com` — never `graph.instagram.com`.
EAA Page Tokens are permanent — never call `ig_refresh_token`.

## Niche Registration

`GenericPipelineRunner` accepts a `niche_roots` mapping of niche_id → root dir. All 5 niches: ai_creators, gaming, sports, movies, anime.

## Shared Configs

`genlab-core/config/` holds shared configs (moved from Content Scraper in Sprint 62):
- `lists_config.yaml` — SharePoint list IDs
- `platform_caps.yaml` — daily post caps per platform
- `disk_quota.yaml` — disk usage quotas
Symlinks in `Content Scraper/config/` for backward compat.

## Frame Compositor (Sprint 62 redesign)

Three layout modes in `frame_compositor.py`:
- **Landscape** (w/h ≥ 1.33): sandwich — black bars top+bottom, video 810px in middle
- **Portrait** (w/h ≤ 0.75): fill canvas, dark gradient overlay for logo+hook
- **Square** (0.75–1.33): compact header (160px), video fills most of canvas
Logo path resolved to absolute against niche_root from visuals.yaml location.

## Cleanup Protection

`disk_quota.py._is_published()` protects any run directory with:
visuals/, visuals_v2/, visuals_v3/, rendered/, clips/ containing MP4s.
Approved posts' media is NEVER deleted by cleanup.

## Sprint 63 Additions

### New Modules
- `media/relevance_filter.py` — Post-fetch niche content validation
- `http/circuit_breaker.py` — CircuitBreaker + @resilient decorator
- `learning/linucb.py` — LinUCB contextual bandit (6D features)
- `observability/logging.py` — structlog JSON logging config
- `observability/metrics_writer.py` — PipelineMetrics JSONL per-stage timing
- `tools/create_niche.py` — Scaffold new niche from template
- `tools/validate_configs.py` — Pydantic config pre-flight checker
- `engagement/platform_clients/` — 5 reply clients (YT, IG, FB, X, Threads)

### Key Changes
- `pipeline/stages/fetch_insights.py` — queries SharePoint (not current-run context)
- `pipeline/stages/validate_videos.py` — VMAF gate enabled (≥85)
- `media/video_compositor.py` — per-platform transcode via PLATFORM_SPECS
- `media/trending_video_fetcher.py` — RelevanceFilter + circuit breaker
- `engagement/comment_processor.py` — hybrid auto-reply (auto/review/discard)
- `engagement/poller.py` — YouTube 30min interval, quota handling, Facebook poller
- `learning/arm_loader.py` — LinUCB state persistence
- `publishing/niche_credentials.py` — ai_creators→BLACKBOXBRIEF mapped
- `config/platform_encode_specs.yaml` — per-platform encode overrides
- `config/alerting.yaml` — threshold definitions
