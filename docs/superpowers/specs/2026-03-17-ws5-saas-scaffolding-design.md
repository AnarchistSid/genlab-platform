# WS5: SaaS Scaffolding

**Goal**: G5 SaaS Architecture 62% → 78%
**Effort**: ~5h
**Dependencies**: None

## Problem

1. No `create_niche.py` scaffold — adding a niche requires ~30 min of manual copy-paste
2. No `validate_configs.py` — config errors discovered at runtime
3. No `--dry-run` flag for testing new niche pipelines
4. 7 residual `ai_news` hardcodes in genlab-core
5. Rate limiter not wired into CW, SR, FD (B17)

## Changes

### 1. `create_niche.py` — `genlab-core/src/genlab_core/tools/create_niche.py` (NEW)

CLI script that scaffolds a new niche from template:

```
Usage: uv run python -m genlab_core.tools.create_niche \
    --niche-id fitness \
    --brand-name "FitPulse" \
    --accent-color "#00FF88" \
    --output-dir /Users/anarchistsid/GenLab/FitPulse
```

Steps:
1. Copy `CriticalRush/niches/_template/` to `{output_dir}/`
2. String-replace `{NICHE_ID}` → `fitness`, `{BRAND_NAME}` → `FitPulse`, `{ACCENT_COLOR}` → `#00FF88`
3. Rename strategy classes: `TemplateContentResearchStrategy` → `FitnessContentResearchStrategy`
4. Generate `config/persona.yaml` with sensible defaults
5. Create `assets/` directory with placeholder logo instructions
6. Print registration checklist:
   - Add to `pipeline_runner.NICHE_ROOTS`
   - Add to `dashboard/configs/niches_registry.yaml`
   - Add platform credentials to `.env`
   - Run `validate_configs.py` to verify

### 2. `validate_configs.py` — `genlab-core/src/genlab_core/tools/validate_configs.py` (NEW)

Pydantic validators for all 7 YAML config schemas:

```python
class NicheYamlSchema(BaseModel):
    niche_id: str
    brand_name: str
    accent_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    video_gate: Literal["require"] = "require"
    fallback_to_text_render: Literal[False] = False

class SourcesYamlSchema(BaseModel):
    tier_1: TierConfig
    tier_2: Optional[TierConfig] = None
    content_filter: Optional[ContentFilterConfig] = None

class PublishingYamlSchema(BaseModel):
    platforms: Dict[str, PlatformConfig]

# ... ScheduleYaml, ScoringWeightsYaml, VisualsYaml, TemplatesYaml
```

Usage:
```bash
uv run python -m genlab_core.tools.validate_configs --niche-dir /path/to/FitPulse
# or validate all niches:
uv run python -m genlab_core.tools.validate_configs --all
```

Reports: missing required fields, invalid types, unreachable URLs, missing env vars.

### 3. `--dry-run` flag on GenericPipelineRunner

Add `dry_run: bool = False` parameter to `GenericPipelineRunner.__init__()`.

When `dry_run=True`:
- `BacklogClient` methods become no-ops (log what would be written)
- Platform publish functions become no-ops (log what would be published)
- Video download proceeds normally (needed for rendering test)
- LLM calls proceed normally (needed for content quality test)
- Video rendering proceeds normally

Implementation: inject a `DryRunBacklogClient` proxy that wraps all write methods with logging.

### 4. Clean up 7 residual `ai_news` hardcodes

Replace with `ai_creators` (the canonical niche_id) in:

| File | Line | Change |
|---|---|---|
| `media/video_sourcer.py` | 26 | `"ai_news"` → `"ai_creators"` in subreddit map |
| `media/trending_video_fetcher.py` | 79, 114, 130 | Update YOUTUBE_CATEGORIES, NICHE_SEARCH_KEYWORDS, QUOTA keys |
| `media/download_top_videos.py` | 33 | Update keyword map |
| `intel/google_trends.py` | 28, 37 | Update category/seed maps |
| `engagement/comment_processor.py` | 101-102 | Remove `ai_news` alias handling |
| `scoring/composite_scorer.py` | 41 | Update velocity threshold key |

Keep backward compatibility: add `ai_news` as alias in `_NICHE_ALIASES` where lookups happen, so existing SharePoint data still works.

### 5. Wire rate_limiter into CW, SR, FD

Add `TokenBucket` rate limiting to each niche's content research strategy for API calls:

```python
# In cw_strategies/content_research.py
from genlab_core.ratelimit.token_bucket import TokenBucket

_yt_limiter = TokenBucket(rate=5, capacity=10)  # 5 req/s burst 10

class SportContentResearchStrategy(ContentResearchStrategy):
    def execute(self, context):
        _yt_limiter.acquire()  # Block if over rate
        # ... existing YouTube/API calls
```

## Files Modified/Created

| File | Change |
|---|---|
| `genlab-core/src/genlab_core/tools/create_niche.py` | NEW — scaffold script |
| `genlab-core/src/genlab_core/tools/validate_configs.py` | NEW — config validator |
| `genlab-core/src/genlab_core/pipeline/pipeline_runner.py` | Add --dry-run flag |
| `genlab-core/src/genlab_core/http/backlog_client.py` | Add DryRunBacklogClient |
| `genlab-core/src/genlab_core/media/video_sourcer.py` | ai_news → ai_creators |
| `genlab-core/src/genlab_core/media/trending_video_fetcher.py` | ai_news → ai_creators |
| `genlab-core/src/genlab_core/media/download_top_videos.py` | ai_news → ai_creators |
| `genlab-core/src/genlab_core/intel/google_trends.py` | ai_news → ai_creators |
| `genlab-core/src/genlab_core/engagement/comment_processor.py` | Remove ai_news alias |
| `genlab-core/src/genlab_core/scoring/composite_scorer.py` | ai_news → ai_creators |
| `ClutchWire/cw_strategies/content_research.py` | Add rate limiter |
| `SpliceReel/sr_strategies/content_research.py` | Add rate limiter |
| `FrameDrift/fd_strategies/content_research.py` | Add rate limiter |
| `genlab-core/tests/tools/test_create_niche.py` | NEW |
| `genlab-core/tests/tools/test_validate_configs.py` | NEW |

## Validation

- `uv run python -m genlab_core.tools.create_niche --niche-id test_niche --brand-name "TestBrand" --accent-color "#FF0000" --output-dir /tmp/test_niche` creates valid structure
- `uv run python -m genlab_core.tools.validate_configs --niche-dir /tmp/test_niche` passes
- `grep -rn "ai_news" genlab-core/src/ --include="*.py" | grep -v test_ | grep -v alias | grep -v comment` returns 0 results
- Pipeline with `--dry-run` completes without writing to SharePoint
- All existing tests pass

## Risks

- `ai_news` → `ai_creators` rename in code may break if any external system still uses `ai_news` — mitigated by keeping alias in lookups
- DryRunBacklogClient must intercept ALL write paths — verify with integration test
