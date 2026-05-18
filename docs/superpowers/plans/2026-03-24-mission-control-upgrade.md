# Mission Control Upgrade — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate all fake/hardcoded data from Mission Control and surface real metrics from the database — reach, likes, hooks, sparklines, QC stats, revenue.

**Architecture:** Backend-first — fix 3 API endpoints and create 1 new one, then wire frontend components to real data. Each task is independently buildable and testable.

**Tech Stack:** Backend: Flask, psycopg3, PostgreSQL. Frontend: React 19, TanStack Query 5, Tailwind CSS v4, TypeScript 5.9.

**Spec:** `docs/superpowers/specs/2026-03-24-mission-control-upgrade-design.md`

**Backend working directory:** `/Users/anarchistsid/GenLab/dashboard/server`

**Frontend working directory:** `/Users/anarchistsid/GenLab/dashboard/frontend`

**Build command:** `cd /Users/anarchistsid/GenLab/dashboard/frontend && npm run build`

**Backend test:** `curl -s -u "admin:gSsVEgt9x5LoulzsNf0bzQ" http://localhost:5151/api/v1/<endpoint> | python3 -m json.tool`

---

## Task 1: Backend — Add engagement aggregates + sparkline data to overview API

Add `total_reach`, `total_likes`, `total_comments`, and `niche_daily_reach` to the cross-niche overview response. Also fix platform health to exclude unconfigured platforms from the denominator.

**Files:**
- Modify: `server/api/overview.py` — `_build_overview()` function

- [ ] **Step 1:** Read `server/api/overview.py` fully to understand the `_build_overview()` function and the returned dict structure.

- [ ] **Step 2:** Add engagement aggregation. Inside `_build_overview()`, BEFORE the final `return` statement, add a Postgres query that aggregates reach/likes/comments from the `analytics` table. Filter out test records (`niche_id NOT LIKE 'rls_test%' AND niche_id NOT LIKE 'test_%'`). The engagement data lives in the `extra` JSONB column: `extra->>'reach'`, `extra->>'likes'`, `extra->>'comments'`. Add `total_reach`, `total_likes`, `total_comments` to the `global` dict in the return value.

- [ ] **Step 3:** Add per-niche daily reach for sparklines. Query `analytics` grouped by `niche_id` and `collected_at::date` for the last 14 days. Return as `niche_daily_reach: { "ai_creators": [{"date": "2026-03-20", "reach": 1234}, ...], ... }` in the response.

- [ ] **Step 4:** Fix platform health. In the `global.platform_health` computation, the frontend currently divides ok count by total count (including "not_configured"). Don't change the backend — just add a new field `configured_platform_count` that counts platforms where status != "not_configured". The frontend will use this for the percentage.

- [ ] **Step 5:** Restart dashboard server and verify:
```bash
launchctl kickstart -k gui/$(id -u)/com.genlab.review-server
sleep 3
curl -s -u "admin:gSsVEgt9x5LoulzsNf0bzQ" http://localhost:5151/api/v1/cross-niche/overview | python3 -c "
import json, sys
d = json.load(sys.stdin).get('data', {})
g = d.get('global', {})
print(f'total_reach: {g.get(\"total_reach\")}')
print(f'total_likes: {g.get(\"total_likes\")}')
print(f'total_comments: {g.get(\"total_comments\")}')
print(f'configured_platform_count: {g.get(\"configured_platform_count\")}')
ndr = d.get('niche_daily_reach', {})
for niche, days in ndr.items():
    print(f'{niche}: {len(days)} days of sparkline data')
"
```
Expected: real numbers (reach ~620K, likes ~37K), sparkline arrays per niche.

- [ ] **Step 6:** Commit:
```bash
cd /Users/anarchistsid/GenLab/dashboard && git add server/api/overview.py && git commit -m "feat(api): add engagement aggregates + sparkline data to overview endpoint"
```

---

## Task 2: Backend — Add hook text to top-posts API

Join `analytics` → `publishing_analytics` → `blueprints` to include hook text and title in top posts.

**Files:**
- Modify: `server/api/analytics.py` — `top_posts()` function (around line 125)

- [ ] **Step 1:** Read the `top_posts()` function in `analytics.py`. Currently it queries the analytics table and returns `post_id, platform, niche_id, likes, comments, reach, collected_at`. It has no hook_text.

- [ ] **Step 2:** After building the `posts` list, add an enrichment step. Query `publishing_analytics` JOIN `blueprints` to get hook text:
```python
# Enrich with hook text from blueprints via publishing_analytics
dsn = os.environ.get("DATABASE_URL", "")
if dsn and top:
    try:
        import psycopg
        from psycopg.rows import dict_row
        post_ids = [p["post_id"] for p in top]
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                "SELECT pa.post_id, b.hook, b.hook_text, b.title "
                "FROM publishing_analytics pa "
                "JOIN blueprints b ON pa.blueprint_id = b.id "
                "WHERE pa.post_id = ANY(%s)",
                (post_ids,)
            ).fetchall()
            hook_map = {r["post_id"]: r for r in rows}
            for p in top:
                linked = hook_map.get(p["post_id"], {})
                p["hook_text"] = linked.get("hook") or linked.get("hook_text") or ""
                p["title"] = linked.get("title") or ""
    except Exception as exc:
        logger.debug("top-posts hook enrichment failed: %s", exc)
```

- [ ] **Step 3:** Verify:
```bash
curl -s -u "admin:gSsVEgt9x5LoulzsNf0bzQ" http://localhost:5151/api/v1/analytics/top-posts | python3 -c "
import json, sys
d = json.load(sys.stdin).get('data', {})
posts = d.get('posts', d) if isinstance(d, dict) else d
for p in (posts if isinstance(posts, list) else [])[:3]:
    print(f'{p.get(\"platform\")}: likes={p.get(\"likes\")} hook={p.get(\"hook_text\",\"MISSING\")[:50]}')
"
```
Expected: hook_text populated (e.g., "Minecraft Dungeons 2 is actually happening").

- [ ] **Step 4:** Commit:
```bash
cd /Users/anarchistsid/GenLab/dashboard && git add server/api/analytics.py && git commit -m "feat(api): add hook_text to top-posts via publishing_analytics join"
```

---

## Task 3: Backend — Create quality-stats endpoint

New endpoint that reads pipeline run reports for QC stats.

**Files:**
- Modify: `server/api/pipeline.py` — add new route

- [ ] **Step 1:** Read `server/api/pipeline.py` to understand the existing route structure and how `GENLAB_ROOT` / run directories are accessed.

- [ ] **Step 2:** Add a `/quality-stats` route that:
1. Iterates `.tmp/runs/` directories sorted by mtime descending
2. Reads `run_report.json` from each
3. Takes the latest report per niche (max 5 niches)
4. Aggregates `metrics.stories_count` (hooks generated), `metrics.qc.passed/failed/total`, `metrics.video_validation.passed/fixed`
5. Returns the aggregated stats

- [ ] **Step 3:** Verify:
```bash
curl -s -u "admin:gSsVEgt9x5LoulzsNf0bzQ" http://localhost:5151/api/v1/pipeline/quality-stats | python3 -m json.tool
```
Expected: real numbers from run reports.

- [ ] **Step 4:** Commit:
```bash
cd /Users/anarchistsid/GenLab/dashboard && git add server/api/pipeline.py && git commit -m "feat(api): add /pipeline/quality-stats endpoint from run reports"
```

---

## Task 4: Frontend — Update types + add new hooks + API client methods

Add types, hooks, and client methods for the new/modified backend data.

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/query-keys.ts`
- Create: `frontend/src/hooks/use-quality-stats.ts`
- Create: `frontend/src/hooks/use-revenue.ts`
- Create: `frontend/src/hooks/use-publishing-metrics.ts`
- Create: `frontend/src/hooks/use-publishing-alerts.ts`

- [ ] **Step 1:** In `types.ts`, add to `CrossNicheOverviewResponse.global`:
```tsx
total_reach?: number;
total_likes?: number;
total_comments?: number;
configured_platform_count?: number;
```
Add to the response root level:
```tsx
niche_daily_reach?: Record<string, Array<{ date: string; reach: number }>>;
```
Add `hook_text?: string` and `title?: string` to the `TopPost` interface.
Add a new `QualityStats` interface.

- [ ] **Step 2:** In `client.ts`, add `pipeline.qualityStats()` method.

- [ ] **Step 3:** In `query-keys.ts`, add `qualityStats` and `revenue.summary` keys.

- [ ] **Step 4:** Create 4 new hook files:
- `use-quality-stats.ts` — wraps `pipeline.qualityStats()` with `staleTime: 300_000`
- `use-revenue.ts` — wraps `revenue.summary()` with `staleTime: 300_000`
- `use-publishing-metrics.ts` — wraps `metrics.publishing()` with React Query (replaces manual fetch in PublishingHealth)
- `use-publishing-alerts.ts` — wraps `alerts.publishing()` with React Query + `refetchInterval: 60_000` (replaces manual fetch in AlertBanner)

- [ ] **Step 5:** Run `npm run build` — must pass.

- [ ] **Step 6:** Commit:
```bash
cd /Users/anarchistsid/GenLab/dashboard && git add -A frontend/src/api/ frontend/src/hooks/ && git commit -m "feat(dashboard): add types, hooks, and client methods for MC upgrade"
```

---

## Task 5: Frontend — Fix KPI Hero, Top Post, Channel Strip

Fix the 3 most critical data issues.

**Files:**
- Modify: `frontend/src/views/mission-control/KpiHero.tsx`
- Modify: `frontend/src/views/mission-control/TopPostSpotlight.tsx`
- Modify: `frontend/src/views/mission-control/ChannelStrip.tsx`

- [ ] **Step 1:** In `KpiHero.tsx`:
- Wire `stats.reach` to `overviewData.global.total_reach`
- Wire engagement card to `overviewData.global.total_likes` (rename label from "ENGAGEMENT" to "TOTAL LIKES")
- Fix health %: use `configured_platform_count` as denominator instead of total platforms
- Change delta text for PUBLISHED TODAY to show `${stats.published} posts` instead of "X remaining"

- [ ] **Step 2:** In `TopPostSpotlight.tsx`:
- Remove `FALLBACK_POST` constant
- Use `topPost.hook_text || topPost.title || "Untitled"` for the hook display
- Show `EmptyState` when no posts exist
- Handle the case where `posts` is returned as `{ posts: [...] }` (the API wraps in data.posts)

- [ ] **Step 3:** In `ChannelStrip.tsx`:
- Remove `generateSparkData()` function
- Get sparkline data from `useCrossNicheOverview()` — access `data.niche_daily_reach[ch.id]`
- Map `dailyReach` array to numbers for `MiniSparkline`
- Replace "X followers" with per-niche total reach from analytics (or show `published_today` count more prominently if no reach)
- Show empty sparkline gracefully when no data (pass empty array to MiniSparkline)

- [ ] **Step 4:** Run `npm run build` — must pass.

- [ ] **Step 5:** Commit:
```bash
cd /Users/anarchistsid/GenLab/dashboard && git add -A frontend/src/views/mission-control/ && git commit -m "fix(dashboard): wire KPI Hero, Top Post, Channel Strip to real data"
```

---

## Task 6: Frontend — Fix Content Quality, Trend Radar, Engagement Feed

Fix the remaining fake data cards.

**Files:**
- Modify: `frontend/src/views/mission-control/ContentQuality.tsx`
- Modify: `frontend/src/views/mission-control/TrendRadar.tsx`
- Modify: `frontend/src/views/mission-control/EngagementFeed.tsx`

- [ ] **Step 1:** In `ContentQuality.tsx`:
- Remove TODO comment
- Import `useQualityStats` from `@/hooks/use-quality-stats`
- Replace hardcoded `STATS` with data from the hook:
  - "Hooks generated" → `data.hooks_generated`
  - "QC rejected" → `data.qc_failed`
  - "Videos fixed" → `data.videos_fixed`
  - "QC pass rate" → computed from `data.qc_passed / data.qc_total * 100`
- Show loading shimmer when `isLoading`

- [ ] **Step 2:** In `TrendRadar.tsx`:
- Remove `FALLBACK_TRENDS` constant
- Remove `MultiplierBadge` component entirely
- Remove the fake multiplier calculation (`3.5 - items.length * 0.4`)
- Show trends as keyword + niche color dot (use `getNicheInfo(nicheId).hex` for the dot color)
- Show `EmptyState` when no trends data instead of fake fallback

- [ ] **Step 3:** In `EngagementFeed.tsx`:
- Remove `FALLBACK_COMMENTS` constant
- Remove `estimateToxicity()` function and `ToxicityBadge` usage (unreliable heuristic)
- Simplify response parsing from 10-line defensive parser to: `const comments: EngagementComment[] = (resp.data as EngagementComment[] | undefined) ?? [];`
- Show proper empty state when no comments: "No comments yet — engagement appears 2-6h after publishing"

- [ ] **Step 4:** Run `npm run build` — must pass.

- [ ] **Step 5:** Commit:
```bash
cd /Users/anarchistsid/GenLab/dashboard && git add -A frontend/src/views/mission-control/ && git commit -m "fix(dashboard): replace fake Content Quality, Trend Radar, Engagement Feed with real data"
```

---

## Task 7: Frontend — Fix architecture issues + add revenue

Fix manual fetch patterns, wrong constants, and add revenue display.

**Files:**
- Modify: `frontend/src/views/mission-control/PublishingHealth.tsx`
- Modify: `frontend/src/views/mission-control/AlertBanner.tsx`
- Modify: `frontend/src/views/mission-control/PipelineCountdowns.tsx`
- Modify: `frontend/src/views/mission-control/MonetisationCompact.tsx`

- [ ] **Step 1:** In `PublishingHealth.tsx`:
- Replace `useState` + `useEffect` fetch with `usePublishingMetrics()` hook
- Replace local `PLATFORM_LABELS` with import from `@/lib/platforms`
- Add loading/error states

- [ ] **Step 2:** In `AlertBanner.tsx` (the MC-specific one):
- Replace `useState` + `useEffect` + `setInterval` with `usePublishingAlerts()` hook
- Remove manual interval management

- [ ] **Step 3:** In `PipelineCountdowns.tsx`:
- Replace hardcoded `SCHEDULES` array with niche registry:
```tsx
import { getNicheInfo } from "@/niches/registry";
const SCHEDULES = [
  { id: "ai_creators", utcHour: 2, utcMinute: 30 },
  { id: "gaming", utcHour: 4, utcMinute: 0 },
  { id: "anime", utcHour: 6, utcMinute: 0 },
  { id: "movies", utcHour: 8, utcMinute: 0 },
  { id: "sports", utcHour: 10, utcMinute: 0 },
].map((s) => {
  const info = getNicheInfo(s.id);
  return { ...s, name: info.shortLabel, accent: info.hex };
});
```

- [ ] **Step 4:** In `MonetisationCompact.tsx`:
- Add affiliate click data from `useRevenueSummary()` hook
- Show "X clicks today · ₹Y est. revenue" below the progress bars
- Or add a small row at the bottom: `5 clicks today | ₹650 est. 30d`

- [ ] **Step 5:** Run `npm run build` — must pass.

- [ ] **Step 6:** Rebuild for served dashboard + restart:
```bash
cd /Users/anarchistsid/GenLab/dashboard/frontend && npm run build
launchctl kickstart -k gui/$(id -u)/com.genlab.review-server
```

- [ ] **Step 7:** Commit:
```bash
cd /Users/anarchistsid/GenLab/dashboard && git add -A frontend/src/ && git commit -m "fix(dashboard): convert manual fetches to React Query, fix constants, add revenue display"
```

---

## Task 8: Final verification

- [ ] **Step 1:** Verify all quality gates:
```bash
cd /Users/anarchistsid/GenLab/dashboard/frontend
echo "=== Fake data constants ===" && grep -rn "FALLBACK_POST\|FALLBACK_COMMENTS\|FALLBACK_TRENDS\|generateSparkData\|TODO.*Replace" src/views/mission-control/ --include="*.tsx" | wc -l
echo "=== Manual useEffect fetches ===" && grep -rn "useEffect.*\n.*metrics\.publishing\|useEffect.*\n.*alerts\.publishing" src/views/mission-control/ --include="*.tsx" | wc -l
echo "=== estimateToxicity ===" && grep -rn "estimateToxicity" src/views/mission-control/ --include="*.tsx" | wc -l
echo "=== Local PLATFORM_LABELS ===" && grep -rn "const PLATFORM_LABELS" src/views/mission-control/ --include="*.tsx" | wc -l
echo "=== Hardcoded accent in countdowns ===" && grep -rn '#3b82f6\|#F5EDD6.*accent' src/views/mission-control/PipelineCountdowns.tsx | wc -l
```
All should be 0.

- [ ] **Step 2:** Visual verification at `http://localhost:5151`:
- KPI Hero shows real reach (~620K) and likes (~37K), not "—"
- Top Post shows actual hook text, not "Top performing post"
- Channel Strip sparklines show real trends, not random waves
- Content Quality shows real QC stats, not "—"
- Trend Radar shows keywords without fake multiplier badges
- Engagement Feed shows real comments or clean empty state
- MonetisationCompact shows click/revenue data

- [ ] **Step 3:** Final commit updating parent repo:
```bash
cd /Users/anarchistsid/GenLab
git add dashboard
git commit -m "feat(dashboard): Mission Control upgrade — all fake data replaced with real metrics"
```
