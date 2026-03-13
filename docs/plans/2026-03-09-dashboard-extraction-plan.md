# Dashboard Extraction & Shared Resource Consolidation — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Clean project organization — extract shared dashboard to GenLab root, fix analytics accuracy, consolidate shared scripts into genlab-core.

**Architecture:** The dashboard (React frontend + Flask server + REST API) currently lives in Content Scraper but serves both niches. We extract it to `GenLab/dashboard/` as a uv workspace member. Shared engagement/token scripts move to genlab-core. Import paths update throughout.

**Tech Stack:** Python 3.13, Flask, React 19, TypeScript, Vite, uv workspace, launchd, genlab-core (hatchling)

**Key Dependency:** The API layer has cross-imports with `execution/review_server.py` (socketio, _resolve_video_url, _execute_review_action) and `execution/check_token_health.py`. These must be resolved during extraction.

---

## Phase A: Fix Analytics Accuracy (Quick Wins)

### Task 1: Surface data quality in analytics API

**Files:**
- Modify: `/Users/anarchistsid/GenLab/Content Scraper/execution/api/analytics.py`

**Step 1: Add platform data availability tracking**

In `_build_overview()`, after building `platform_metrics`, add a `platform_data_status` dict that tracks whether each platform has real API data vs nothing:

```python
# After line ~200 (after platform_metrics loop)
EXPECTED_PLATFORMS = ["instagram", "youtube", "x_twitter", "facebook"]
platform_data_status = {}
for plat in EXPECTED_PLATFORMS:
    pm = platform_metrics.get(plat)
    if pm and (pm["reach"] > 0 or pm["impressions"] > 0 or pm["likes"] > 0):
        platform_data_status[plat] = "available"
    elif pm and pm["posts"] > 0:
        platform_data_status[plat] = "no_metrics"  # posts exist but no engagement data
    else:
        platform_data_status[plat] = "no_data"
```

**Step 2: Include status in API response**

Add `platform_data_status` to the overview response dict (near the return statement):

```python
"platform_data_status": platform_data_status,
```

**Step 3: Update per-platform response format**

In the `by_platform` section, add `data_status` field to each platform entry:

```python
by_platform[plat] = {
    "reach": pm["reach"] if pm else 0,
    "posts": pm["posts"] if pm else 0,
    "avg_engagement_rate": eng_rate,
    "metric_label": labels.get(plat, "Reach"),
    "data_status": platform_data_status.get(plat, "no_data"),
}
```

**Step 4: Verify**

Run: `curl -s -u admin:***REMOVED*** "https://review.aspirehub.ai/api/v1/analytics/overview?window=7d&niche_id=all" | python3 -m json.tool | grep data_status`

Expected: `data_status` fields per platform. X/Twitter and Facebook should show `no_metrics`.

---

### Task 2: Dashboard shows "No API data" badge for unavailable platforms

**Files:**
- Modify: `/Users/anarchistsid/GenLab/Content Scraper/dashboard/src/api/types.ts`
- Modify: Analytics view that renders platform breakdown (find via grep for `Platform Breakdown`)

**Step 1: Add data_status to PlatformMetrics type**

```typescript
export interface PlatformMetrics {
  reach: number;
  posts: number;
  avg_engagement_rate: number | null;
  metric_label: string;
  data_status?: "available" | "no_metrics" | "no_data";
}
```

**Step 2: Update platform breakdown rendering**

Where the platform breakdown renders reach numbers, check `data_status`:
- If `"no_metrics"`: show "No API data" in muted text instead of "0"
- If `"no_data"`: show "—"
- If `"available"`: show the number normally

**Step 3: Build and verify**

Run: `cd dashboard && npm run build`
Expected: Clean build, no TypeScript errors.

---

## Phase B: Extract Dashboard to GenLab Root

### Task 3: Create dashboard workspace member scaffold

**Files:**
- Create: `/Users/anarchistsid/GenLab/dashboard/pyproject.toml`
- Create: `/Users/anarchistsid/GenLab/dashboard/server/__init__.py`
- Create: `/Users/anarchistsid/GenLab/dashboard/server/api/__init__.py`
- Create: `/Users/anarchistsid/GenLab/dashboard/server/core/__init__.py`
- Modify: `/Users/anarchistsid/GenLab/pyproject.toml` (add dashboard member)

**Step 1: Create pyproject.toml**

```toml
[project]
name = "genlab-dashboard"
version = "0.1.0"
description = "Gen Lab Operations Dashboard — shared review server + React frontend"
requires-python = ">=3.12"
dependencies = [
    "genlab-core",
    "flask>=3.0",
    "flask-socketio>=5.3",
    "python-dotenv>=1.0",
    "gunicorn>=22.0",
    "eventlet>=0.36",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

**Step 2: Create __init__.py files**

```python
# server/__init__.py
"""Gen Lab Operations Dashboard — Flask review server."""

# server/api/__init__.py
"""API v1 route modules for the operations dashboard."""

# server/core/__init__.py
"""Core business logic for the dashboard."""
```

**Step 3: Add to workspace**

Update `/Users/anarchistsid/GenLab/pyproject.toml`:
```toml
[tool.uv.workspace]
members = [
    "genlab-core",
    "CriticalRush",
    "Content Scraper",
    "dashboard",
]
```

**Step 4: Verify workspace resolves**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv sync --dry-run`
Expected: No resolution errors.

---

### Task 4: Move frontend

**Step 1: Move React app**

```bash
mv "/Users/anarchistsid/GenLab/Content Scraper/dashboard" "/Users/anarchistsid/GenLab/dashboard/frontend"
```

**Step 2: Verify build still works**

```bash
cd /Users/anarchistsid/GenLab/dashboard/frontend && npm run build
```

Expected: Clean build (no source changes needed — the frontend is self-contained).

---

### Task 5: Move API layer

**Step 1: Copy API files**

```bash
cp -r "/Users/anarchistsid/GenLab/Content Scraper/execution/api/"*.py "/Users/anarchistsid/GenLab/dashboard/server/api/"
```

**Step 2: Update imports in every API file**

Replace all occurrences across the API files:
- `from execution.api.` → `from server.api.`
- `from execution.review_server import` → `from server.review_server import`
- `from execution.utils.config_loader` → resolve (move config_loader or inline)
- `from execution.check_token_health` → resolve (move to genlab-core in Task 8)
- `from core.publishing_queue` → `from server.core.publishing_queue`

**Specific files and their cross-imports to fix:**

`server/api/blueprints.py`:
- Line 107: `from execution.review_server import _resolve_video_url` → `from server.review_server import _resolve_video_url`
- Line 278: `from execution.review_server import _execute_review_action` → `from server.review_server import _execute_review_action`
- Line 289: `from execution.review_server import socketio` → `from server.review_server import socketio`

`server/api/pipeline.py`:
- Line 345: `from execution.review_server import express_state, run_express_pipeline` → `from server.review_server import express_state, run_express_pipeline`
- Line 377: `from execution.review_server import socketio` → `from server.review_server import socketio`

`server/api/publishing_queue.py`:
- Line 27: `from core.publishing_queue` → `from server.core.publishing_queue`
- Line 53: `from execution.api.blueprints` → `from server.api.blueprints`
- Lines 87,114,139: `from execution.review_server import socketio` → `from server.review_server import socketio`
- Line 160: `from execution.api.overview` → `from server.api.overview`

`server/api/schedule.py`:
- Line 27: `from execution.utils.config_loader import load_config` → inline or move config_loader

`server/api/token_health.py`:
- Lines 129,148,161: `from execution.check_token_health import ...` → `from genlab_core.platform.token_health import ...` (after Task 8)

`server/api/overview.py`:
- Line 262: `from execution.api.pipeline` → `from server.api.pipeline`

**Step 3: Move core/publishing_queue.py**

```bash
cp "/Users/anarchistsid/GenLab/Content Scraper/core/publishing_queue.py" "/Users/anarchistsid/GenLab/dashboard/server/core/publishing_queue.py"
```

---

### Task 6: Move review server

**Step 1: Copy review_server.py**

```bash
cp "/Users/anarchistsid/GenLab/Content Scraper/execution/review_server.py" "/Users/anarchistsid/GenLab/dashboard/server/review_server.py"
```

**Step 2: Update imports in review_server.py**

Lines 384-395 change from:
```python
from execution.api.analytics import bp as analytics_bp
from execution.api.blueprints import bp as blueprints_bp, health_bp as focus_health_bp
...
```
To:
```python
from server.api.analytics import bp as analytics_bp
from server.api.blueprints import bp as blueprints_bp, health_bp as focus_health_bp
from server.api.config_routes import bp as config_bp
from server.api.config_routes import settings_bp
from server.api.pipeline import bp as pipeline_bp
from server.api.schedule import bp as schedule_bp
from server.api.stories import bp as stories_bp
from server.api.niches import bp as niches_bp
from server.api.overview import bp as overview_bp
from server.api.publishing_queue import bp as queue_bp
from server.api.token_health import bp as token_health_bp
from server.api.platform_posts import bp as platform_posts_bp
```

**Step 3: Update static file serving path**

The server serves dashboard dist from a relative path. Find the `send_from_directory` call for `dashboard/dist` and update to:
```python
DASHBOARD_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
```

**Step 4: Update PROJECT_ROOT resolution**

The server resolves PROJECT_ROOT for media/cache access. It needs to point to the Content Scraper directory for media files. Make this configurable:
```python
PROJECT_ROOT = Path(os.environ.get("GENLAB_PROJECT_ROOT", Path(__file__).resolve().parent.parent.parent / "Content Scraper"))
```

---

### Task 7: Update wrapper scripts and launchd

**Files:**
- Create: `/Users/anarchistsid/GenLab/dashboard/runbooks/review_server_wrapper.sh`
- Modify: `/Users/anarchistsid/Library/LaunchAgents/com.genlab.review-server.plist`

**Step 1: Create new wrapper script**

```bash
#!/usr/bin/env bash
set -euo pipefail

DASHBOARD_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GENLAB_ROOT="$(cd "$DASHBOARD_ROOT/.." && pwd)"
PROJECT_ROOT="$GENLAB_ROOT/Content Scraper"

# Load environment from Content Scraper .env
if [ -f "$PROJECT_ROOT/.env" ]; then
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
        key=$(echo "$key" | xargs)
        value=$(echo "$value" | xargs)
        [ -z "$key" ] && continue
        export "$key=$value"
    done < "$PROJECT_ROOT/.env"
fi

export GENLAB_PROJECT_ROOT="$PROJECT_ROOT"

# Activate uv workspace venv
WORKSPACE_VENV="$GENLAB_ROOT/.venv/bin"
if [ -d "$WORKSPACE_VENV" ]; then
    export PATH="$WORKSPACE_VENV:$PATH"
fi

# Build dashboard if dist is stale
DIST="$DASHBOARD_ROOT/frontend/dist"
if [ ! -d "$DIST" ] || [ "$(find "$DASHBOARD_ROOT/frontend/src" -newer "$DIST/index.html" 2>/dev/null | head -1)" ]; then
    echo "[$(date)] Building dashboard..."
    cd "$DASHBOARD_ROOT/frontend"
    npm run build
fi

cd "$DASHBOARD_ROOT"

exec gunicorn \
    --worker-class eventlet \
    --workers 2 \
    --timeout 120 \
    --bind 0.0.0.0:5151 \
    --access-logfile "$PROJECT_ROOT/.tmp/logs/review_server_access.log" \
    --error-logfile "$PROJECT_ROOT/.tmp/logs/review_server_error.log" \
    --capture-output \
    "server.review_server:app"
```

**Step 2: Update launchd plist**

Update ProgramArguments and WorkingDirectory:
```xml
<key>ProgramArguments</key>
<array>
    <string>/bin/bash</string>
    <string>/Users/anarchistsid/GenLab/dashboard/runbooks/review_server_wrapper.sh</string>
</array>
<key>WorkingDirectory</key>
<string>/Users/anarchistsid/GenLab/dashboard</string>
```

Update log paths:
```xml
<key>StandardOutPath</key>
<string>/Users/anarchistsid/GenLab/Content Scraper/.tmp/logs/review_server_stdout.log</string>
```

**Step 3: Reload launchd**

```bash
launchctl unload ~/Library/LaunchAgents/com.genlab.review-server.plist
# Edit plist
launchctl load ~/Library/LaunchAgents/com.genlab.review-server.plist
```

**Step 4: Verify server starts**

```bash
curl -s -u admin:***REMOVED*** https://review.aspirehub.ai/api/health | python3 -m json.tool
```

Expected: `{"status": "ok"}` or similar.

---

### Task 8: Clean up Content Scraper (remove moved files)

**Step 1: Remove moved files from Content Scraper**

```bash
rm -rf "/Users/anarchistsid/GenLab/Content Scraper/dashboard"
rm -rf "/Users/anarchistsid/GenLab/Content Scraper/execution/api"
rm "/Users/anarchistsid/GenLab/Content Scraper/execution/review_server.py"
rm "/Users/anarchistsid/GenLab/Content Scraper/core/publishing_queue.py"
rm "/Users/anarchistsid/GenLab/Content Scraper/runbooks/review_server_wrapper.sh"
```

**Step 2: Verify Content Scraper daily pipeline still works**

The daily pipeline scripts (fetch_ai_news.py, etc.) should NOT import from execution/api/ or review_server. Verify:

```bash
grep -rn "from execution.api\|from execution.review_server\|import execution.api\|import execution.review_server" "/Users/anarchistsid/GenLab/Content Scraper/execution/"*.py 2>/dev/null | grep -v __pycache__
```

Expected: Only hits in files that were already moved (none remaining).

**Step 3: Verify dashboard still works**

```bash
curl -s -u admin:***REMOVED*** https://review.aspirehub.ai/api/v1/analytics/overview?window=7d | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['summary']['total_reach'])"
```

Expected: Real reach number (e.g., 18673).

---

## Phase C: Extract Shared Scripts to genlab-core

### Task 9: Extract token health checker to genlab-core

**Files:**
- Create: `/Users/anarchistsid/GenLab/genlab-core/src/genlab_core/platform/token_health.py`
- Modify: `/Users/anarchistsid/GenLab/dashboard/server/api/token_health.py` (update imports)

**Step 1: Extract core functions from check_token_health.py**

Read `/Users/anarchistsid/GenLab/Content Scraper/execution/check_token_health.py` and extract:
- `check_meta_token()` — Instagram/Facebook token validation
- `check_youtube()` — YouTube OAuth token validation
- `check_facebook()` — Facebook page token validation
- `check_twitter()` — X/Twitter API key validation

Create `genlab_core/platform/token_health.py` with these functions, importing credentials from environment variables (no hardcoded paths).

**Step 2: Update dashboard API import**

In `dashboard/server/api/token_health.py`, change:
```python
from execution.check_token_health import check_meta_token
```
To:
```python
from genlab_core.platform.token_health import check_meta_token
```

**Step 3: Run genlab-core tests**

```bash
cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package genlab-core pytest genlab-core/tests/ -x -q
```

Expected: 474+ tests pass (1 pre-existing fail allowed).

---

### Task 10: Extract engagement analytics poller to genlab-core

**Files:**
- Create: `/Users/anarchistsid/GenLab/genlab-core/src/genlab_core/engagement/analytics_poller.py`
- Create: `/Users/anarchistsid/GenLab/genlab-core/src/genlab_core/engagement/__init__.py`

**Step 1: Extract platform-agnostic fetching logic**

From `/Users/anarchistsid/GenLab/Content Scraper/execution/fetch_insights.py`, extract:
- Instagram insights fetching (Graph API)
- YouTube statistics fetching (Data API v3)
- X/Twitter public_metrics fetching (API v2)
- Facebook insights fetching (Graph API)
- Multi-window strategy (FRESH/WARM/COLD)
- Upsert to Analytics table via BacklogClient

Parameterize by `niche_id` so CriticalRush can import and use it.

**Step 2: Create thin wrapper in Content Scraper**

Replace `fetch_insights.py` content with:
```python
"""Fetch engagement insights — delegates to genlab_core.engagement.analytics_poller."""
from genlab_core.engagement.analytics_poller import main
if __name__ == "__main__":
    main()
```

**Step 3: Verify fetch_insights still works**

```bash
cd "/Users/anarchistsid/GenLab/Content Scraper" && ~/.local/bin/uv run python execution/fetch_insights.py --dry-run
```

Expected: Dry run completes without errors.

---

### Task 11: Extract audience metrics poller to genlab-core

**Files:**
- Create: `/Users/anarchistsid/GenLab/genlab-core/src/genlab_core/engagement/audience_poller.py`

Same pattern as Task 10 — extract from `fetch_audience_metrics.py`, parameterize by niche_id.

---

### Task 12: Update CLAUDE.md and workspace docs

**Files:**
- Modify: `/Users/anarchistsid/GenLab/Content Scraper/CLAUDE.md` — remove dashboard/review_server references, update directory structure
- Create: `/Users/anarchistsid/GenLab/dashboard/CLAUDE.md` — document the dashboard package
- Modify: `/Users/anarchistsid/.claude/projects/-Users-anarchistsid-GenLab/memory/MEMORY.md` — update project structure

**Step 1: Update Content Scraper CLAUDE.md directory structure**

Remove `dashboard/`, `execution/api/`, `execution/review_server.py` from the tree.
Add note: "Dashboard lives at `GenLab/dashboard/` (shared workspace member)."

**Step 2: Create dashboard CLAUDE.md**

Document: server architecture, API endpoints, frontend build, launchd config, how to run locally.

**Step 3: Update MEMORY.md project structure**

Update the "Project Structure" section to include the new dashboard member.

---

## Verification Checklist (After All Tasks)

- [ ] `curl https://review.aspirehub.ai/api/health` returns OK
- [ ] Dashboard loads in browser, all pages work (Mission Control, Analytics, Focus Review, Publishing Queue, Channel Health)
- [ ] Analytics shows `data_status` per platform (no misleading zeros)
- [ ] Channel Health shows 4/4 platforms (100% when all operational, no Postiz)
- [ ] Content Scraper daily pipeline runs without import errors
- [ ] genlab-core tests pass (474+)
- [ ] Dashboard frontend builds cleanly
- [ ] `uv sync` resolves all workspace members
- [ ] Launchd review server daemon starts and stays alive
