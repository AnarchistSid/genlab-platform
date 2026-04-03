# Operations Command Center — Design Document

**Date:** 2026-02-27
**Status:** Draft
**Supersedes:** `2026-02-26-review-server-redesign.md` (scope expanded from review-only to full Microsoft Lists replacement)
**Goal:** Replace Microsoft Lists as the daily operations interface with a state-of-the-art, AI-native dashboard at `review.aspirehub.ai`.

---

## Problem Statement

The current system uses Microsoft Lists as both database and daily UI. This creates three problems:

1. **Split attention** — reviewing content in the Flask dashboard, checking schedule in Microsoft Lists, monitoring pipeline in log files
2. **No real-time feedback** — Microsoft Lists doesn't push updates when the publisher daemon publishes a post or the pipeline completes
3. **No intelligence layer** — no predictive scheduling, no auto-approve, no natural language control over the pipeline

The existing review server (`execution/review_server.py`) is a localhost-only Flask app with 12 routes and ~1,200 lines of embedded HTML. It handles content review but nothing else.

---

## Solution Overview

| Component | Current | Target |
|-----------|---------|--------|
| Scope | Review-only dashboard | Full operations command center (8 views) |
| Frontend | ~1,200 lines embedded HTML | React 19 SPA (TypeScript, Vite, shadcn/ui) |
| API | 12 Flask routes | 28+ REST endpoints under `/api/v1/` + OpenAPI spec |
| Real-time | Socket.IO emit (server push only) | Bidirectional Socket.IO with optimistic updates |
| Intelligence | None | AI command bar, predictive scheduling, auto-approve, content suggestions |
| Hosting | localhost:5151 | `review.aspirehub.ai` via Cloudflare named tunnel |
| Process | Manual Flask dev server | launchd daemon + Gunicorn, auto-restart |
| Auth | Optional HTTP Basic Auth | Cloudflare Access (Google SSO) |
| Offline | None | PWA with IndexedDB, queued actions |
| Performance | N/A | Edge caching (Cloudflare Workers), Web Workers, virtualized lists |

Microsoft Lists remains the headless database. The dashboard becomes the sole human interface.

---

## 1. Architecture

### Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **UI framework** | React 19 + TypeScript | Component model, type safety |
| **Build** | Vite 6 | Fast HMR (<200ms), production builds (~2s) |
| **Routing** | TanStack Router | Type-safe routes, search param validation, lazy loading |
| **Server state** | TanStack Query v5 | Caching, background refetch, optimistic updates |
| **Client state** | Zustand + Immer | UI prefs, selections, layout state |
| **URL state** | nuqs | Filter/sort/pagination persisted in URL |
| **Components** | shadcn/ui (New York variant) + Radix Primitives | Fully owned, customizable component library |
| **Styling** | Tailwind CSS 4 | Utility-first, dark theme, responsive |
| **Charts** | Recharts | Composable chart components (area, bar, line, donut) |
| **Animation** | Framer Motion | Page transitions, drag feedback, micro-interactions |
| **Command palette** | cmdk | Fuzzy search + AI natural language actions |
| **Toasts** | Sonner | Stackable toasts with built-in undo |
| **Drag & drop** | dnd-kit | Schedule reordering, calendar assignment |
| **Virtualization** | TanStack Virtual | 1000+ item lists at 60fps |
| **Validation** | Zod | Runtime API response validation |
| **API client** | Auto-generated from OpenAPI | Zero hand-written fetch code |
| **Real-time** | Socket.IO client | Live updates from publisher/pipeline |
| **Offline** | Service Worker + IndexedDB | PWA shell caching, offline review queue |
| **Backend** | Flask (existing) + Gunicorn + eventlet | REST API + Socket.IO + static file server |

### Project Structure

```
project-root/
dashboard/
  package.json
  vite.config.ts
  tsconfig.json
  index.html
  public/
    manifest.json               # PWA manifest
    sw.js                       # Service worker
    icons/                      # PWA icons (192x192, 512x512)
  src/
    main.tsx                    # React entry point
    app.tsx                     # Router + providers + global layout
    api/
      client.ts                 # Auto-generated typed API client
      types.ts                  # Auto-generated response types
      socket.ts                 # Socket.IO singleton + event types
    components/
      ui/                       # shadcn/ui primitives (Button, Card, Badge, Dialog, etc.)
      layout/
        shell.tsx               # App shell: sidebar + main + activity feed
        sidebar.tsx             # Navigation + workspace switcher
        command-palette.tsx     # cmdk + AI actions
        activity-feed.tsx       # Real-time event stream (collapsible panel)
        notification-center.tsx # Bell icon + dropdown
        keyboard-help.tsx       # Shortcut overlay (triggered by Cmd+/)
      blueprints/
        blueprint-card.tsx      # Grid card with thumbnail, status, quick actions
        blueprint-detail.tsx    # Full detail panel (split pane right side)
        review-actions.tsx      # Approve/reject/revise buttons + feedback form
        platform-preview.tsx    # IG/YT/TW/FB preview tabs
        comparison-view.tsx     # Side-by-side blueprint comparison
        version-diff.tsx        # Content versioning + diff viewer
        ai-suggestions.tsx      # AI content improvement panel
      schedule/
        schedule-board.tsx      # Week view with drag-drop time slots
        calendar-month.tsx      # Month view with slot fill indicators
        time-slot.tsx           # Single slot component (droppable)
        drag-card.tsx           # Draggable blueprint card
        smart-suggestion.tsx    # Predictive scheduling suggestion banner
      charts/
        kpi-card.tsx            # Metric card with sparkline
        platform-chart.tsx      # Per-platform success/failure over time
        heatmap.tsx             # Time-of-day x day-of-week performance
        cost-tracker.tsx        # Daily cost with budget threshold line
        template-ranking.tsx    # Bar chart of template performance
      review/
        focus-mode.tsx          # Full-screen card-by-card review
        progress-bar.tsx        # Review progress indicator
        auto-approve-timer.tsx  # Confidence-based countdown bar
      shared/
        video-player.tsx        # Full video player (seek, loop, volume, PiP)
        carousel-viewer.tsx     # Multi-slide carousel with arrows
        status-badge.tsx        # Color-coded status pills
        filter-bar.tsx          # Faceted filter with URL persistence
        data-table.tsx          # Sortable, filterable table (TanStack Table)
        date-range-picker.tsx   # Date range selector for analytics
        empty-state.tsx         # Illustrated empty states per view
        offline-banner.tsx      # "You're offline" indicator
        stale-data-banner.tsx   # "Microsoft Lists unreachable" warning
    views/                      # Route-level components (all lazy loaded)
      pipeline.tsx              # Pipeline Overview (home)
      blueprints.tsx            # Content Board (filterable grid + split pane)
      blueprint-detail.tsx      # Full-page blueprint detail
      schedule.tsx              # Publishing Dashboard (week + month views)
      analytics.tsx             # Analytics (charts + tables)
      stories.tsx               # Stories Explorer
      runs.tsx                  # Pipeline Runs list
      run-detail.tsx            # Single run report
      settings.tsx              # Config viewer + notification preferences
      focus-review.tsx          # Focus Mode (full-screen review flow)
    hooks/
      use-blueprints.ts         # TanStack Query: blueprint CRUD
      use-stories.ts            # TanStack Query: stories
      use-schedule.ts           # TanStack Query: schedule + drag mutations
      use-analytics.ts          # TanStack Query: analytics data
      use-pipeline.ts           # TanStack Query: pipeline status + runs
      use-socket.ts             # Socket.IO connection + event handlers
      use-keyboard.ts           # Global keyboard shortcut registration
      use-ai-command.ts         # AI command bar: intent parsing + execution
      use-notifications.ts      # Notification state + webhook config
      use-offline.ts            # Online/offline detection + queue
      use-focus-mode.ts         # Focus review state machine
    stores/
      ui-store.ts               # Sidebar, theme, layout prefs, panels
      selection-store.ts        # Multi-select for batch operations
      workspace-store.ts        # Multi-brand workspace context
      notification-store.ts     # Unread count, notification list
    workers/
      search-indexer.worker.ts  # Builds fuzzy search index in background
      filter-engine.worker.ts   # Heavy filtering/sorting off main thread
    lib/
      utils.ts                  # cn(), formatDate(), formatDuration()
      constants.ts              # Status colors, keyboard mappings, config
      ai-actions.ts             # OpenAI function-calling action definitions
      export.ts                 # CSV/PDF export utilities
    styles/
      globals.css               # Tailwind imports + CSS custom properties
  dist/                         # Vite build output (gitignored)
```

### How Flask Serves the SPA

```python
# In review_server.py — replaces embedded DASHBOARD_HTML
@app.route("/")
@app.route("/<path:path>")
def serve_dashboard(path=""):
    """All routes fall through to index.html for client-side routing."""
    dist_dir = PROJECT_ROOT / "dashboard" / "dist"
    file_path = dist_dir / path
    if path and file_path.exists() and file_path.is_file():
        return send_from_directory(dist_dir, path)
    return send_from_directory(dist_dir, "index.html")
```

### Vite Dev / Prod Workflow

```typescript
// vite.config.ts
export default defineConfig({
  server: {
    proxy: {
      "/api": "http://localhost:5151",
      "/socket.io": { target: "http://localhost:5151", ws: true },
    },
  },
  build: {
    outDir: "dist",
    rollupOptions: {
      output: { manualChunks: { recharts: ["recharts"], framer: ["framer-motion"] } },
    },
  },
});
```

Development: `cd dashboard && npm run dev` (Vite on :5173, proxies API to Flask :5151).
Production: `cd dashboard && npm run build` outputs static files. Flask serves `dist/` + API on :5151. Single port, no proxy.

---

## 2. Backend API

All endpoints under `/api/v1/`. JSON responses. Consistent pagination. OpenAPI spec auto-generated.

### Blueprints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/blueprints` | List. Filters: `status`, `platform`, `template`, `story_id`, `scheduled_after`, `scheduled_before`, `search`. Sort: `scheduled_for`, `priority_score`, `created_at`. Paginated. |
| GET | `/api/v1/blueprints/:id` | Full detail: content, platform adaptations, visual paths, review history, version list |
| POST | `/api/v1/blueprints/:id/review` | Body: `{action: "approve"|"reject"|"revise", issue?, notes?, fix?}` |
| POST | `/api/v1/blueprints/batch-review` | Body: `{ids: [...], action, issue?, notes?}` |
| PATCH | `/api/v1/blueprints/:id/schedule` | Body: `{scheduled_for: "2026-02-28T12:00:00+05:30"}` |
| PATCH | `/api/v1/blueprints/:id/content` | Inline edit. Body: `{hook_text?, caption?, hashtags?}`. Creates a version. |
| POST | `/api/v1/blueprints/:id/retry-publish` | Body: `{platform: "instagram"|"youtube"|"twitter"|"facebook"}` |
| GET | `/api/v1/blueprints/:id/versions` | Version history with diffs |
| GET | `/api/v1/blueprints/:id/versions/:version` | Specific version snapshot |

### Stories

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/stories` | List. Filters: `source`, `min_score`, `cluster_id`, `date_from`, `date_to`, `search`. |
| GET | `/api/v1/stories/:id` | Full detail: claims, linked blueprints, source URLs, cluster info |

### Schedule

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/schedule` | Range query: `?from=2026-02-27&to=2026-03-05`. Returns days with slots and assigned blueprints. |
| PATCH | `/api/v1/schedule/reorder` | Body: `{blueprint_id, from_slot, to_slot, from_date?, to_date?}` |
| GET | `/api/v1/schedule/coverage` | Slot fill rates per day for coverage visualization |
| GET | `/api/v1/schedule/suggestions` | AI-powered optimal slot suggestions based on historical performance |

### Analytics

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/analytics/publishing` | Per-platform publish success/failure/retry. Params: `?from&to&platform&granularity=day|week` |
| GET | `/api/v1/analytics/content` | Template performance, hook rankings, engagement proxies |
| GET | `/api/v1/analytics/pipeline` | Run durations, step success rates, cost per run |
| GET | `/api/v1/analytics/heatmap` | Performance by time-of-day x day-of-week matrix |
| GET | `/api/v1/analytics/trends` | Score/volume trends over configurable time ranges |

### Pipeline

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/pipeline/status` | Current state: last run, next scheduled, active step, health |
| GET | `/api/v1/pipeline/runs` | List runs. Filters: `status`, `date_from`, `date_to`. |
| GET | `/api/v1/pipeline/runs/:id` | Full run report from `.tmp/runs/:id/run_report.json` |
| POST | `/api/v1/pipeline/trigger` | Trigger express pipeline run |

### Config (read-only via API)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/config/sources` | Source list from `sources.yaml` |
| GET | `/api/v1/config/templates` | Template list from Microsoft Lists Templates table |
| GET | `/api/v1/config/schedule-slots` | Schedule config from `publishing.yaml` |
| GET | `/api/v1/config/scoring` | Scoring weights from `scoring_weights.yaml` |

### AI

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/ai/command` | Natural language command. Body: `{query: "approve all pending for today"}`. Returns: `{action, preview, params}` |
| POST | `/api/v1/ai/command/execute` | Execute a previewed command. Body: `{action, params, confirmed: true}` |
| GET | `/api/v1/ai/suggestions/:blueprint_id` | Content improvement suggestions for a blueprint |
| GET | `/api/v1/ai/auto-approve-score/:blueprint_id` | Confidence score + breakdown |

### Notifications

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/notifications` | List notifications. Filters: `unread`, `type`. |
| PATCH | `/api/v1/notifications/read` | Mark notifications as read. Body: `{ids: [...]}` or `{all: true}` |
| GET | `/api/v1/notifications/preferences` | Webhook/email notification settings |
| PATCH | `/api/v1/notifications/preferences` | Update notification settings |

### System

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/health` | Uptime, Microsoft Lists status, cache stats, disk usage |
| GET | `/api/v1/openapi.json` | Auto-generated OpenAPI 3.1 spec |
| GET | `/api/media/<path>` | Serve rendered visuals and video clips (existing, unchanged) |

### Pagination Convention

All list endpoints return:
```json
{
  "data": [...],
  "meta": { "page": 1, "per_page": 25, "total": 142, "total_pages": 6 }
}
```

### Socket.IO Events (server to client)

| Event | Payload | Trigger |
|-------|---------|---------|
| `blueprint:updated` | `{id, status, platform_publish_status, updated_fields}` | Any blueprint status change |
| `blueprint:published` | `{id, platform, success, error?, url?}` | Each platform publish completes |
| `pipeline:started` | `{run_id, triggered_by}` | Pipeline begins |
| `pipeline:progress` | `{run_id, step, step_index, total_steps, status, message}` | Each pipeline step |
| `pipeline:complete` | `{run_id, summary, duration, errors}` | Pipeline finished |
| `notification:new` | `{id, type, title, body, created_at}` | New notification created |
| `schedule:changed` | `{date, slot, blueprint_id, action}` | Schedule modification |

### Socket.IO Events (client to server)

| Event | Payload | Purpose |
|-------|---------|---------|
| `subscribe:blueprint` | `{id}` | Subscribe to updates for a specific blueprint |
| `unsubscribe:blueprint` | `{id}` | Unsubscribe |
| `subscribe:pipeline` | `{}` | Subscribe to pipeline progress |

---

## 3. Views

### 3.1 Pipeline Overview (Home — `/`)

The landing page. System health at a glance.

**Sections:**
- **KPI row** — 4 cards: Pending Review (count + delta), Drafted (count + delta), Visual Ready (count + delta), Published This Week (count + delta). Each card has a 7-day sparkline.
- **Today's schedule** — Horizontal 4-slot timeline. Each slot shows: assigned blueprint thumbnail, status (published/next/assigned/empty). Click slot to jump to blueprint.
- **Pipeline status** — Current state card: last run time, next scheduled, active step (if running), health indicator (green/yellow/red).
- **Recent activity feed** — Last 20 events from Socket.IO. Clickable (jumps to entity). Auto-scrolls. Filter by type.
- **Platform health** — 4 horizontal bars (IG/YT/TW/FB) showing 7-day publish success rate.
- **Needs attention** — Alert list: publish failures, stuck blueprints (>24h in same status), pipeline errors, token expiry warnings.
- **Smart suggestions** — AI-generated: "3 blueprints ready for review", "Thursday has 2 empty slots — assign from 5 unscheduled VISUAL_READY posts?"

### 3.2 Content Board (`/blueprints`)

Primary workhorse view. Filterable card grid with split-pane detail.

**Layout:** Responsive card grid (left, 60%) + detail panel (right, 40%, resizable). Panel slides in on card click.

**Card grid:**
- 3 columns desktop, 2 tablet, 1 mobile
- Each card: thumbnail (poster frame or first slide), hook text (2 lines, truncated), schedule time, status badge, platform badges (IG/YT/TW/FB with per-platform status), quick action buttons (approve/reject)
- Hover: thumbnail auto-plays video (muted). Subtle scale(1.02) lift.
- Batch: Space to toggle select. Selected cards show checkbox. Batch action bar appears at bottom.
- Virtual scrolling via TanStack Virtual for 100+ cards

**Filter bar:**
- Status pills: All | Pending | Drafted | Visual Ready | Published | Failed
- Platform filter dropdown
- Template type dropdown
- Date range picker
- Free text search
- Sort: scheduled date, priority score, created date
- All filters in URL (nuqs) — bookmarkable

**Detail panel:**
- Video player (full controls, loop, seek, volume, picture-in-picture button)
- Carousel viewer (left/right arrows for multi-slide)
- Hook text, full caption, hashtags
- Platform preview tabs (IG phone frame / YT community post / TW tweet preview / FB post preview)
- Per-platform publish status badges with timestamps
- Metadata: template type, story source, urgency, confidence score, file sizes
- Review actions: Approve (green), Reject (red), Revise (yellow), Reschedule (blue)
- Feedback form on reject/revise: issue category dropdown + notes textarea
- AI suggestions panel (collapsible): hook strength score, caption analysis, hashtag recommendations
- Version history: collapsible list of edits with expandable diffs

**Keyboard shortcuts (active when Content Board is focused):**
- `j/k` — navigate up/down in card list
- `Enter` — open detail panel for focused card
- `Escape` — close detail panel
- `a` — approve focused blueprint
- `r` — reject (opens feedback form)
- `v` — revise (opens feedback form)
- `s` — skip (move to next)
- `[/]` — previous/next blueprint in detail panel
- `Space` — toggle select for batch
- `Cmd+A` — select all visible
- `e` — enter Focus Review Mode for filtered set
- `c` — enter Comparison Mode (requires 2 selected)

### 3.3 Blueprint Detail (`/blueprints/:id`)

Full-page version of the detail panel for deep inspection.

**Tabs:**
- **Overview** — Video player (large), full content (hook, caption, hashtags, CTA), metadata grid, review action buttons with undo
- **Instagram** — Phone-frame mockup showing carousel slides or reel. Swipe through slides. Character count validation.
- **YouTube** — Community post preview: text layout, image placement, character limits
- **Twitter** — Tweet preview with character count. Thread preview if multi-tweet. Media attachment preview.
- **Facebook** — Post preview with image/video, text truncation preview
- **History** — Full audit trail: status changes (with timestamps), review actions (with reviewer feedback), publish attempts (with success/failure details), content edits (with diffs)
- **AI Analysis** — Confidence score breakdown, content suggestions, predicted performance, similar past posts

### 3.4 Focus Review Mode (`/focus-review`)

Full-screen, distraction-free, card-by-card review. Enters from Content Board with current filter applied.

**Layout:** Centered large content area. No sidebar, no header navigation. Progress bar at bottom.

**Flow:**
1. Large video player or carousel viewer (60% viewport height)
2. Hook text + caption below
3. Platform badges row
4. Confidence score bar (color: green >90%, yellow 70-90%, red <70%)
5. Action buttons: Approve (a) | Reject (r) | Skip (s) — large, centered
6. On reject: inline feedback form slides down (issue category + notes)
7. After action: 5-second undo toast (Sonner). Auto-advances to next card.
8. Progress: "3 of 12 reviewed" with visual bar
9. Completion: summary screen — "12 reviewed: 9 approved, 2 rejected, 1 skipped" with option to undo any

**Mobile:** Swipe right = approve, swipe left = reject, swipe up = skip. Haptic feedback via Vibration API.

**Auto-approve integration:** Blueprints with confidence >threshold show auto-approve countdown bar (30s, animated via Framer Motion). Countdown cancellable. If user doesn't act and countdown completes, auto-approve fires.

### 3.5 Publishing Dashboard (`/schedule`)

Two sub-views: Week Board and Month Calendar. Toggle between them.

**Week Board (default):**
- 7-day horizontal grid. Each day has 4 time slot rows (08:00, 12:00, 16:00, 20:00).
- Each slot: droppable zone. Shows assigned blueprint card (thumbnail, hook snippet, status badge).
- Empty slots: dashed border, "Drop here" on hover.
- Drag blueprints between slots/days (dnd-kit). Conflict detection: red highlight if two posts in same slot.
- Unscheduled pool at bottom: VISUAL_READY blueprints without `scheduled_for`. Draggable into slots.
- Coverage bar per day: filled/total slots ratio

**Month Calendar:**
- Standard month grid. Each day cell shows slot fill dots (filled/empty).
- Color coding: green = all published, blue = scheduled, yellow = needs review, red = has failures, gray = empty
- Click day to expand inline: shows 4 slots with blueprint cards
- Drag blueprints from unscheduled pool onto any day (auto-assigns first empty slot)

**Smart scheduling suggestions:**
- Banner at top: "Based on 342 posts, 12:00 IST has 23% higher engagement. Move BP-a1b2 from 08:00?" [Accept] [Dismiss]
- Empty slot suggestions: "Thursday has 2 empty slots. 5 VISUAL_READY posts available." [Auto-fill] [Choose manually]

### 3.6 Analytics (`/analytics`)

Date range selector at top (7d / 30d / 90d / custom). All charts respond to range.

**Panels (responsive grid, 2 columns desktop, 1 mobile):**
- **Publishing success rate** — Line chart, per-platform, over time. Hover shows exact values.
- **Posts per day** — Bar chart stacked by platform. Shows publish volume trends.
- **Performance heatmap** — Time-of-day (y) x day-of-week (x) matrix. Cell color = engagement proxy. Shows optimal posting times.
- **Template ranking** — Horizontal bar chart. Templates ranked by approval rate + publish success. Click template to filter Content Board.
- **Top hooks** — Table: hook text, template, engagement score, publish date. Sortable.
- **Pipeline health** — Area chart: run duration over time. Overlaid: step-level breakdown on hover.
- **Cost tracker** — Sparkline: daily API cost. Horizontal threshold line at $5/day budget. Red when exceeded.
- **Error frequency** — Donut chart: errors by type (publish failure, render error, QC failure, API timeout). Click segment to filter Pipeline Runs.

**Export:** CSV button on each chart/table. PDF report button generates full analytics report.

### 3.7 Stories Explorer (`/stories`)

Data table view for browsing raw AI stories from the pipeline.

**Table columns:** Title, Source, Score, Cluster ID, # Blueprints, Published Date, Status
**Features:**
- Sortable by any column
- Faceted filters: source, score range, date range, has-blueprints
- Free text search (searches title + summary)
- Click row to expand: full story text, claims with citations, linked blueprints (clickable), risk classification
- Bulk export to CSV

### 3.8 Pipeline Runs (`/runs`)

Historical run browser.

**Table:** Run ID, Date, Duration, Steps Completed/Total, Errors, Cost
**Detail view (`/runs/:id`):** Full run report visualization:
- Step-by-step breakdown: each pipeline step with duration bar, status icon, input/output counts
- Clustering metrics: total clusters, avg size, stories collapsed
- Dedup counts: title/URL/content/TF-IDF
- Blueprint diversity: before/after filtering counts
- Error details: failed steps with full error messages, stack traces
- Cost breakdown: per-step API costs

### 3.9 Settings (`/settings`)

Configuration viewer + notification preferences.

**Tabs:**
- **Sources** — Read-only table of sources from `sources.yaml` with last fetch status
- **Scoring** — Read-only display of scoring weights from `scoring_weights.yaml`
- **Schedule** — Schedule slots from `publishing.yaml`, timezone display
- **Templates** — Template list from Microsoft Lists with constraint summaries
- **Notifications** — Configurable: Slack webhook URL, email digest (daily/weekly/off), notification types to receive
- **Auto-approve** — Confidence threshold slider (default: off). Enable/disable. Preview: "This would auto-approve N of your last 50 posts."
- **System** — Microsoft Lists connectivity status, API token health, disk usage, cache stats, uptime

---

## 4. AI-Native Features

### 4.1 AI Command Bar

Upgrades the cmdk command palette from navigation to natural language operations.

**Architecture:**

```
User input → cmdk fuzzy match (instant, local)
           → if no match: send to /api/v1/ai/command
           → Flask calls OpenAI gpt-4o-mini with function definitions
           → Returns: {action, params, preview_description, affected_items}
           → UI shows preview with [Confirm] [Cancel]
           → On confirm: POST /api/v1/ai/command/execute
```

**OpenAI function definitions map to API endpoints:**

```json
{
  "functions": [
    {"name": "list_blueprints", "parameters": {"status": "string", "platform": "string", "date_range": "string"}},
    {"name": "batch_review", "parameters": {"blueprint_ids": "array", "action": "string"}},
    {"name": "reschedule", "parameters": {"blueprint_id": "string", "new_time": "string"}},
    {"name": "get_failures", "parameters": {"platform": "string", "date_range": "string"}},
    {"name": "trigger_pipeline", "parameters": {}},
    {"name": "get_analytics", "parameters": {"metric": "string", "date_range": "string"}}
  ]
}
```

**Example interactions:**

| Input | Action | Preview |
|-------|--------|---------|
| "approve all visual ready for today" | batch_review | "Will approve 3 blueprints: BP-a1b2, BP-c3d4, BP-e5f6" |
| "what failed this week" | get_failures | Shows inline results: "2 Twitter failures (rate limit), 1 IG (token)" |
| "move saturday posts to monday" | reschedule (x4) | "Will reschedule 4 blueprints from Mar 1 → Mar 3" |
| "best template this month" | get_analytics | Shows inline: "carousel_5 — 94% approval, 12 posts published" |
| "show me unscheduled posts" | navigate + filter | Jumps to /blueprints?status=VISUAL_READY&scheduled=none |
| "run pipeline now" | trigger_pipeline | "Will trigger express pipeline. Confirm?" |

**Safety:** All destructive actions (approve, reject, reschedule, trigger) require explicit confirmation. Read-only queries show results inline without confirmation.

**Cost:** ~$0.001 per query with gpt-4o-mini. At 50 queries/day = $0.05/day.

### 4.2 Predictive Scheduling

Analyzes `Publishing_Analytics` data to suggest optimal posting times.

**Algorithm (runs server-side, cached 1 hour):**

```python
def compute_slot_scores():
    """Simple regression: engagement proxy by (time_slot, day_of_week)."""
    analytics = client.publishing_analytics.all(
        filter="published_at != ''",
        fields=["published_at", "platform", "engagement_proxy"]
    )
    # Group by (hour, day_of_week) → mean engagement
    # Normalize to 0-100 scale
    # Return matrix + per-slot recommendations
```

**UI integration:**
- Heatmap in Analytics view shows the matrix visually
- Schedule Board shows suggestion banners for suboptimal slots
- When dragging a blueprint to a slot, tooltip shows slot's historical performance score
- AI command bar can answer "when should I post this?"

### 4.3 Auto-Approve with Confidence Scoring

**Confidence score computation (server-side):**

```python
def compute_confidence(blueprint):
    score = 0.0
    # QC gates: +30 if all passed cleanly, +20 if passed with fixes, +10 if skipped
    score += qc_score(blueprint)
    # Template track record: historical approval rate for this template type
    score += template_approval_rate(blueprint["template_id"]) * 25
    # Story authority: source reliability score
    score += blueprint["story_authority_score"] * 25
    # Risk level: -20 for high risk, -10 for medium, 0 for low
    score += risk_adjustment(blueprint)
    # Content quality signals: hook length, caption completeness, hashtag count
    score += content_quality_score(blueprint) * 20
    return min(100, max(0, score))
```

**UI behavior by threshold (configurable in Settings):**
- **>threshold (default 90):** Auto-approve countdown bar appears (30 seconds). Animated progress bar (Framer Motion). Click to cancel. If countdown completes without cancellation, auto-approve fires.
- **70-threshold:** "Recommended for approval" badge. No auto-action.
- **<70:** "Review carefully" warning with confidence breakdown showing which factors scored low.

**Focus Review Mode integration:** Auto-approve countdown appears on high-confidence cards. Reviewer can let it count down (hands-free for obvious approvals) or override.

### 4.4 AI Content Suggestions

Server-side analysis + optional LLM enhancement for each blueprint.

**Computed analysis (no LLM, instant):**
- Hook length assessment (optimal: 8-15 words)
- Caption character count vs platform limits
- Hashtag competition analysis (if hashtag performance data available)
- Template fit score
- Similar past posts (cosine similarity on hook text)

**LLM-enhanced suggestions (on-demand, user clicks "Get AI suggestions"):**
- Hook alternatives (3 variations with predicted strength)
- Caption tone adjustment for each platform
- Hashtag recommendations based on topic
- CTA suggestions

**UI:** Collapsible panel in Blueprint Detail. Computed analysis shows immediately. "Get AI suggestions" button triggers LLM call (~$0.002 per blueprint).

---

## 5. Comparison Mode & Content Versioning

### 5.1 Comparison Mode

Select exactly 2 blueprints → side-by-side view.

**Trigger:** Select 2 cards in Content Board → "Compare" button appears in batch action bar. Or keyboard: select 2 with Space, press `c`.

**Layout:** Two-column, synchronized scroll:
- Left: Blueprint A video/carousel + content
- Right: Blueprint B video/carousel + content
- Bottom: Diff table highlighting differences (template, schedule, scores, platform readiness)
- Action: "Approve A" / "Approve B" / "Approve Both" / "Reject Both"

**Use case:** Two blueprints cover the same story with different angles or templates. Compare to pick the stronger one.

### 5.2 Content Versioning

Every content edit (via inline editing or API) creates a version.

**Data model (Microsoft Lists field additions):**
- `content_versions`: JSON array of `{version, timestamp, changes, source}` — last 20 versions
- Source: "auto-generated", "manual-edit", "ai-suggestion", "platform-adaptation"

**Diff viewer:**
- List of versions with timestamps and change source
- Click version → shows side-by-side diff: old (red strikethrough) vs new (green highlight)
- "Revert to this version" button on any historical version

---

## 6. Workflow Features

### 6.1 Inline Editing

Edit content directly in the Content Board or Blueprint Detail without navigating away.

**Editable fields:**
- Hook text: double-click → inline text input with character counter
- Caption: double-click → expandable textarea with platform limit indicators
- Hashtags: double-click → tag input (chips with add/remove)
- Scheduled time: double-click → date/time picker dropdown

**Behavior:**
- Optimistic update: UI changes immediately
- Auto-save after 500ms debounce
- Undo with Cmd+Z (reverts to previous version)
- Creates a content version on save
- PATCH `/api/v1/blueprints/:id/content`

### 6.2 Notification System

**In-app notifications:**
- Bell icon in header with unread count badge
- Dropdown panel: notification list, grouped by type, with timestamps
- Click notification → navigates to relevant entity
- "Mark all read" button

**Notification types:**
- Publish success/failure (per platform)
- Pipeline started/completed/failed
- Blueprint stuck in status > 24 hours
- API token expiry warning (7 days before)
- Auto-approve actions taken
- Schedule gaps detected

**Webhook integrations (configurable in Settings):**

Slack webhook:
```json
{
  "slack_webhook_url": "https://hooks.slack.com/services/...",
  "events": ["publish_failure", "pipeline_error", "token_expiry"]
}
```

Email digest:
```json
{
  "email": "user@example.com",
  "frequency": "daily",  // daily | weekly | off
  "include": ["schedule_summary", "publish_results", "alerts"]
}
```

**Implementation:** Flask background thread checks for notification triggers every 60 seconds. Stores notifications in a lightweight SQLite file (`.tmp/notifications.db`) — separate from Microsoft Lists to keep notification latency low.

### 6.3 Multi-Brand Workspace (Future-Ready)

Architecture supports multiple brands from day one. Not fully built, but wired.

**Data model:** Every API query accepts optional `?brand=blackbox-brief` parameter. Default: the only brand.

**UI:** Workspace switcher in sidebar (currently shows only "Blackbox Brief"). Adding a brand later requires:
1. New Microsoft Lists base (or filtered views) for the brand
2. Brand config in `publishing.yaml`
3. Brand appears in workspace switcher

**Implementation:** `WorkspaceProvider` context wraps the app. All TanStack Query keys include `workspaceId`. Switching workspace invalidates all queries.

### 6.4 Export & Reporting

**CSV export:** Available on every table view (Stories, Blueprints, Analytics, Pipeline Runs). Button generates CSV client-side from current filtered data.

**PDF report:** Weekly summary report. Sections:
- Posts published (count per platform, success rate)
- Top performers (highest engagement proxy)
- Pipeline health (run success rate, avg duration)
- Alerts and errors
- Schedule coverage (slots filled vs empty)

Generated server-side via `weasyprint` or `reportlab`. Endpoint: `GET /api/v1/reports/weekly?week=2026-W09`. Downloadable from Settings or via scheduled email.

---

## 7. Performance

### 7.1 Edge Caching (Cloudflare Workers)

Cloudflare Worker deployed on the `review.aspirehub.ai` route. Caches API responses at the edge.

**Cache rules:**

| Pattern | TTL | Purge on |
|---------|-----|----------|
| `/api/v1/blueprints?status=PUBLISHED` | 60s | Any blueprint status change |
| `/api/v1/analytics/*` | 5min | Never (data refreshes naturally) |
| `/api/v1/health` | 10s | Never |
| `/api/v1/config/*` | 10min | Config file change |
| `/api/v1/stories` | 60s | Pipeline completion |
| `POST/PATCH/*` | Never cached | Purges related GET keys |

**Impact:** Dashboard loads <100ms for cached views (served from nearest Cloudflare PoP).

### 7.2 Web Workers

Two dedicated workers for heavy client-side operations:

**search-indexer.worker.ts:**
- On app load: fetches all blueprint + story titles/hooks
- Builds Fuse.js fuzzy search index in background
- Updates incrementally via Socket.IO events
- Powers instant results in command palette (<10ms search)

**filter-engine.worker.ts:**
- When filter criteria change on large datasets (>500 items)
- Sorts, filters, groups data off main thread
- Returns filtered results via postMessage
- Main thread stays at 60fps during heavy operations

### 7.3 Virtualized Lists

TanStack Virtual on all list views:
- Content Board card grid: only renders cards in viewport + 3-card overscan
- Stories table: only renders visible rows
- Pipeline Runs table: same
- Activity feed: only renders visible events

**Impact:** 1000+ blueprints render without lag. DOM node count stays <200 regardless of data size.

### 7.4 Code Splitting

TanStack Router lazy-loads each view:
```typescript
const PipelineView = lazy(() => import("./views/pipeline"));
const AnalyticsView = lazy(() => import("./views/analytics"));
// Recharts only loads when Analytics view is opened
```

**Bundle budget:**
- Shell (layout + sidebar + command palette): ~80KB gzipped
- Per-view: ~20-40KB gzipped each
- Recharts (analytics only): ~45KB gzipped
- Framer Motion: ~30KB gzipped
- Total initial load: ~120KB gzipped (shell + home view)

---

## 8. Offline & PWA

### 8.1 Service Worker

**Caching strategy:**
- App shell (HTML + JS + CSS + fonts): Cache-first. Instant load on return visits.
- API responses: Network-first with fallback to cached. Shows stale-data banner when serving cached.
- Media files (images, video thumbnails): Cache-first with background revalidation.

### 8.2 IndexedDB

- Stores last 100 blueprints with full data (minus video files)
- Stores last 50 stories
- Stores last 7 days of notifications

**Offline review queue:**
- User reviews blueprints while offline → actions stored in IndexedDB queue
- When back online: queue syncs sequentially, shows "Synced N offline actions" toast
- Conflict resolution: server state wins. If blueprint was already reviewed by another path (e.g., auto-approve), shows "Action superseded" notification.

### 8.3 PWA Manifest

```json
{
  "name": "Blackbox Brief Operations",
  "short_name": "BB Ops",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#0a0a0a",
  "theme_color": "#6366f1",
  "icons": [
    {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
    {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png"}
  ]
}
```

Installable on iOS (Add to Home Screen), Android (install prompt), macOS/Windows (Chrome install).

---

## 9. Design Language

### Colors

| Token | Value | Usage |
|-------|-------|-------|
| `--bg-primary` | `#0a0a0a` | Page background |
| `--bg-surface` | `#141414` | Cards, panels, modals |
| `--bg-elevated` | `#1c1c1c` | Hover states, active items |
| `--border` | `#262626` | Card borders, dividers |
| `--text-primary` | `#fafafa` | Headings, body text |
| `--text-secondary` | `#a1a1aa` | Labels, metadata |
| `--text-muted` | `#52525b` | Placeholders, disabled |
| `--accent` | `#6366f1` | Primary actions, links, active nav |
| `--accent-secondary` | `#8b5cf6` | Secondary highlights |
| `--success` | `#22c55e` | Published, approved, healthy |
| `--warning` | `#f59e0b` | Pending, needs attention |
| `--error` | `#ef4444` | Failed, rejected, errors |
| `--info` | `#06b6d4` | Informational, neutral badges |

### Typography

- Font family: Inter (loaded from repo `templates/fonts/`)
- Headings: 600 weight, tracking -0.025em
- Body: 400 weight, 14px base, 1.5 line height
- Mono (code, IDs): JetBrains Mono, 13px

### Motion (Framer Motion)

| Context | Duration | Easing |
|---------|----------|--------|
| Page transition | 150ms | ease-out |
| Panel slide (detail) | 250ms | spring(stiffness: 300, damping: 30) |
| Card hover | 100ms | ease-out |
| Toast enter | 200ms | spring |
| Drag | real-time | spring(stiffness: 500, damping: 30) |
| Auto-approve countdown | 30s | linear |

All animations respect `prefers-reduced-motion: reduce` — disabled entirely when preference set.

### Responsive Breakpoints

| Breakpoint | Layout |
|------------|--------|
| < 640px (mobile) | Single column, bottom nav, swipe gestures, no sidebar |
| 640-1024px (tablet) | 2-column grid, collapsible sidebar, touch targets ≥44px |
| > 1024px (desktop) | 3-column grid, persistent sidebar, full keyboard support |

---

## 10. Accessibility

- ARIA labels on all interactive elements
- Focus rings visible on keyboard navigation (2px ring, accent color)
- Skip-to-content link (hidden until Tab)
- Screen reader announcements for: toast notifications, status changes, navigation, action confirmations
- Color is never the sole indicator — always paired with icon or text
- Reduced motion mode (respects OS preference, toggle in Settings)
- High contrast mode (toggle in Settings — increases border contrast, uses solid fills instead of gradients)
- Minimum touch targets: 44x44px on mobile
- Focus trap in modals and command palette

---

## 11. Security

All existing security measures maintained and extended:

- CSRF token on all POST/PATCH requests (existing per-session nonce)
- Microsoft Lists record ID validation: `^\d+$` regex (integer IDs)
- Path traversal prevention on media routes (existing)
- CORS: locked to `review.aspirehub.ai` origin
- Cloudflare Access: primary auth layer (Google SSO)
- Content Security Policy headers for the React SPA
- No credentials in URLs or client-side storage
- API rate limiting: 100 req/min per endpoint (prevents runaway polling)
- AI command execution: requires explicit confirmation step (no auto-execute)
- Notification webhook URLs: validated against allowlist patterns
- IndexedDB: no sensitive data (no tokens, no API keys) — only content data

---

## 12. Infrastructure

### Cloudflare Named Tunnel

No changes from previous design:
- Route: `review.aspirehub.ai` → `http://localhost:5151`
- HTTPS termination at Cloudflare
- Cloudflare Access (Google SSO, free tier)

### launchd Daemon

Plist: `runbooks/com.genlab.review-server.plist`
- `KeepAlive: true` — auto-restart on crash
- `ThrottleInterval: 10` — prevent restart storms
- Pre-start: `cd dashboard && npm run build` (ensures latest frontend)
- Start: `gunicorn --workers 2 --worker-class eventlet --timeout 120 --bind 0.0.0.0:5151`
- Logs: `.tmp/logs/review_server_{stdout,stderr}.log`

### Gunicorn

- 2 workers (lightweight server)
- eventlet worker class (Socket.IO support)
- `--timeout 120` for large media requests
- `--access-logfile .tmp/logs/review_server_access.log`

### Build Integration

```bash
# Pre-start hook in launchd plist or wrapper script:
cd /path/to/project/dashboard && npm run build 2>&1 | tee ../.tmp/logs/dashboard_build.log

# Alternative: git post-merge hook for automatic rebuild on pull
# .git/hooks/post-merge:
# cd dashboard && npm run build
```

---

## 13. Non-Goals (Explicitly Out of Scope)

- No migration away from Microsoft Lists as database — it remains the headless backend
- No multi-user collaboration (presence indicators, shared cursors) — one operator at a time
- No mobile-native app — PWA covers mobile use cases
- No custom report builder — predefined analytics panels + CSV export is sufficient
- No A/B test management UI — A/B tests are managed via code/config
- No direct Microsoft Lists schema editing from the dashboard — schema changes are code-managed
- No video editing — video is rendered by the pipeline, dashboard only previews

---

## 14. Verification Criteria

### Infrastructure
- [ ] Flask serves React SPA from `dashboard/dist/` at `/`
- [ ] Client-side routing works (direct URL access to `/blueprints/recXXX` loads correctly)
- [ ] Vite dev mode proxies API calls to Flask
- [ ] Production build < 200KB gzipped initial load
- [ ] `review.aspirehub.ai` resolves and loads dashboard
- [ ] Cloudflare Access prompts for Google login
- [ ] launchd daemon starts on boot, recovers from crashes
- [ ] Gunicorn serves with 2 eventlet workers

### Core Views
- [ ] Pipeline Overview shows live KPIs, schedule, activity feed
- [ ] Content Board: card grid loads, filters work, URL-persisted
- [ ] Content Board: detail panel opens on card click, video plays
- [ ] Content Board: approve/reject/revise update Microsoft Lists
- [ ] Content Board: batch select + batch review works
- [ ] Blueprint Detail: all tabs render (IG/YT/TW/FB/History)
- [ ] Schedule Board: week view with drag-drop between slots
- [ ] Month Calendar: slot fill indicators, day expansion
- [ ] Analytics: all chart panels render with real data
- [ ] Stories: sortable table with search and filters
- [ ] Pipeline Runs: list + detail views with step breakdown
- [ ] Settings: config display + notification preferences save

### AI Features
- [ ] Command palette: fuzzy search finds blueprints/stories instantly
- [ ] AI command: natural language → preview → confirm → execute
- [ ] Predictive scheduling: suggestions appear on Schedule Board
- [ ] Auto-approve: confidence score displays, countdown bar works
- [ ] AI suggestions: computed analysis appears in Blueprint Detail

### Interactivity
- [ ] Keyboard shortcuts: j/k navigate, a/r/v actions, Cmd+K palette, g+p/b/s navigation
- [ ] Focus Review Mode: full-screen flow with swipe on mobile
- [ ] Inline editing: double-click hook/caption, auto-saves with debounce
- [ ] Comparison Mode: select 2 → side-by-side view
- [ ] Content versioning: edit history with diffs
- [ ] Undo: 5-second window on all destructive actions

### Real-Time
- [ ] Socket.IO: blueprint status updates appear live
- [ ] Socket.IO: pipeline progress shows in real-time
- [ ] Socket.IO: publish results update platform badges
- [ ] Notifications: in-app bell icon with unread count
- [ ] Activity feed: live event stream

### Performance
- [ ] TanStack Virtual: 500+ cards render without jank
- [ ] Web Worker: search index builds in background
- [ ] Code splitting: Analytics view loads Recharts on demand
- [ ] Edge cache: Cloudflare Worker caches GET responses

### Offline & PWA
- [ ] Service Worker: app loads instantly on return visits
- [ ] PWA: installable on mobile (Add to Home Screen)
- [ ] Offline: cached blueprints browsable without network
- [ ] Offline queue: review actions queue and sync on reconnect

### Accessibility
- [ ] Keyboard navigation through all views
- [ ] Screen reader: ARIA labels on all interactive elements
- [ ] Reduced motion: animations disabled when OS preference set
- [ ] Touch targets: ≥ 44px on mobile
- [ ] Focus rings visible on keyboard navigation

### Security
- [ ] CSRF token on all mutating requests
- [ ] Record ID validation on all Microsoft Lists operations
- [ ] CSP headers present
- [ ] No credentials in client-side code or storage
- [ ] AI commands require confirmation before execution
