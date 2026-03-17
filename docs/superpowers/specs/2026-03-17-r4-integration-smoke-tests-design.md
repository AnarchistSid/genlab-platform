# R4: End-to-End Pipeline Smoke Tests

**Goal**: Add integration tests that verify the full pipeline works stage-by-stage for each niche.
**Effort**: ~3h

## Problem

4,076 unit tests but zero integration tests that run a mini-pipeline. Unit tests mock everything; integration tests verify the stages actually compose correctly.

## Changes

### 1. Create `genlab-core/tests/integration/test_pipeline_smoke.py`

One parameterized test per niche that:
1. Creates a mock BacklogClient (in-memory dict, no SharePoint)
2. Creates mock YouTube API responses (from fixture JSON)
3. Runs the pipeline with all stages in order
4. Asserts: stories fetched > 0, blueprints composed > 0, no exceptions
5. Asserts: run_report written with expected keys

### 2. Create mock fixtures

- `tests/integration/fixtures/mock_youtube_trending.json` — 3 mock trending videos
- `tests/integration/fixtures/mock_sharepoint_empty.json` — empty lists
- `tests/integration/fixtures/mock_anthropic_response.json` — mock LLM content

### 3. Mock strategy for FFmpeg (skip real rendering)

```python
class MockVisualRenderStrategy(VisualRenderStrategy):
    def execute(self, context):
        for story in context.get("stories", []):
            story.setdefault("media", {})["rendered_path"] = "/tmp/mock_reel.mp4"
        return context
```

### 4. Pytest marker

```python
@pytest.mark.integration
def test_pipeline_smoke_gaming(mock_apis):
    ...
```

Run with: `pytest -m integration` (excluded from default `pytest` runs via `pyproject.toml`)

## Files

| File | Change |
|---|---|
| `genlab-core/tests/integration/__init__.py` | NEW |
| `genlab-core/tests/integration/test_pipeline_smoke.py` | NEW — 5 parameterized tests |
| `genlab-core/tests/integration/conftest.py` | NEW — shared mock fixtures |
| `genlab-core/tests/integration/fixtures/` | NEW — 3 JSON fixture files |
| `genlab-core/pyproject.toml` | Add `integration` marker, exclude from default |
