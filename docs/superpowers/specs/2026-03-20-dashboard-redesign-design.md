# Dashboard Redesign — Design Spec

**Date:** 2026-03-20
**Philosophy:** Unified Command Center — operational confidence AND strategic insight
**Approach:** Upgrade all views, add 2 new views (Learning Intelligence, Engagement)

---

## Navigation (10 views)

| # | View | Status | Route |
|---|------|--------|-------|
| 1 | Mission Control | Redesign | `/` |
| 2 | Content Review | New (merges Focus Review + Blueprints + Queue) | `/content` |
| 3 | Analytics | Upgrade | `/analytics` |
| 4 | Learning Intelligence | New | `/learning` |
| 5 | Engagement | New | `/engagement` |
| 6 | Pipeline Monitor | Upgrade | `/pipeline` |
| 7 | Schedule | Upgrade | `/schedule` |
| 8 | System Health | New (merges Channel Health + Token Health) | `/health` |
| 9 | Monetisation | Upgrade | `/monetisation` |
| 10 | Settings | Keep | `/settings` |

**Removed views:** Stories (standalone), Runs (merged into Pipeline), old Blueprints, old Focus Review, old Publishing Queue, old Channel Health.

---

## 1. Mission Control (`/`)

**Layout:** KPI Hero + Bento Grid. 7 rows.

### Row 0: Error Alert Banner (conditional)
- Dismissable red-tinted strip
- Only renders when there's an actionable problem (token expired, pipeline failed, agent down)
- Source: `/api/v1/health/detailed`

### Row 1: KPI Hero (4 cards)
| Card | Primary | Delta |
|------|---------|-------|
| Total Reach | `617K` | `+2.1K today` |
| Engagement | `36.9K` likes | `+84 today` |
| Published Today | `5/5` | `All channels active` |
| System Health | `31/31` | `Healthy` |

- Source: `/api/v1/overview` + `/api/v1/health/detailed`
- Animated count-up on mount (existing `useCountUp` hook)

### Row 2: Top Post + Learning Loop + AI Insight (3 cards, 5:3:3 ratio)

**Top Post Spotlight:**
- Video thumbnail (64×64, fallback to placeholder)
- Hook text (bold), channel + platform + date
- Engagement: likes (platform color), comments, reach
- Source: `/api/v1/analytics/content-performance` (sort by likes desc, limit 1)

**Learning Loop:**
- Status dot (green = Thompson active)
- Best performing arm with mean percentage
- Avg reward, max reward
- Progress indicators: `LinUCB 4/50 · XGB 15/200 · Config 15/50`
- Source: `/api/v1/learning/status`

**AI Insight Card:**
- Gradient border (purple→blue)
- Auto-generated daily insight from bandit arm comparisons
- Example: "BB's deepfake content gets 8x more engagement than tutorials"
- Source: `/api/v1/learning/status` (compute client-side from arm means)

### Row 3: Today's Publishes + Upcoming Queue (1:1 ratio)

**Today's Publishes:**
- Timeline format: `HH:MM` → niche color dot → platform icons with ✓/✗
- Source: `/api/v1/queue` filtered by today

**Upcoming Queue:**
- Next 3 scheduled posts
- Each: date + niche color + hook text + approval status badge
- Inline approve button if not yet approved
- Source: `/api/v1/queue` filtered by status=SCHEDULED

### Row 4: Channel Strip with Sparklines (5 equal columns)
- Per channel: niche accent left border, channel name, key metric (largest number), follower count
- 7-day sparkline (gradient fill matching niche color)
- Source: `/api/v1/overview` per niche

### Row 5: Live Engagement + Trend Radar + Content Quality (2:1:1 ratio)

**Live Engagement Feed:**
- Last 5-10 interactions: timestamp, platform icon, username, comment text (truncated), toxicity badge (safe/review/flagged)
- Total count at bottom
- Source: `/api/v1/engagement/status` + new `/api/v1/engagement/recent`

**Trend Radar:**
- Google Trends topics with multiplier badges (3.0x purple, 1.5x blue, 1.0x gray)
- Cache TTL indicator
- Source: new `/api/v1/trends`

**Content Quality Scorecard:**
- Hooks generated, banned blocked, dupes rejected, relevance passes
- Compact stat list
- Source: pipeline run_report data via `/api/v1/pipeline/runs` (latest)

### Row 6: Pipeline Countdowns + Monetisation (1:1 ratio)

**Next Pipeline Runs:**
- 5 niche countdowns in 2-column grid
- Publisher time at bottom
- Live countdown (JS interval)

**Monetisation:**
- Nearest threshold per channel (not all thresholds — those go on dedicated page)
- Single progress bar per channel with niche color
- Source: `/api/v1/monetisation/progress`

---

## 2. Content Review (`/content`)

**Merges:** Focus Review + Blueprints + Publishing Queue

**Layout:** Filter bar + card grid + Focus Mode overlay

### Filter Bar
- Status pills: ALL | VISUAL_READY | DRAFTED | PUBLISHED | ARCHIVED
- Niche dropdown (All / per-niche)
- Sort: newest / priority score / engagement
- Search by hook text

### Card Grid
Each card:
- Video thumbnail (from visual_paths, served via `/api/media/`)
- Hook text (bold, max 2 lines)
- Title (secondary text)
- Priority score badge
- Niche accent dot + niche name
- Platform publish status: IG ✓ YT ✓ FB ✓ X ✗ TH ✗
- If published: engagement mini-stats (likes, comments)
- Action buttons: Approve / Reject / Schedule (contextual by status)

### Focus Mode (overlay)
- Full-screen card with video player
- Platform-specific caption previews (IG, YT, Twitter, FB)
- Left/right navigation through filtered set
- Keyboard shortcuts: A=approve, R=reject, →=next

---

## 3. Analytics (`/analytics`)

**Layout:** KPI summary + tabbed charts

### KPI Summary (4 cards)
- Total Likes, Total Reach, Total Comments, Engagement Rate

### Tabs
1. **Overview** — cross-niche comparison bar chart + 7d/14d/30d time series
2. **By Platform** — platform breakdown donut + per-platform metrics table
3. **Top Posts** — sortable table of top 20 posts with engagement data, sparklines
4. **By Niche** — per-niche deep dive with individual charts

### Data Source
- `/api/v1/analytics/overview` + `/api/v1/analytics/content-performance`
- Real data: 36.9K likes, 617K reach, 296 comments across 194 analytics records

---

## 4. Learning Intelligence (`/learning`)

**Layout:** 5 tabs

### Tab 1: Overview
- Status card: which learning mode is active (Thompson / LinUCB)
- 3 progress bars: LinUCB (4/50), Hook Classifier (15/200), Config Update (15/50)
- Summary stats: rewards computed, avg reward, max reward
- Last update timestamp

### Tab 2: Bandit Arms
- Per-niche expandable sections
- Each arm: arm_id, alpha, beta, n_plays, mean (alpha/(alpha+beta))
- Visual: horizontal bar representing Beta distribution mean with confidence interval
- Sort by mean desc (best performing first)

### Tab 3: Rewards
- Time-series chart of reward_48h values over time
- Color-coded by niche
- Moving average line
- Distribution histogram

### Tab 4: Hook Classifier
- Training progress (15/200 examples)
- If trained: feature importance chart (8 features)
- Recent predictions table: hook text → predicted score
- Model accuracy if available

### Tab 5: Config Updates
- Log of YAML changes made by config_update_flow
- Each entry: date, file changed, field, old value → new value
- Empty state: "No config updates yet — need 50 completed tasks per platform"

### Data Source
- `/api/v1/learning/status` + `/api/v1/learning/bandit-state` + `/api/v1/learning/hook-classifier-status`

---

## 5. Engagement (`/engagement`)

**Layout:** Two-panel with stats header

### Stats Header
- Total comments (73), auto-replied (0), pending review (0), discarded (0)
- Platform breakdown: IG 71, YT 9, FB 0

### Left Panel: Comment Feed
- Scrollable list of real comments
- Each: platform icon, @username, comment text, timestamp, toxicity badge
- Toxicity badge: safe (green), review (amber), toxic (red)
- Click to expand → show full context + post it was on

### Right Panel: Reply Queue
- Pending human-review replies
- Each: original comment, generated reply, confidence score, toxicity score
- Actions: Approve (sends reply), Edit (modify before sending), Reject (discard)
- Empty state when no pending replies

### Filters
- Niche dropdown
- Platform filter
- Status: all / safe / review / toxic

### Data Source
- New `/api/v1/engagement/recent` (fetch real IG/YT comments via API)
- `/api/v1/engagement/pending-replies`
- `/api/v1/engagement/status`

---

## 6. Pipeline Monitor (`/pipeline`)

**Upgrade from existing:**
- Keep: stage waterfall, run history table, log viewer
- Add: arm_id column in run details
- Add: content quality stats per run (hooks generated, banned blocked, dupes rejected)
- Add: YouTube quota usage per run
- Add: link to published posts from run

---

## 7. Schedule (`/schedule`)

**Upgrade from existing:**
- Keep: calendar view, drag-and-drop
- Add: niche accent colors on calendar slots
- Add: engagement prediction badge (from hook classifier when trained)
- Add: quick-approve from calendar card

---

## 8. System Health (`/health`)

**Merges:** Channel Health + Token Health + LaunchAgent status

### Section 1: Platform Tokens
- Grid: 5 niches × 6 platforms
- Each cell: status dot (green/amber/red) + last verified timestamp
- Click to refresh individual token

### Section 2: Infrastructure
- PostgreSQL, Redis, Prefect Server, Cloudflare Tunnel
- Status dot + response time + uptime

### Section 3: LaunchAgents
- 31 agents grouped: always-on (10), scheduled (21)
- Each: name, status (running/stopped/scheduled), PID, last run time
- Source: `/api/v1/health/detailed` (already includes `launch_agents`)

---

## 9. Monetisation (`/monetisation`)

**Upgrade from existing:**
- Full multi-threshold view: all 5 channels × 4 platform thresholds
- IG Followers (10K), YT Subscribers (1K), YT Watch Hours (4K), FB Page Likes (5K)
- Two-column grid per channel with progress bars
- Projected timeline: "At current growth rate, BB reaches 10K IG followers in ~X months"
- Historical growth chart (if data available)

---

## 10. Settings (`/settings`)

**Keep existing** with minor enhancements:
- Add: niche config viewer (read-only YAML display)
- Add: credential status summary
- Add: notification preferences for alerts

---

## Backend API Changes Needed

| Endpoint | Status | Purpose |
|----------|--------|---------|
| `/api/v1/learning/status` | Done | Comprehensive learning loop state |
| `/api/v1/engagement/recent` | New | Fetch real IG/YT comments via platform APIs |
| `/api/v1/engagement/fetch-comments` | Done | Manual comment backfill |
| `/api/v1/trends` | New | Google Trends cache data |
| `/api/v1/health/detailed` | Done (has LaunchAgent status) | System health |
| `/api/v1/overview` | Existing | Cross-niche overview |
| `/api/v1/pipeline/runs` | Existing | Run history with quality stats |

---

## Design System

**Keep existing Obsidian tokens** — the dark theme, typography scale, spacing, radius, shadows, platform colors, and niche accent colors are well-designed. No changes to the design system foundation.

**Component approach:**
- Keep existing Radix UI primitives (Button, Card, Dialog, Tabs, etc.)
- Use Recharts for all new charts (already in use)
- Use Framer Motion for animations (already in use)
- Use cmdk for command palette (already in use)

---

## Implementation Priority

| Phase | Views | Effort |
|-------|-------|--------|
| 1 | Mission Control redesign | 2-3 days |
| 2 | Learning Intelligence (new) + Engagement (new) | 2-3 days |
| 3 | Content Review (merge 3 views) | 1-2 days |
| 4 | Analytics + System Health upgrades | 1-2 days |
| 5 | Pipeline + Schedule + Monetisation + Settings upgrades | 1-2 days |

**Total estimated effort:** 7-12 days of focused frontend work.
