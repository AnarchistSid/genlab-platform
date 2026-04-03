# New Niche Template

Copy this directory to create a new Gen Lab channel.

## Steps

1. Copy this directory: `cp -r niches/_template/ /path/to/NewChannel/`
2. Update `config/niche.yaml` with niche_id, display_name, accent_color
3. Update `config/sources.yaml` with RSS feeds, YouTube channels
4. Update `config/visuals.yaml` with logo path, accent color, branding
5. Update `config/templates.yaml` with hook formulas, forbidden styles
6. Update `config/scoring_weights.yaml` with niche-specific weights
7. Update `config/schedule.yaml` with publishing windows
8. Update `config/publishing.yaml` with platform account IDs
9. Implement 6 strategy classes in `strategies/`:
   - content_research.py (ContentResearchStrategy)
   - scoring.py (ScoringStrategy)
   - writing.py (WritingStrategy)
   - hooks.py (HookStrategy)
   - visual_render.py (VisualRenderStrategy)
   - platform_adaptation.py (PlatformAdaptationStrategy)
10. Create `run_pipeline.py` (copy from any existing niche)
11. Add channel logo to `assets/logos/`
12. Add persona YAML to `genlab-core/src/genlab_core/engagement/personas/`
13. Register in parent `pyproject.toml` as workspace member
14. Create launchd plists for pipeline + publisher

## Required Config Files

- `config/niche.yaml` — pipeline stages, video_sourcing, feature_flags
- `config/sources.yaml` — RSS feeds, YouTube channels, reddit_sources
- `config/visuals.yaml` — logo, accent color, frame layout
- `config/templates.yaml` — hook formulas, forbidden_styles, captions
- `config/scoring_weights.yaml` — scoring dimensions + weights
- `config/schedule.yaml` — publishing windows (UTC)
- `config/publishing.yaml` — platform enablement, account IDs
