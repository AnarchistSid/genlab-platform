# CLAUDE.md — FrameDrift (Anime)

Anime content channel. `niche_id: "anime"`, accent: `#7B3FE4`.

## Architecture

Video-first pipeline: `FetchTrendingVideos → AnimeContentResearch → AnimeScoring → DownloadTopVideos → AnimeWriting → AnimeHooks → AnimeVisualRender → AnimePlatformAdaptation → PushToBacklog`

Strategy classes live in `fd_strategies/` (prefix prevents sys.modules collisions).

## Key Config Files

- `config/niche.yaml` — Pipeline stages, video sourcing (`video_gate: require`), freshness + trend cycle
- `config/sources.yaml` — ANN, Crunchyroll, MAL RSS + YouTube anime channels + Google Trends
- `config/visuals.yaml` — Logo path, accent color, sandwich layout
- `config/scoring_weights.yaml` — Trend cycle multipliers (EMERGING 1.4x, DECLINING 0.5x)
- `config/templates.yaml` — Anime hook formulas (anime_premiere, voice_actor_trigger, manga_release, studio_collab)
- `config/publishing.yaml` — Platform enablement, rate limits, schedule

## Story Classification

Stories are classified by type for hook routing:
- `is_new_release` — anime premieres, season debuts, episode drops
- `is_creator_spotlight` — known creators/studios + trigger words (directed, starring, voicing)
- `is_event_coverage` — conventions, expos, festivals (Comiket, Anime Expo)
- `is_collab` — studio crossovers, collaborations

## Video-Only Mandate

- `video_gate: require` — blueprints without verified video clips are blocked
- `fallback_to_text_render: false` — no solid-color placeholder videos
- Google Trends integration via `stub_mode` flag in sources.yaml

## Build & Test

```bash
~/.local/bin/uv run --package framedrift pytest FrameDrift/tests/ -x
```

## Credentials

Per-niche env vars: `FRAMEDRIFT_YOUTUBE_CLIENT_ID`, etc.
