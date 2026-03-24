# BlackboxBrief Extraction Plan

**Goal**: Reduce BlackboxBrief from 85K lines to ~10K by migrating shared execution/ modules to genlab-core.

**Principle**: BB should only contain AI-creators-specific strategy wrappers (`bb_strategies/`), niche-specific prompts, and config YAML. All pipeline logic that could serve any channel belongs in genlab-core.

## Current State

BB `execution/` has 55K lines across 35+ modules. genlab-core `pipeline/stages/` already has 18 stages.

## Extraction Tiers

### Tier 1 — Already Duplicated (validate + consolidate)

These modules exist in both BB and genlab-core. BB should switch to genlab-core's version.

| BB Module (lines) | genlab-core Equivalent | Action |
|---|---|---|
| `publish_all_platforms.py` (2,366) | `publishing/publish_all_platforms.py` (538) | BB calls genlab-core's publisher. Delete BB version. |
| `validate_videos.py` (1,671) | `pipeline/stages/validate_videos.py` | Consolidate — BB version has extra logic; merge into gc. |
| `fetch_insights.py` (1,208) | `scripts/run_fetch_insights.py` (657) | Already migrated. Delete BB copy. |
| `render_text_overlays.py` (4,729) | `pipeline/stages/render_text_overlays.py` | Consolidate. BB version is 4x larger — audit what's BB-specific. |
| `generate_audio.py` (2,024) | `pipeline/stages/generate_audio.py` | Consolidate. |

**Savings**: ~12K lines from BB (most is duplicate logic).

### Tier 2 — Generic Logic (migrate to genlab-core)

These modules are used only by BB but contain no BB-specific logic.

| BB Module (lines) | Target in genlab-core | Notes |
|---|---|---|
| `compose_blueprints.py` (1,674) | `pipeline/stages/compose_blueprints.py` | Story×template mapping is generic |
| `adapt_for_platforms.py` (1,558) | `pipeline/stages/adapt_for_platforms.py` | Platform-native rewrites are generic |
| `write_post_content.py` (1,286) | `writing/post_content_writer.py` | LLM writing is generic (prompts are config-driven) |
| `generate_content.py` (1,408) | `writing/content_generator.py` | Same — LLM wrapper with niche prompts from YAML |
| `generate_hooks.py` (1,900) | `writing/hook_generator.py` | Formula-based hooks already in gc's `llm_hook_generator.py` |
| `dedupe_rank_items.py` (1,114) | `intelligence/dedupe_ranker.py` | TF-IDF is niche-agnostic |
| `prepare_for_review.py` (1,049) | `pipeline/stages/prepare_for_review.py` | Review prep is generic |
| `assemble_video_reel.py` (1,109) | Already covered by `rendering/video_renderer.py` | Consolidate |
| `extract_media.py` (1,237) | `media/media_extractor.py` | Media extraction is generic |

**Savings**: ~12K lines from BB.

### Tier 3 — BB-Specific (keep in BB)

These modules have BB-specific logic that doesn't generalize.

| BB Module (lines) | Reason to Keep |
|---|---|
| `fetch_ai_creators.py` (973) | RSS sources + parsing specific to AI news |
| `ab_testing.py` (1,030) | BB-specific A/B test framework |
| `render_visuals.py` (1,947) | Has BB-specific carousel/slide rendering (not just video) |
| `utils/video_downloader.py` (1,061) | Could migrate, but genlab-core already has `download_top_videos.py` |
| `utils/text_optimizer.py` (943) | BB-specific text sizing for overlays |

### Tier 4 — BB utils/ (already have gc equivalents)

| BB util | genlab-core equivalent | Action |
|---|---|---|
| `utils/cache.py` | `cache/disk_cache.py` | Delete BB, use gc |
| `utils/stable_ids.py` | `cache/stable_ids.py` | Already migrated |
| `utils/text_sanitizer.py` | `cache/text_sanitizer.py` | Already migrated |
| `utils/backlog_client.py` | `http/backlog_client.py` | Already migrated |
| `utils/rate_limiter.py` | `ratelimit/token_bucket.py` | Already migrated |

## Execution Order

1. **Week 1**: Tier 1 — validate genlab-core equivalents work, switch BB imports, delete BB copies
2. **Week 2**: Tier 2 — migrate 9 modules, update BB's `bb_strategies/` to call gc stages
3. **Week 3**: Tier 4 — delete remaining BB utils that have gc equivalents
4. **Ongoing**: Tier 3 stays in BB as niche-specific code

## Expected Result

| Package | Current | After |
|---|---|---|
| BlackboxBrief | 85K lines | ~10K lines (strategies + prompts + BB-specific) |
| genlab-core | 37K lines | ~50K lines (all shared pipeline logic) |

## Risk Mitigation

- Each module migration is a single PR with before/after test comparison
- BB's 1,400 tests must all pass after each migration
- `import-linter` enforces that genlab-core never imports from BB
- Symlinks for backward compat during transition if needed
