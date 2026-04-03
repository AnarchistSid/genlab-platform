# Dashboard Overhaul v2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 18 verified dashboard bugs across security, previews, analytics, and performance.

**Architecture:** Three independent phases. Phase 1 (crashers + security) touches isolated files with no cross-dependencies. Phase 2 (previews + analytics) modifies platform-preview.tsx and analytics hooks. Phase 3 (performance) addresses polling, socket reconnect, and stale state. Phases 2 and 3 can run in parallel after Phase 1.

**Tech Stack:** React 19 + TypeScript (Vite, TanStack Query), Python 3.13 + Flask (backend API), pytest (backend tests). No frontend test framework — verify via `tsc -b` and manual browser check.

---

## Phase 1: Crashers + Security

### Task 1: Fix React Rules of Hooks violation in kpi-card.tsx

**Files:**
- Modify: `dashboard/src/components/charts/kpi-card.tsx:20-47`

**Step 1: Move useId() above the loading guard**

The `useId()` call on line 46 is after a conditional `return` on line 30. Move it to the top of the component body (line 20, right after destructuring).

```tsx
// dashboard/src/components/charts/kpi-card.tsx
export function KpiCard({
  title,
  value,
  change,
  changeLabel,
  icon: Icon,
  trend,
  loading = false,
  inverseChange = false,
}: KpiCardProps) {
  const gradientId = useId(); // ← MOVED: must be before any early return

  if (loading) {
    return (
      <Card className="bg-bg-surface border-border">
        <CardContent className="pt-6">
          <div className="flex items-center justify-between mb-3">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="size-8 rounded-md" />
          </div>
          <Skeleton className="h-8 w-16 mb-2" />
          <Skeleton className="h-3 w-20" />
          {trend && <Skeleton className="h-10 w-full mt-3" />}
        </CardContent>
      </Card>
    );
  }

  // gradientId already defined above
  const trendData = trend?.map((v, i) => ({ index: i, value: v }));
```

**Step 2: Verify build**

Run: `cd dashboard && npx tsc -b --noEmit`
Expected: No errors

**Step 3: Commit**

```bash
git add dashboard/src/components/charts/kpi-card.tsx
git commit -m "fix(1.1): move useId() above loading guard — Rules of Hooks violation"
```

---

### Task 2: Fix integer parsing crashes in backend API routes

**Files:**
- Modify: `execution/api/pipeline.py:74-75`
- Modify: `execution/api/stories.py:19-20`
- Modify: `execution/api/blueprints.py:140-141`
- Test: `tests/test_review_server.py` (add test)

**Step 1: Write failing test**

Add to `tests/test_review_server.py`:

```python
def test_invalid_page_param_returns_400(client):
    """Non-numeric page/per_page params must return 400, not crash."""
    resp = client.get("/api/blueprints?page=abc")
    assert resp.status_code == 400
    assert "invalid" in resp.json.get("error", "").lower()
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/anarchistsid/GenLab/Content\ Scraper && venv/bin/python -m pytest tests/test_review_server.py::test_invalid_page_param_returns_400 -v`
Expected: FAIL (500 or ValueError)

**Step 3: Create a shared helper and apply to all 3 files**

Add a helper function at the top of each API blueprint file, or create a shared utility. Simplest: inline fix in each file.

For `execution/api/pipeline.py:74-75`:
```python
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(int(request.args.get("per_page", 20)), 50)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid page or per_page parameter"}), 400
```

Apply the identical pattern to:
- `execution/api/stories.py:19-20` (defaults: page=1, per_page=25, max=100)
- `execution/api/blueprints.py:140-141` (defaults: page=1, per_page=25, max=100)

**Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_review_server.py::test_invalid_page_param_returns_400 -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `venv/bin/python -m pytest tests/ -x -q`
Expected: All pass

**Step 6: Commit**

```bash
git add execution/api/pipeline.py execution/api/stories.py execution/api/blueprints.py tests/test_review_server.py
git commit -m "fix(1.2): guard int() parsing in API pagination — return 400 on invalid input"
```

---

### Task 3: Fix password timing attack + unbounded login dict

**Files:**
- Modify: `execution/review_server.py:50,209`
- Test: `tests/test_review_server.py` (add test)

**Step 1: Write failing test for constant-time comparison**

```python
def test_login_uses_constant_time_comparison(monkeypatch):
    """Password comparison must use hmac.compare_digest, not ==."""
    import inspect
    import execution.review_server as mod
    source = inspect.getsource(mod.login)
    # The source should NOT contain 'password ==' (timing-vulnerable)
    assert "password ==" not in source or "compare_digest" in source
```

**Step 2: Fix password comparison (line 209)**

```python
# execution/review_server.py line 209
# Before:
#     if username == _AUTH_USER and password == _AUTH_PASS:
# After:
import hmac
if hmac.compare_digest(username.encode(), _AUTH_USER.encode()) and hmac.compare_digest(password.encode(), _AUTH_PASS.encode()):
```

**Step 3: Fix unbounded login dict (line 50)**

Replace the `_login_attempts` logic with periodic cleanup. In the login function (around line 202), add IP cleanup:

```python
# Purge stale IPs every 100 login attempts (keep dict bounded)
if len(_login_attempts) > 200:
    cutoff = now - _LOGIN_RATE_WINDOW * 10  # 10 minutes
    stale = [ip for ip, times in _login_attempts.items() if all(t < cutoff for t in times)]
    for ip in stale:
        del _login_attempts[ip]
```

**Step 4: Run tests**

Run: `venv/bin/python -m pytest tests/test_review_server.py -v`
Expected: All pass

**Step 5: Commit**

```bash
git add execution/review_server.py tests/test_review_server.py
git commit -m "fix(1.3): constant-time password comparison + bounded login attempts dict"
```

---

### Task 4: Fix CSV formula injection in export.ts

**Files:**
- Modify: `dashboard/src/lib/export.ts:12-18`

**Step 1: Add formula prefix sanitization**

```typescript
// dashboard/src/lib/export.ts — replace escapeCSVField
function escapeCSVField(value: unknown): string {
  const str = value == null ? "" : String(value);
  // Sanitize formula injection: prefix dangerous leading chars with a tab
  const sanitized = /^[=+\-@\t\r]/.test(str) ? `\t${str}` : str;
  // If the field contains a comma, double-quote, or newline, wrap it in quotes
  if (sanitized.includes(",") || sanitized.includes('"') || sanitized.includes("\n") || sanitized.includes("\r")) {
    return `"${sanitized.replace(/"/g, '""')}"`;
  }
  return sanitized;
}
```

**Step 2: Verify build**

Run: `cd dashboard && npx tsc -b --noEmit`
Expected: No errors

**Step 3: Commit**

```bash
git add dashboard/src/lib/export.ts
git commit -m "fix(1.4): sanitize CSV formula injection — prefix leading =+\-@ with tab"
```

---

### Task 5: Fix pipeline.tsx hasError AND→OR

**Files:**
- Modify: `dashboard/src/views/pipeline.tsx:162-166`

**Step 1: Change && to ||**

```tsx
// Before:
  const hasError =
    statusQuery.isError &&
    blueprintsQuery.isError &&
    runsQuery.isError &&
    scheduleQuery.isError;

// After:
  const hasError =
    statusQuery.isError ||
    blueprintsQuery.isError ||
    runsQuery.isError ||
    scheduleQuery.isError;
```

**Step 2: Verify build**

Run: `cd dashboard && npx tsc -b --noEmit`
Expected: No errors

**Step 3: Commit**

```bash
git add dashboard/src/views/pipeline.tsx
git commit -m "fix(1.6): hasError uses OR — show error if ANY query fails, not all"
```

---

### Task 6: Phase 1 build gate

**Step 1: Full frontend build**

Run: `cd dashboard && npm run build`
Expected: `✓ built in` with zero errors

**Step 2: Full backend test suite**

Run: `cd /Users/anarchistsid/GenLab/Content\ Scraper && venv/bin/python -m pytest tests/ -x -q`
Expected: All pass

**Step 3: Commit phase summary (if needed)**

No commit needed — all changes already committed individually.

---

## Phase 2: Previews + Analytics

### Task 7: Fix YouTube Short/Video misclassification

**Files:**
- Modify: `dashboard/src/components/blueprints/platform-preview.tsx:125-156`
- Modify: `dashboard/src/api/types.ts` (may need `video_duration` field)

**Step 1: Check if video_duration is available in Blueprint type**

The Blueprint type in `types.ts` does not have `video_duration`. We need to get it. Options:
- Parse from HTML5 video metadata (client-side, async — complex)
- Add to Blueprint type and have backend include it

The simplest approach: add `video_duration` to the Blueprint interface and have the backend return it from the blueprint record. The Microsoft Lists Blueprints table should already have this data from `content_meta`.

Add to `dashboard/src/api/types.ts` Blueprint interface:
```typescript
  video_duration?: number | null;  // seconds
```

**Step 2: Update YouTubePreview to use duration**

```tsx
// dashboard/src/components/blueprints/platform-preview.tsx
function YouTubePreview({ blueprint }: { blueprint: Blueprint }) {
  const ytContent = blueprint.youtube_content;
  const videoUrl = blueprint.visual_paths ?? undefined;

  // YouTube: ≤180s = Short (portrait 9:16), >180s = Video (landscape 16:9)
  const duration = blueprint.video_duration ?? 0;
  const isLongVideo = duration > 180;
  const effectiveVideoUrl = isLongVideo
    ? (blueprint.landscape_video_url ?? videoUrl)
    : videoUrl;

  if (!ytContent && !videoUrl && !effectiveVideoUrl) {
    return <EmptyPlatform platform="YouTube" icon={Youtube} />;
  }

  const communityPost = (ytContent?.community_post_text as string) ?? "";
  const title = (ytContent?.title as string) ?? "";
  const description = (ytContent?.description as string) ?? "";
  const displayText = communityPost || description;

  return (
    <div className="space-y-3">
      <PlatformStatusBadge blueprint={blueprint} platform="youtube" />
      {effectiveVideoUrl && (
        <div className="mx-auto w-full max-w-[320px] overflow-hidden rounded-xl border border-[#262626] bg-[#0a0a0a]">
          <div className="flex items-center gap-2 border-b border-[#262626] px-3 py-2">
            <div className="flex size-6 items-center justify-center rounded-full bg-red-600/20">
              <Youtube className="size-3.5 text-red-400" />
            </div>
            <span className="text-xs font-semibold text-[#fafafa]">
              {isLongVideo ? "YouTube Video" : "YouTube Short"}
            </span>
          </div>
          <div className={isLongVideo ? "aspect-[16/9] bg-[#141414]" : "aspect-[9/16] bg-[#141414]"}>
            <VideoWithFallback
              src={effectiveVideoUrl}
              controls
              className="size-full object-contain"
              preload="metadata"
            />
          </div>
        </div>
      )}
      {/* ... rest unchanged ... */}
```

**Step 3: Verify build**

Run: `cd dashboard && npx tsc -b --noEmit`
Expected: No errors

**Step 4: Commit**

```bash
git add dashboard/src/components/blueprints/platform-preview.tsx dashboard/src/api/types.ts
git commit -m "fix(2.1): YouTube Short/Video classification uses duration, not landscape_video_url"
```

---

### Task 8: Fix Facebook preview to use source aspect ratio

**Files:**
- Modify: `dashboard/src/components/blueprints/platform-preview.tsx:303-364`

**Step 1: Remove forced landscape preference**

The current code on line 308 prefers `landscape_video_url` over `visual_paths`. Facebook should use the original source video (usually portrait for Reels content).

```tsx
function FacebookPreview({ blueprint }: { blueprint: Blueprint }) {
  const fbContent = blueprint.facebook_content;
  const caption = (fbContent?.facebook_caption as string) ?? blueprint.caption ?? "";
  const displayText = caption || blueprint.hook_text || "";
  // Facebook uses original source aspect ratio — use visual_paths (original), not landscape_video_url
  const videoUrl = blueprint.visual_paths ?? undefined;
  const slides = blueprint.slide_previews;
  const isVideo = isVideoUrl(slides?.[0]?.url);
  const hasImageSlides = slides && slides.length > 0 && !isVideo;
  const hasVideo = isVideo || Boolean(videoUrl);
  const effectiveVideoUrl = videoUrl ?? (isVideo ? slides![0].url : undefined);
  // ... rest of component unchanged
```

**Step 2: Verify build**

Run: `cd dashboard && npx tsc -b --noEmit`

**Step 3: Commit**

```bash
git add dashboard/src/components/blueprints/platform-preview.tsx
git commit -m "fix(2.2): Facebook preview uses source aspect ratio, not forced landscape"
```

---

### Task 9: Fix analytics hooks missing date params

**Files:**
- Modify: `dashboard/src/hooks/use-analytics.ts:31-43`
- Modify: `dashboard/src/views/analytics.tsx:49-50`

**Step 1: Update hooks to accept params**

```typescript
// dashboard/src/hooks/use-analytics.ts
export function usePipelineAnalytics(params?: Record<string, string>) {
  return useQuery<{ data: PipelineRun[] }>({
    queryKey: ["analytics", "pipeline", params],
    queryFn: () => analytics.pipeline(params),
  });
}

export function useHeatmap(params?: Record<string, string>) {
  return useQuery<{ data: HeatmapCell[] }>({
    queryKey: ["analytics", "heatmap", params],
    queryFn: () => analytics.heatmap(params),
  });
}
```

**Step 2: Pass params in analytics view**

```typescript
// dashboard/src/views/analytics.tsx lines 49-50
// Before:
  const pipelineQuery = usePipelineAnalytics();
  const heatmapQuery = useHeatmap();
// After:
  const pipelineQuery = usePipelineAnalytics(params);
  const heatmapQuery = useHeatmap(params);
```

**Step 3: Verify build**

Run: `cd dashboard && npx tsc -b --noEmit`

**Step 4: Commit**

```bash
git add dashboard/src/hooks/use-analytics.ts dashboard/src/views/analytics.tsx
git commit -m "fix(2.3,2.6): pass date range params to pipeline analytics and heatmap hooks"
```

---

### Task 10: Guard engagement NaN/Infinity

**Files:**
- Modify: `dashboard/src/components/charts/engagement-breakdown.tsx:82,88`

**Step 1: Add safe formatting helpers**

```tsx
// Near the top of engagement-breakdown.tsx, add helper:
function safePercent(rate: number | undefined): string {
  if (rate == null || !isFinite(rate)) return "N/A";
  return (rate * 100).toFixed(2) + "%";
}

function safeScore(score: number | undefined): string {
  if (score == null || !isFinite(score)) return "—";
  return score.toFixed(1);
}
```

**Step 2: Replace unsafe formatting on lines 82 and 88**

```tsx
// Line 82 — replace:
//   {(p.avg_engagement_rate * 100).toFixed(2)}%
// With:
   {safePercent(p.avg_engagement_rate)}

// Line 88 — replace:
//   {p.avg_viral_score.toFixed(1)}
// With:
   {safeScore(p.avg_viral_score)}
```

**Step 3: Verify build**

Run: `cd dashboard && npx tsc -b --noEmit`

**Step 4: Commit**

```bash
git add dashboard/src/components/charts/engagement-breakdown.tsx
git commit -m "fix(2.4): guard engagement rate and viral score against NaN/Infinity"
```

---

### Task 11: Improve empty state message for unadapted platforms

**Files:**
- Modify: `dashboard/src/components/blueprints/platform-preview.tsx:286-301`

**Step 1: Update EmptyPlatform component**

```tsx
function EmptyPlatform({
  platform,
  icon: Icon,
}: {
  platform: string;
  icon: typeof Youtube;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-[#262626] py-10">
      <Icon className="mb-2 size-8 text-[#3f3f46]" />
      <p className="text-sm text-[#52525b]">
        Platform adaptation runs before publishing
      </p>
      <p className="mt-1 text-xs text-[#3f3f46]">
        {platform} content will appear after finalization
      </p>
    </div>
  );
}
```

**Step 2: Verify build**

Run: `cd dashboard && npx tsc -b --noEmit`

**Step 3: Commit**

```bash
git add dashboard/src/components/blueprints/platform-preview.tsx
git commit -m "fix(2.7): clearer empty state for unadapted platform previews"
```

---

### Task 12: Phase 2 build gate

**Step 1: Full frontend build**

Run: `cd dashboard && npm run build`
Expected: `✓ built in` with zero errors

**Step 2: Full backend test suite**

Run: `venv/bin/python -m pytest tests/ -x -q`
Expected: All pass

---

## Phase 3: Performance + Reliability

### Task 13: Remove global refetchInterval — set per-query

**Files:**
- Modify: `dashboard/src/App.tsx:21-29`
- Modify: `dashboard/src/views/pipeline.tsx` (add refetchInterval to relevant queries)
- Modify: `dashboard/src/views/analytics.tsx` (set longer interval)

**Step 1: Remove global refetchInterval from QueryClient**

```tsx
// dashboard/src/App.tsx
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,  // 30s stale time (was 8s)
      retry: 2,
      // REMOVED: refetchInterval: 8_000 — now set per-query
    },
  },
});
```

**Step 2: Add targeted refetchInterval where needed**

In `dashboard/src/hooks/use-blueprints.ts` (if it exists) or in the Pipeline view's query calls, add `refetchInterval: 15_000` for pipeline status and blueprints.

For analytics queries in `dashboard/src/hooks/use-analytics.ts`, add `refetchInterval: 60_000` (1 min) only for engagement/publishing queries, no auto-refetch for pipeline runs or heatmap.

**Step 3: Verify build**

Run: `cd dashboard && npx tsc -b --noEmit`

**Step 4: Commit**

```bash
git add dashboard/src/App.tsx dashboard/src/hooks/
git commit -m "fix(3.1): per-query refetchInterval — 15s for pipeline, 60s for analytics, none for settings"
```

---

### Task 14: Fix socket.io permanent give-up

**Files:**
- Modify: `dashboard/src/api/socket.ts`

**Step 1: Set infinite reconnection with capped delay**

```typescript
// dashboard/src/api/socket.ts
export function getSocket(): Socket {
  if (!socket) {
    socket = io({
      transports: ["websocket"],
      reconnection: true,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 60000,  // cap at 60s (was 30s)
      reconnectionAttempts: Infinity,  // never give up (was 10)
    });
  }
  return socket;
}
```

**Step 2: Verify build**

Run: `cd dashboard && npx tsc -b --noEmit`

**Step 3: Commit**

```bash
git add dashboard/src/api/socket.ts
git commit -m "fix(3.2): socket.io reconnects forever with 60s max delay — never gives up"
```

---

### Task 15: Fix keyboard listener re-registration

**Files:**
- Modify: `dashboard/src/hooks/use-keyboard.ts:147`

**Step 1: Remove reviewMutation from deps, use ref**

The `reviewMutation` object changes identity every render. Use `useRef` to hold the mutation's `mutate` function.

At the top of the hook (before the useEffect):
```typescript
const reviewMutateRef = useRef(reviewMutation.mutate);
reviewMutateRef.current = reviewMutation.mutate;
```

In the handleKeyDown, use `reviewMutateRef.current(...)` instead of `reviewMutation.mutate(...)`.

Then update the deps array:
```typescript
  }, [navigate, location.pathname, selectedIds, toggle, clearGPrefix]);
  // Removed: reviewMutation (unstable ref — now accessed via reviewMutateRef)
```

**Step 2: Verify build**

Run: `cd dashboard && npx tsc -b --noEmit`

**Step 3: Commit**

```bash
git add dashboard/src/hooks/use-keyboard.ts
git commit -m "fix(3.3): stabilize keyboard listener — reviewMutation accessed via ref, not deps"
```

---

### Task 16: Fix focus mode stale index

**Files:**
- Modify: `dashboard/src/hooks/use-focus-mode.ts:33`

**Step 1: Add useEffect to clamp index when items change**

After line 33 (`const [currentIndex, setCurrentIndex] = useState(0);`), add:

```typescript
// Reset index when items array changes (e.g., after refetch with different results)
useEffect(() => {
  setCurrentIndex((prev) => {
    if (items.length === 0) return 0;
    return Math.min(prev, items.length - 1);
  });
}, [items.length]);
```

Add `useEffect` to the imports from "react" at the top of the file.

**Step 2: Verify build**

Run: `cd dashboard && npx tsc -b --noEmit`

**Step 3: Commit**

```bash
git add dashboard/src/hooks/use-focus-mode.ts
git commit -m "fix(3.4): clamp focus mode currentIndex when items array shrinks"
```

---

### Task 17: Fix blueprint detail stale video error

**Files:**
- Modify: `dashboard/src/components/blueprints/blueprint-detail.tsx:56`

**Step 1: Reset videoError when blueprintId changes**

After line 56 (`const [videoError, setVideoError] = useState(false);`), add:

```typescript
useEffect(() => {
  setVideoError(false);
}, [blueprintId]);
```

Make sure `useEffect` is imported from "react".

**Step 2: Verify build**

Run: `cd dashboard && npx tsc -b --noEmit`

**Step 3: Commit**

```bash
git add dashboard/src/components/blueprints/blueprint-detail.tsx
git commit -m "fix(3.5): reset videoError state when switching blueprints"
```

---

### Task 18: Final build gate + rebuild dashboard

**Step 1: Full frontend build**

Run: `cd dashboard && npm run build`
Expected: `✓ built in` with zero errors

**Step 2: Full backend test suite**

Run: `venv/bin/python -m pytest tests/ -x -q`
Expected: All pass

**Step 3: Reload backend server**

Run: `kill -HUP $(pgrep -f 'gunicorn.*review_server') 2>/dev/null || echo "Gunicorn not running — restart manually"`

This picks up all Python changes without downtime.

**Step 4: Commit final state**

Only if any cleanup needed. All individual fixes already committed.

---

## Verification Checklist (Post-Implementation)

After all 18 tasks, verify in Chrome browser:

- [ ] Pipeline page loads without crash (task 1 — hooks fix)
- [ ] Pipeline shows error banner if backend is down (task 5)
- [ ] `/api/blueprints?page=abc` returns 400, not 500 (task 2)
- [ ] YouTube preview: 79s video shows "YouTube Short" (9:16) (task 7)
- [ ] YouTube preview: 200s video shows "YouTube Video" (16:9) (task 7)
- [ ] Facebook preview: shows source aspect ratio (task 8)
- [ ] Analytics: cost chart shows non-zero values for date range (task 9)
- [ ] Analytics: engagement rate shows "N/A" for Facebook (task 10)
- [ ] VISUAL_READY post Twitter tab: says "adaptation runs before publishing" (task 11)
- [ ] Network tab: pipeline queries poll ~15s, analytics ~60s (task 13)
- [ ] No keyboard listener churn in React DevTools (task 15)
