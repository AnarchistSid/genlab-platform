# Gaming Clips -> CriticalRush Merge Plan

> **Status:** COMPLETE
> **Date:** 2026-03-06
> **Objective:** Merge all 6 Gaming Clips capabilities into CriticalRush's genlab-core 3-layer architecture, then delete Gaming Clips.

---

## Architecture Overview

### Target State

CriticalRush already runs a 9-stage pipeline via `core/pipeline_runner.py` with `context_dict` pass-through. Gaming Clips capabilities will be transplanted as:

- **Strategy implementations** (genlab-core interfaces) for scoring, rendering, and learning
- **New pipeline stages** for commentary audio and caption burn-in
- **Tools** (stateless utilities) under `niches/gaming/tools/`
- **Config files** under `niches/gaming/config/`

### genlab-core Interfaces (strategies.py)

| Interface | Method | Used By |
|-----------|--------|---------|
| `ScoringStrategy` | `execute(context) -> context` | Capability 1 |
| `VisualRenderStrategy` | `execute(context) -> context` | Capability 3 |
| **`PerformanceLearner`** | `execute(context) -> context` | Capability 6 (**NEW — must be added to genlab-core**) |

Capabilities 2, 4, 5 are internal components consumed by strategies/stages, not top-level interfaces.

---

## Capability 1: 7-Dimension Clip Scoring -> GamingScoringStrategy

### What It Does
Scores each clip across 7 weighted dimensions (virality_potential, entertainment_value, recency, source_authority, production_quality, chat_excitement, highlight_detection), applies publisher tier multipliers (GREEN=1.0, YELLOW=0.8, RED=0.0), and returns ranked clips.

### Source Files
| File | Role |
|------|------|
| `Gaming Clips/execution/stages/clip_scorer.py` (417 lines) | ClipScorer class, 7 scoring functions, batch scoring with median fallback |
| `Gaming Clips/config/scoring_weights.yaml` (88 lines) | All weights, thresholds, per-dimension config |
| `Gaming Clips/execution/utils/audio_analyzer.py` | Audio energy analysis (librosa RMS) for entertainment_value |
| `Gaming Clips/execution/utils/chat_excitement_scorer.py` | 5-signal chat excitement scoring |
| `Gaming Clips/execution/utils/crispy_scorer.py` (368 lines) | Neural net highlight detection (see Capability 2) |
| `Gaming Clips/config/game_registry.yaml` | Publisher tier lookups for get_publisher_tier() |

### Target Files
| File | Action |
|------|--------|
| `CriticalRush/niches/gaming/stages/score_gaming_clips.py` | **CREATE** — GamingScoringStrategy implementing `ScoringStrategy.execute()` |
| `CriticalRush/niches/gaming/tools/audio_analyzer.py` | **CREATE** — transplant audio analysis utility |
| `CriticalRush/niches/gaming/tools/chat_excitement_scorer.py` | **CREATE** — transplant chat excitement scorer |
| `CriticalRush/niches/gaming/config/scoring_weights.yaml` | **CREATE** — move scoring config |
| `CriticalRush/niches/gaming/config/game_registry.yaml` | **CREATE** — move game registry |

### genlab-core Interface
Implements `ScoringStrategy`. The `execute()` method receives `context_dict` with `stories` (clips), scores each, attaches `scores` dict and `final_score` to each clip, sorts by score, and returns updated context.

### Config vs Code
| Item | Location | Rationale |
|------|----------|-----------|
| 7 dimension weights | YAML (`scoring_weights.yaml`) | Tunable without code changes |
| Dimension thresholds (min_clip_score, min_compilation_score) | YAML | Tunable |
| Per-dimension parameters (half-life, sweet spots, baselines) | YAML | Tunable |
| Publisher tier mapping (GREEN/YELLOW/RED) | YAML (`game_registry.yaml`) | Data, not logic |
| Scoring formulas (how each dimension computes its score) | Code | Business logic — deterministic |
| Median fallback strategy for missing signals | Code | Algorithm, not config |

### Known Bugs to Fix During Transplant

**Bug: scored_clip schema missing fields**
- **Location:** `Gaming Clips/schemas/scored_clip.schema.json` lines 20-42
- **Problem:** `scores.required` only lists 5 of 7 dimensions — `highlight_detection` and `chat_excitement` are missing from both `required` and `properties`
- **Fix:** Add both fields to the schema's `properties` and `required` arrays. Transplant the corrected schema to `CriticalRush/niches/gaming/schemas/scored_clip.schema.json`

### Integration Point
`PipelineRunner` stage order: the new scoring stage runs after FETCH and FILTER, before ENRICH. It replaces or augments the existing filtering logic with score-based ranking and threshold enforcement.

---

## Capability 2: Neural Net Highlight Detection -> Component of GamingScoringStrategy

### What It Does
Pre-trained numpy MLP models (Crispy) detect kill/highlight frames in gameplay video for Valorant, CS2, and Overwatch. Scores the ratio of highlight frames to total sampled frames. Used as one of the 7 scoring dimensions (weight: 0.12).

### Source Files
| File | Role |
|------|------|
| `Gaming Clips/execution/utils/crispy_scorer.py` (368 lines) | CrispyScorer: frame sampling, game-specific preprocessing, MLP forward pass |
| `Gaming Clips/models/crispy/valorant.npy` | Valorant MLP weights |
| `Gaming Clips/models/crispy/valorant-mask.png` | Valorant HUD mask |
| `Gaming Clips/models/crispy/csgo2.npy` | CS2 MLP weights |
| `Gaming Clips/models/crispy/csgo2-mask.png` | CS2 HUD mask |
| `Gaming Clips/models/crispy/overwatch.npy` | Overwatch MLP weights |
| `Gaming Clips/config/crispy_models.yaml` | Per-game model paths + thresholds |

### Target Files
| File | Action |
|------|--------|
| `CriticalRush/niches/gaming/tools/crispy_scorer.py` | **CREATE** — transplant with Overwatch bug fix |
| `CriticalRush/niches/gaming/models/crispy/` | **CREATE** directory, copy all .npy and .png model files |
| `CriticalRush/niches/gaming/config/crispy_models.yaml` | **CREATE** — move config, update paths to new model location |

### genlab-core Interface
None — this is an internal component called by GamingScoringStrategy (Capability 1). Not a top-level strategy.

### Config vs Code
| Item | Location | Rationale |
|------|----------|-----------|
| Model file paths per game | YAML (`crispy_models.yaml`) | New games added without code changes |
| Confidence thresholds per game | YAML | Tunable |
| Frame sample rate | YAML | Performance tuning |
| min_consecutive_frames (false positive filter) | YAML | Tunable |
| Preprocessing pipelines (edge detection, color filtering, mask overlay) | Code | Game-specific image processing logic |
| MLP forward pass (sigmoid activation) | Code | Fixed neural net inference |

### Known Bugs to Fix During Transplant

**Bug: crispy_scorer O(n^2) Overwatch loops**
- **Location:** `Gaming Clips/execution/utils/crispy_scorer.py` lines 248-264
- **Problem:** `_preprocess_overwatch()` uses nested `for x / for y` loops with `getpixel()` / `putpixel()` — O(n^2) per-pixel Python operations on every sampled frame. Extremely slow for 1080p+ frames.
- **Current code:**
  ```python
  for x in range(image.width):
      for y in range(image.height):
          red = r.getpixel((x, y))
          green = g.getpixel((x, y))
          blue = b.getpixel((x, y))
          # ... putpixel calls
  ```
- **Fix:** Replace with numpy vectorized operations:
  ```python
  arr = np.array(image)  # H x W x 3
  r, g, b = arr[:,:,0], arr[:,:,1], arr[:,:,2]
  # vectorized color filtering conditions
  mask = (r > threshold) & (g < threshold) & (b < threshold)
  result = np.where(mask[..., None], arr, 0)
  image = Image.fromarray(result.astype(np.uint8))
  ```
  This converts O(n^2) Python loops to O(1) numpy operations (constant number of array ops regardless of pixel count). Expected speedup: 100-500x for 1080p frames.

---

## Capability 3: Compilation Assembly -> GamingVisualRenderStrategy

### What It Does
Two-phase system: (1) **Planning** — selects clips into themed compilations with diversity preference, pacing strategies, transition assignment, trim points, and commentary slot generation. (2) **Assembly** — FFmpeg pipeline normalizes clips to 9:16, concatenates with xfade transitions, applies text overlays (streamer name, game title, hook, CTA).

### Source Files
| File | Role |
|------|------|
| `Gaming Clips/execution/stages/compilation_planner.py` (442 lines) | Pure deterministic planning: theme detection, clip selection, pacing, transitions, trim points |
| `Gaming Clips/execution/assemble_compilation.py` (382 lines) | FFmpeg assembly: normalize, concat, transitions, overlays |
| `Gaming Clips/execution/utils/ffmpeg_utils.py` | FFmpeg helpers: probe, concat, trim, normalize, loudnorm |
| `Gaming Clips/execution/utils/ffmpeg_filters.py` | Filter chain builders: scale_crop, blurred_pillarbox, xfade, drawtext, loudnorm |
| `Gaming Clips/config/compilation_rules.yaml` | Assembly rules: length, pacing, transitions |
| `Gaming Clips/config/overlay_styles.yaml` | Text overlay templates: fonts, sizes, colors, positions |
| `Gaming Clips/config/platform_specs.yaml` | Per-platform video specs |

### Target Files
| File | Action |
|------|--------|
| `CriticalRush/niches/gaming/stages/render_gaming_video.py` | **EVOLVE** — existing file (226 lines) gains compilation planning + assembly. Implements `VisualRenderStrategy.execute()` |
| `CriticalRush/niches/gaming/tools/compilation_planner.py` | **CREATE** — transplant planning logic as pure utility |
| `CriticalRush/niches/gaming/tools/ffmpeg_utils.py` | **CREATE** — transplant FFmpeg helpers |
| `CriticalRush/niches/gaming/tools/ffmpeg_filters.py` | **CREATE** — transplant filter chain builders |
| `CriticalRush/niches/gaming/config/compilation_rules.yaml` | **CREATE** — move compilation config |
| `CriticalRush/niches/gaming/config/overlay_styles.yaml` | **CREATE** — move overlay config |
| `CriticalRush/niches/gaming/config/platform_specs.yaml` | **CREATE** — move platform specs |

### genlab-core Interface
Implements `VisualRenderStrategy`. The existing `render_gaming_video.py` already has a dual-path render (short-video-maker service -> FFmpeg fallback). The merge evolves this into a full `VisualRenderStrategy` that:
1. Runs compilation planning (theme detection, clip selection, pacing, transitions)
2. Normalizes clips to 9:16 via FFmpeg
3. Concatenates with xfade transitions
4. Applies text overlays
5. Returns context with rendered compilation paths

### Config vs Code
| Item | Location | Rationale |
|------|----------|-----------|
| Target durations, clip count ranges | YAML (`compilation_rules.yaml`) | Tunable per compilation type |
| Transition style weights (hard_cut 60%, zoom 15%, etc.) | YAML | Tunable |
| Overlay font/size/color/position | YAML (`overlay_styles.yaml`) | Design choices, not logic |
| Platform video specs (resolution, fps, codec) | YAML (`platform_specs.yaml`) | Platform requirements change |
| Pacing strategies (escalating, wave, front_loaded) | Code | Algorithm implementations |
| Theme detection logic | Code | NLP/heuristic logic |
| Diversity-first clip selection | Code | Core algorithm |
| FFmpeg filter chain construction | Code | Complex filter graph building |

### Known Bugs to Fix During Transplant
None of the 3 specified bugs apply to this capability. However, during transplant:
- Reconcile the existing `render_gaming_video.py` dual-path approach (short-video-maker -> FFmpeg) with the new full compilation assembly pipeline
- Ensure the textfile= approach for Unicode overlay text (already in CriticalRush) is preserved

---

## Capability 4: ElevenLabs Commentary -> GENERATE_AUDIO Stage

### What It Does
Generates AI voice commentary for compilations to satisfy YouTube's "substantial original creative input" requirement. Three phases: (1) Generate commentary text per slot (intro, clip_intro, hype, reaction, outro) using LLM prompts. (2) Synthesize speech via ElevenLabs (paid, primary) or edge-tts (free, fallback). (3) Duck gameplay audio at commentary timestamps and mix commentary audio in.

### Source Files
| File | Role |
|------|------|
| `Gaming Clips/execution/generate_commentary.py` (371 lines) | Commentary orchestrator: text generation, slot building, audio ducking, TTS synthesis |
| `Gaming Clips/execution/utils/tts_client.py` (139 lines) | Dual-path TTS: ElevenLabs -> edge-tts fallback, cost tracking |
| `Gaming Clips/config/content_prompts.yaml` | LLM prompts for commentary generation per style |

### Target Files
| File | Action |
|------|--------|
| `CriticalRush/niches/gaming/stages/generate_gaming_audio.py` | **CREATE** — new pipeline stage: GENERATE_AUDIO |
| `CriticalRush/niches/gaming/tools/tts_client.py` | **CREATE** — transplant dual-path TTS client |
| `CriticalRush/niches/gaming/config/content_prompts.yaml` | **CREATE** — move commentary prompts |

### genlab-core Interface
No existing interface — this becomes a **new pipeline stage** in PipelineRunner, not a strategy implementation. It runs after RENDER_VIDEO and before RENDER_TEXT_OVERLAYS (Capability 5).

New stage entry in `pipeline_runner.py`:
```python
StageConfig("GENERATE_AUDIO", "niches.gaming.stages.generate_gaming_audio", "generate_audio")
```

### Config vs Code
| Item | Location | Rationale |
|------|----------|-----------|
| Commentary prompt templates per style | YAML (`content_prompts.yaml`) | Prompt engineering is iterative |
| ElevenLabs voice ID | YAML (niche.yaml) or .env | Per-niche voice selection |
| Min commentary lines per compilation | YAML | YouTube policy compliance tunable |
| Audio ducking parameters (volume reduction, fade duration) | YAML | Audio engineering tunable |
| TTS provider priority (ElevenLabs -> edge-tts) | Code | Cost optimization logic |
| Commentary slot generation logic | Code | Business logic for slot timing |
| FFmpeg audio ducking filter chain | Code | Complex filter construction |

### Known Bugs to Fix During Transplant
None of the 3 specified bugs apply. During transplant:
- Ensure cost tracking integrates with CriticalRush's run report system
- Edge-tts fallback must work offline (no API key required)

---

## Capability 5: Whisper ASS Captions -> RENDER_TEXT_OVERLAYS Stage

### What It Does
Generates word-by-word karaoke-style captions using Whisper transcription and ASS subtitle format. Words are displayed in groups of max 5, UPPERCASE, with per-word color highlighting (`\c` color overrides in ASS BGR format). Captions are burned into video via FFmpeg `ass` filter. Positioned at 350px bottom margin for platform safe zones.

### Source Files
| File | Role |
|------|------|
| `Gaming Clips/execution/utils/ass_subtitle_generator.py` (442 lines) | ASS file generation: header, styles, word grouping, per-word color highlight, dialogue lines |
| `Gaming Clips/execution/utils/caption_generator.py` | Whisper transcription -> word-level timestamps |
| `Gaming Clips/config/captions.yaml` | Whisper model config + ASS subtitle styling |

### Target Files
| File | Action |
|------|--------|
| `CriticalRush/niches/gaming/stages/render_gaming_overlays.py` | **CREATE** — new pipeline stage: RENDER_TEXT_OVERLAYS |
| `CriticalRush/niches/gaming/tools/ass_subtitle_generator.py` | **CREATE** — transplant ASS generator |
| `CriticalRush/niches/gaming/tools/caption_generator.py` | **CREATE** — transplant Whisper captioning |
| `CriticalRush/niches/gaming/config/captions.yaml` | **CREATE** — move caption config |

### genlab-core Interface
No existing interface — this becomes a **new pipeline stage** in PipelineRunner. Runs after GENERATE_AUDIO (Capability 4).

New stage entry in `pipeline_runner.py`:
```python
StageConfig("RENDER_TEXT_OVERLAYS", "niches.gaming.stages.render_gaming_overlays", "render_overlays")
```

### Config vs Code
| Item | Location | Rationale |
|------|----------|-----------|
| Whisper model name/size | YAML (`captions.yaml`) | Model selection tunable |
| ASS style parameters (font, size, color, margins) | YAML | Design choices |
| Highlight color (BGR format) | YAML | Branding tunable |
| Max words per group | YAML | Readability tunable |
| Word grouping algorithm | Code | Display logic |
| ASS dialogue line construction with `\c` overrides | Code | ASS format specification |
| FFmpeg ass filter invocation | Code | Filter construction |

### Known Bugs to Fix During Transplant
None of the 3 specified bugs apply. During transplant:
- Verify ASS BGR color format is correctly documented in config comments (common confusion: BGR `&H0000FF&` = red, not blue)
- Ensure fontsdir path resolves correctly in new directory structure

---

## Capability 6: Thompson Sampling Bandits -> GamingPerformanceLearner

### What It Does
Multi-armed bandit system using Thompson Sampling with independent Beta-distributed arms per dimension, per platform. Optimizes content formatting decisions (hook style, caption style, posting time, etc.) based on engagement feedback. Includes non-stationary discount factors, exploration boost, decay detection, and schema migration for adding/removing arms.

### Source Files
| File | Role |
|------|------|
| `Gaming Clips/execution/utils/bandit_optimizer.py` (380 lines) | BanditOptimizer: Thompson Sampling, Beta posteriors, select/update/discount/explore |
| `Gaming Clips/execution/utils/platform_bandit_manager.py` (134 lines) | PlatformBanditManager: per-platform optimizer wrapping, decay detection |
| `Gaming Clips/config/bandit_dimensions.yaml` (35 lines) | Bandit arm definitions + engagement thresholds |
| `Gaming Clips/config/platform_bandits.yaml` (59 lines) | Per-platform dimensions, arms, discount factors |
| `Gaming Clips/config/bandit_states/` | Persisted per-platform Beta parameters (JSON) |

### Target Files
| File | Action |
|------|--------|
| `CriticalRush/niches/gaming/tools/bandit_optimizer.py` | **CREATE** — transplant core Thompson Sampling engine |
| `CriticalRush/niches/gaming/tools/platform_bandit_manager.py` | **CREATE** — transplant platform wrapper |
| `CriticalRush/niches/gaming/stages/learn_gaming_performance.py` | **CREATE** — GamingPerformanceLearner implementing new `PerformanceLearner.execute()` |
| `CriticalRush/niches/gaming/config/bandit_dimensions.yaml` | **CREATE** — move + fix dimension names |
| `CriticalRush/niches/gaming/config/platform_bandits.yaml` | **CREATE** — move + fix dimension names |
| `CriticalRush/niches/gaming/config/bandit_states/` | **CREATE** directory for persisted state |
| `genlab-core/genlab_core/strategies.py` | **MODIFY** — add `PerformanceLearner` abstract interface |

### genlab-core Interface
**NEW INTERFACE REQUIRED.** genlab-core currently has 6 abstract strategies but no `PerformanceLearner`. Must add:

```python
class PerformanceLearner(ABC):
    """Learns from engagement feedback to optimize content strategy."""
    @abstractmethod
    def execute(self, context: Any) -> Any:
        ...
```

The GamingPerformanceLearner stage runs at the end of the pipeline (after PUBLISH, before WRITE_REPORT). It:
1. Fetches engagement metrics for recently published posts
2. Classifies success/failure/neutral per engagement thresholds
3. Updates Beta posteriors via `BanditOptimizer.update_binary()`
4. Applies discount for non-stationarity
5. Runs decay detection
6. Persists state to bandit_states/

### Config vs Code
| Item | Location | Rationale |
|------|----------|-----------|
| Dimension names and arms per dimension | YAML (`bandit_dimensions.yaml`) | Arms added/removed without code changes |
| Per-platform dimension overrides | YAML (`platform_bandits.yaml`) | Platform-specific tuning |
| Engagement thresholds (success CR>0.70, failure CR<0.40) | YAML | Tunable |
| Discount factors per platform | YAML | Platform algorithm decay rates differ |
| Thompson Sampling algorithm (Beta draw, argmax) | Code | Core algorithm |
| Binary classification logic (success/failure/neutral) | Code | Business logic |
| Schema migration (add/remove arms) | Code | Data migration logic |
| Decay detection algorithm | Code | Statistical analysis |

### Known Bugs to Fix During Transplant

**Bug: Bandit dimension name mismatch**
- **Location:** `Gaming Clips/config/bandit_dimensions.yaml` vs `Gaming Clips/config/platform_bandits.yaml`
- **Problem:** The two config files use different names for the same dimensions:

  | bandit_dimensions.yaml | platform_bandits.yaml | Resolution |
  |----------------------|---------------------|------------|
  | `hook_style` | `hook_type` | Standardize to `hook_style` |
  | `caption_style` | `caption_keyword_style` | Standardize to `caption_style` |
  | `posting_time` | `posting_window` | Standardize to `posting_time` |

- **Additional problem:** `platform_bandits.yaml` contains dimensions not defined in `bandit_dimensions.yaml`: `duration_bucket`, `thumbnail_style`, `audio_type`, `cta_type`. These need to either be added to `bandit_dimensions.yaml` or removed from `platform_bandits.yaml`.
- **Fix:** Standardize all dimension names in both files. Add missing dimensions to `bandit_dimensions.yaml` with proper arm definitions and thresholds. Validate that `BanditOptimizer.load()` schema migration handles the rename gracefully (it currently handles add/remove but not rename — may need a one-time migration).

---

## Pipeline Stage Order (Post-Merge)

```
Current CriticalRush (9 stages):
  1. FETCH_STORIES
  2. FILTER_STORIES
  3. ENRICH_STORIES
  4. EXTRACT_MEDIA
  5. WRITE_CONTENT
  6. RENDER_VIDEO
  7. PUSH_TO_BACKLOG
  8. PUBLISH
  9. WRITE_REPORT

Post-Merge (13 stages):
  1. FETCH_STORIES          (existing)
  2. FILTER_STORIES         (existing)
  3. SCORE_CLIPS            (NEW - Capability 1+2: GamingScoringStrategy)
  4. ENRICH_STORIES         (existing)
  5. EXTRACT_MEDIA          (existing)
  6. WRITE_CONTENT          (existing)
  7. RENDER_VIDEO           (EVOLVED - Capability 3: GamingVisualRenderStrategy)
  8. GENERATE_AUDIO         (NEW - Capability 4: commentary + TTS)
  9. RENDER_TEXT_OVERLAYS    (NEW - Capability 5: Whisper ASS captions)
  10. PUSH_TO_BACKLOG       (existing)
  11. PUBLISH               (existing)
  12. LEARN_PERFORMANCE     (NEW - Capability 6: GamingPerformanceLearner)
  13. WRITE_REPORT          (existing)
```

Note: SCORE_CLIPS runs before ENRICH because scoring determines which clips proceed (threshold enforcement). GENERATE_AUDIO runs before RENDER_TEXT_OVERLAYS because caption burn-in is the final video modification. LEARN_PERFORMANCE runs after PUBLISH because it needs engagement data from published posts.

---

## Implementation Phases

### Phase 1: Scoring (Capabilities 1 + 2)
- Add `PerformanceLearner` to genlab-core strategies.py
- Transplant crispy_scorer.py with Overwatch vectorization fix
- Transplant clip_scorer.py as GamingScoringStrategy
- Transplant audio_analyzer.py, chat_excitement_scorer.py
- Move scoring_weights.yaml, game_registry.yaml, crispy_models.yaml, model files
- Fix scored_clip schema
- Wire SCORE_CLIPS stage into pipeline_runner.py
- Test: score a batch of clips, verify all 7 dimensions produce values, verify schema validates

### Phase 2: Compilation (Capability 3)
- Transplant compilation_planner.py, ffmpeg_utils.py, ffmpeg_filters.py
- Evolve render_gaming_video.py into GamingVisualRenderStrategy
- Move compilation_rules.yaml, overlay_styles.yaml, platform_specs.yaml
- Reconcile existing dual-path render with new compilation assembly
- Test: plan and assemble a compilation from scored clips

### Phase 3: Audio + Captions (Capabilities 4 + 5)
- Transplant generate_commentary.py, tts_client.py, ass_subtitle_generator.py, caption_generator.py
- Create GENERATE_AUDIO and RENDER_TEXT_OVERLAYS stages
- Move content_prompts.yaml, captions.yaml
- Wire stages into pipeline_runner.py
- Test: generate commentary + captions for a compiled video

**Implementation notes:**
- GENERATE_AUDIO and RENDER_TEXT_OVERLAYS must be separate stage classes with independent error handling.
- GENERATE_AUDIO fallback: if ElevenLabs fails or LLM script generation fails, fall back to background music only (no commentary).
- RENDER_TEXT_OVERLAYS fallback: if Whisper fails, fall back to FFmpeg simple text overlay.
- A failure in one must never cascade to the other.

### Phase 4: Learning (Capability 6)
- **Pre-implementation:** Read `Content Scraper/execution/performance_learner.py` first. Understand how Blackbox Brief handles the 24-48 hour analytics delay problem. Document the decision: does LEARN_PERFORMANCE run inline (same pipeline run as PUBLISH) or as a separate cron job? This decision affects bandit state schema. Default assumption: separate cron job, because platform analytics are not available immediately after publishing.
- Transplant bandit_optimizer.py, platform_bandit_manager.py
- Fix bandit dimension name mismatch across both config files
- Create GamingPerformanceLearner stage
- Wire LEARN_PERFORMANCE stage into pipeline_runner.py
- Test: simulate engagement feedback, verify Beta posterior updates

### Phase 5: Verification + Cleanup
- End-to-end pipeline test: FETCH -> ... -> LEARN_PERFORMANCE -> WRITE_REPORT
- Verify all configs load from new paths
- Verify all model files accessible
- Delete Gaming Clips directory

---

## Files NOT Being Transplanted

The following Gaming Clips components are **excluded** from this merge because CriticalRush already has equivalents or they are not needed:

| Gaming Clips File | Reason for Exclusion |
|-------------------|---------------------|
| `execution/sources/*_fetcher.py` (6 fetchers) | CriticalRush has `fetch_gaming_stories.py` with its own fetchers |
| `execution/utils/stable_ids.py` | CriticalRush has its own ID generation |
| `execution/utils/cache.py` | CriticalRush has its own caching |
| `execution/utils/text_sanitizer.py` | CriticalRush has its own sanitization |
| `execution/utils/backlog_client.py` | CriticalRush has its own backlog integration |
| `execution/utils/rate_limiter.py` | CriticalRush has its own rate limiting |
| `execution/utils/video_hasher.py` | **MOVED TO PHASE 2** — perceptual dedup (not identity dedup). Prevents duplicate clips across sources (same viral clip re-uploaded to Twitch + YouTube + Reddit). Runs as pre-assembly dedup step in Phase 2. |
| `execution/utils/scheduling.py` | CriticalRush has its own scheduling |
| `execution/utils/script_bootstrap.py` | CriticalRush has its own bootstrap |
| `execution/utils/*_client.py` (platform API clients) | CriticalRush has its own platform clients |
| `directives/` (12 SOPs) | CriticalRush has its own pipeline flow |
| `inspo_library/` | Reference material, not runtime code |
| `runbooks/` | CriticalRush has its own orchestration |
| `schemas/` (except scored_clip) | CriticalRush has its own schemas |
| `assets/` | Evaluate during Phase 2 — fonts/transitions may be needed |

---

## Risk Register

| Risk | Mitigation |
|------|------------|
| genlab-core interface change breaks other niches | PerformanceLearner is additive (new class), not a modification. No existing code affected. |
| FFmpeg filter chains conflict between existing render and new compilation assembly | Phase 2 explicitly reconciles the two approaches. Existing dual-path preserved as fallback. |
| Model files (.npy) large in git | Models are small (<1MB each). Acceptable in repo. |
| Bandit state migration (rename dimensions) | One-time migration script during Phase 4. Old state files backed up before migration. |
| Missing Python dependencies (scipy, librosa, faster-whisper, opencv-python-headless) | Add to CriticalRush requirements.txt during Phase 1. |

---

## Summary of All Bug Fixes

| # | Bug | Source Location | Fix | Phase |
|---|-----|----------------|-----|-------|
| 1 | Bandit dimension name mismatch | `config/bandit_dimensions.yaml` vs `config/platform_bandits.yaml` | Standardize names, add missing dimensions | Phase 4 |
| 2 | crispy_scorer O(n^2) Overwatch loops | `execution/utils/crispy_scorer.py:248-264` | Replace getpixel/putpixel with numpy vectorized ops | Phase 1 |
| 3 | scored_clip schema missing fields | `schemas/scored_clip.schema.json` | Add highlight_detection + chat_excitement to properties and required | Phase 1 |

---

## Completion Summary

**All 5 phases complete. Gaming Clips deleted. 195/195 tests passing.**

### Phase Results

| Phase | Scope | Tests | Status |
|-------|-------|-------|--------|
| Phase 1 | Scoring (Capabilities 1+2) | 29 | PASS |
| Phase 2 | Compilation (Capability 3) | 43 | PASS |
| Phase 3 | Audio + Captions (Capabilities 4+5) | 28 | PASS |
| Phase 4 | Learning (Capability 6) | 43 | PASS |
| Phase 5 | End-to-end verification + cleanup | 52 pre-existing (5 fixed) | PASS |

### Bug Fixes Applied

| # | Bug | Resolution |
|---|-----|------------|
| 1 | Bandit dimension name mismatch | Only `platform_bandits.yaml` transplanted — single source of truth. `bandit_dimensions.yaml` NOT transplanted. |
| 2 | crispy_scorer O(n²) Overwatch loops | Replaced `getpixel`/`putpixel` with numpy vectorized ops in `_preprocess_overwatch()` |
| 3 | scored_clip schema missing fields | Added `highlight_detection` and `chat_excitement` to schema properties and required arrays |

### Phase 5 Verification Steps

1. **5.1 — Dangling references:** No functional references to Gaming Clips found
2. **5.2 — YAML config loading:** All 12 config files load cleanly
3. **5.3 — Model files:** All 3 .npy model files load cleanly
4. **5.4 — End-to-end dry run:** All 12 pipeline stages execute without exceptions; PlatformBanditManager loaded 4 platforms
5. **5.5 — Learner dry run:** GamingPerformanceLearner processes 0 records, no exceptions
6. **5.6 — Pre-existing test fixes:** 5 tests fixed (mock setup mismatches: `monkeypatch.setenv` → `patch("genlab_core.settings.settings")`)
7. **5.7 — Full test suite:** 195/195 passing
8. **5.8 — Gaming Clips deleted:** `rm -rf "/Users/anarchistsid/GenLab/Gaming Clips"`
9. **5.9 — Merge plan updated:** This section

### Key Architectural Decisions

- **LEARN_PERFORMANCE** runs as a separate scheduled job, NOT inline with the pipeline (see `docs/phase4_learner_decision.md`)
- **Bandit selection** happens at publish time (read-only `select()`); posterior updates happen hours/days later
- **Discount timing** is correct: applied BEFORE the Beta update, not after
- **GENERATE_AUDIO** and **RENDER_TEXT_OVERLAYS** are separate stage classes with independent error handling (Amendment 2)
- **Pipeline stage count:** 12 stages (LEARN_PERFORMANCE is not a pipeline stage — it's a standalone CLI/cron job)
