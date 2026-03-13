# Dashboard Extraction & Shared Resource Consolidation

**Date:** 2026-03-09
**Status:** Approved
**Goal:** Clean project organization — channel folders contain only channel files, shared resources live at GenLab root.

---

## Part A: Fix Analytics Accuracy

### Root Causes
- Facebook: page token expired, insights return near-zero
- X/Twitter: free API tier returns no impression metrics
- Dashboard shows misleading "0" instead of "N/A" for unavailable data

### Changes
1. Analytics API: mark platforms with zero data + no API errors as `data_unavailable`
2. Dashboard: show "No API data" badge instead of "0" for unavailable platforms
3. Surface `is_estimated` flag visually when estimates are used

---

## Part B: Extract Dashboard to GenLab Root

### What Moves

| Source | Destination |
|--------|-------------|
| `Content Scraper/dashboard/` | `GenLab/dashboard/frontend/` |
| `Content Scraper/execution/review_server.py` | `GenLab/dashboard/server/review_server.py` |
| `Content Scraper/execution/api/` | `GenLab/dashboard/server/api/` |
| `Content Scraper/core/publishing_queue.py` | `GenLab/dashboard/server/core/publishing_queue.py` |
| `Content Scraper/runbooks/review_server_wrapper.sh` | `GenLab/dashboard/runbooks/review_server_wrapper.sh` |

### What Stays in Content Scraper
- All 60 execution scripts (fetch, rank, compose, render, publish)
- All utils, configs, directives, schemas, assets, inspo_library
- Channel-specific runbooks (daily_intel.sh, orchestrator.sh, publisher_wrapper.sh)
- `core/context.py`, `core/settings.py` (BB-specific overrides)

### Infrastructure Updates
- Add `dashboard` to uv workspace members in `GenLab/pyproject.toml`
- Create `GenLab/dashboard/pyproject.toml` (workspace member, depends on genlab-core)
- Update launchd plists to point to new paths
- Update Cloudflare tunnel wrapper path

---

## Part C: Extract Shared Scripts to genlab-core

### New Modules

#### `genlab_core.engagement.analytics_poller`
Extracted from `Content Scraper/execution/fetch_insights.py` (500 LOC).
Multi-platform engagement fetching (IG Graph API, YT Data API, X API v2, FB Graph API).
Multi-window strategy: FRESH (6-48h), WARM (2-7d), COLD (7-30d).
Parameterized by niche_id. Writes to Analytics Microsoft Lists table.
**Unblocks CriticalRush self-learning loop.**

#### `genlab_core.engagement.audience_poller`
Extracted from `Content Scraper/execution/fetch_audience_metrics.py` (200 LOC).
Daily follower/audience snapshots per platform.

#### `genlab_core.platform.token_health`
Extracted from `Content Scraper/execution/check_token_health.py` (300 LOC).
Pre-flight token validation for all platforms.
Fail-fast behavior for cron pipelines.

### Adoption of Existing genlab-core Modules by Content Scraper
- `genlab_core.platform.platform_rules` — replace hardcoded rules in adapt_for_platforms.py
- `genlab_core.learning.reward_shaper` — replace custom reward computation

---

## Target Layout

```
GenLab/
  pyproject.toml              # workspace root (members: genlab-core, dashboard, Content Scraper, CriticalRush)
  genlab-core/                # shared library
    src/genlab_core/
      engagement/             # NEW
        analytics_poller.py
        audience_poller.py
      platform/
        token_health.py       # NEW
        (existing)
  dashboard/                  # NEW — shared UI
    frontend/                 # React app
      src/
      package.json
      vite.config.ts
    server/                   # Flask review server + API
      review_server.py
      api/
      core/
    runbooks/
      review_server_wrapper.sh
    pyproject.toml
  Content Scraper/            # Blackbox Brief channel ONLY
    execution/                # BB-specific scripts
    config/
    directives/
    schemas/
    assets/
    runbooks/                 # BB-specific (daily_intel, publisher)
    tests/
    pyproject.toml
  CriticalRush/               # Gaming channel
    (unchanged)
```

---

## Risks & Mitigations
- **Launchd plists**: Must update paths atomically (unload old, load new)
- **Import paths**: Server imports `execution.api.*` — need to update to `server.api.*`
- **Media serving**: Review server serves media from `.tmp/` — needs path config
- **Cloudflare tunnel**: Wrapper script path changes
- **uv workspace**: Dashboard needs its own pyproject.toml with genlab-core dependency
