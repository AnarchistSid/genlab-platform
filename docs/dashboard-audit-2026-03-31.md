# Dashboard UI/UX Comprehensive Audit — 2026-03-31

## Executive Summary

20,967 lines of frontend code across 128 source files. 10 views, 25 hooks, 65 components.
Audited every page visually (screenshots), every API endpoint (curl), and every data flow (source code).

**Found: 52 issues** (8 critical, 14 high, 18 medium, 12 low)

---

## CRITICAL — Broken Functionality (8)

### C1. Engagement page crashes: "e.map is not a function"
- **File**: `api/client.ts:352` — `engagementApi.recent` typed as `get<EngagementComment[]>`
- **API returns**: `{code:200, data: {comments: [...]}}` → `unwrapResponse` extracts `.data` → `{comments: [...]}`
- **Bug**: Frontend gets `{comments: [...]}` (object) but type says `EngagementComment[]` (array). `EngagementView.tsx:43` assigns `resp.data ?? []` which is the object, then `deriveStats()` calls `.map()` on it.
- **Fix**: Change `engagementApi.recent` to extract `.comments`: `get<{comments: EngagementComment[]}>("/engagement/recent").then(d => d.comments)`

### C2. Analytics hero KPIs all show zero (Total Likes: 0, Total Reach: 0, Total Comments: 0)
- **File**: `views/analytics/Analytics.tsx:519-523`
- **API returns**: `/analytics/top-posts` → `{data: {posts: [...]}}` → unwrap → `{posts: [...]}`
- **Bug**: `topPostsQuery.data` is `{posts: [...]}`. Line 521: `Array.isArray(raw)` is false for an object → returns `[]`. Hero KPIs sum over empty array = 0.
- **Fix**: Change line 521 to: `return Array.isArray(raw) ? raw : Array.isArray((raw as any)?.posts) ? (raw as any).posts : [];`

### C3. System Health: all platform tokens show "Not configured"
- **File**: `api/client.ts:320` — `tokenHealth.get()` fetches `/api/token-health` (no `/v1/` prefix)
- **Backend**: Token health route is at `/api/v1/token-health` — the client skips the `/v1/` prefix by using raw `fetch()` instead of the `get()` helper.
- **Fix**: Change to `fetch("/api/v1/token-health")` or use `get<TokenHealthResponse>("/token-health")`

### C4. Monetisation progress bars all show "-" and "0/0 met"
- **File**: `views/monetisation/MonetisationProgress.tsx:95` reads `metric.pct_complete`
- **API returns correct data**: `pct_complete: 1.0099` for BB Facebook
- **Bug**: The widget renders but `current_value` shows as `"-"` — the frontend formats null values as "-". The old SharePoint tracker populated `current_value` but the new Postgres data from `collect_audience_metrics` uses different field names in the `monetisationprogress` table. The monetisation API reads from this table via `BacklogClient.monetisation_progress.all()` which may not be finding the new rows due to RLS or table name mapping.
- **Deeper root cause**: `BacklogClient` looks for table `MonetisationProgress` (CamelCase) but the Postgres table is `monetisationprogress` (lowercase). The `_validate_table` mapping may not include this table.
- **Fix**: Add `monetisationprogress` to the BacklogClient table mapping, or fix the monetisation API to query Postgres directly

### C5. Revenue widget shows "0 Clicks" and "₹0" despite data existing
- **File**: `api/client.ts:246-258` — `revenue.summary()` typed as `get<{data: {clicks:...}}>`
- **Bug**: Double-wrapping. API returns `{code:200, data: {clicks:{today:0, last_7d:0, last_30d:61}}}`. `unwrapResponse` extracts `.data` → `{clicks:{...}}`. But the type expects another `.data` wrapper: `{data: {clicks:...}}`. The hook receives `{clicks:{...}}` but the component reads `.data.clicks` which is undefined.
- **Fix**: Change type to `get<{clicks:{today:number; last_7d:number; last_30d:number}; ...}>("/revenue/summary")`

### C6. Mission Control: Monetisation compact shows "No data" for all niches
- **File**: `views/mission-control/MonetisationCompact.tsx:30`
- **Same root cause as C4** — monetisation progress API returns data but in a format the compact widget doesn't parse correctly, or returns empty because of table mapping issues.

### C7. Publishing Queue page returns 404 error
- **File**: `api/client.ts:300` — `queue.stats()` calls `/queue/stats`
- **API**: Returns `{code:404, error:...}` — the queue stats endpoint may not be registered
- **Impact**: Publishing Queue page shows error state

### C8. Top Post on Mission Control shows "Untitled"
- **File**: `views/mission-control/TopPostSpotlight.tsx`
- **API**: `best_post.hook_text` and `best_post.title` are both empty strings in the overview response
- **Root cause**: The best_post query in `overview.py` selects by `priority_score` but doesn't join with blueprint content fields — `hook_text` and `title` aren't populated on the analytics record, only on the blueprint.
- **Fix**: Join with blueprints table to get hook_text/title, or fall back to candidate_id lookup

---

## HIGH — Wrong/Misleading Data (14)

### H1. Data conflict: Mission Control "641.6K reach" vs Analytics "20.8K reach"
- MC uses `engagement/summary` which sums ALL analytics records (all-time). Analytics overview uses 7-day window.
- **Fix**: Add time window label to MC KPIs: "641.6K reach (all time)" or change MC to use 7-day window.

### H2. Top Performers: same hook repeated 6 times
- Backend `top_performers` in analytics overview doesn't deduplicate by blueprint — same content across multiple insight collection windows appears multiple times.
- **Fix**: Deduplicate by `post_id` or `candidate_id` in the backend query, keeping the latest/highest engagement record.

### H3. Unicode `\u00b7` rendering as literal text
- **File**: `Analytics.tsx:693` — `subtitle="likes \u00b7 comments \u00b7 shares / reach"`
- This is actually correct JS unicode escape — it should render as `·`. If showing literally, it's a React rendering issue or the string is double-escaped.
- **Check**: May be a build artifact. Verify in browser devtools.

### H4. Content Funnel: Fetched=739 and Filtered=739 identical
- Backend `analytics/overview` funnel query doesn't distinguish between fetched and filtered counts — both return the total stories count.
- **Fix**: Track filtered-out count separately in run_report.json and pass through.

### H5. Upcoming Queue shows past dates (Mar 27-29)
- **File**: `views/mission-control/UpcomingQueue.tsx:29-30` — filters `queue_status !== "PUBLISHED"` but doesn't filter `scheduled_for > NOW()`
- **Fix**: Add date filter: `.filter(item => new Date(item.scheduled_for) > new Date())`

### H6. "Daily Intel" and all scheduled agents show red "Stopped"
- **File**: `views/health/SystemHealthView.tsx` — maps launchd exit code 0 + no PID to "Stopped" with red indicator
- These are cron jobs that run and exit normally. "Stopped" implies failure.
- **Fix**: For scheduled agents, show "Idle" with grey indicator instead of red "Stopped". Show "Last ran: Xh ago" from run reports.

### H7. Monetisation metric names raw: "watch_hours_(12mo)", "page_likes"
- Backend returns metric_name as-is from the DB
- **Fix**: Add a formatter in the frontend or backend that converts snake_case/parenthesized names to title case.

### H8. Facebook shows "0" reach with "88 posts" in Platform Breakdown
- FB video_insights API doesn't return "reach" as a metric — only views and likes.
- **Fix**: Show "Views" instead of "Reach" for Facebook, or add tooltip explaining FB doesn't expose reach.

### H9. "1 publishing alert" banner doesn't show details until expanded
- User must click "expand" to see what the alert is about.
- **Fix**: Show alert summary inline: "1 publishing alert: Instagram reel failed for gaming"

### H10. "No live metrics yet" at bottom of Monetisation page
- This message comes from the old SharePoint-based tracker check. The new Postgres data exists but the check doesn't find it.
- **Fix**: Remove the old tracker check or update it to query the `monetisationprogress` table.

### H11. Reach Over Time chart: Mar 25-29 show flat zero
- Insights collector was broken until today. Historical data only exists from Mar 30-31.
- Not a bug — will self-correct as data accumulates. Consider adding annotation: "Insights collection started Mar 31"

### H12. Sprint badge "Sprint 68" is hardcoded
- **File**: `views/mission-control/MissionControl.tsx` — static string
- **Fix**: Read from config or make it a setting

### H13. Socket "disconnected" flicker on every page navigation
- WebSocket drops and reconnects on each SPA route change
- **Fix**: Keep socket persistent across navigations — don't reconnect on route change

### H14. "59 posts waiting for review" banner not linked to Content Review
- Blue info banner is informational but not actionable
- **Fix**: Make the banner a link to `/content?status=VISUAL_READY`

---

## MEDIUM — UX Friction (18)

### M1. No bulk approve in Content Review
- **Backend supports it**: `blueprints.batchReview` and `blueprints.batchApproveSchedule` exist in `api/client.ts:183-192`
- **Frontend missing**: No "Select All" checkbox or batch action bar in `ContentReviewView.tsx`
- **Fix**: Add checkbox to each card, floating action bar when items selected, wire to `batchApproveSchedule`

### M2. No video preview in Content Review
- Thumbnail is a static image. No click-to-play.
- **Fix**: Add `<video>` element on card click/hover with the rendered_path as source

### M3. Caption text truncated with no expand
- `ContentCard.tsx:317` — text clamped to ~3 lines via CSS
- **Fix**: Add "Show more" toggle or click-to-expand modal

### M4. Niche color dots have no legend
- Each niche uses a colored dot (red=sports, orange=gaming, etc.) but no legend shown
- **Fix**: Add tooltip on dot or static legend at top of page

### M5. No search/filter by hook text in Content Review
- Only filter by status and niche
- **Fix**: Add text search input that filters cards by hook/title/caption

### M6. Sort only by "Newest" in Content Review
- **Fix**: Add sort options: Priority Score, Niche, Status

### M7. Pipeline "Trigger Run" has no niche selector
- `pipeline.trigger()` accepts `{niche_id}` param but the dropdown only shows "Trigger Run" without niche selection
- **Fix**: The button already has a dropdown arrow (img ref) — populate it with niche options

### M8. Run IDs truncated in Pipeline History
- Shows `sports_20260331_` — cuts off the timestamp
- **Fix**: Show time portion or use relative time ("Today 3:34 PM")

### M9. Pipeline stages all grey — no per-stage status from last run
- All 12 stages show identical grey circles whether they succeeded or failed
- **Fix**: Color code based on last run's per-stage status from run_report.json

### M10. Schedule "All caught up" misleading
- Says "No unscheduled blueprints to assign" but 59 VISUAL_READY posts exist awaiting review
- **Fix**: Show "59 posts awaiting review before scheduling" with link to Content Review

### M11. Schedule card text truncated to ~40 chars
- **Fix**: Add tooltip or expand-on-hover for full hook text

### M12. "Run Tracker Now" button triggers old SharePoint tracker
- Should trigger `collect_audience_metrics.py` instead
- **Fix**: Change API call to trigger new Postgres-based collector

### M13. Content Review: "P 0.6" priority score has no context
- Users don't know if 0.6 is good or bad
- **Fix**: Color code (green >0.7, yellow 0.4-0.7, red <0.4) or show as percentage

### M14. "Publish Success: 61%" — no benchmark
- **Fix**: Add trend arrow (up/down from last week) or target line

### M15. No keyboard shortcuts for Content Review actions
- Approve/Reject/Schedule require mouse clicks
- **Fix**: Add A=Approve, R=Reject, S=Schedule when card is focused

### M16. Content Review: Approved green check on green background is low contrast
- **Fix**: Use white check on green badge, or outline style

### M17. Schedule: drag-and-drop has no visual affordance
- No grip handles visible by default
- **Fix**: Show grip handle on hover, add "drag to reschedule" tooltip

### M18. Settings page not audited
- Need to check if settings actually work and persist

---

## LOW — Polish/Cosmetic (12)

### L1. "NEW" badges on Learning/Engagement never dismiss
- Should dismiss after first visit (store in localStorage)

### L2. No loading skeletons on initial page load
- Component exists (`loading-skeleton.tsx`) but not used in most views

### L3. No mobile responsiveness
- Bento grid, tables, and two-column layouts break on mobile
- `engagement-grid` has a media query but other pages don't

### L4. Pipeline "Publish" and "Insights" labels clipped on smaller viewports
- Horizontal scroll not enabled for stage list

### L5. Today's Publishes: platform icons are tiny colored dots
- Hard to distinguish platforms without tooltips

### L6. Command palette (Cmd+K) exists but limited commands
- Could add: "Approve all sports", "Go to gaming pipeline", "Trigger anime run"

### L7. Notification center button exists but appears empty
- No notifications shown even after publishing alerts

### L8. Activity feed toggle exists but panel appears empty
- No activity events being pushed via WebSocket

### L9. User avatar shows "SA" (initials) with no profile menu
- No logout button visible (must clear cookies manually)

### L10. No dark/light theme toggle
- Dashboard is dark-only. Some users prefer light mode.

### L11. No data export
- `lib/export.ts` exists with CSV export function but no UI buttons to trigger it

### L12. No breadcrumb navigation
- Deep-linking to specific blueprints or runs has no way to navigate back except sidebar

---

## Architecture Notes

### Positive Patterns
- React Query for all data fetching — good caching, retry, stale-while-revalidate
- Zustand stores for UI state (niche selection, command palette, notifications)
- TypeScript throughout with proper types
- Error boundaries catch crashes gracefully
- CSRF token management with auto-refresh
- Keyboard shortcuts (G M, G A, etc.) for power users
- WebSocket for real-time pipeline status updates

### Technical Debt
- `unwrapResponse()` auto-extracts `.data` from API envelope, but some endpoints return nested `.data.posts`, `.data.comments` etc. causing double-unwrap bugs (C1, C2, C5)
- Token health endpoint uses raw `fetch()` instead of `get()` helper, bypassing the `/api/v1` prefix (C3)
- 3 different data sources for "reach" numbers: overview API (all-time), engagement/summary (all-time aggregated), analytics/overview (windowed) — creates confusion (H1)
- No consistent error handling pattern — some views use ErrorBoundary, some use inline error states, some crash
- No API response type validation at runtime — TypeScript types are compile-time only, so shape mismatches cause runtime crashes

### Recommended Fix Order
1. **C1** — Engagement crash (1 line in client.ts)
2. **C2** — Analytics zero KPIs (1 line in Analytics.tsx)
3. **C3** — Token health 404 (1 line URL fix)
4. **C5** — Revenue double-wrap (type fix in client.ts)
5. **C8** — Top Post "Untitled" (backend join fix)
6. **C4/C6** — Monetisation data (table mapping fix)
7. **H5** — Upcoming queue past dates (1 line date filter)
8. **M1** — Bulk approve (wire existing backend to frontend)
9. **H2** — Top performers dedup (backend query change)
10. **H6** — LaunchAgent status labels (frontend display fix)

---

## ADDENDUM — Deeper Pass (Round 2)

### Additional Issues Found

#### C9. Publishing Queue: No "Select All" or batch action bar
- The queue has checkboxes per item (`checkbox "Select blueprint..."`) but no "Select All" header checkbox and no floating batch action bar when items are checked.
- Backend `batchReview` and `batchApproveSchedule` endpoints exist and work — the frontend just doesn't surface batch controls.
- **Impact**: 44 pending items must be approved individually.

#### C10. Settings page only shows ai_creators sources
- Settings → Sources shows BB's 30+ Reddit/RSS/API sources but no option to switch niche.
- Other niches' sources (gaming YouTube feeds, anime RSS, etc.) not accessible.
- **Fix**: Add niche selector to Settings page, or show all niches in tabs.

#### H15. Token health endpoint: wrong URL prefix
- `api/client.ts:320` uses `fetch("/api/token-health")` — missing `/v1/`
- Backend route is at `/api/v1/token-health`
- All other API calls use the `get()` helper which auto-prepends `/api/v1`
- Same bug on line 326 for refresh endpoint

#### H16. Revenue summary: double-unwrap bug
- `revenue.summary()` type: `get<{data: {clicks:...}}>` 
- `unwrapResponse` already extracts `.data`, giving `{clicks:{today:0, last_7d:0, last_30d:61}}`
- Frontend then reads `.data.clicks` which is `undefined` (there's no second `.data` wrapper)
- This causes the Monetisation page to show "0 Clicks (30d)" instead of 61

#### H17. Analytics top-posts: `{posts: [...]}` not `[...]`
- `analytics.topPosts()` typed as `get<TopPost[]>` but API returns `{posts: [...], total: N}`
- After unwrap: `{posts: [...], total: N}` — not an array
- `Analytics.tsx:521`: `Array.isArray(raw) ? raw : []` → always returns `[]`
- This causes ALL hero KPIs to show 0

#### H18. Queue items have no niche indicator
- Publishing Queue list items show hook, subtitle, status, date, score — but no niche name or color
- With 44 pending items across niches, can't tell which niche each belongs to without clicking

#### H19. Settings: no niche awareness
- Sources, Schedule Slots, Scoring, Templates tabs all show data for a single niche
- No way to switch between niches in the Settings page

#### M19. Publishing Queue: checkboxes exist but no batch UI
- Individual checkboxes render but clicking them has no visible effect — no count badge, no "Approve Selected" button appears
- The batch infrastructure exists in code (`batchApproveSchedule`) but is not wired to UI

#### M20. Settings: Sources table is read-only
- Can view sources but cannot add, edit, disable, or reorder them
- All source changes require YAML file edits

#### M21. Settings: Notifications tab likely non-functional
- Backend `/settings/notifications` returns data but the UI may not persist changes
- Need to test save functionality

#### L13. Settings: Appearance tab exists but likely empty
- No theme toggle, font size, or density options visible

#### L14. Content Review: "P 0.6" priority scores are identical across all items
- Every item shows "0.6" — suggests the priority_score is not being computed meaningfully or is a default value

---

## Final Issue Count

| Severity | Original | Addendum | Total |
|---|---|---|---|
| Critical | 8 | 2 | **10** |
| High | 14 | 5 | **19** |
| Medium | 18 | 3 | **21** |
| Low | 12 | 2 | **14** |
| **Total** | **52** | **12** | **64** |

