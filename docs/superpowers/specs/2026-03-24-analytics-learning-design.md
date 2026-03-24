# Analytics + Learning Upgrade

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Analytics show real engagement data (currently returns zero reach), add cross-niche comparison, make Learning page operationally useful (not just technical bandit stats), and connect both pages so insights drive action.

**Date:** 2026-03-24

---

## 1. Problem Statement

### 1.1 Analytics Page — Zero Reach (Critical)

The analytics overview endpoint returns `total_reach: 0` for all platforms and all niches, even though the `analytics` table has **620,709 total reach** and **37,112 likes**.

**Root cause:** The overview endpoint (`_build_overview()` in `analytics.py`) filters analytics records by `published_at >= cutoff` (7 days). But the top-performing posts were published on March 5 (19 days ago) — their metrics were collected on March 22 but `published_at` predates the window. The endpoint should use `collected_at` (when metrics were fetched) or include ALL records regardless of publish date, since engagement data is cumulative and always relevant.

Additionally:
- `summary.total_reach` computes from the filtered set (which is empty → 0)
- `by_platform` reach is all 0
- `time_series` shows all-zero data points
- `best_post` has 0 reach
- `avg_engagement_rate` is null

The Analytics page then displays these zeros faithfully — the frontend code is correct, the backend data is wrong.

### 1.2 Analytics Page — Not Actionable

Even when data is fixed:
- **No cross-niche comparison** — can filter by single niche or "all", but no side-by-side view
- **No "what changed" signal** — no week-over-week deltas, no trend arrows
- **No content type insights** — can't see which hooks/formats/topics perform best
- **No publishing success breakdown** — funnel shows pipeline stages but not platform success rates
- **Top Performers table** only shows 10 items with no sorting/filtering

### 1.3 Learning Page — Too Technical

The Learning page shows raw bandit internals:
- **Thompson Sampling alpha/beta** — meaningless to an operator
- **LinUCB threshold progress** — percentages toward a technical threshold
- **Reward values** (avg: 0.0764, max: 0.4376) — what do these numbers mean operationally?
- **No connection to content decisions** — can't see "because of learning, we're now choosing X over Y"
- **No visualization of what the system learned** — no "AI Creator content outperforms Gaming by 3x" insight
- **BanditArms tab** — shows raw arm IDs and alpha/beta values, not human-readable insights

### 1.4 Missing Connection Between Analytics + Learning

- Analytics shows "what happened" (reach, likes)
- Learning shows "what the system knows" (arm weights)
- **No bridge** — can't see "this content type got 3x more reach, AND the learning loop has shifted allocation toward it"

## 2. Scope

**In scope:**
- Fix analytics overview endpoint to return real reach/engagement data (backend)
- Add cross-niche comparison view to Analytics
- Add week-over-week deltas to KPI cards
- Add publishing success rate per niche to Analytics
- Make Learning Overview human-readable (translate bandit stats into operational insights)
- Add "What the AI Learned" summary card to Learning
- Connect top analytics performers to learning arm data

**Out of scope:**
- Real-time analytics streaming
- Historical analytics beyond 30 days
- A/B testing infrastructure
- Custom report builder

## 3. Architecture

### 3.1 Backend Fix: Analytics Overview Endpoint

**File:** `server/api/analytics.py` — `_build_overview()` function

**Problem:** Filters by `published_at >= cutoff`, excluding old posts with recent metrics.

**Fix:** Use `collected_at` instead of `published_at` for the date filter. The `collected_at` field (or `fetched_at` in extra JSONB) represents when the metric was actually fetched. For the "7d" window, we want "metrics collected in the last 7 days", not "posts published in the last 7 days".

```python
# Change line ~220-222 from:
dt = _parse_datetime(f.get("published_at", ""))
if not (dt and dt >= cutoff):
    continue

# To:
# Use collected_at for window filtering (when metric was fetched),
# NOT published_at (when the post went live). Engagement metrics
# are cumulative — a 3-week-old post with fresh metrics is relevant.
dt = _parse_datetime(f.get("collected_at") or f.get("fetched_at", ""))
if not (dt and dt >= cutoff):
    continue
```

Also ensure the `reach` field is read from the correct location. The analytics table stores data in `extra` JSONB with keys like `reach`, `likes`, `comments`. The `_cached_analytics_all()` function returns records with `fields` dict — verify `fields.reach` maps to `extra->>'reach'`.

**Verification:** After fix, `total_reach` should be ~620K, not 0.

### 3.2 Backend Enhancement: Cross-Niche Comparison Data

Add a new endpoint or enhance the overview to return per-niche aggregates in a single call:

```python
@bp.route("/cross-niche", methods=["GET"])
def cross_niche_analytics():
    """Per-niche analytics comparison."""
    # For each active niche, return: total_reach, total_likes, total_posts, avg_engagement, publishing_success_rate
```

Or reuse the existing overview by making multiple calls on the frontend (less efficient but simpler).

### 3.3 Frontend: Analytics Cross-Niche Tab

Add a third tab to Analytics: "Overview | Top Posts | **Compare**"

The Compare tab shows a side-by-side view:

| Metric | BB | CR | CW | SR | FD |
|--------|-----|-----|-----|-----|-----|
| Total Reach | 611K | 5.5K | 1K | 2K | 1K |
| Total Likes | 36.9K | 66 | 19 | 92 | 12 |
| Posts | 100 | 96 | 22 | 23 | 20 |
| Pub Success | 64% | 100% | 54% | 35% | 50% |
| Affiliate Clicks | 16 | 12 | 8 | 11 | 14 |

With bar charts for visual comparison.

### 3.4 Frontend: Analytics KPI Deltas

Add week-over-week change indicators to the KPI cards:
- Query both current window and previous window
- Show `↑ 12%` or `↓ 5%` delta badges on each KPI

### 3.5 Frontend: Learning Overview — Human-Readable

Replace raw bandit stats with operational insights:

**Current:** "Thompson Sampling Active · 24 rewards"
**New:** "AI is learning from 24 published posts. BlackboxBrief content currently wins — its engagement rate is 3x higher than average."

**Current:** ThresholdsCard with LinUCB/XGB/Config progress bars
**New:** "Learning Progress" card with:
- "24 of 50 observations needed for contextual learning (48%)"
- "After 50 observations, the system switches from exploration to exploitation"

**Current:** SummaryStatsRow with raw avg_reward (0.0764)
**New:** "Reward Summary" with human labels:
- "Average content quality score: 7.6/100"
- "Best performing content scored 43.8/100"
- "27 posts have completed the full feedback cycle"

**Current:** FeedbackPipeline with awaiting_6h/24h/48h/168h counts
**New:** "Feedback Collection" timeline visualization showing posts flowing through the pipeline stages

### 3.6 Frontend: Learning — "What the AI Learned" Card

New card that translates bandit arm data into actionable insights:

```
What the AI Has Learned:

• BlackboxBrief (AI Creators): "tool_demo" content type has the highest
  engagement. The system has allocated 60% of selections to this type.

• All 5 niches are still in exploration mode (< 50 observations each).
  The system is testing different content types equally.

• Content posted between 12:00-14:00 IST performs 20% better than
  morning posts (from LinUCB context feature analysis).
```

This card reads from the same learning/status data but presents it as natural language insights.

## 4. File Changes Manifest

### 4.1 Backend

| File | Change |
|------|--------|
| `server/api/analytics.py` | Fix date filter in `_build_overview()`: use `collected_at` instead of `published_at`. Ensure reach/likes/comments are read from correct field paths. |
| `server/api/analytics.py` | Add `/analytics/cross-niche` endpoint for comparison data |

### 4.2 Frontend

| File | Change |
|------|--------|
| `views/analytics/Analytics.tsx` | Add "Compare" tab with cross-niche comparison table + bar charts. Add KPI deltas (week-over-week). |
| `views/learning/LearningOverview.tsx` | Rewrite to show human-readable insights instead of raw stats. Translate avg_reward to percentage, show observation progress in plain English, reframe threshold progress. |
| `views/learning/LearningView.tsx` | Add "What AI Learned" tab or card |
| `views/learning/BanditArms.tsx` | Add human-readable column explaining what each arm means operationally |
| `api/client.ts` | Add `analytics.crossNiche()` method |
| `api/types.ts` | Add `CrossNicheAnalytics` type |
| `hooks/use-analytics-overview.ts` | Add delta computation (current vs previous window) |

## 5. Quality Gates

- `analytics/overview` returns `total_reach > 0` (currently returns 0)
- Cross-niche comparison shows data for all 5 niches
- KPI cards show week-over-week deltas
- Learning Overview is human-readable (no raw alpha/beta values on the overview tab)
- "What AI Learned" card generates meaningful insights
- `npm run build` passes

## 6. Migration Order

1. Backend: Fix analytics overview date filter (collected_at)
2. Backend: Add cross-niche comparison endpoint
3. Frontend: Fix Analytics KPIs (now showing real data)
4. Frontend: Add cross-niche comparison tab
5. Frontend: Add KPI deltas
6. Frontend: Rewrite Learning Overview for humans
7. Frontend: Add "What AI Learned" card
8. Build + verification
