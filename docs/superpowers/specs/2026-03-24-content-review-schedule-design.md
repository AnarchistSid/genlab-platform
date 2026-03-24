# Content Review + Schedule Workflow Upgrade

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Streamline the review-to-publish workflow so approved posts flow smoothly into the schedule, content previews are richer, and inline editing is possible — reducing the 4-step cross-page process to a single action.

**Date:** 2026-03-24

---

## 1. Problem Statement

### 1.1 Workflow Disconnect (Critical)

The review → schedule flow requires 4 steps across 2 pages:
1. Review post on Content Review page → click Approve
2. Navigate to Schedule page
3. Find the post in the unscheduled pool at bottom
4. Drag to a slot or click "Assign to slot"

**Fix:** Add "Approve & Auto-Schedule" button that approves AND schedules to the next available slot in one action. Keep plain "Approve" for manual scheduling.

### 1.2 Review Flow Issues

| Issue | Location | Severity |
|-------|----------|----------|
| **No inline editing** — can't fix a weak hook without going to a separate editor | FocusOverlay, ContentCard | High |
| **FocusOverlay shows only 4 platforms** — hardcoded `["instagram", "youtube", "twitter", "facebook"]`, missing Threads | FocusOverlay.tsx:55 | Medium |
| **ContentCard thumbnails don't autoplay** — videos show static frame with play icon, no hover preview | ContentCard.tsx | Medium |
| **No batch approve** — must click each post individually | ContentReviewView.tsx | Medium |
| **Content Review and Publishing Queue overlap** — both show VISUAL_READY posts, confusing which to use | Navigation | Medium |
| **FocusOverlay uses `as any` cast** — `const f = bp as any` for platform caption access | FocusOverlay.tsx:29 | Low |
| **Duplicate `resolveThumb()`** — defined in both ContentCard.tsx:14 and FocusOverlay.tsx:20 | Both files | Low |
| **Duplicate `fmtNumber()`** — ContentCard.tsx:18 duplicates `formatCompact` from lib/format | ContentCard.tsx | Low |
| **NICHE_OPTIONS hardcoded** — doesn't use niche registry | ContentReviewView.tsx:31-37 | Low |

### 1.3 Schedule Issues

| Issue | Location | Severity |
|-------|----------|----------|
| **No auto-schedule capability** — all scheduling is manual (drag or dialog) | schedule-board.tsx | High |
| **NICHE_ROWS hardcoded** — doesn't use niche registry | schedule-board.tsx:69-75 | Low |
| **Schedule preview only shows 4 platforms** — missing Threads in platform tabs | schedule-preview.tsx | Medium |
| **No schedule conflict detection** — can schedule 2 posts for same niche on same day | schedule-board.tsx | Medium |
| **Unscheduled pool has no niche filter** — shows all niches mixed together | schedule-board.tsx:384-435 | Low |

## 2. Scope

**In scope:**
- "Approve & Auto-Schedule" action (backend + frontend)
- Inline hook editing in FocusOverlay
- Batch approve in Content Review
- Add Threads to platform previews (FocusOverlay + SchedulePreview)
- Fix hardcoded constants (NICHE_OPTIONS, NICHE_ROWS, PLATFORMS)
- Deduplicate resolveThumb + fmtNumber
- Remove `as any` cast in FocusOverlay
- Video hover preview on ContentCard thumbnails
- Schedule conflict warning

**Out of scope:**
- Merging Content Review and Publishing Queue into one view (design decision for later)
- Full inline editor for all fields (just hook text for now)
- Drag-and-drop between Content Review and Schedule (complex, low ROI)

## 3. Architecture

### 3.1 Backend: Auto-Schedule Endpoint

New endpoint: `POST /api/v1/blueprints/:id/approve-and-schedule`

Logic:
1. Set `action_taken = "approved"`, `reviewed_at = now()`
2. Find the next available slot for this blueprint's `niche_id`:
   - Query blueprints with `scheduled_for` in the next 7 days for this niche
   - Find the first day (starting tomorrow) with no scheduled post for this niche
   - Set `scheduled_for` to that day at the niche's standard publish time (from schedule config)
3. Return the updated blueprint with `scheduled_for` set

```python
@bp.route("/<blueprint_id>/approve-and-schedule", methods=["POST"])
def approve_and_schedule(blueprint_id):
    # 1. Approve the blueprint
    # 2. Find next available slot for this niche
    # 3. Set scheduled_for
    # 4. Return updated blueprint
```

Standard publish times per niche (from CLAUDE.md):
- ai_creators: 06:30 UTC (12:00 IST)
- gaming: 06:30 UTC
- sports: 06:30 UTC
- movies: 06:30 UTC
- anime: 06:30 UTC

### 3.2 Backend: Inline Hook Edit Endpoint

Reuse existing: `PATCH /api/v1/blueprints/:id/content` — already supports `{ hook_text: "..." }`.

### 3.3 Backend: Batch Approve Endpoint

Reuse existing: `POST /api/v1/blueprints/batch-review` — already supports `{ ids: [...], action: "approved" }`.

Add batch auto-schedule variant: `POST /api/v1/blueprints/batch-approve-schedule` — approves all and auto-schedules each to its next available slot.

### 3.4 Frontend Changes

#### 3.4.1 Content Review — "Approve & Schedule" Button

In `ContentCard.tsx`, add a second action button next to "Approve":
```
[✓ Approve]  [✓📅 Approve & Schedule]  [✗ Reject]
```

The "Approve & Schedule" button calls the new endpoint. On success, shows a toast: "Scheduled for Mar 26 (12:00 IST)".

#### 3.4.2 FocusOverlay — Inline Hook Edit + Auto-Schedule

Add an "Edit" icon next to the hook text. Clicking it turns the hook into an inline `<textarea>`. On blur or Enter, it calls `PATCH /blueprints/:id/content` to save.

Add "Approve & Schedule" button alongside existing Approve/Reject:
```
[✓ Approve]  [📅 Approve & Schedule]  [✗ Reject]
```

Keyboard shortcut: `S` for approve-and-schedule (alongside existing `A` for approve, `R` for reject).

#### 3.4.3 FocusOverlay — Fix Platforms

Replace hardcoded `const PLATFORMS = ["instagram", "youtube", "twitter", "facebook"]` with:
```tsx
import { PLATFORM_IDS } from "@/lib/platforms";
const PLATFORMS = PLATFORM_IDS; // includes threads
```

Also fix `getPlatformCaption()` to handle `threads` platform.

#### 3.4.4 Content Review — Batch Approve

Add a selection mode:
- Checkbox on each ContentCard
- When any are selected, show a floating action bar: "X selected: [Approve All] [Approve & Schedule All] [Reject All]"
- Uses existing `useBatchReview` hook for approve/reject
- New `useBatchApproveSchedule` hook for approve+schedule

#### 3.4.5 ContentCard — Video Hover Preview

Replace static thumbnail with hover-to-play:
```tsx
<video
  src={thumb}
  muted
  loop
  playsInline
  preload="metadata"
  className="..."
  onMouseEnter={(e) => e.currentTarget.play()}
  onMouseLeave={(e) => { e.currentTarget.pause(); e.currentTarget.currentTime = 0; }}
/>
```

#### 3.4.6 Schedule Board — Conflict Warning

When scheduling a post (drag or dialog), check if the target niche already has a post on that day. If so, show a confirmation: "CriticalRush already has a post on Mar 26. Schedule anyway?"

#### 3.4.7 Deduplicate Utilities

- Delete `resolveThumb()` from both ContentCard.tsx and FocusOverlay.tsx — use `getThumbnailInfo()` from `@/lib/format` (already exists and is used by schedule components)
- Delete `fmtNumber()` from ContentCard.tsx — use `formatCompact` from `@/lib/format`
- Replace `NICHE_OPTIONS` in ContentReviewView.tsx with niche registry: `getAllNiches().map(...)`
- Replace `NICHE_ROWS` in schedule-board.tsx with niche registry
- Fix `as any` in FocusOverlay — properly type the blueprint fields

#### 3.4.8 Schedule Preview — Add Threads

Add "Threads" tab to the platform tabs in `schedule-preview.tsx` alongside IG, YT, FB, X.

## 4. File Changes Manifest

### 4.1 Backend (2 new endpoints in existing files)

| File | Change |
|------|--------|
| `server/api/blueprints.py` | Add `POST /:id/approve-and-schedule` endpoint |
| `server/api/blueprints.py` | Add `POST /batch-approve-schedule` endpoint |

### 4.2 Frontend (10 modified, 2 new)

| File | Change |
|------|--------|
| `api/client.ts` | Add `blueprints.approveAndSchedule(id)` and `blueprints.batchApproveSchedule(ids)` |
| `api/types.ts` | No changes needed (Blueprint type already has `scheduled_for`) |
| `hooks/use-blueprints.ts` | Add `useApproveAndSchedule()` and `useBatchApproveSchedule()` hooks |
| `views/content/ContentReviewView.tsx` | Add batch selection mode, floating action bar, replace NICHE_OPTIONS with registry |
| `views/content/ContentCard.tsx` | Add "Approve & Schedule" button, video hover preview, delete local `resolveThumb`/`fmtNumber`, add selection checkbox |
| `views/content/FocusOverlay.tsx` | Add inline hook edit, "Approve & Schedule" button + `S` shortcut, fix PLATFORMS to include Threads, fix `as any`, delete local `resolveThumb`, add `getPlatformCaption` for threads |
| `components/schedule/schedule-board.tsx` | Replace NICHE_ROWS with registry, add conflict detection |
| `components/schedule/schedule-preview.tsx` | Add Threads tab |
| `components/blueprints/platform-preview.tsx` | Add Threads support if not already present |

## 5. Quality Gates

- "Approve & Schedule" works end-to-end: button → API → blueprint gets scheduled_for → appears on schedule board
- Inline hook edit saves correctly via PATCH API
- Batch approve works for 2+ selected posts
- FocusOverlay shows all 5 platforms (IG, YT, FB, X, Threads)
- No hardcoded NICHE_OPTIONS, NICHE_ROWS, or PLATFORMS arrays
- No duplicate `resolveThumb` or `fmtNumber` functions
- No `as any` casts in FocusOverlay
- Video thumbnails play on hover in ContentCard
- Schedule conflict warning shown when double-booking a niche
- `npm run build` passes with zero errors
- Keyboard shortcuts work: A (approve), S (approve+schedule), R (reject), arrows (navigate), Esc (close)

## 6. Migration Order

1. Backend: Add approve-and-schedule endpoint
2. Backend: Add batch-approve-schedule endpoint
3. Frontend: Add API client methods + hooks
4. Frontend: Fix FocusOverlay (platforms, inline edit, auto-schedule, remove `as any`)
5. Frontend: Fix ContentCard (auto-schedule button, hover video, dedup utils, selection checkbox)
6. Frontend: Fix ContentReviewView (batch selection, floating action bar, registry constants)
7. Frontend: Fix Schedule Board (registry constants, conflict detection)
8. Frontend: Fix Schedule Preview (add Threads tab)
9. Build + visual verification
