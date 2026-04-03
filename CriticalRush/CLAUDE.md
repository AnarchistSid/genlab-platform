# CLAUDE.md — CriticalRush (Gaming Pipeline Runner)

Gaming content channel + multi-niche pipeline orchestrator. `niche_id: "gaming"`, accent: `#f97316`.

## Architecture

### Gaming Pipeline
`FetchTrendingVideos → FetchGamingStories → FilterGamingStories → EnrichWithIGDB → ExtractGamingMedia → ScoreGamingClips → WriteGamingContent → AdaptGamingContent → RenderGamingVideo → GenerateGamingAudio ∥ RenderTextOverlays → PushToBacklog → PublishGamingContent → WriteRunReport`

Gaming stages live in `niches/gaming/stages/`. Post-render stages run in parallel groups.

### Multi-Niche Orchestration
`pipeline_runner.py` orchestrates all 5 niches. `NICHE_ROOTS` maps:
- `sports` → ClutchWire
- `movies` → SpliceReel
- `anime` → FrameDrift
- `ai_news` → BlackboxBrief
- `gaming` → self (CriticalRush)

### Self-Learning System
- Bandit exploration (arm_loader, meta_prior)
- Feedback collection (metric_collector, pending_feedback_store)
- Hook quality prediction (hook_classifier, hook_features)
- Config auto-update (config_writer, config_update_flow)

## Key Config (Gaming Niche)

- `niches/gaming/config/niche.yaml` — Pipeline stages, `video_gate: require`
- `niches/gaming/config/sources.yaml` — RSS (IGN, Kotaku, PC Gamer), YouTube, IGDB, Reddit, Twitch
- `niches/gaming/config/visuals.yaml` — Logo, accent, sandwich layout
- `niches/gaming/config/publishing.yaml` — Bespoke publishers (Postiz disabled), rate limits

## Video-Only Mandate

- `video_gate: require`, `fallback_to_text_render: false`
- Compilation mode: 3+ clips → one compilation video, assigned to primary story only
- RenderGamingVideo runs in sandbox isolation

## Publishing

Uses bespoke per-platform publishers (NOT Postiz — integration IDs unconfigured).
Two paths: `_publish_via_postiz()` (disabled) and `_publish_live_legacy()` (active).
Jaccard dedup (0.55 threshold) prevents near-duplicate publishes.

## Build & Test

```bash
~/.local/bin/uv run --package criticalrush pytest CriticalRush/tests/ -x
```

## Credentials

Per-niche env vars: `CRITICALRUSH_YOUTUBE_CLIENT_ID`, etc.
Global credentials exist but niche_credentials guard blocks fallback.
