# Monetisation Page Upgrade

**Goal:** Add affiliate revenue tracking (clicks, products, revenue estimates) alongside the existing threshold progress, making the Monetisation page the single source of truth for all revenue-related data.

**Date:** 2026-03-24

---

## 1. Problem Statement

The Monetisation page only shows platform threshold progress (followers toward 10K IG, 1K YT subs, etc.). It has NO visibility into:
- Affiliate click data (61 clicks across 27 products — available from `/revenue/summary`)
- Revenue estimates (₹650 est. 30d — available but not shown)
- Top-performing products (PS5 Console: 8 clicks — data exists in DB)
- Click trends over time (daily click data in `affiliate_clicks` table)
- Per-network performance (Amazon US: 52 clicks, CueLinks: 9 — available)

All this data EXISTS in the backend but isn't surfaced on the Monetisation page.

## 2. Architecture

### 2.1 No Backend Changes Needed

All data is already available:
- `/api/v1/revenue/summary` — clicks (today/7d/30d), by_product, by_niche, by_network, est_revenue
- `/api/v1/monetisation/progress` — threshold progress per niche/platform
- `affiliate_clicks` table — raw click data with timestamps for trends

Only need a new endpoint for daily click trends.

### 2.2 Backend: Add click trends endpoint

New route in `server/api/revenue.py`:

```python
@bp.route("/click-trends", methods=["GET"])
def click_trends():
    """Daily affiliate click counts for the last 14 days."""
    # Query: SELECT created_at::date as day, COUNT(*) FROM affiliate_clicks
    #        WHERE created_at >= NOW() - INTERVAL '14 days' GROUP BY day ORDER BY day
```

### 2.3 Frontend: Enhance MonetisationProgress.tsx

Add three new sections below the existing threshold progress cards:

1. **Revenue KPIs** — 4 cards: Clicks Today, Clicks 7d, Clicks 30d, Est. Revenue 30d
2. **Top Products** — table showing product name, clicks, network, with bar chart
3. **Click Trends** — small area chart showing daily clicks over 14 days
4. **Network Breakdown** — small pie/bar showing Amazon US vs CueLinks split

## 3. File Changes

| File | Change |
|------|--------|
| `server/api/revenue.py` | Add `/click-trends` endpoint |
| `frontend/src/api/client.ts` | Add `revenue.clickTrends()` method |
| `frontend/src/api/types.ts` | Add `ClickTrend` type |
| `frontend/src/views/monetisation/MonetisationProgress.tsx` | Add revenue section with KPIs, top products, trends, network breakdown |

## 4. Quality Gates

- Revenue KPIs show real click data (57 today, 61 7d)
- Top products table shows PS5 Console at top with 8 clicks
- Click trends chart shows daily data
- Network breakdown shows Amazon US vs CueLinks split
- Existing threshold progress still works
- `npm run build` passes
