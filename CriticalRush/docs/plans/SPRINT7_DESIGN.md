# Sprint 7: Dynamic Stage Discovery — Design Summary

## Current State

### `_load_stages()` signature
`(self, niche_id: str, config: Dict[str, Any]) -> List[Any]`

Returns list of stage instances. Each stage is instantiated with no args (`cls()`).

### Stage protocol
- **No base class**. Duck typing only.
- Constructor: `__init__(self)` — no arguments
- Execution: `execute(context: Dict[str, Any]) -> Dict[str, Any]`
- context_dict keys: `stories`, `blueprints`, `run_stats`, `feature_flags`, `niche_config`

### NICHE_ROOTS
Dict mapping `niche_id → Path`:
- `gaming` → CriticalRush project root
- `sports` → `/GenLab/ClutchWire`
- `movies` → `/GenLab/SpliceReel`
- `anime` → `/GenLab/FrameDrift`

### niche.yaml
- Has `pipeline_overrides` (1 entry, unused by runner) but **no `stages:` key**
- Need to add `pipeline.stages` list

### genlab-core
- Has `PipelineContext` dataclass (context.py) — not used in stages (they get plain dict)
- **No base PipelineRunner** — CriticalRush runner is standalone
- No `NicheConfigError` — needs to be created

### Prefect integration
- `gaming_flow.py` uses its own `_make_task()` with `importlib.import_module`
- Does NOT call PipelineRunner — builds its own context and calls stages directly
- **Not affected by this refactor** (separate concern)

## Hardcoded stages (exact order)

| # | Class | Module |
|---|-------|--------|
| 1 | FetchGamingStories | niches.gaming.stages.fetch_gaming_stories |
| 2 | FilterGamingStories | niches.gaming.stages.filter_gaming_stories |
| 3 | EnrichWithIGDB | niches.gaming.stages.enrich_with_igdb |
| 4 | ExtractGamingMedia | niches.gaming.stages.extract_gaming_media |
| 5 | ScoreGamingClips | niches.gaming.stages.score_gaming_clips |
| 6 | WriteGamingContent | niches.gaming.stages.write_gaming_content |
| 7 | AdaptGamingContent | niches.gaming.stages.adapt_gaming_content |
| 8 | RenderGamingVideo | niches.gaming.stages.render_gaming_video |
| 9 | GenerateGamingAudio | niches.gaming.stages.generate_gaming_audio |
| 10 | RenderTextOverlays | niches.gaming.stages.render_text_overlays |
| 11 | PushToBacklog | niches.gaming.stages.push_to_backlog |
| 12 | PublishGamingContent | niches.gaming.stages.publish_gaming_content |
| 13 | WriteRunReport | niches.gaming.stages.write_run_report |

## Design

1. Add `pipeline.stages` to gaming niche.yaml — 13 entries in exact order above
2. Replace `_load_stages()` with importlib-based dynamic loader
3. Add `NicheConfigError` to genlab-core
4. Remove all `niches.gaming.*` imports from pipeline_runner.py
5. Empty `stages: []` for ClutchWire/SpliceReel/FrameDrift (stub agents)
