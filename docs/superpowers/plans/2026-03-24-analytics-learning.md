# Analytics + Learning Upgrade — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix analytics showing zero reach (backend date filter bug), add cross-niche comparison, make Learning page human-readable.

**Architecture:** Backend-first — fix the critical date filter bug, then enhance frontend with new views and human-readable insights.

**Tech Stack:** Backend: Flask, psycopg3, PostgreSQL. Frontend: React 19, TanStack Query 5, Tailwind CSS v4, Recharts 3.

**Spec:** `docs/superpowers/specs/2026-03-24-analytics-learning-design.md`

**Backend dir:** `/Users/anarchistsid/GenLab/dashboard/server`
**Frontend dir:** `/Users/anarchistsid/GenLab/dashboard/frontend`
**Build:** `cd /Users/anarchistsid/GenLab/dashboard/frontend && npm run build`

---

## Task 1: Backend — Fix analytics overview date filter

**THE critical fix.** The analytics overview returns zero reach for everything because `_build_overview()` filters analytics records by `published_at` (when the post went live — often 2-3 weeks ago) instead of `collected_at` (when metrics were fetched — within the last 7 days).

**Files:**
- Modify: `server/api/analytics.py` — `_build_overview()` function (line ~210)

- [ ] **Step 1:** Read `server/api/analytics.py` lines 210-230 where `_build_overview()` filters analytics records.

- [ ] **Step 2:** Change line ~220 from:
```python
dt = _parse_datetime(f.get("published_at", ""))
```
to:
```python
# Use collected_at/fetched_at for window filtering — these represent when
# metrics were actually fetched, not when the post was published. A 3-week-old
# post with metrics collected yesterday IS relevant to the "7-day" view.
dt = _parse_datetime(f.get("collected_at") or f.get("fetched_at", ""))
```

Also check: `f.get("reach", 0)` reads from the `extra` JSONB (where reach is stored). Verify this returns values > 0 by checking a sample record in the debugger or adding a log line.

- [ ] **Step 3:** Restart server and verify:
```bash
launchctl kickstart -k gui/$(id -u)/com.genlab.review-server
sleep 3
AUTH="admin:***REMOVED***"
curl -s -u "$AUTH" "http://localhost:5151/api/v1/analytics/overview?window=7d&niche_id=all" | python3 -c "
import json, sys
d = json.load(sys.stdin).get('data', {})
s = d.get('summary', {})
print(f'total_reach: {s.get(\"total_reach\")} (should be ~620K, was 0)')
print(f'total_posts: {s.get(\"total_posts\")}')
print(f'avg_engagement: {s.get(\"avg_engagement_rate\")}')
for plat, m in d.get('by_platform', {}).items():
    print(f'  {plat}: reach={m.get(\"reach\")}, posts={m.get(\"posts\")}')
"
```
Expected: `total_reach` should now be > 0 (approximately 620,000).

- [ ] **Step 4:** Commit:
```bash
cd /Users/anarchistsid/GenLab/dashboard && git add server/api/analytics.py && git commit -m "fix(api): analytics overview date filter — use collected_at instead of published_at"
```

---

## Task 2: Backend — Add cross-niche comparison endpoint

New endpoint that returns per-niche analytics + publishing metrics in a single call.

**Files:**
- Modify: `server/api/analytics.py` — add new route

- [ ] **Step 1:** Add a `/cross-niche` route that:
  - Queries the analytics table grouped by niche_id for totals (reach, likes, comments, posts)
  - Queries publishing metrics per niche (success rate)
  - Queries affiliate clicks per niche from revenue data
  - Returns a dict keyed by niche_id

```python
@bp.route("/cross-niche", methods=["GET"])
def cross_niche_analytics():
    """Per-niche analytics comparison — reach, likes, posts, success rate, clicks."""
    import psycopg
    from psycopg.rows import dict_row

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return api_error(message="DATABASE_URL not configured")

    result = {}
    try:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            # Engagement aggregates per niche
            rows = conn.execute("""
                SELECT niche_id,
                    COUNT(*) AS records,
                    COALESCE(SUM(COALESCE((extra->>'reach')::numeric, 0)), 0)::bigint AS total_reach,
                    COALESCE(SUM(COALESCE((extra->>'likes')::numeric, 0)), 0)::bigint AS total_likes,
                    COALESCE(SUM(COALESCE((extra->>'comments')::numeric, 0)), 0)::bigint AS total_comments
                FROM analytics
                WHERE niche_id NOT LIKE 'rls_test%%' AND niche_id NOT LIKE 'test_%%'
                GROUP BY niche_id ORDER BY total_reach DESC
            """).fetchall()
            for r in rows:
                result[r["niche_id"]] = {
                    "total_reach": int(r["total_reach"]),
                    "total_likes": int(r["total_likes"]),
                    "total_comments": int(r["total_comments"]),
                    "analytics_records": r["records"],
                }

            # Publishing success rates per niche
            pub_rows = conn.execute("""
                SELECT niche_id,
                    COUNT(*) FILTER (WHERE status = 'SUCCESS') AS success,
                    COUNT(*) FILTER (WHERE status IN ('FAILED', 'SKIPPED')) AS failed
                FROM publishing_analytics
                WHERE niche_id IS NOT NULL
                GROUP BY niche_id
            """).fetchall()
            for r in pub_rows:
                niche = r["niche_id"]
                result.setdefault(niche, {})
                total = r["success"] + r["failed"]
                result[niche]["publish_success"] = r["success"]
                result[niche]["publish_failed"] = r["failed"]
                result[niche]["publish_rate"] = round(r["success"] / total * 100) if total > 0 else 0

            # Affiliate clicks per niche
            click_rows = conn.execute("""
                SELECT niche_id, COUNT(*) AS clicks
                FROM affiliate_clicks
                WHERE created_at >= NOW() - INTERVAL '30 days'
                GROUP BY niche_id
            """).fetchall()
            for r in click_rows:
                niche = r["niche_id"]
                result.setdefault(niche, {})
                result[niche]["affiliate_clicks_30d"] = r["clicks"]

    except Exception as exc:
        logger.error("cross-niche analytics failed: %s", exc)
        return api_error(error=str(exc))

    return api_success(data=result)
```

- [ ] **Step 2:** Restart and verify:
```bash
launchctl kickstart -k gui/$(id -u)/com.genlab.review-server
sleep 3
curl -s -u "$AUTH" "http://localhost:5151/api/v1/analytics/cross-niche" | python3 -m json.tool | head -30
```

- [ ] **Step 3:** Commit:
```bash
cd /Users/anarchistsid/GenLab/dashboard && git add server/api/analytics.py && git commit -m "feat(api): add /analytics/cross-niche endpoint for niche comparison"
```

---

## Task 3: Frontend — Add types, client, hooks for new data

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/query-keys.ts`

- [ ] **Step 1:** In `types.ts`, add:
```tsx
export interface CrossNicheAnalytics {
  [nicheId: string]: {
    total_reach: number;
    total_likes: number;
    total_comments: number;
    analytics_records: number;
    publish_success?: number;
    publish_failed?: number;
    publish_rate?: number;
    affiliate_clicks_30d?: number;
  };
}
```

- [ ] **Step 2:** In `client.ts`, add to `analytics`:
```tsx
crossNiche: () => get<CrossNicheAnalytics>("/analytics/cross-niche"),
```

- [ ] **Step 3:** In `query-keys.ts`, add to `analytics`:
```tsx
crossNiche: () => ["analytics", "cross-niche"] as const,
```

- [ ] **Step 4:** Run `npm run build`, commit:
```bash
cd /Users/anarchistsid/GenLab/dashboard && git add -A frontend/src/api/ && git commit -m "feat(dashboard): add cross-niche analytics types + client"
```

---

## Task 4: Frontend — Add Compare tab to Analytics

Add a third tab showing cross-niche comparison with a table and bar charts.

**Files:**
- Modify: `frontend/src/views/analytics/Analytics.tsx`

- [ ] **Step 1:** Read `Analytics.tsx` to understand the existing tab system (currently "overview" and "top-posts").

- [ ] **Step 2:** Add a third tab option `"compare"` to `TabOption` type and tab bar.

- [ ] **Step 3:** Create a `CrossNicheComparison` component inside the file (or separate file):
```tsx
function CrossNicheComparison() {
  const { data, isLoading } = useQuery<CrossNicheAnalytics>({
    queryKey: queryKeys.analytics.crossNiche(),
    queryFn: () => analytics.crossNiche(),
    staleTime: 300_000,
  });

  if (isLoading) return <LoadingSkeleton variant="table" />;
  if (!data) return <EmptyState icon={BarChart3} title="No comparison data" />;

  const niches = Object.entries(data).sort((a, b) => b[1].total_reach - a[1].total_reach);
  const maxReach = Math.max(...niches.map(([, d]) => d.total_reach), 1);

  return (
    <ChartCard title="Cross-Niche Performance">
      <div className="data-table">
        <table>
          <thead>
            <tr>
              <th>Channel</th>
              <th className="text-right">Reach</th>
              <th className="text-right">Likes</th>
              <th className="text-right">Posts</th>
              <th className="text-right">Pub Rate</th>
              <th className="text-right">Clicks</th>
              <th style={{ width: "30%" }}>Reach Distribution</th>
            </tr>
          </thead>
          <tbody>
            {niches.map(([nicheId, d]) => {
              const info = getNicheInfo(nicheId);
              return (
                <tr key={nicheId}>
                  <td>
                    <span className="inline-flex items-center gap-2">
                      <span className="size-2 rounded-full" style={{ backgroundColor: info.hex }} />
                      {info.label}
                    </span>
                  </td>
                  <td className="text-right font-mono text-xs">{formatCompact(d.total_reach)}</td>
                  <td className="text-right font-mono text-xs">{formatCompact(d.total_likes)}</td>
                  <td className="text-right font-mono text-xs">{d.analytics_records}</td>
                  <td className="text-right font-mono text-xs">{d.publish_rate ?? 0}%</td>
                  <td className="text-right font-mono text-xs">{d.affiliate_clicks_30d ?? 0}</td>
                  <td>
                    <ProgressBar
                      value={(d.total_reach / maxReach) * 100}
                      color={info.hex}
                      height={6}
                      animated
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </ChartCard>
  );
}
```

- [ ] **Step 4:** Wire the "compare" tab content:
```tsx
{activeTab === "compare" && <CrossNicheComparison />}
```

- [ ] **Step 5:** Run `npm run build`, commit:
```bash
cd /Users/anarchistsid/GenLab/dashboard && git add frontend/src/views/analytics/ && git commit -m "feat(dashboard): add cross-niche comparison tab to Analytics"
```

---

## Task 5: Frontend — Rewrite Learning Overview for humans

Replace raw bandit stats with human-readable operational insights.

**Files:**
- Modify: `frontend/src/views/learning/LearningOverview.tsx`

- [ ] **Step 1:** Read the current file (201 lines).

- [ ] **Step 2:** Rewrite `StatusCard` to show human-readable status:
```tsx
// Instead of: "Thompson Sampling Active" / "LinUCB Active"
// Show: "Learning from X posts across Y niches"
// Plus: "The system is still exploring (needs Z more observations for contextual learning)"
//   OR: "Contextual learning active — the system now adapts content selection to time of day, niche, and content features"
```

- [ ] **Step 3:** Rewrite `SummaryStatsRow` to use human labels:
- "Rewards Computed: 24" → "Posts Analyzed: 24"
- "Avg Reward: 0.0764" → "Average Content Score: 7.6 / 100"
- "Max Reward: 0.4376" → "Best Content Score: 43.8 / 100"
- "Analytics Records: 50" → "Engagement Records: 50"

- [ ] **Step 4:** Rewrite `ThresholdsCard` with explanatory text:
- "LinUCB: 4/50 plays" → "Contextual Learning: 4 of 50 observations needed (8%). After 50, the system starts choosing content based on time-of-day, niche, and 10 other signals."
- "Hook Classifier: 24/100 examples" → "Hook Quality Predictor: 24 of 100 examples needed. After 100, the system pre-scores hooks before publishing."

- [ ] **Step 5:** Add a "What the AI Learned" card at the top that generates a natural language summary from bandit data:
```tsx
function AiLearnedCard({ data }: { data: LearningStatus }) {
  // Find best performing niche
  const nicheAvgs = Object.entries(data.bandit_arms).map(([niche, arms]) => {
    const active = arms.filter(a => a.n_plays > 0);
    const avg = active.length ? active.reduce((s, a) => s + a.mean, 0) / active.length : 0;
    return { niche, avg, bestArm: active.length ? active.reduce((b, a) => a.mean > b.mean ? a : b) : null };
  }).filter(n => n.avg > 0).sort((a, b) => b.avg - a.avg);

  let insight = "";
  if (nicheAvgs.length < 2) {
    insight = `The learning loop has analyzed ${data.rewards_computed} posts so far. All niches are still in exploration mode — the system is testing different content types equally before making optimization decisions.`;
  } else {
    const best = nicheAvgs[0];
    const label = getNicheInfo(best.niche).label;
    const armLabel = best.bestArm?.arm_id.replace(/_/g, " ") ?? "content";
    insight = `${label}'s "${armLabel}" content type is currently leading. The system has analyzed ${data.rewards_computed} posts across ${nicheAvgs.length} niches and is ${data.linucb_max_plays >= data.linucb_threshold ? "now using contextual signals (time, niche, content features) to optimize selections" : "still in exploration mode, collecting more data before optimizing"}.`;
  }

  return (
    <ChartCard title="What the AI Has Learned">
      <p className="text-sm text-text-secondary leading-relaxed">{insight}</p>
    </ChartCard>
  );
}
```

- [ ] **Step 6:** Run `npm run build`, commit:
```bash
cd /Users/anarchistsid/GenLab/dashboard && git add frontend/src/views/learning/ && git commit -m "feat(dashboard): rewrite Learning Overview with human-readable insights"
```

---

## Task 6: Final verification

- [ ] **Step 1:** Verify analytics shows real data:
```bash
AUTH="admin:***REMOVED***"
curl -s -u "$AUTH" "http://localhost:5151/api/v1/analytics/overview?window=7d&niche_id=all" | python3 -c "
import json, sys; d=json.load(sys.stdin).get('data',{}); s=d.get('summary',{})
print(f'reach={s.get(\"total_reach\")} likes_sum={sum(p.get(\"likes\",0) for p in d.get(\"by_platform\",{}).values())} posts={s.get(\"total_posts\")}')
"
```
Expected: reach > 0.

- [ ] **Step 2:** Verify cross-niche comparison:
```bash
curl -s -u "$AUTH" "http://localhost:5151/api/v1/analytics/cross-niche" | python3 -c "
import json, sys; d=json.load(sys.stdin).get('data',{})
for n, v in d.items(): print(f'  {n}: reach={v.get(\"total_reach\")} rate={v.get(\"publish_rate\",\"?\")}%')
"
```

- [ ] **Step 3:** Rebuild + restart:
```bash
cd /Users/anarchistsid/GenLab/dashboard/frontend && npm run build
launchctl kickstart -k gui/$(id -u)/com.genlab.review-server
```

- [ ] **Step 4:** Visual check at `http://localhost:5151`:
- Analytics page shows real reach, likes, engagement in KPIs and charts
- Compare tab shows all 5 niches side-by-side with reach bars
- Learning Overview shows "Posts Analyzed: 24", "Average Content Score: 7.6/100"
- "What the AI Learned" card shows natural language insight

- [ ] **Step 5:** Final commit:
```bash
cd /Users/anarchistsid/GenLab && git add dashboard && git commit -m "feat(dashboard): Analytics + Learning upgrade — real data, cross-niche comparison, human-readable insights"
```
