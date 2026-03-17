# Dashboard ARCHIVED Status Handling — Design Spec

**Date:** 2026-03-15
**Status:** Draft
**Scope:** Dashboard backend (Flask) + frontend (React) changes to surface auto-archived blueprints

---

## Problem

The auto-archiver runs before human review, causing items to jump from DRAFTED → ARCHIVED without passing through VISUAL_READY. The dashboard has three blind spots:

1. **Blueprints list** default filter in `blueprints.py` `list_blueprints()` shows `INTEL_READY, DRAFTED, VISUAL_READY, PUBLISHED` — ARCHIVED is excluded entirely.
2. **Review queue** `review_queue()` only fetches `VISUAL_READY` with blank `action_taken`. If auto-archiver archives items before review, the queue is always empty.
3. **Overview** `_build_overview()` only fetches `VISUAL_READY + PUBLISHED` for stats. Auto-archived items are invisible to Mission Control.

**Result:** Operators see an empty review queue with no explanation, no visibility into what was archived or why, and no way to tune thresholds.

---

## Solution

Option C: Overview card with auto-archive summary stats **+** Archived tab in blueprints list **+** review queue empty state.

---

## Backend Changes

### 1. `dashboard/server/api/blueprints.py` — Expand action_taken validation

**Current:**
```python
ALLOWED_ACTIONS = {"approved", "rejected", "revised", "skipped"}
if action_taken:
    if action_taken not in ALLOWED_ACTIONS:
        return api_error(error=f"Invalid action_taken: {action_taken}")
```

**Change:** Use a closed allowlist that includes known auto-archive reasons. This prevents OData injection (a prefix check like `startswith("auto_archived_")` would allow payloads like `auto_archived_foo','1'='1`):

```python
ALLOWED_ACTIONS = {
    "approved", "rejected", "revised", "skipped",
    "auto_archived_template_hook", "auto_archived_no_video",
    "auto_archived_low_score", "auto_archived_duplicate",
    "auto_archived_stale", "auto_archived_no_clip",
}
```

No change to the default status filter — ARCHIVED remains opt-in via `?status=ARCHIVED`. This prevents the main list from being flooded.

### 2. `dashboard/server/api/overview.py` — Add auto-archive stats via separate query

**Problem with adding ARCHIVED to the main query:** SharePoint Lists have a 5,000 item list view threshold. ARCHIVED records accumulate over time and would eventually break the overview query. Instead, use a **separate bounded query** for today's archives only.

**Current:**
```python
all_records = client.blueprints.all(
    formula="OR({status}='VISUAL_READY',{status}='PUBLISHED')"
)
```

**Change:** Keep the main query unchanged. Add a second query for today's archives:

```python
# Existing query — unchanged
all_records = client.blueprints.all(
    formula="OR({status}='VISUAL_READY',{status}='PUBLISHED')"
)

# NEW: Separate bounded query for today's archived records only
archived_today_records = []
try:
    archived_records = client.blueprints.all(
        formula=f"AND({{status}}='ARCHIVED',{{reviewed_at}}>='{today_start_utc.isoformat()}')"
    )
    archived_today_records = archived_records
except Exception as e:
    logger.warning("Failed to fetch archived records for overview: %s", e)
```

**New stats** added to the `"global"` section of the response:

```python
archive_by_reason = {}
for r in archived_today_records:
    reason = (r.get("fields", {}).get("action_taken") or "unknown").strip()
    archive_by_reason[reason] = archive_by_reason.get(reason, 0) + 1

total_archived_today = len(archived_today_records)
# Note: pass_rate is approximate — uses reviewed_at for archives, published_at for publishes.
# These are different timestamps but from the same day's pipeline output.
pass_rate = (
    round(total_published / (total_published + total_archived_today), 2)
    if (total_published + total_archived_today) > 0
    else None
)
```

Added to the response `"global"` dict:
```python
"auto_archive_today": {
    "total": total_archived_today,
    "by_reason": archive_by_reason,
    "pass_rate": pass_rate,
}
```

**Per-niche archived counts** also added to `niches_data`:
```python
niche_archived: dict[str, int] = {}
for r in archived_today_records:
    n = _bp_niche_overview(r.get("fields", {}))
    niche_archived[n] = niche_archived.get(n, 0) + 1
```
Then in the per-niche loop: `"archived_today": niche_archived.get(niche_id, 0)`.

### 3. Review queue — no backend change needed

The existing `review_queue()` endpoint already returns a `"fallback": true` flag when no VISUAL_READY items exist. The empty state is a frontend-only fix.

---

## Frontend Changes

### 4. `dashboard/frontend/src/views/blueprints.tsx` — Add ARCHIVED to status filters

**Current** (lines 35-43):
```typescript
const STATUS_FILTERS = [
  { value: "INTEL_READY", label: "Intel Ready" },
  // ... existing values ...
  { value: "ERROR", label: "Error" },
];
```

**Change:** Add after ERROR:
```typescript
  { value: "ARCHIVED", label: "Archived" },
```

**Also required:** `blueprint-card.tsx` does NOT currently render `action_taken`. Add a small badge below the status pill when `action_taken` starts with `auto_archived_`:

```tsx
{item.action_taken?.startsWith("auto_archived_") && (
  <Badge variant="outline" className="text-xs text-muted-foreground">
    {item.action_taken.replace("auto_archived_", "").replace(/_/g, " ")}
  </Badge>
)}
```

This makes the archive reason visible at the card level without requiring click-through to details.

### 5. `dashboard/frontend/src/views/mission-control/MissionControl.tsx` — Auto-archive card

**New component:** `AutoArchiveCard` — a bento card in the Mission Control grid.

**Data source:** `data.global.auto_archive_today` from the `useCrossNicheOverview` hook.

**Layout:**
```
┌─────────────────────────────────┐
│ Auto-Archived Today        [12] │
│ Pass rate: 45%                  │
│                                 │
│ template_hook ████████░░ 6      │
│ no_video      ████░░░░░░ 3      │
│ low_score     ██░░░░░░░░ 2      │
│ duplicate     █░░░░░░░░░ 1      │
│                                 │
│ [View Archived →]               │
└─────────────────────────────────┘
```

- Total count in the card header (big number, same style as pending/published counts)
- Pass rate as a subtitle
- Breakdown bars: each `by_reason` entry as a label + count. Simple inline bars (`div` with percentage width and accent background).
- When `total = 0`: show "No items archived today" subtitle, no bars.
- When all `by_reason` entries are `"unknown"`: show a diagnostic note "Auto-archiver is not setting action_taken reasons."
- "View Archived" link navigates to `/blueprints?status=ARCHIVED`

**Grid placement:** Add `area-archive` to the CSS grid in `mission-control.css`. Insert between `perf/health` row and `monetisation` row:
```css
grid-template-areas:
  "queue          pipeline"
  "niches         niches"
  "schedule       schedule"
  "perf           health"
  "archive        archive"
  "monetisation   monetisation";
```

Also add a corresponding `SkeletonCard` entry in the `LoadingSkeleton` component.

**Type extension:** Add to `CrossNicheOverviewResponse` in `dashboard/frontend/src/api/client.ts` (line 272):

```typescript
global: {
  // existing fields...
  auto_archive_today: {
    total: number;
    by_reason: Record<string, number>;
    pass_rate: number | null;
  };
};
```

And per-niche (in the `niches` array type):
```typescript
  archived_today: number;
```

### 6. `dashboard/frontend/src/components/review/focus-mode.tsx` — Empty state

**File:** `focus-mode.tsx` (NOT `focus-review.tsx` — that's a one-line wrapper).

**Modify the existing `FocusEmpty` component** (lines 116-143). Currently shows a generic "No items to review" with "Run the pipeline or check back later."

**Change to:**
```tsx
function FocusEmpty() {
  const navigate = useNavigate();
  return (
    <div
      className="flex h-screen w-screen items-center justify-center"
      style={{ background: "var(--bg-base)" }}
    >
      <div className="flex flex-col items-center gap-4 text-center">
        <CheckCircle2 className="size-12" style={{ color: "var(--text-disabled)" }} />
        <h2 className="text-lg font-semibold" style={{ color: "var(--text-primary)" }}>
          No items pending review
        </h2>
        <p className="text-sm max-w-sm" style={{ color: "var(--text-muted)" }}>
          All items were auto-processed. Check the Archived tab for details on
          what was filtered and why.
        </p>
        <Button
          variant="outline"
          className="mt-2"
          style={{ borderColor: "var(--border)" }}
          onClick={() => navigate("/blueprints?status=ARCHIVED")}
        >
          View Archived
        </Button>
      </div>
    </div>
  );
}
```

Import `CheckCircle2` from `lucide-react` (replace existing `ImageIcon` import if unused elsewhere).

---

## Files Modified

| File | Change |
|------|--------|
| `dashboard/server/api/blueprints.py` | Expand `ALLOWED_ACTIONS` with known auto_archived values |
| `dashboard/server/api/overview.py` | Separate bounded query for today's ARCHIVED records + stats |
| `dashboard/frontend/src/views/blueprints.tsx` | Add ARCHIVED to STATUS_FILTERS |
| `dashboard/frontend/src/components/blueprints/blueprint-card.tsx` | Render `action_taken` badge for auto-archived items |
| `dashboard/frontend/src/views/mission-control/MissionControl.tsx` | New AutoArchiveCard component + SkeletonCard |
| `dashboard/frontend/src/views/mission-control/mission-control.css` | Add area-archive grid area |
| `dashboard/frontend/src/api/client.ts` | Extend `CrossNicheOverviewResponse` type |
| `dashboard/frontend/src/components/review/focus-mode.tsx` | Update FocusEmpty with archive-aware copy + "View Archived" button |

## Files NOT Modified

- `dashboard/server/review_server.py` — Delegates to `blueprints.py`
- `dashboard/frontend/src/hooks/useCrossNicheOverview.ts` — Already fetches overview; type change is in `client.ts`

---

## Testing

### Backend
- `test_list_blueprints_archived_status` — Verify `?status=ARCHIVED` returns ARCHIVED records
- `test_action_taken_auto_archived_accepted` — Verify `?action_taken=auto_archived_no_video` passes validation
- `test_action_taken_unknown_auto_archived_rejected` — Verify `?action_taken=auto_archived_evil_injection` is rejected (not in closed set)
- `test_action_taken_invalid_rejected` — Verify `?action_taken=malicious_value` is rejected
- `test_overview_includes_auto_archive_stats` — Verify overview response has `auto_archive_today` with `total`, `by_reason`, `pass_rate`
- `test_overview_pass_rate_none_when_zero_items` — Verify `pass_rate` is `null` when no items
- `test_overview_archived_excludes_yesterday` — Verify ARCHIVED records from before today are excluded from stats
- `test_overview_per_niche_archived_count` — Verify per-niche `archived_today` is populated

### Frontend
- `test_auto_archive_card_renders_zero` — AutoArchiveCard with `total: 0` shows "No items archived today"
- `test_auto_archive_card_renders_breakdown` — AutoArchiveCard with multiple `by_reason` entries renders bars
- `test_auto_archive_card_unknown_diagnostic` — When all reasons are "unknown", diagnostic note appears
- Manual QA: verify Archived tab shows items, Mission Control card renders, review queue empty state appears

---

## Edge Cases

1. **No ARCHIVED items today**: `auto_archive_today.total = 0`, `by_reason = {}`, `pass_rate = null`. AutoArchiveCard shows "0" with "No items archived today" subtitle. Not an error state.
2. **ARCHIVED items without `reviewed_at`**: Excluded from today's count by the OData date filter.
3. **Unknown `action_taken` values**: `by_reason` includes whatever string is in `action_taken`. When all entries are `"unknown"`, the card shows a diagnostic note.
4. **SharePoint 5K threshold**: Avoided by using a separate bounded query with a date filter (`reviewed_at >= today`). The main overview query is unchanged.
5. **`pass_rate` timestamp fields**: Published items use `published_at`, archived items use `reviewed_at`. These are different fields but both represent today's pipeline output. The pass rate is an approximation, not a true cohort metric. This is documented.
