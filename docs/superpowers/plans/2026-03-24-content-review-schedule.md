# Content Review + Schedule Workflow — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Streamline review-to-publish workflow — "Approve & Auto-Schedule" in one click, inline hook editing, batch approve, video hover previews, Threads support, schedule conflict detection.

**Architecture:** Backend-first — add 2 new endpoints, then wire frontend components. Each task is independently buildable.

**Tech Stack:** Backend: Flask, psycopg3, PostgreSQL. Frontend: React 19, TanStack Query 5, Tailwind CSS v4, TypeScript 5.9, dnd-kit.

**Spec:** `docs/superpowers/specs/2026-03-24-content-review-schedule-design.md`

**Backend dir:** `/Users/anarchistsid/GenLab/dashboard/server`

**Frontend dir:** `/Users/anarchistsid/GenLab/dashboard/frontend`

**Build:** `cd /Users/anarchistsid/GenLab/dashboard/frontend && npm run build`

---

## Task 1: Backend — Approve-and-schedule endpoints

Add two new endpoints to `server/api/blueprints.py`:
1. `POST /api/v1/blueprints/<id>/approve-and-schedule` — approves a single blueprint and auto-schedules to next available slot
2. `POST /api/v1/blueprints/batch-approve-schedule` — approves and auto-schedules multiple blueprints

**Files:**
- Modify: `server/api/blueprints.py`

- [ ] **Step 1:** Read `server/api/blueprints.py` fully to understand existing review/approve patterns and how `scheduled_for` is set.

- [ ] **Step 2:** Add a helper function `_find_next_available_slot(niche_id)` that:
  - Queries blueprints with `scheduled_for` in the next 7 days for this niche_id
  - Finds the first day (starting from tomorrow) that has NO scheduled blueprint for this niche
  - Returns a datetime at 06:30 UTC (12:00 IST — the standard publish time)
  - If all 7 days are full, returns day 8

```python
def _find_next_available_slot(niche_id: str) -> datetime:
    """Find the next day with no scheduled post for this niche."""
    from datetime import UTC, datetime, timedelta
    import os
    import psycopg
    from psycopg.rows import dict_row

    dsn = os.environ.get("DATABASE_URL", "")
    tomorrow = datetime.now(UTC).replace(hour=6, minute=30, second=0, microsecond=0) + timedelta(days=1)

    if dsn:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                "SELECT scheduled_for::date AS day FROM blueprints "
                "WHERE niche_id = %s AND scheduled_for >= %s "
                "AND scheduled_for < %s AND status != 'ARCHIVED'",
                (niche_id, tomorrow, tomorrow + timedelta(days=7)),
            ).fetchall()
            scheduled_days = {r["day"] for r in rows}

    for i in range(8):
        candidate = tomorrow + timedelta(days=i)
        if candidate.date() not in scheduled_days:
            return candidate

    return tomorrow + timedelta(days=7)
```

- [ ] **Step 3:** Add `POST /<id>/approve-and-schedule` endpoint:
  - Set `action_taken = "approved"`, `reviewed_at = now()`
  - Call `_find_next_available_slot(niche_id)` to get the scheduled date
  - Set `scheduled_for` on the blueprint
  - Return `{ status: "ok", scheduled_for: "<ISO datetime>" }`

- [ ] **Step 4:** Add `POST /batch-approve-schedule` endpoint:
  - Accepts `{ ids: string[] }`
  - For each id: approve + auto-schedule (same logic as single)
  - Return `{ data: [{ id, scheduled_for }, ...] }`

- [ ] **Step 5:** Restart server and verify:
```bash
launchctl kickstart -k gui/$(id -u)/com.genlab.review-server
sleep 3
# Test with a real VISUAL_READY blueprint id
BP_ID=$(psql -d genlab -tAc "SELECT id FROM blueprints WHERE status='VISUAL_READY' AND action_taken IS NULL LIMIT 1")
echo "Testing with blueprint: $BP_ID"
curl -s -u "admin:***REMOVED***" -X POST "http://localhost:5151/api/v1/blueprints/$BP_ID/approve-and-schedule" -H "Content-Type: application/json" | python3 -m json.tool
```

- [ ] **Step 6:** Commit:
```bash
cd /Users/anarchistsid/GenLab/dashboard && git add server/api/blueprints.py && git commit -m "feat(api): add approve-and-schedule + batch-approve-schedule endpoints"
```

---

## Task 2: Frontend — API client + hooks

Add client methods and React Query hooks for the new endpoints.

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/hooks/use-blueprints.ts`

- [ ] **Step 1:** In `client.ts`, add to the `blueprints` object:
```tsx
approveAndSchedule: (id: string) =>
  mutate<{ status: string; scheduled_for: string }>("POST", `/blueprints/${id}/approve-and-schedule`, {}),
batchApproveSchedule: (body: { ids: string[] }) =>
  mutate<{ data: Array<{ id: string; scheduled_for: string }> }>("POST", "/blueprints/batch-approve-schedule", body),
```

- [ ] **Step 2:** In `use-blueprints.ts`, add two new hooks:

```tsx
export function useApproveAndSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => blueprints.approveAndSchedule(id),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.blueprints.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.schedule.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.queue.all() });
      const date = data.scheduled_for
        ? new Date(data.scheduled_for).toLocaleDateString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
        : "";
      toast.success(`Approved & scheduled for ${date}`);
    },
    onError: (error: Error) => toast.error(`Approve & schedule failed: ${error.message}`),
  });
}

export function useBatchApproveSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (ids: string[]) => blueprints.batchApproveSchedule({ ids }),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.blueprints.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.schedule.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.queue.all() });
      toast.success(`${data.data.length} posts approved & scheduled`);
    },
    onError: (error: Error) => toast.error(`Batch approve & schedule failed: ${error.message}`),
  });
}
```

- [ ] **Step 3:** Run `npm run build` — must pass.

- [ ] **Step 4:** Commit:
```bash
cd /Users/anarchistsid/GenLab/dashboard && git add frontend/src/api/client.ts frontend/src/hooks/use-blueprints.ts && git commit -m "feat(dashboard): add approve-and-schedule API client + hooks"
```

---

## Task 3: Frontend — Fix FocusOverlay

Add inline hook edit, "Approve & Schedule" button, fix platforms, remove `as any`.

**Files:**
- Modify: `frontend/src/views/content/FocusOverlay.tsx`

- [ ] **Step 1:** Read the full file (308 lines).

- [ ] **Step 2:** Fix platform list — replace `const PLATFORMS = ["instagram", "youtube", "twitter", "facebook"]` with:
```tsx
import { PLATFORM_IDS } from "@/lib/platforms";
const PLATFORMS = PLATFORM_IDS;
```
Add `threads` case to `getPlatformCaption()`:
```tsx
if (platform === "threads") {
  const th = (bp as Record<string, unknown>).threads_content;
  if (typeof th === "string") return th || null;
  return null;
}
```

- [ ] **Step 3:** Fix `as any` cast — replace `const f = bp as any` with proper typed access:
```tsx
// Instead of: const f = bp as any; return f.caption;
// Use: return (bp as Record<string, unknown>).caption as string | null;
// Or access via the Blueprint type which already has caption, youtube_content, etc.
```

- [ ] **Step 4:** Remove local `resolveThumb()` — import `getThumbnailInfo` from `@/lib/format`:
```tsx
import { getThumbnailInfo } from "@/lib/format";
// Replace: const thumb = bp ? resolveThumb(bp) : null;
// With:    const thumbInfo = bp ? getThumbnailInfo(bp) : null;
//          const thumb = thumbInfo?.url ?? null;
//          const isVideo = thumbInfo?.isVideo ?? false;
```

- [ ] **Step 5:** Add inline hook edit. After the hook text display, add an edit mode:
```tsx
const [editingHook, setEditingHook] = useState(false);
const [hookDraft, setHookDraft] = useState("");
const updateContent = useUpdateContent(); // from use-blueprints

// In render, replace the hook <p> with:
{editingHook ? (
  <textarea
    value={hookDraft}
    onChange={(e) => setHookDraft(e.target.value)}
    onBlur={() => {
      if (hookDraft !== bp.hook_text) {
        updateContent.mutate({ id: bp.id, body: { hook_text: hookDraft } });
      }
      setEditingHook(false);
    }}
    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); e.currentTarget.blur(); } }}
    className="w-full text-base font-bold text-text-primary bg-bg-elevated border border-border rounded-md px-2 py-1 resize-none"
    autoFocus
    rows={2}
    maxLength={60}
  />
) : (
  <p
    className="text-base font-bold text-text-primary leading-snug cursor-pointer hover:bg-bg-elevated rounded px-1 -mx-1 transition-colors"
    onClick={() => { setHookDraft(bp.hook_text ?? ""); setEditingHook(true); }}
    title="Click to edit hook"
  >
    {bp.hook_text || "Untitled"}
  </p>
)}
```

- [ ] **Step 6:** Add "Approve & Schedule" button alongside Approve/Reject in the action footer:
```tsx
import { useApproveAndSchedule } from "@/hooks/use-blueprints";
const approveAndSchedule = useApproveAndSchedule();

// In the footer, add between Approve and Reject:
<Button
  variant="outline"
  size="sm"
  className="border-indigo-600/30 text-indigo-400 hover:bg-indigo-600/15 flex-1"
  onClick={() => approveAndSchedule.mutate(bp.id)}
  disabled={approveAndSchedule.isPending}
>
  <Calendar className="size-3.5" />
  Schedule
  <span className="text-xs text-text-ghost ml-1">(S)</span>
</Button>
```

- [ ] **Step 7:** Add keyboard shortcut `S` for approve-and-schedule:
```tsx
if (e.key === "s" && bp) { approveAndSchedule.mutate(bp.id); return; }
```

- [ ] **Step 8:** Run `npm run build` — must pass.

- [ ] **Step 9:** Commit:
```bash
cd /Users/anarchistsid/GenLab/dashboard && git add frontend/src/views/content/FocusOverlay.tsx && git commit -m "feat(dashboard): FocusOverlay — inline hook edit, approve+schedule, fix platforms"
```

---

## Task 4: Frontend — Fix ContentCard + ContentReviewView

Add auto-schedule button, video hover, batch selection, deduplicate utils, registry constants.

**Files:**
- Modify: `frontend/src/views/content/ContentCard.tsx`
- Modify: `frontend/src/views/content/ContentReviewView.tsx`

- [ ] **Step 1:** In `ContentCard.tsx`:
- Delete local `resolveThumb()` — use `getThumbnailInfo` from `@/lib/format`
- Delete local `fmtNumber()` — use `formatCompact` from `@/lib/format`
- Add video hover preview: change `<video>` tag to include `onMouseEnter`/`onMouseLeave` handlers for play/pause. Remove the permanent dark overlay with play icon — only show it when not hovering.
- Add "Approve & Schedule" button: import `useApproveAndSchedule`, render alongside existing Approve button
- Add selection checkbox prop: `selected?: boolean; onSelect?: (id: string) => void`

- [ ] **Step 2:** In `ContentReviewView.tsx`:
- Replace hardcoded `NICHE_OPTIONS` with niche registry: `import { getAllNiches } from "@/niches/registry"` then build options dynamically
- Add batch selection state:
```tsx
const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
const toggleSelect = (id: string) => setSelectedIds(prev => {
  const next = new Set(prev);
  next.has(id) ? next.delete(id) : next.add(id);
  return next;
});
const clearSelection = () => setSelectedIds(new Set());
```
- Add floating action bar when items are selected:
```tsx
{selectedIds.size > 0 && (
  <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-30 flex items-center gap-3 rounded-xl border border-border bg-bg-raised px-4 py-3 shadow-lg">
    <span className="text-sm font-semibold text-text-primary">{selectedIds.size} selected</span>
    <Button size="sm" onClick={() => batchReview.mutate({ ids: [...selectedIds], action: "approved" })}>Approve All</Button>
    <Button size="sm" variant="outline" onClick={() => batchApproveSchedule.mutate([...selectedIds])}>Approve & Schedule</Button>
    <Button size="sm" variant="ghost" onClick={clearSelection}>Cancel</Button>
  </div>
)}
```
- Pass `selected` and `onSelect` props to each ContentCard

- [ ] **Step 3:** Run `npm run build` — must pass.

- [ ] **Step 4:** Commit:
```bash
cd /Users/anarchistsid/GenLab/dashboard && git add -A frontend/src/views/content/ && git commit -m "feat(dashboard): ContentCard + Review — auto-schedule, batch select, video hover, dedup utils"
```

---

## Task 5: Frontend — Fix Schedule Board + Preview

Replace hardcoded constants, add conflict detection, add Threads to preview.

**Files:**
- Modify: `frontend/src/components/schedule/schedule-board.tsx`
- Modify: `frontend/src/components/schedule/schedule-preview.tsx`

- [ ] **Step 1:** In `schedule-board.tsx`:
- Replace hardcoded `NICHE_ROWS` with niche registry:
```tsx
import { getActiveNiches } from "@/niches/registry";
const NICHE_ROWS = getActiveNiches().map(n => ({
  id: n.id, label: n.id === "ai_creators" ? "BB" : n.id === "gaming" ? "CR" : n.id === "sports" ? "CW" : n.id === "movies" ? "SR" : "FD",
  color: n.accentHex,
}));
```
Or better — use `getNicheInfo(id).shortLabel` which already returns "BB", "Gaming", etc. Adjust to 2-letter codes if needed.

- [ ] **Step 2:** Add schedule conflict detection to `handleDragEnd`:
```tsx
// Before calling reorder.mutate, check if the target date+niche already has a post
const targetDate = overData.date;
const draggedNiche = draggedBp?.niche_id;
const existingSlots = scheduleByDate.get(targetDate)?.slots ?? [];
const conflict = existingSlots.find(s =>
  s.niche_id === draggedNiche && s.status !== "empty" && s.blueprint?.id !== blueprintId
);
if (conflict) {
  // Use window.confirm for simplicity, or a toast warning
  const nicheLabel = getNicheInfo(draggedNiche ?? "").label;
  if (!window.confirm(`${nicheLabel} already has a post on ${targetDate}. Schedule anyway?`)) {
    return;
  }
}
```

- [ ] **Step 3:** In `schedule-preview.tsx`:
- Read the file and check what platform tabs exist
- Add Threads tab if not present — add `{ value: "threads", label: "Threads", icon: ... }` to the tabs
- Ensure `PlatformPreview` component handles `threads` platform

- [ ] **Step 4:** Run `npm run build` — must pass.

- [ ] **Step 5:** Rebuild + restart:
```bash
cd /Users/anarchistsid/GenLab/dashboard/frontend && npm run build
launchctl kickstart -k gui/$(id -u)/com.genlab.review-server
```

- [ ] **Step 6:** Commit:
```bash
cd /Users/anarchistsid/GenLab/dashboard && git add -A frontend/src/components/schedule/ && git commit -m "feat(dashboard): schedule — registry constants, conflict detection, Threads preview"
```

---

## Task 6: Final verification

- [ ] **Step 1:** Verify all quality gates:
```bash
cd /Users/anarchistsid/GenLab/dashboard/frontend
echo "=== Hardcoded NICHE arrays ===" && grep -rn "const NICHE_OPTIONS\|const NICHE_ROWS\b" src/views/ src/components/schedule/ --include="*.tsx" | grep -v "registry" | wc -l
echo "=== Hardcoded PLATFORMS ===" && grep -rn 'const PLATFORMS = \[' src/views/content/ --include="*.tsx" | wc -l
echo "=== Duplicate resolveThumb ===" && grep -rn "function resolveThumb" src/ --include="*.tsx" | wc -l
echo "=== Duplicate fmtNumber ===" && grep -rn "function fmtNumber" src/ --include="*.tsx" | wc -l
echo "=== as any in FocusOverlay ===" && grep -rn "as any" src/views/content/FocusOverlay.tsx | wc -l
```
All should be 0.

- [ ] **Step 2:** Test the full workflow:
1. Go to Content Review → find a VISUAL_READY post
2. Click "Approve & Schedule" → verify toast shows scheduled date
3. Go to Schedule → verify the post appears on the scheduled date
4. Open FocusOverlay → click on hook text → edit → verify it saves
5. Press `S` key → verify it auto-schedules
6. Select multiple posts with checkboxes → click "Approve & Schedule" in floating bar

- [ ] **Step 3:** Final commit:
```bash
cd /Users/anarchistsid/GenLab && git add dashboard && git commit -m "feat(dashboard): Content Review + Schedule workflow upgrade"
```
