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

## World-Class Polish (applies to all views)

### 1. Micro-Animations & Transitions
- KPI numbers count up on mount (existing `useCountUp` hook, apply everywhere)
- Cards stagger-fade in (Framer Motion `staggerChildren: 0.05`)
- Sparklines draw themselves (SVG `stroke-dashoffset` animation)
- Progress bars animate to their width (CSS transition on width)
- KPI deltas pulse green briefly on positive change
- Hover states: `scale(1.01)` + border luminance increase
- Page transitions: `AnimatePresence` with slide + fade

### 2. Real-Time Pulse
- Socket.IO already in place — extend to emit:
  - `engagement_new` — new like/comment (update feed + KPI counter)
  - `publish_complete` — post published (update timeline + KPI)
  - `pipeline_stage` — stage progress (update pipeline view)
  - `learning_update` — bandit reward computed (update learning card)
- Breathing pulse dot in sidebar logo (CSS animation, green when connected, amber when reconnecting)
- New engagement feed items slide in with a 2s subtle glow border

### 3. Depth & Layering
- Cards: `border: 1px solid var(--border)` → on hover add `box-shadow: inset 0 1px 0 rgba(255,255,255,0.04)`
- AI Insight card: animated gradient border using `@keyframes` rotating `conic-gradient`
- Active nav item: pill background with `box-shadow: 0 0 12px rgba(var(--niche-color), 0.15)`
- Popovers/dropdowns: `backdrop-filter: blur(12px)` + slightly transparent background
- Modal overlays: `backdrop-filter: blur(4px)` on backdrop

### 4. Premium Data Visualization
- Replace ASCII sparklines with Recharts `<AreaChart>` mini components (no axes, just the shape)
- Animated gradient fills using `<defs><linearGradient>` with niche accent colors
- Platform breakdown: `<PieChart>` donut with platform brand colors
- Posting heatmap: 7×24 grid (day × hour) with opacity-mapped cells
- Niche radar chart: 5-axis comparison (reach, engagement, growth, quality, consistency)
- Every data point has a tooltip on hover

### 5. Command Palette Power
- Already uses cmdk — extend with:
  - Search posts by hook text (fuzzy match)
  - `approve [niche]` — quick-approve all VISUAL_READY for a niche
  - `goto learning` / `goto analytics` — navigation
  - `show top posts` — jump to analytics top posts tab
  - Recent actions section (last 5 approvals/rejects)
  - `export analytics csv` — trigger download

### 6. Keyboard-First Navigation
- Already has keyboard shortcuts — extend:
  - `G M` Mission Control, `G L` Learning, `G E` Engagement, `G C` Content Review
  - `J/K` navigate lists (blueprints, comments, posts)
  - `A` approve, `R` reject, `S` schedule (context-aware)
  - `Space` preview/expand selected item
  - `Esc` close any overlay
  - `?` show keyboard shortcut overlay
  - Shortcut hints shown as `<kbd>` tags next to actions

### 7. Contextual Quick Actions
- Right-click context menu (Radix `<ContextMenu>`) on:
  - Channel cards → "View analytics", "Run pipeline now", "Open IG profile", "Open YT channel"
  - Post cards → "View on platform", "Copy hook", "See engagement", "Archive"
  - KPI cards → "View detailed breakdown", "Export"
- Hover any KPI number → tooltip with per-platform breakdown
- Click any niche dot → filter entire dashboard to that niche

### 8. Dark Mode Polish
- Add CSS noise texture overlay on `body::before` (PNG, `opacity: 0.02`)
- Greeting section: subtle radial gradient mesh (`radial-gradient` with niche color at 3% opacity)
- Platform icons: use full-color SVG icons (Instagram gradient, YouTube red play button, etc.)
- Channel cards: niche accent color as subtle background tint (`rgba(accent, 0.03)`) not just left border
- Selected/active states: colored ring (`box-shadow: 0 0 0 2px var(--accent)`)
- Focus rings: match niche accent color

### 9. Responsive & Mobile
- Sidebar: collapsible to icon-only on tablet (`<768px`), hidden on mobile (`<640px`)
- Mobile: bottom tab bar with 5 primary views (Mission, Content, Analytics, Learning, Settings)
- KPI hero: horizontal scrollable strip on mobile
- Card grids: stack to single column
- Channel strip: horizontal scroll on mobile
- Swipe gestures on content review cards (right=approve, left=reject)
- PWA manifest + service worker for offline shell
- `<meta name="apple-mobile-web-app-capable">` for iOS home screen

### 10. Empty States & Onboarding
- Every card has a meaningful empty state:
  - Engagement: "No comments yet — engagement typically appears 2-6 hours after publishing"
  - Learning: "The bandit needs 50 observations to activate LinUCB — currently at 4/50"
  - Analytics: "Metrics are collected every 6 hours — first data point expected at [time]"
- Skeleton loading: shimmer animation matching card dimensions (existing pattern, apply consistently)
- First-time tooltip tour (optional, dismissable, persisted in localStorage)

### 11. Export & Sharing
- Screenshot card as PNG: `html2canvas` on any card via context menu
- Export analytics as CSV: download button in Analytics view header
- Weekly report: auto-generated markdown/PDF with KPI summary, top posts, learning progress
- Copy KPI summary: "617K reach · 36.9K likes · 5/5 published" → clipboard
- Share link: read-only URL with auth token (future — not MVP)

### 12. Sound Design
- Subtle UI sounds (opt-in, muted by default, toggle in Settings):
  - `approve.mp3` — soft click on approve action
  - `reject.mp3` — lower tone on reject
  - `publish.mp3` — achievement chime when post publishes
  - `viral.mp3` — celebratory tone when post exceeds 1K likes
  - `notification.mp3` — gentle ping for new comments
- Use Web Audio API for low-latency playback
- Respect `prefers-reduced-motion` and mute preference

---

## Implementation Priority

| Phase | Scope | Effort |
|-------|-------|--------|
| 1 | Mission Control redesign + micro-animations + dark polish | 3-4 days |
| 2 | Learning Intelligence (new) + Engagement (new) | 2-3 days |
| 3 | Content Review (merge 3 views) + keyboard nav + contextual actions | 2-3 days |
| 4 | Analytics upgrade + premium data viz | 2-3 days |
| 5 | System Health + Pipeline + Schedule + Monetisation upgrades | 2-3 days |
| 6 | Real-time pulse (WebSocket events) + command palette extensions | 1-2 days |
| 7 | Responsive/mobile + PWA + empty states | 2-3 days |
| 8 | Export/sharing + sound design + final polish | 1-2 days |

**Total estimated effort:** 15-23 days of focused frontend work.
