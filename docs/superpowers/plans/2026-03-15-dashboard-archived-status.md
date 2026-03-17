# Dashboard ARCHIVED Status Handling — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface auto-archived blueprints in the dashboard with an overview stats card, Archived status filter tab, action_taken badge on cards, and a review queue empty state.

**Architecture:** Backend already fetches ARCHIVED records and returns `total_archived` in the overview. This plan extends that with `by_reason` breakdown + `pass_rate`, expands the `action_taken` allowlist, and adds 4 frontend components: StatusBadge for ARCHIVED, `action_taken` badge on BlueprintCard, AutoArchiveCard in Mission Control, and updated FocusEmpty.

**Tech Stack:** Flask (Python), React 19, TypeScript, TanStack Query, Framer Motion, Lucide icons.

---

## Critical Context

**What already exists (read before implementing):**

- `overview.py` line 181: Formula ALREADY includes `{status}='ARCHIVED'` — the backend already fetches archived records.
- `overview.py` line 211: `total_archived = len(archived_records)` is ALREADY computed.
- `overview.py` line 324: `"total_archived": total_archived` is ALREADY returned in the response.
- `blueprints.py` lines 396-401: `include_archived` query param ALREADY exists on `list_blueprints()`.
- **What's MISSING:** `by_reason` breakdown, `pass_rate`, expanded `ALLOWED_ACTIONS`, frontend ARCHIVED tab, archive card, blueprint-card badge, focus-mode empty state, TypeScript types.

**Key file locations:**
- Backend: `dashboard/server/api/blueprints.py`, `dashboard/server/api/overview.py`
- Frontend types: `dashboard/frontend/src/api/client.ts` (line 272 — `CrossNicheOverviewResponse`)
- Blueprint card: `dashboard/frontend/src/components/blueprints/blueprint-card.tsx`
- Status badge: `dashboard/frontend/src/components/shared/status-badge.tsx`
- Mission Control: `dashboard/frontend/src/views/mission-control/MissionControl.tsx`
- Grid CSS: `dashboard/frontend/src/views/mission-control/mission-control.css`
- Review empty state: `dashboard/frontend/src/components/review/focus-mode.tsx` (NOT `focus-review.tsx`)
- Status filters: `dashboard/frontend/src/views/blueprints.tsx` (line 35 — `STATUS_FILTERS`)

---

## Chunk 1: Backend Changes

### Task 1: Expand ALLOWED_ACTIONS in blueprints.py

**Files:**
- Modify: `dashboard/server/api/blueprints.py`
- Test: `dashboard/tests/test_api_blueprints.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to dashboard/tests/test_api_blueprints.py

def test_action_taken_auto_archived_accepted(client, mock_graph_client):
    """auto_archived_* values should pass validation."""
    mock_graph_client.blueprints.all.return_value = []
    resp = client.get("/api/v1/blueprints?status=ARCHIVED&action_taken=auto_archived_no_video")
    assert resp.status_code == 200

def test_action_taken_unknown_rejected(client, mock_graph_client):
    """Unknown action_taken values should be rejected."""
    resp = client.get("/api/v1/blueprints?status=ARCHIVED&action_taken=evil_injection")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("error") or "Invalid" in str(data)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package dashboard pytest dashboard/tests/test_api_blueprints.py -k "auto_archived" -v`
Expected: FAIL (`auto_archived_no_video` not in ALLOWED_ACTIONS)

- [ ] **Step 3: Expand the allowlist**

In `dashboard/server/api/blueprints.py`, replace line 409:

```python
ALLOWED_ACTIONS = {"approved", "rejected", "revised", "skipped"}
```

With:

```python
ALLOWED_ACTIONS = {
    "approved", "rejected", "revised", "skipped",
    "auto_archived_template_hook", "auto_archived_no_video",
    "auto_archived_low_score", "auto_archived_duplicate",
    "auto_archived_stale", "auto_archived_no_clip",
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package dashboard pytest dashboard/tests/test_api_blueprints.py -k "auto_archived" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/anarchistsid/GenLab/dashboard
git add server/api/blueprints.py tests/test_api_blueprints.py
git commit -m "feat(blueprints): expand ALLOWED_ACTIONS with auto_archived values"
```

---

### Task 2: Add by_reason breakdown + pass_rate to overview

**Files:**
- Modify: `dashboard/server/api/overview.py`
- Test: `dashboard/tests/test_api_smoke.py` (or new `test_overview_archive.py`)

- [ ] **Step 1: Write the failing test**

```python
# dashboard/tests/test_overview_archive.py
"""Tests for overview auto-archive stats."""
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone


class TestOverviewArchiveStats(unittest.TestCase):

    @patch("server.api.overview._get_client")
    @patch("server.api.overview._load_registry")
    @patch("server.api.overview._platform_health_from_reports")
    @patch("server.api.pipeline._merge_prefect_status", side_effect=lambda x: x)
    @patch("server.api.pipeline._prefect_healthy", return_value=False)
    def test_overview_includes_auto_archive_today(
        self, _ph, _mp, _phr, mock_registry, mock_client_fn
    ):
        from server.api.overview import _build_overview

        mock_registry.return_value = [{"id": "sports", "status": "active", "display_name": "CW"}]
        _phr.return_value = {}

        now_iso = datetime.now(timezone.utc).isoformat()
        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = [
            {"id": "1", "fields": {"status": "ARCHIVED", "action_taken": "auto_archived_no_video",
                                   "reviewed_at": now_iso, "niche_id": "sports"}},
            {"id": "2", "fields": {"status": "ARCHIVED", "action_taken": "auto_archived_template_hook",
                                   "reviewed_at": now_iso, "niche_id": "sports"}},
            {"id": "3", "fields": {"status": "PUBLISHED", "published_at": now_iso,
                                   "niche_id": "sports", "priority_score": "0.8", "hook_text": "Test"}},
        ]
        mock_client_fn.return_value = mock_client

        result = _build_overview()

        archive = result["global"]["auto_archive_today"]
        assert archive["total"] == 2
        assert archive["by_reason"]["auto_archived_no_video"] == 1
        assert archive["by_reason"]["auto_archived_template_hook"] == 1
        assert archive["pass_rate"] is not None
        # 1 published / (1 published + 2 archived) = 0.33
        assert 0.3 <= archive["pass_rate"] <= 0.34
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package dashboard pytest dashboard/tests/test_overview_archive.py -v`
Expected: FAIL (KeyError: `auto_archive_today`)

- [ ] **Step 3: Add by_reason and pass_rate to _build_overview()**

In `dashboard/server/api/overview.py`, after line 211 (`total_archived = len(archived_records)`), add:

```python
    # Auto-archive breakdown by action_taken reason
    archive_by_reason: dict[str, int] = {}
    niche_archived: dict[str, int] = {}
    for r in archived_records:
        fields = r.get("fields", {})
        reason = (fields.get("action_taken") or "unknown").strip()
        archive_by_reason[reason] = archive_by_reason.get(reason, 0) + 1
        n = _bp_niche_overview(fields)
        niche_archived[n] = niche_archived.get(n, 0) + 1

    # Pass rate: published / (published + archived) — approximate, uses different timestamp fields
    pass_rate = (
        round(total_published / (total_published + total_archived), 2)
        if (total_published + total_archived) > 0
        else None
    )
```

In the `"global"` dict of the return statement (after line 324 `"total_archived": total_archived,`), add:

```python
            "auto_archive_today": {
                "total": total_archived,
                "by_reason": archive_by_reason,
                "pass_rate": pass_rate,
            },
```

In the per-niche `niches_data.append(...)` block (after line 254 `"published_today": ...`), add:

```python
            "archived_today": niche_archived.get(niche_id, 0),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package dashboard pytest dashboard/tests/test_overview_archive.py -v`
Expected: PASS

- [ ] **Step 5: Run full backend test suite**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package dashboard pytest dashboard/tests/ -x -q`
Expected: All passing

- [ ] **Step 6: Commit**

```bash
cd /Users/anarchistsid/GenLab/dashboard
git add server/api/overview.py tests/test_overview_archive.py
git commit -m "feat(overview): add auto_archive_today with by_reason + pass_rate"
```

---

## Chunk 2: Frontend Changes

### Task 3: Add ARCHIVED to StatusBadge + STATUS_FILTERS

**Files:**
- Modify: `dashboard/frontend/src/components/shared/status-badge.tsx`
- Modify: `dashboard/frontend/src/views/blueprints.tsx`

- [ ] **Step 1: Add ARCHIVED config to status-badge.tsx**

In `dashboard/frontend/src/components/shared/status-badge.tsx`, add after the `NEEDS_REVIEW` entry (line 32):

```typescript
  ARCHIVED: {
    label: "Archived",
    className: "bg-gray-500/15 text-gray-400 border-gray-500/25",
  },
```

- [ ] **Step 2: Add ARCHIVED to STATUS_FILTERS in blueprints.tsx**

In `dashboard/frontend/src/views/blueprints.tsx`, add after the `ERROR` entry (line 42):

```typescript
  { value: "ARCHIVED", label: "Archived" },
```

- [ ] **Step 3: Commit**

```bash
cd /Users/anarchistsid/GenLab/dashboard
git add frontend/src/components/shared/status-badge.tsx frontend/src/views/blueprints.tsx
git commit -m "feat(ui): add ARCHIVED status to filter tabs and status badge"
```

---

### Task 4: Add action_taken badge to BlueprintCard

**Files:**
- Modify: `dashboard/frontend/src/components/blueprints/blueprint-card.tsx`

- [ ] **Step 1: Add Badge import**

Add `Badge` to the imports in `blueprint-card.tsx`:

```typescript
import { Badge } from "@/components/ui/badge";
```

- [ ] **Step 2: Add action_taken badge below StatusBadge**

In `blueprint-card.tsx`, after the `<StatusBadge status={blueprint.status} />` line (line 150), add:

```tsx
            {blueprint.action_taken?.startsWith("auto_archived_") && (
              <Badge variant="outline" className="text-xs text-muted-foreground border-gray-600/30">
                {blueprint.action_taken.replace("auto_archived_", "").replace(/_/g, " ")}
              </Badge>
            )}
```

- [ ] **Step 3: Verify it builds**

Run: `cd /Users/anarchistsid/GenLab/dashboard/frontend && npm run build 2>&1 | tail -5`
Expected: Build succeeds (check `action_taken` exists on Blueprint type — if not, add it to `api/types.ts`)

- [ ] **Step 4: Commit**

```bash
cd /Users/anarchistsid/GenLab/dashboard
git add frontend/src/components/blueprints/blueprint-card.tsx
git commit -m "feat(card): show action_taken reason badge on auto-archived items"
```

---

### Task 5: Extend CrossNicheOverviewResponse TypeScript type

**Files:**
- Modify: `dashboard/frontend/src/api/client.ts`

- [ ] **Step 1: Add auto_archive_today to global type**

In `dashboard/frontend/src/api/client.ts`, in the `global` block of `CrossNicheOverviewResponse` (line 275-280), add after `platform_health`:

```typescript
    total_archived?: number;
    auto_archive_today?: {
      total: number;
      by_reason: Record<string, number>;
      pass_rate: number | null;
    };
```

- [ ] **Step 2: Add archived_today to niches array type**

In the `niches` array type (line 281-296), add after `published_today: number;` (line 289):

```typescript
    archived_today?: number;
```

- [ ] **Step 3: Commit**

```bash
cd /Users/anarchistsid/GenLab/dashboard
git add frontend/src/api/client.ts
git commit -m "feat(types): extend CrossNicheOverviewResponse with archive stats"
```

---

### Task 6: AutoArchiveCard in Mission Control

**Files:**
- Modify: `dashboard/frontend/src/views/mission-control/MissionControl.tsx`
- Modify: `dashboard/frontend/src/views/mission-control/mission-control.css`

- [ ] **Step 1: Add area-archive to CSS grid**

In `mission-control.css`, update the grid template (lines 39-44):

```css
  grid-template-areas:
    "queue          pipeline"
    "niches         niches"
    "schedule       schedule"
    "perf           health"
    "archive        archive"
    "monetisation   monetisation";
```

Update the mobile grid (lines 51-58):

```css
    grid-template-areas:
      "queue"
      "pipeline"
      "niches"
      "schedule"
      "perf"
      "health"
      "archive"
      "monetisation";
```

Add after line 67:

```css
.area-archive  { grid-area: archive; }
```

- [ ] **Step 2: Add AutoArchiveCard component to MissionControl.tsx**

Add this component before the main `MissionControl` export:

```tsx
// ── Card: Auto-Archive Today ─────────────────────────────

function AutoArchiveCard({
  data,
  index,
}: {
  data: CrossNicheOverviewResponse;
  index: number;
}) {
  const navigate = useNavigate();
  const archive = data.global.auto_archive_today;
  if (!archive) return null;

  const total = archive.total;
  const passRate = archive.pass_rate;
  const reasons = Object.entries(archive.by_reason).sort(([, a], [, b]) => b - a);
  const maxCount = reasons.length > 0 ? reasons[0][1] : 1;

  return (
    <div className="bento-card area-archive" style={{ animationDelay: `${index * 60}ms` }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h3 className="card-title">Auto-Archived Today</h3>
        <span style={{ fontSize: 28, fontWeight: 700, color: "var(--text-primary)" }}>
          {total}
        </span>
      </div>

      {passRate !== null && passRate !== undefined ? (
        <p className="card-caption" style={{ marginTop: 4 }}>
          Pass rate: {Math.round(passRate * 100)}%
        </p>
      ) : (
        <p className="card-caption" style={{ marginTop: 4, color: "var(--text-disabled)" }}>
          No items processed today
        </p>
      )}

      {reasons.length > 0 && (
        <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 6 }}>
          {reasons.slice(0, 5).map(([reason, count]) => (
            <div key={reason} style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span
                style={{
                  fontSize: 11,
                  color: "var(--text-muted)",
                  width: 100,
                  textOverflow: "ellipsis",
                  overflow: "hidden",
                  whiteSpace: "nowrap",
                }}
              >
                {reason.replace("auto_archived_", "").replace(/_/g, " ")}
              </span>
              <div
                style={{
                  flex: 1,
                  height: 6,
                  borderRadius: 3,
                  background: "var(--bg-elevated)",
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    width: `${(count / maxCount) * 100}%`,
                    height: "100%",
                    borderRadius: 3,
                    background: "var(--color-orange, #f97316)",
                  }}
                />
              </div>
              <span style={{ fontSize: 11, color: "var(--text-secondary)", minWidth: 18, textAlign: "right" }}>
                {count}
              </span>
            </div>
          ))}
        </div>
      )}

      {total === 0 && (
        <p style={{ marginTop: 12, fontSize: 13, color: "var(--text-disabled)" }}>
          No items archived today
        </p>
      )}

      <div className="card-actions" style={{ marginTop: 12 }}>
        <button className="btn-ghost" onClick={() => navigate("/blueprints?status=ARCHIVED")}>
          View Archived <ArrowRight size={14} />
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Add AutoArchiveCard to the grid in the main render**

Find where cards are rendered in the main `MissionControl` component. Add `AutoArchiveCard` after `HealthCard` and before `MonetisationWidget`:

```tsx
<AutoArchiveCard data={data} index={5} />
```

Also add a `SkeletonCard` to `LoadingSkeleton`:

```tsx
<SkeletonCard className="area-archive" />
```

- [ ] **Step 4: Verify it builds**

Run: `cd /Users/anarchistsid/GenLab/dashboard/frontend && npm run build 2>&1 | tail -5`
Expected: Build succeeds

- [ ] **Step 5: Commit**

```bash
cd /Users/anarchistsid/GenLab/dashboard
git add frontend/src/views/mission-control/MissionControl.tsx frontend/src/views/mission-control/mission-control.css
git commit -m "feat(mc): add AutoArchiveCard to Mission Control grid"
```

---

### Task 7: Update FocusEmpty in focus-mode.tsx

**Files:**
- Modify: `dashboard/frontend/src/components/review/focus-mode.tsx`

- [ ] **Step 1: Update FocusEmpty component**

In `dashboard/frontend/src/components/review/focus-mode.tsx`, replace the `FocusEmpty` function (lines 116-143):

```tsx
function FocusEmpty() {
  const navigate = useNavigate();
  return (
    <div
      className="flex h-screen w-screen items-center justify-center"
      style={{ background: "var(--bg-base)" }}
    >
      <div className="flex flex-col items-center gap-4 text-center">
        <CheckCircle2 className="size-12" style={{ color: "var(--color-green, #22c55e)" }} />
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

- [ ] **Step 2: Update imports**

Ensure `CheckCircle2` is imported from `lucide-react`. Remove `ImageIcon` import if no longer used elsewhere in the file.

- [ ] **Step 3: Verify it builds**

Run: `cd /Users/anarchistsid/GenLab/dashboard/frontend && npm run build 2>&1 | tail -5`
Expected: Build succeeds

- [ ] **Step 4: Commit**

```bash
cd /Users/anarchistsid/GenLab/dashboard
git add frontend/src/components/review/focus-mode.tsx
git commit -m "feat(review): update empty state with archive-aware copy + View Archived button"
```

---

## Chunk 3: Final Integration

### Task 8: Build frontend + smoke test

- [ ] **Step 1: Full frontend build**

Run: `cd /Users/anarchistsid/GenLab/dashboard/frontend && npm run build`
Expected: Build succeeds with no type errors

- [ ] **Step 2: Run backend tests**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package dashboard pytest dashboard/tests/ -x -q`
Expected: All passing

- [ ] **Step 3: Final commit**

```bash
cd /Users/anarchistsid/GenLab/dashboard
git add -A
git commit -m "chore(dashboard): Sprint 61 — ARCHIVED status handling

- Expanded ALLOWED_ACTIONS with auto_archived_* values
- Overview: auto_archive_today with by_reason + pass_rate
- Frontend: ARCHIVED filter tab, action_taken badge on cards
- Mission Control: AutoArchiveCard with breakdown bars
- Focus Review: archive-aware empty state with View Archived link"
```
