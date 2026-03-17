# GenLab Upgrade Sweep — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clean up dead dependencies, consolidate brand assets, update stale packages, remove SharePoint remnants, and reduce LaunchAgent sprawl.

**Architecture:** Non-breaking incremental changes across all 7 workspace members. Each task is independently committable and testable.

**Tech Stack:** Python 3.14, uv workspace, PostgreSQL (psycopg2), React 19 + Vite 7

---

## Chunk 1: Dependency Cleanup (Tier 1)

### Task 1: Remove dead Python deps from genlab-core

**Files:**
- Modify: `genlab-core/pyproject.toml`

- [ ] **Step 1: Remove asyncpg and sqlalchemy from dependencies**

Remove `asyncpg>=0.29` and `sqlalchemy>=2.0` from `[project] dependencies`. Keep `alembic>=1.13` (has active migrations dir). Keep `psycopg2-binary>=2.9`.

- [ ] **Step 2: Run uv lock to regenerate lockfile**

Run: `uv lock`

- [ ] **Step 3: Verify genlab-core tests still pass**

Run: `uv run --package genlab-core pytest genlab-core/tests/ -k "not postgres and not integration" --tb=short -q`

- [ ] **Step 4: Remove sqlalchemy from dashboard deps**

Remove `sqlalchemy>=2.0` from `dashboard/pyproject.toml` (zero imports confirmed).

- [ ] **Step 5: Run uv lock + dashboard tests**

Run: `uv lock && uv run --package genlab-dashboard pytest dashboard/tests/ --tb=short -q`

- [ ] **Step 6: Commit**

```
feat(deps): remove unused asyncpg and sqlalchemy dependencies
```

### Task 2: Move AI SDK deps to genlab-core

**Files:**
- Modify: `genlab-core/pyproject.toml`
- Modify: `BlackboxBrief/pyproject.toml`

- [ ] **Step 1: Add anthropic and openai to genlab-core deps**
- [ ] **Step 2: Remove from BB (inherited via genlab-core)**
- [ ] **Step 3: uv lock + verify BB tests pass**
- [ ] **Step 4: Commit**

### Task 3: Update yt-dlp pin

**Files:**
- Modify: `CriticalRush/pyproject.toml`

- [ ] **Step 1: Update yt-dlp>=2024.1.1 to yt-dlp>=2025.3**
- [ ] **Step 2: uv lock + verify CR tests pass**
- [ ] **Step 3: Commit**

## Chunk 2: Root-Level Cleanup (Tier 1)

### Task 4: Move root brand PNGs to channel assets/

- [ ] **Step 1: Create CriticalRush/assets/ if needed**
- [ ] **Step 2: Move each PNG to its channel's assets/ dir**
- [ ] **Step 3: Delete debug screenshots (blueprints-*.png)**
- [ ] **Step 4: Commit**

## Chunk 3: SharePoint Removal (Tier 2)

### Task 5: Remove SharePoint code paths

**Files:**
- Modify: `genlab-core/pyproject.toml` — drop msgraph-sdk, azure-identity from optional deps
- Modify: `genlab-core/src/genlab_core/http/backlog_client.py` — remove SharePoint path
- Modify: `dashboard/server/core/graph_sync.py` — remove SharePoint path
- Delete: `genlab-core/src/genlab_core/http/graph_proxy.py` (replaced by formula_sql.py)
- Modify: `BlackboxBrief/pyproject.toml` — drop msgraph-sdk, azure-identity

### Task 6: Add GitHub Actions CI

**Files:**
- Create: `.github/workflows/test.yml`

## Chunk 4: LaunchAgent Consolidation (Tier 2)

### Task 7: Consolidate engagement pollers into single multi-niche plist
### Task 8: Consolidate fetch-insights into single plist with internal scheduling
### Task 9: Consolidate per-niche publishers into unified publisher
