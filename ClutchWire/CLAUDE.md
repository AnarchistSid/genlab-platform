# CLAUDE.md — ClutchWire (Sports)

Sports content channel. `niche_id: "sports"`, accent: `#FF2040`.

## Architecture

Video-first pipeline: `FetchTrendingVideos → SportContentResearch → SportScoring → DownloadTopVideos → SportWriting → SportHooks → SportVisualRender → SportPlatformAdaptation → PushToBacklog`

Strategy classes live in `cw_strategies/` (prefix prevents sys.modules collisions).

## Key Config Files

- `config/niche.yaml` — Pipeline stages, video sourcing (`video_gate: require`)
- `config/sources.yaml` — ESPN API + RSS + YouTube + Reddit sources
- `config/visuals.yaml` — Logo path, accent color, sandwich layout
- `config/scoring_weights.yaml` — Scoring dimensions + trend cycle multipliers
- `config/templates.yaml` — Hook formulas, caption CTAs, platform constraints
- `config/publishing.yaml` — Platform enablement, rate limits, schedule

## Video-Only Mandate

- `video_gate: require` — blueprints without verified video clips are blocked
- `fallback_to_text_render: false` — no solid-color placeholder videos
- Content is written AROUND the trending video clip, not the other way around

## Build & Test

```bash
~/.local/bin/uv run --package clutchwire pytest ClutchWire/tests/ -x
```

## Credentials

Per-niche env vars: `CLUTCHWIRE_YOUTUBE_CLIENT_ID`, `CLUTCHWIRE_YOUTUBE_CLIENT_SECRET`, etc.
`niche_credentials.py` blocks fallback to global vars (cross-channel guard).
