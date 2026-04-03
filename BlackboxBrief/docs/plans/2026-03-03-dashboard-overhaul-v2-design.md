# Dashboard Overhaul v2 — Design Document

**Date:** 2026-03-03
**Status:** Proposed
**Scope:** 18 targeted fixes across 3 phases

---

## Context

The 46-fix codebase upgrade (committed P0–P3) hardened the backend pipeline but did not touch the dashboard. A separate 40-fix dashboard plan was drafted but **never implemented**. This design supersedes that plan with 18 verified fixes — every issue confirmed via live browser testing + code audit.

**Evidence method:** Chrome browser verification of localhost:5151 across Pipeline, Analytics, and Content Board views, cross-referenced with source code grep/read.

---

## Phase 1: Crashers + Security (6 fixes)

### 1.1 [P0] `kpi-card.tsx` — React Rules of Hooks violation

**File:** `dashboard/src/components/charts/kpi-card.tsx`
**Bug:** `useId()` on line 46 is called AFTER a conditional early return (lines 30–43). React hooks must be called in the same order every render. When `loading` transitions true→false, React sees a different hook count and crashes the entire component tree.
**Fix:** Move `const gradientId = useId()` above the `if (loading)` guard.

### 1.2 [P1] Backend integer parsing crashes

**Files:** `execution/api/pipeline.py:74-75`, `execution/api/stories.py:19-20`, `execution/api/blueprints.py:140-141`
**Bug:** `int(request.args.get(...))` with no try/except. Non-numeric query params (e.g., `?page=abc`) crash the endpoint with an unhandled ValueError.
**Fix:** Wrap in try/except, return 400 with error message on invalid input.

### 1.3 [P1] Password comparison not constant-time

**File:** `execution/review_server.py:209`
**Bug:** `password == _AUTH_PASS` uses Python string equality, which short-circuits on first mismatched character. Vulnerable to timing attacks. Note: `hmac.compare_digest` is already used for CSRF (line 330) but not for password.
**Fix:** Replace with `hmac.compare_digest(password.encode(), _AUTH_PASS.encode())`.

### 1.4 [P2] CSV formula injection

**File:** `dashboard/src/lib/export.ts`
**Bug:** `escapeCSVField` doesn't sanitize leading `=`, `+`, `-`, `@` characters. Opening exported CSV in Excel could execute formulas from untrusted content.
**Fix:** Prefix fields starting with these characters with a single quote (`'`).

### 1.5 [P2] Login attempts dict unbounded

**File:** `execution/review_server.py:50`
**Bug:** `_login_attempts` dict grows forever in memory. Each unique IP adds an entry that's never cleaned up.
**Fix:** Add TTL-based cleanup — purge entries older than 1 hour on each login attempt, or use a bounded LRU dict.

### 1.6 [P1] `pipeline.tsx` hasError uses AND instead of OR

**File:** `dashboard/src/views/pipeline.tsx:162-166`
**Bug:** `hasError` requires ALL four queries to fail (`&&`). If only one query fails, the error state is never shown — the page silently shows stale/missing data.
**Fix:** Change `&&` to `||` so error shows if ANY query fails.

---

## Phase 2: Previews + Analytics (7 fixes)

### 2.1 [P1] YouTube Short/Video misclassification

**File:** `dashboard/src/components/blueprints/platform-preview.tsx:129`
**Bug:** `const isLandscape = Boolean(blueprint.landscape_video_url)` — determines Short vs Video by checking if a landscape URL exists. Since the pipeline auto-renders landscape versions for Facebook, `landscape_video_url` is often present even for short videos. A 79-second video incorrectly shows as "YouTube Video" (16:9) instead of "YouTube Short" (9:16).
**Fix:** Use video duration: `≤180s → Short (portrait 9:16)`, `>180s → Video (landscape 16:9)`. Duration is available from the video metadata or can be extracted from the blueprint's content_meta.

### 2.2 [P1] Facebook preview forced landscape

**File:** `dashboard/src/components/blueprints/platform-preview.tsx`
**Bug:** Facebook preview card uses a fixed landscape (16:9) aspect ratio. Should use the source video's original aspect ratio (portrait/landscape/square as-is).
**Fix:** Detect source aspect ratio from video metadata or visual_paths and render accordingly. Facebook supports all aspect ratios natively.

### 2.3 [P2] Analytics cost always $0

**File:** `dashboard/src/views/analytics.tsx:49`
**Bug:** `usePipelineAnalytics()` called without date range params. Backend returns all runs including test/manual runs with $0 cost, diluting the averages.
**Fix:** Pass `params` (date range) to `usePipelineAnalytics(params)`, matching how `useEngagement(params)` already works on line 52.

### 2.4 [P2] Facebook engagement rate Infinity/NaN

**File:** `dashboard/src/components/charts/engagement-breakdown.tsx:82`
**Bug:** `(p.avg_engagement_rate * 100).toFixed(2)` — when Facebook returns 0 impressions (Standard Access limitation), engagement rate computes as Infinity or NaN. Displayed as absurd percentages like 88.24%.
**Fix:** Guard with `isFinite()` check. Display "N/A" or 0 when data is unreliable. Also guard `avg_viral_score` on line 88 similarly.

### 2.5 [P2] Pipeline runs show test/manual runs

**File:** `dashboard/src/views/analytics.tsx` + backend `execution/api/pipeline.py`
**Bug:** Pipeline run list includes `test_publish`, `rerender_202`, etc. — non-daily-intel runs with $0 cost and 0s duration that skew charts.
**Fix:** Add `run_type` filter to backend endpoint. Frontend should default to `run_type=daily_intel`.

### 2.6 [P2] Heatmap missing date range params

**File:** `dashboard/src/views/analytics.tsx:50`
**Bug:** `useHeatmap()` called without params, same as the pipeline analytics issue (2.3).
**Fix:** Pass `params` to `useHeatmap(params)`.

### 2.7 [P3] VISUAL_READY empty state messages

**File:** `dashboard/src/components/blueprints/platform-preview.tsx`
**Bug:** VISUAL_READY posts show "Not adapted for X / Twitter yet" for Twitter and generic caption for Facebook. This is expected (adaptation runs during finalize), but the message is unclear.
**Fix:** Change to "Platform adaptation runs before publishing" with a muted info style, not an error style.

---

## Phase 3: Performance + Reliability (5 fixes)

### 3.1 [P2] Global refetchInterval too aggressive

**File:** `dashboard/src/App.tsx:25`
**Bug:** `refetchInterval: 8_000` on ALL TanStack Query queries. Analytics, settings, export — everything polls every 8 seconds. Wastes bandwidth and backend resources.
**Fix:** Remove global refetchInterval. Set per-query: Pipeline/Content: 15s, Analytics: 60s, Settings: none.

### 3.2 [P2] Socket.io gives up permanently

**File:** `dashboard/src/api/socket.ts`
**Bug:** `reconnectionAttempts: 10` with no recovery. After 10 failures (~5 min with backoff), real-time updates stop forever until page refresh.
**Fix:** Set `reconnectionAttempts: Infinity` with a max delay cap of 60s. Add a visible "Reconnecting..." banner when disconnected.

### 3.3 [P2] Keyboard listener re-registers every render

**File:** `dashboard/src/hooks/use-keyboard.ts:147`
**Bug:** `reviewMutation` in useEffect dependency array creates a new reference each render (TanStack Query returns new mutation object per render). This tears down and re-adds the keyboard event listener on every render.
**Fix:** Remove `reviewMutation` from the deps array. Use `reviewMutation.mutate` (stable ref) or wrap the mutation call in a ref.

### 3.4 [P2] Focus mode stale index

**File:** `dashboard/src/hooks/use-focus-mode.ts:33`
**Bug:** `currentIndex` state not reset when `items` array changes (e.g., after refetch with different results). The index can point past the end of the new array.
**Fix:** Add a useEffect that clamps `currentIndex` to `Math.min(currentIndex, items.length - 1)` when `items` changes. Reset to 0 if items is empty.

### 3.5 [P2] Blueprint detail stale video error

**File:** `dashboard/src/components/blueprints/blueprint-detail.tsx:56`
**Bug:** `videoError` state not reset when `blueprintId` changes. If one blueprint's video fails to load, switching to another blueprint still shows the error state.
**Fix:** Add `useEffect(() => setVideoError(false), [blueprintId])`.

---

## Out of Scope

These were in the old 40-fix plan but are NOT included here:

- **Facebook Advanced Access** — Requires Meta Business Verification (organizational process, not code fix). Facebook will continue showing 0 impressions/reach until Advanced Access is granted.
- **CSRF token race condition** — Theoretical issue with concurrent mutations. Current TTL + retry logic handles it adequately.
- **Backend cost field population** — The pipeline scripts handle cost tracking. Dashboard just needs to filter out non-daily runs (fix 2.5).
- **SocketIO disconnect on unmount** — The singleton pattern is intentional for cross-page real-time updates.

---

## Implementation Order

| Phase | Est. Files | Risk | Depends On |
|-------|-----------|------|------------|
| Phase 1 (Crashers + Security) | 6 files | Low — isolated fixes | Nothing |
| Phase 2 (Previews + Analytics) | 4 files | Medium — preview logic changes | Phase 1 |
| Phase 3 (Performance) | 5 files | Low — behavioral improvements | Phase 1 |

**Phase 2 and Phase 3 can run in parallel after Phase 1.**

---

## Verification Plan

Each fix must be verified:
- **Phase 1:** TypeScript build + existing tests + manual crash test for 1.1
- **Phase 2:** Browser verification of each preview tab + analytics charts
- **Phase 3:** Network tab inspection for polling frequency + socket reconnect test

Final gate: `npm run build` clean + `pytest` green + visual spot-check of all 5 dashboard pages.
