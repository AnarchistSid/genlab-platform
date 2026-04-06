# Architecture

GenLab is a video-first content automation platform organized as a Python monorepo.

## Layers

```
Layer 1 — genlab-core/        Shared infrastructure (never changes per niche)
Layer 2 — <Channel>/strategies/  Pluggable strategies (abstract interfaces per niche)
Layer 3 — <Channel>/config/     Niche configuration (pure YAML)
```

## Packages

| Package | Purpose |
|---------|---------|
| `genlab-core` | Shared library: pipeline runner, platform clients, learning loop, engagement engine, video compositor |
| `BlackboxBrief` | AI/Tech news channel (niche_id: `ai_creators`) |
| `CriticalRush` | Gaming channel (niche_id: `gaming`) |
| `ClutchWire` | Sports channel (niche_id: `sports`) |
| `SpliceReel` | Movies channel (niche_id: `movies`) |
| `FrameDrift` | Anime channel (niche_id: `anime`) |
| `dashboard` | Operations dashboard (React + Flask) |

## Pipeline

```mermaid
graph LR
    A[Fetch Trending Videos] --> B[Score & Filter]
    B --> C[Write Content via LLM]
    C --> D[Render Video with FFmpeg]
    D --> E[Human Review via Dashboard]
    E --> F[Publish to 6 Platforms]
    F --> G[Collect Engagement Metrics]
    G --> H[Update Learning Loop]
    H --> A
```

## Key Design Decisions

- **Video-only**: No text posts, carousels, or static images. Every piece of content is a video reel.
- **Config-driven niches**: Adding a new channel requires ~200 lines of strategy code + YAML config. No shared code changes.
- **Learning loop**: LinUCB contextual bandit with 6D features optimizes content selection based on engagement feedback.
- **Engagement engine**: Hybrid auto-reply system with toxicity filtering and rate limiting per platform.

## Deployment

Production runs on a Hetzner cloud server:
- PostgreSQL + Redis in Docker
- All Python services via systemd (6 always-on + 24 timers)
- Caddy reverse proxy with Cloudflare HTTPS
- 50GB Hetzner Volume for media storage

## Testing

```bash
# All tests
uv run --package genlab-core pytest genlab-core/tests/ -x

# Specific channel
uv run --package criticalrush pytest CriticalRush/tests/ -x
```

261 test files across 7 packages.
