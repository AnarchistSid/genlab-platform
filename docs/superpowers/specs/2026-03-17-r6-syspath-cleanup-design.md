# R6: sys.path Cleanup

**Goal**: Eliminate 133 `sys.path.insert` hacks in BlackboxBrief by using proper package imports.
**Effort**: ~4h

## Problem

133 `sys.path.insert(0, ...)` calls in BlackboxBrief. These cause:
- Import order bugs (wrong module loaded)
- Fragile test discovery
- "Module not found" errors that disappear on retry
- Prevents proper IDE navigation

## Approach

BlackboxBrief is already a uv workspace member. The issue is that `execution/` scripts use `sys.path.insert(0, PROJECT_ROOT)` to import sibling modules instead of using the package structure.

### Fix Strategy

1. Ensure `execution/__init__.py` and `execution/utils/__init__.py` exist (may already)
2. Create a thin `bb` package namespace so scripts can do `from bb.execution.utils.video_downloader import ...`
3. Replace `sys.path.insert` + bare `from execution.X import Y` with proper imports
4. Keep test conftest.py sys.path (pytest convention, acceptable)

### Migration waves

**Wave 1**: `execution/utils/*.py` (16 files) — these are imported by everything else
**Wave 2**: `execution/*.py` (25 files) — main pipeline scripts
**Wave 3**: `scripts/*.py` and `setup/*.py` (12 files) — one-off scripts

For each file:
1. Remove `sys.path.insert(0, ...)` lines
2. Change `from execution.utils.X import Y` to relative or absolute import
3. Run `uv run pytest tests/` to verify

### Keep as-is

- `tests/conftest.py` sys.path (pytest needs it)
- `bb_strategies/*.py` BB_ROOT insert (strategy wrappers, different pattern)

## Files

| File | Change |
|---|---|
| `BlackboxBrief/execution/__init__.py` | Ensure exists |
| `BlackboxBrief/execution/utils/__init__.py` | Ensure exists |
| ~40 Python files in execution/, scripts/, setup/ | Remove sys.path.insert, fix imports |
| `BlackboxBrief/tests/` | Verify all tests still pass after each wave |
