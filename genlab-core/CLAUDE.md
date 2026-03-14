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

`pipeline_runner.SUPPORTED_NICHES` maps niche_id → root dir. All 5 niches: ai_news, gaming, sports, movies, anime.
