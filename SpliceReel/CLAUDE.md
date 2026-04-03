# CLAUDE.md — SpliceReel (Movies)

Movies content channel. `niche_id: "movies"`, accent: `#C9A84C`.

## Architecture

Video-first pipeline: `FetchTrendingVideos → MovieContentResearch → MovieScoring → DownloadTopVideos → MovieWriting → MovieHooks → MovieVisualRender → MoviePlatformAdaptation → PushToBacklog`

Strategy classes live in `sr_strategies/` (prefix prevents sys.modules collisions).
OMDb selective enrichment for items scoring ≥0.45.

## Key Config Files

- `config/niche.yaml` — Pipeline stages, video sourcing (`video_gate: require`), freshness decay
- `config/sources.yaml` — TMDB API + RSS (Deadline, Variety) + YouTube trailers
- `config/visuals.yaml` — Logo path, accent color, sandwich layout
- `config/scoring_weights.yaml` — Film lifecycle modes (pre-release, opening weekend, long tail)
- `config/templates.yaml` — Hook formulas, caption CTAs, platform constraints
- `config/publishing.yaml` — Platform enablement, rate limits, schedule

## Video-Only Mandate

- `video_gate: require` — blueprints without verified video clips are blocked
- `fallback_to_text_render: false` — no solid-color placeholder videos
- Content is written AROUND the trending video clip, not the other way around

## Build & Test

```bash
~/.local/bin/uv run --package splicereel pytest SpliceReel/tests/ -x
```

## Credentials

Per-niche env vars: `SPLICEREEL_YOUTUBE_CLIENT_ID`, etc.
Env vars needed: `TMDB_API_KEY`, `OMDB_API_KEY`.
