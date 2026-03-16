# Environment & Config Consolidation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate cross-channel contamination — move shared credentials to a root `.env`, move shared configs to `genlab-core/config/`, remove all `sys.path` hacks and hardcoded "Content Scraper" paths, update all 40 launchd plists.

**Architecture:** Create `GenLab/.env` as the single shared env file. Each niche keeps ONLY its own prefixed credentials in `{Niche}/.env`. Move `lists_config.yaml`, `platform_caps.yaml`, `disk_quota.yaml` from Content Scraper to `genlab-core/config/`. Update all Python code that references Content Scraper paths to use `genlab_core.settings` resolution. Update all plists.

**Tech Stack:** Python, YAML, launchd plists, dotenv

---

## Audit Summary (from 2026-03-16 session)

### What needs to move

| What | From | To |
|---|---|---|
| 48 cross-channel env vars | `Content Scraper/.env` | `GenLab/.env` (shared) + per-niche `.env` |
| `lists_config.yaml` | `Content Scraper/config/` | `genlab-core/config/` |
| `platform_caps.yaml` | `Content Scraper/config/` | `genlab-core/config/` |
| `disk_quota.yaml` | `Content Scraper/config/` | `genlab-core/config/` |
| `execution/check_token_health.py` | `Content Scraper/execution/` | `genlab-core/src/genlab_core/monitoring/` |
| `execution/utils/youtube_client.py` | `Content Scraper/execution/` | `genlab-core/src/genlab_core/platforms/` |

### What needs path updates

| File | Current reference | Fix |
|---|---|---|
| `daily_cap.py:31` | `Content Scraper/config/platform_caps.yaml` | `genlab-core/config/platform_caps.yaml` |
| `quota_daemon.py:44` | `Content Scraper/config/disk_quota.yaml` | `genlab-core/config/disk_quota.yaml` |
| `trending_video_fetcher.py:825-826` | `Content Scraper/config/sources.yaml` | niche_root resolution |
| `run_monetisation_tracker.py:16` | `Content Scraper` .env loading | root .env |
| `review_server.py:33` | `Content Scraper` sys.path | genlab_core imports |
| `token_health.py:148,161` | `from execution.check_token_health` | `from genlab_core.monitoring` |
| `blueprints.py:26` | `Content Scraper` media path | niche_root resolution |
| `schedule.py:23` | `Content Scraper` | niche_root resolution |
| `config_routes.py:55` | `Content Scraper/config/` | `genlab-core/config/` |
| `engagement.py:112` | `Content Scraper` | genlab_core |

### 21 launchd plists to update
All plists with `BACKLOG_CONFIG_PATH` pointing to `Content Scraper/config/lists_config.yaml` → change to `genlab-core/config/lists_config.yaml`.

### 7 scripts with sys.path hacks
`social_analytics.py`, `viral_detector.py`, `content_memory.py`, `token_health.py`, `backfill_*.py`, `seed_bandit_arms.py` — all need `sys.path.insert(Content Scraper)` removed, replaced with proper uv workspace imports.

---

## Chunk 1: Create root .env and split Content Scraper/.env

### Task 1: Create GenLab/.env with shared credentials

**Files:**
- Create: `GenLab/.env`
- Modify: `Content Scraper/.env`
- Modify: `.gitignore` (ensure `.env` is listed)

- [ ] **Step 1: Identify shared vs BB-only vars in Content Scraper/.env**
Shared: `AZURE_*`, `SHAREPOINT_*`, `YOUTUBE_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.
BB-only: `META_ACCESS_TOKEN`, `META_IG_USER_ID`, `META_IG_APP_ID`, `META_IG_APP_SECRET`, `FB_PAGE_ACCESS_TOKEN`, `META_FB_PAGE_ID`
Cross-channel (move to root): All `CRITICALRUSH_*`, `CLUTCHWIRE_*`, `SPLICEREEL_*`, `FRAMEDRIFT_*` vars

- [ ] **Step 2: Create GenLab/.env**
Copy shared vars + all per-niche prefixed vars to `GenLab/.env`. Add header comment:
```
# GenLab shared environment — loaded by all pipeline runners and services
# Per-niche credentials use PREFIX_ convention (CRITICALRUSH_, CLUTCHWIRE_, etc.)
# BB (ai_creators) uses unprefixed vars as the legacy default
```

- [ ] **Step 3: Strip cross-channel vars from Content Scraper/.env**
Remove all `CRITICALRUSH_*`, `CLUTCHWIRE_*`, `SPLICEREEL_*`, `FRAMEDRIFT_*` lines.
Keep only BB's own vars + shared vars that BB needs.

- [ ] **Step 4: Verify .gitignore covers GenLab/.env**

- [ ] **Step 5: Update genlab_core settings.py to load root .env**
Add root `.env` loading in `settings.py` so all genlab-core consumers get the shared vars.

- [ ] **Step 6: Test that pipeline runners still find credentials**
Run: `uv run --package genlab-core python -c "from genlab_core.publishing.niche_credentials import resolve_meta_credentials; print(resolve_meta_credentials('gaming'))"`

- [ ] **Step 7: Commit**

### Task 2: Copy per-niche credentials to each niche's .env

**Files:**
- Modify: `CriticalRush/.env`, `ClutchWire/.env`, `SpliceReel/.env`, `FrameDrift/.env`

- [ ] **Step 1: For each niche, ensure its own .env has its prefixed vars**
These should already be partially done from the FB token fix earlier. Verify all platform vars are present.

- [ ] **Step 2: Commit**

---

## Chunk 2: Move shared configs to genlab-core

### Task 3: Move lists_config.yaml, platform_caps.yaml, disk_quota.yaml

**Files:**
- Create: `genlab-core/config/lists_config.yaml` (copy from Content Scraper)
- Create: `genlab-core/config/platform_caps.yaml` (copy from Content Scraper)
- Create: `genlab-core/config/disk_quota.yaml` (copy from Content Scraper)
- Keep originals as symlinks for backward compatibility

- [ ] **Step 1: Copy configs**
- [ ] **Step 2: Create symlinks in Content Scraper/config/ pointing to genlab-core/config/**
- [ ] **Step 3: Update all Python files that hardcode Content Scraper paths** (6 files listed above)
- [ ] **Step 4: Run tests**
- [ ] **Step 5: Commit**

### Task 4: Update all launchd plists

**Files:**
- Modify: 14 plists with `BACKLOG_CONFIG_PATH`
- Modify: quota-monitor plist

- [ ] **Step 1: sed replace across all plists**
```bash
for plist in ~/Library/LaunchAgents/com.genlab.*.plist; do
  sed -i '' 's|Content Scraper/config/lists_config.yaml|genlab-core/config/lists_config.yaml|g' "$plist"
  sed -i '' 's|Content Scraper/config/disk_quota.yaml|genlab-core/config/disk_quota.yaml|g' "$plist"
done
```

- [ ] **Step 2: Reload all affected plists**
- [ ] **Step 3: Verify services start correctly**

---

## Chunk 3: Remove sys.path hacks

### Task 5: Fix 7 scripts in /scripts/

**Files:**
- Modify: `scripts/social_analytics.py`, `scripts/viral_detector.py`, `scripts/content_memory.py`, `scripts/token_health.py`, `scripts/backfill_*.py`, `scripts/seed_bandit_arms.py`

- [ ] **Step 1: Replace sys.path hacks with proper imports**
All these scripts should be run via `uv run --package genlab-core` which puts genlab_core on the path automatically. Remove `sys.path.insert(0, '/Users/anarchistsid/GenLab/Content Scraper')` lines.

- [ ] **Step 2: For scripts that import `execution.*`**, either:
  - Move the needed function to genlab_core (preferred)
  - Or use `uv run --project "Content Scraper"` for BB-specific scripts

- [ ] **Step 3: Run each script with --help or dry-run to verify**
- [ ] **Step 4: Commit**

### Task 6: Fix dashboard imports

**Files:**
- Modify: `dashboard/server/review_server.py`
- Modify: `dashboard/server/api/token_health.py`
- Modify: `dashboard/server/api/blueprints.py`
- Modify: `dashboard/server/api/schedule.py`
- Modify: `dashboard/server/api/config_routes.py`
- Modify: `dashboard/server/api/engagement.py`

- [ ] **Step 1: Move check_token_health to genlab_core.monitoring**
Copy `Content Scraper/execution/check_token_health.py` → `genlab-core/src/genlab_core/monitoring/check_token_health.py`. Update imports in dashboard.

- [ ] **Step 2: Remove sys.path.insert from review_server.py**
Replace `from execution.*` imports with `from genlab_core.*` equivalents.

- [ ] **Step 3: Update media path resolution**
Replace hardcoded `Content Scraper` media paths with `settings.get_project_root()` resolution using niche_root mapping.

- [ ] **Step 4: Run dashboard tests**
- [ ] **Step 5: Commit**

---

## Verification

- [ ] Run full genlab-core test suite
- [ ] Run each pipeline: `uv run python ClutchWire/run_pipeline.py --dry-run`
- [ ] Verify all 40 launchd services start: `launchctl list | grep genlab`
- [ ] Verify dashboard serves: `curl http://localhost:5151/api/v1/health`
- [ ] Grep for remaining "Content Scraper" references outside Content Scraper itself
