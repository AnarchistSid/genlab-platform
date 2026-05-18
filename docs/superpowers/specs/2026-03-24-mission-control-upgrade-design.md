# Mission Control Upgrade

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Mission Control truthful (eliminate all fake/hardcoded data) and then redesign it as a comprehensive command center that surfaces everything at a glance — publishing status, engagement, pending actions, monetisation, learning progress, and system health.

**Date:** 2026-03-24

---

## 1. Problem Statement

Mission Control has 13 bento cards, but **5 show fake or broken data**, **2 use manual fetch patterns instead of React Query**, and **several important metrics are available in the database but not surfaced**. The result is a landing page that can't be trusted.

### 1.1 Fake/Broken Data (Critical)

| Card | Issue | Severity |
|------|-------|----------|
| **KPI Hero: TOTAL REACH** | Always "—" (null). Backend overview API has no reach field, but analytics table has 620,709 total reach. | Critical |
| **KPI Hero: ENGAGEMENT** | Always "—" (null). Same gap — analytics table has 37,112 likes + 306 comments but overview API doesn't aggregate them. | Critical |
| **Channel Strip sparklines** | `generateSparkData()` creates random ascending numbers. Not real data. | Critical |
| **Channel Strip followers** | Always "—" (null). `audience_snapshots` table exists but has no follower data populated. | Critical |
| **Content Quality** | All 4 stats show "—". Hardcoded with `TODO: Replace with real API data`. No backend endpoint exists. | Critical |
| **Trend Radar multipliers** | Fabricated: `3.5 - items.length * 0.4` produces fake decreasing values (3.5x, 3.1x, 2.7x). Trends API returns no multiplier data. | High |
| **Top Post Spotlight hook** | Shows "Top performing post" instead of actual hook text. API doesn't join analytics with blueprints. | High |
| **Engagement Feed fallback** | Has 5 hardcoded fake comments ("GTA 6 trailer goes crazy bro") shown without any "sample data" indicator. | Medium |
| **Published today count** | Shows "5" (unique niches) but actual success publishes today is 17 (across platforms). Misleading. | Medium |
| **System Health %** | Counts unconfigured platforms (tiktok, x_twitter) as failures. 4/6 = 67% is misleading — should be 4/4 = 100% of configured. | Medium |
| **PipelineCountdowns accents** | ai_creators uses `#3b82f6` (blue) instead of correct `#00D4FF`. Doesn't use niche registry. | Low |

### 1.2 Architecture Issues

| Issue | Component | Fix |
|-------|-----------|-----|
| Manual `useEffect` + `setState` fetch | `PublishingHealth.tsx` | Convert to React Query hook |
| Manual `useEffect` + `setInterval` fetch | `AlertBanner.tsx` | Convert to React Query hook |
| Defensive response parser (dead code) | `EngagementFeed.tsx:30-38` | Simplify to `resp.data ?? []` |
| Duplicate `PLATFORM_LABELS` constant | `PublishingHealth.tsx:19-26` | Use `@/lib/platforms` |

### 1.3 Missing Data (Available but Not Surfaced)

| Metric | Source | Value | Currently |
|--------|--------|-------|-----------|
| Affiliate clicks (today/7d) | `revenue/summary` API | 5 today, 61 7d | Not shown |
| Estimated revenue 30d | `revenue/summary` API | ₹650 | Not shown |
| Publishing success rate per niche | `metrics/publishing` API | gaming:100%, movies:35% | Not shown on MC |
| Failed publishes today | `publishing_analytics` table | 8 | Not shown |
| Per-niche reach | `analytics` table aggregatable | ai:611K, gaming:5.5K, etc. | Random fake sparkline |
| QC pass rate | Run report `metrics.qc` | 100% (latest) | Hardcoded "—" |
| Video validation stats | Run report `metrics.video_validation` | 5 passed, 1 fixed | Not shown |
| Error rate 24h | `health/detailed` API | 0.24% | Not shown |
| Disk usage | `health/detailed` API | 61% | Not shown |
| LaunchAgent status | `health/detailed` API | 6 running, 17 stopped | Not shown |

## 2. Scope

**Phase 1 — Data Truthfulness (this spec):**
- Fix all 11 fake/broken data issues listed in §1.1
- Fix all 4 architecture issues in §1.2
- Surface available-but-missing metrics from §1.3
- Backend API fixes (3 endpoint modifications, 2 new endpoints)
- Frontend component fixes (13 files)

**Phase 2 — Layout Redesign (separate spec, builds on Phase 1):**
- Rethink card hierarchy and information architecture
- Add quick actions (approve posts, trigger pipeline)
- Add click-to-navigate on all cards
- Add time-of-day awareness
- Responsive layout improvements

**Out of scope:**
- Follower count population (requires Instagram/YouTube API fetcher — separate infrastructure)
- Real trend multipliers (would need pytrends integration returning actual search volume ratios)

## 3. Architecture

### 3.1 Backend Changes

#### 3.1.1 Modify: `/api/v1/cross-niche/overview` (overview.py)

Add aggregate engagement data to the `global` section of the response. Query the analytics table directly:

```python
# Add to _build_overview() in overview.py
# After existing niche/schedule computation, before return:

# Aggregate engagement from analytics table
total_reach = 0
total_likes = 0
total_comments = 0
try:
    import psycopg
    from psycopg.rows import dict_row
    dsn = os.environ.get("DATABASE_URL", "")
    if dsn:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            row = conn.execute("""
                SELECT
                    COALESCE(SUM(COALESCE((extra->>'reach')::numeric, 0)), 0)::bigint as total_reach,
                    COALESCE(SUM(COALESCE((extra->>'likes')::numeric, 0)), 0)::bigint as total_likes,
                    COALESCE(SUM(COALESCE((extra->>'comments')::numeric, 0)), 0)::bigint as total_comments
                FROM analytics
                WHERE niche_id NOT LIKE 'rls_test%%' AND niche_id NOT LIKE 'test_%%'
            """).fetchone()
            if row:
                total_reach = row["total_reach"]
                total_likes = row["total_likes"]
                total_comments = row["total_comments"]
except Exception as e:
    logger.debug("analytics aggregate failed: %s", e)
```

Add to the returned `global` dict:
```python
"total_reach": total_reach,
"total_likes": total_likes,
"total_comments": total_comments,
```

Also add per-niche daily reach for sparklines (last 14 days):
```python
# Per-niche daily reach for Channel Strip sparklines
niche_daily_reach = {}
try:
    if dsn:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            rows = conn.execute("""
                SELECT niche_id, collected_at::date as day,
                    COALESCE(SUM(COALESCE((extra->>'reach')::numeric, 0)), 0)::bigint as daily_reach
                FROM analytics
                WHERE niche_id NOT LIKE 'rls_test%%' AND niche_id NOT LIKE 'test_%%'
                    AND collected_at >= NOW() - INTERVAL '14 days'
                GROUP BY niche_id, day
                ORDER BY day
            """).fetchall()
            for row in rows:
                nid = row["niche_id"]
                niche_daily_reach.setdefault(nid, []).append({
                    "date": row["day"].isoformat(),
                    "reach": row["daily_reach"],
                })
except Exception as e:
    logger.debug("niche daily reach failed: %s", e)
```

Add `"niche_daily_reach": niche_daily_reach` to the per-niche data or global section.

Also fix the `platform_health` percentage: only count platforms where status != "not_configured" in the denominator.

#### 3.1.2 Modify: `/api/v1/analytics/top-posts` (analytics.py)

Join with `publishing_analytics` → `blueprints` to include hook text:

```python
# After building the posts list from analytics table, enrich with hook text:
if dsn:
    post_ids = [p["post_id"] for p in posts[:20]]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        rows = conn.execute("""
            SELECT pa.post_id, b.hook, b.hook_text, b.title
            FROM publishing_analytics pa
            JOIN blueprints b ON pa.blueprint_id = b.id
            WHERE pa.post_id = ANY(%s)
        """, (post_ids,)).fetchall()
        hook_map = {r["post_id"]: r for r in rows}
        for p in posts:
            linked = hook_map.get(p["post_id"], {})
            p["hook_text"] = linked.get("hook") or linked.get("hook_text") or ""
            p["title"] = linked.get("title") or ""
```

#### 3.1.3 Create: `/api/v1/pipeline/quality-stats` (pipeline.py)

New endpoint that aggregates QC stats from the most recent run report per niche:

```python
@bp.route("/quality-stats", methods=["GET"])
def quality_stats():
    """Aggregate content quality stats from latest pipeline run reports."""
    stats = {
        "hooks_generated": 0,
        "qc_passed": 0,
        "qc_failed": 0,
        "qc_total": 0,
        "videos_validated": 0,
        "videos_fixed": 0,
        "dedup_rejected": 0,
    }

    runs_dir = GENLAB_ROOT / ".tmp" / "runs"
    if not runs_dir.exists():
        return api_success(data=stats)

    # Get latest run report per niche
    seen_niches = set()
    for run_dir in sorted(runs_dir.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True):
        report = run_dir / "run_report.json"
        if not report.exists():
            continue
        data = json.loads(report.read_text())
        niche = data.get("niche_id", "")
        if niche in seen_niches:
            continue
        seen_niches.add(niche)

        m = data.get("metrics", {})
        stats["hooks_generated"] += m.get("stories_count", 0)
        qc = m.get("qc", {})
        stats["qc_passed"] += qc.get("passed", 0)
        stats["qc_failed"] += qc.get("failed", 0)
        stats["qc_total"] += qc.get("total", 0)
        vv = m.get("video_validation", {})
        stats["videos_validated"] += vv.get("passed", 0)
        stats["videos_fixed"] += vv.get("fixed", 0)

        if len(seen_niches) >= 5:
            break

    return api_success(data=stats)
```

#### 3.1.4 Create: `/api/v1/metrics/revenue-compact` or reuse existing `revenue/summary`

The `revenue/summary` endpoint already returns everything needed:
```json
{
  "clicks": { "today": 5, "last_7d": 61, "last_30d": 61 },
  "estimated_revenue_inr_30d": 650,
  "by_network": { "amazon_us": 52, "cuelinks": 9 }
}
```

No new endpoint needed — just wire the frontend to consume it.

### 3.2 Frontend Changes

#### 3.2.1 Fix KPI Hero (`KpiHero.tsx`)

**Current:** Queries only `useCrossNicheOverview()` which has no reach/engagement.

**Fix:** Also query analytics data and wire reach + likes to the KPI cards.

```tsx
// Add to KpiHero:
const { data: overviewData } = useCrossNicheOverview();

const stats = useMemo(() => {
  if (!overviewData) return { reach: null, likes: null, published: 0, healthPct: 100 };

  const g = overviewData.global;
  const published = g.total_published_today;

  // Health: only count configured platforms
  const healthStatuses = Object.entries(g.platform_health);
  const configured = healthStatuses.filter(([, s]) => s !== "not_configured");
  const okCount = configured.filter(([, s]) => s === "ok").length;
  const healthPct = configured.length > 0 ? Math.round((okCount / configured.length) * 100) : 100;

  return {
    reach: g.total_reach ?? null,
    likes: g.total_likes ?? null,
    published,
    healthPct,
  };
}, [overviewData]);
```

Change KPI cards:
- TOTAL REACH → show `stats.reach` (now populated from overview API)
- ENGAGEMENT → rename to "TOTAL LIKES", show `stats.likes`
- SYSTEM HEALTH → use fixed `healthPct` (excludes unconfigured)

#### 3.2.2 Fix Top Post Spotlight (`TopPostSpotlight.tsx`)

**Current:** Shows "Top performing post" because API returns no hook_text.

**Fix:** After backend adds hook_text to top-posts API, display it:

```tsx
const hookText = topPost.hook_text || topPost.title || "Top performing post";
```

Remove the `FALLBACK_POST` constant and show `EmptyState` when no posts exist instead of fake data.

#### 3.2.3 Fix Channel Strip (`ChannelStrip.tsx`)

**Current:** `generateSparkData()` creates random numbers. Followers always null.

**Fix:** Use `niche_daily_reach` from the enhanced overview API for real sparklines:

```tsx
const nicheData = data?.niches.find((n) => n.id === ch.id);
const dailyReach = data?.niche_daily_reach?.[ch.id] ?? [];
const sparkData = dailyReach.map((d) => d.reach);
```

For followers: show the per-niche total reach instead of follower count (followers aren't populated yet). Change label from "X followers" to "620K reach" or show nothing until follower data exists.

#### 3.2.4 Fix Content Quality (`ContentQuality.tsx`)

**Current:** Hardcoded "—" for all 4 stats.

**Fix:** Create a hook `useQualityStats()` that queries the new `/api/v1/pipeline/quality-stats` endpoint:

```tsx
const { data } = useQualityStats();

const STATS = [
  { icon: ShieldCheck, label: "Hooks generated", value: data?.hooks_generated ?? "—", color: "var(--color-blue)" },
  { icon: Ban, label: "QC rejected", value: data?.qc_failed ?? "—", color: "var(--color-red)" },
  { icon: Copy, label: "Videos fixed", value: data?.videos_fixed ?? "—", color: "var(--color-amber)" },
  { icon: CheckCircle2, label: "QC pass rate", value: data?.qc_total ? `${Math.round((data.qc_passed / data.qc_total) * 100)}%` : "—", color: "var(--color-green)" },
];
```

#### 3.2.5 Fix Trend Radar (`TrendRadar.tsx`)

**Current:** Fake multipliers (`3.5 - items.length * 0.4`).

**Fix:** Remove multiplier badges entirely — the trends API doesn't provide volume data. Show trends as a simple keyword list with niche color dots:

```tsx
items.push({
  keyword: kw,
  nicheId,        // which niche this trend belongs to
});
// Remove: multiplier: 3.5 - items.length * 0.4
```

Replace `MultiplierBadge` with a small niche color dot to indicate which channel the trend is for.

#### 3.2.6 Fix Engagement Feed (`EngagementFeed.tsx`)

**Current:** Has fallback fake comments and a defensive response parser.

**Fix:**
1. Delete `FALLBACK_COMMENTS` array entirely
2. Simplify response parsing: `const comments = resp.data ?? [];` (the API always returns unwrapped array)
3. Show `EmptyState` when no comments exist
4. Remove `estimateToxicity()` regex heuristic — either use backend toxicity data or remove badges

#### 3.2.7 Fix Publishing Health (`PublishingHealth.tsx`)

**Current:** Manual `useEffect` + `setState` fetch.

**Fix:** Convert to React Query:

```tsx
const { data } = useQuery({
  queryKey: ["metrics", "publishing"],
  queryFn: () => metrics.publishing(),
  staleTime: 60_000,
  refetchInterval: 60_000,
});
```

Also replace local `PLATFORM_LABELS` with import from `@/lib/platforms`.

#### 3.2.8 Fix Alert Banner (`AlertBanner.tsx` — MC version)

**Current:** Manual `useEffect` + `setInterval` fetch.

**Fix:** Convert to React Query:

```tsx
const { data } = useQuery({
  queryKey: ["alerts", "publishing"],
  queryFn: () => alerts.publishing(),
  staleTime: 60_000,
  refetchInterval: 60_000,
});
```

#### 3.2.9 Fix Pipeline Countdowns (`PipelineCountdowns.tsx`)

**Current:** Hardcoded accent colors that don't match niche registry.

**Fix:** Use `getNicheInfo()`:

```tsx
const SCHEDULES = [
  "ai_creators", "gaming", "anime", "movies", "sports"
].map((id) => {
  const info = getNicheInfo(id);
  return { id, name: info.shortLabel, accent: info.hex, utcHour: ..., utcMinute: ... };
});
```

#### 3.2.10 Add Revenue Card (new)

New card showing affiliate click data from `/api/v1/revenue/summary`:

```tsx
export function RevenueCompact() {
  const { data } = useQuery({
    queryKey: ["revenue", "summary"],
    queryFn: () => revenue.summary(),
    staleTime: 300_000,
  });
  // Show: clicks today, clicks 7d, est. revenue
}
```

Add to bento grid in a new area, or combine with MonetisationCompact.

### 3.3 Type Updates

Add to `CrossNicheOverviewResponse` in `api/types.ts`:

```tsx
global: {
  // ... existing fields ...
  total_reach?: number;
  total_likes?: number;
  total_comments?: number;
};
niche_daily_reach?: Record<string, Array<{ date: string; reach: number }>>;
```

Add `hook_text` and `title` to `TopPost` type:

```tsx
export interface TopPost {
  // ... existing fields ...
  hook_text?: string;
  title?: string;
}
```

Add quality stats type:

```tsx
export interface QualityStats {
  hooks_generated: number;
  qc_passed: number;
  qc_failed: number;
  qc_total: number;
  videos_validated: number;
  videos_fixed: number;
  dedup_rejected: number;
}
```

### 3.4 New Hooks

```tsx
// hooks/use-quality-stats.ts
export function useQualityStats() {
  return useQuery<QualityStats>({
    queryKey: ["pipeline", "quality-stats"],
    queryFn: () => get<QualityStats>("/pipeline/quality-stats"),
    staleTime: 300_000,
  });
}

// hooks/use-revenue.ts
export function useRevenueSummary() {
  return useQuery({
    queryKey: ["revenue", "summary"],
    queryFn: () => revenue.summary(),
    staleTime: 300_000,
  });
}
```

## 4. File Changes Manifest

### 4.1 Backend (3 modified, 1 new)

| File | Change |
|------|--------|
| `server/api/overview.py` | Add `total_reach`, `total_likes`, `total_comments` to global. Add `niche_daily_reach` for sparklines. Fix `platform_health` to exclude unconfigured from denominator. |
| `server/api/analytics.py` | Modify `top_posts()` to join `publishing_analytics` → `blueprints` for hook_text/title. |
| `server/api/pipeline.py` | Add `/quality-stats` endpoint reading latest run reports. |
| `server/api/query-keys.ts` | Add quality-stats and revenue keys. |

### 4.2 Frontend (13 modified, 3 new)

| File | Change |
|------|--------|
| `api/types.ts` | Add `total_reach/likes/comments` to overview type, `hook_text/title` to TopPost, new `QualityStats` |
| `api/client.ts` | Add `pipeline.qualityStats()` method |
| `hooks/use-quality-stats.ts` | NEW — React Query hook for quality stats |
| `hooks/use-revenue.ts` | NEW — React Query hook for revenue summary |
| `views/mission-control/KpiHero.tsx` | Wire reach + likes from overview API, fix health % to exclude unconfigured |
| `views/mission-control/TopPostSpotlight.tsx` | Display `hook_text` from enriched API, remove `FALLBACK_POST` |
| `views/mission-control/ChannelStrip.tsx` | Replace `generateSparkData()` with real `niche_daily_reach` data, replace follower label |
| `views/mission-control/ContentQuality.tsx` | Wire to `/pipeline/quality-stats` endpoint, remove TODO |
| `views/mission-control/TrendRadar.tsx` | Remove fake multiplier badges, show niche dots instead, remove `FALLBACK_TRENDS` |
| `views/mission-control/EngagementFeed.tsx` | Delete `FALLBACK_COMMENTS`, simplify response parsing, remove `estimateToxicity` |
| `views/mission-control/PublishingHealth.tsx` | Convert to React Query, use platform registry |
| `views/mission-control/AlertBanner.tsx` | Convert to React Query |
| `views/mission-control/PipelineCountdowns.tsx` | Use `getNicheInfo()` for accent colors |
| `views/mission-control/MonetisationCompact.tsx` | Add revenue data (clicks today, est. revenue) OR create new `RevenueCompact.tsx` |
| `views/mission-control/MissionControl.tsx` | Add revenue card to bento grid if separate component |

## 5. Quality Gates

- Zero hardcoded fake data in any MC component
- Zero `FALLBACK_*` constants with fake display data
- Zero `generateSparkData()` or fabricated multipliers
- Zero manual `useEffect` + `setState` fetch patterns (all React Query)
- Zero local `PLATFORM_LABELS` constants (all from registry)
- KPI Hero shows real reach and likes from database
- Top Post shows actual hook text from blueprint join
- Channel Strip sparklines show real per-niche daily reach
- Content Quality shows real QC stats from run reports
- `npm run build` passes with zero errors
- Visual parity on cards that had real data (no layout changes in Phase 1)

## 6. Migration Order

1. Backend: Add engagement aggregates to overview API
2. Backend: Add hook_text join to top-posts API
3. Backend: Create quality-stats endpoint
4. Frontend: Update types + add new hooks
5. Frontend: Fix KpiHero (reach + likes + health %)
6. Frontend: Fix TopPostSpotlight (hook text, remove fallback)
7. Frontend: Fix ChannelStrip (real sparklines, remove fake data)
8. Frontend: Fix ContentQuality (wire to quality-stats)
9. Frontend: Fix TrendRadar (remove fake multipliers)
10. Frontend: Fix EngagementFeed (remove fallbacks + dead parser)
11. Frontend: Fix PublishingHealth (React Query + platform registry)
12. Frontend: Fix AlertBanner (React Query)
13. Frontend: Fix PipelineCountdowns (niche registry colors)
14. Frontend: Add revenue display
15. Build + visual verification
