# Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the GenLab operations dashboard into a world-class unified command center with 10 views, 2 new views (Learning Intelligence, Engagement), and 12 polish upgrades.

**Architecture:** Iterative upgrade of the existing React 19 + Vite + TypeScript frontend. Each phase produces a working, deployable dashboard. New views are added alongside existing ones (old routes kept as redirects until removed). Backend API endpoints are added before or alongside the frontend that consumes them.

**Tech Stack:** React 19, TypeScript, Vite, Tailwind CSS v4, Recharts 3, Framer Motion 12, Radix UI, cmdk, Socket.IO, TanStack React Query 5, Zustand 5

**Spec:** `docs/superpowers/specs/2026-03-20-dashboard-redesign-design.md`

---

## File Structure

### New Files

```
dashboard/frontend/src/
├── views/
│   ├── mission-control/
│   │   ├── MissionControl.tsx          (REWRITE — 7-row bento layout)
│   │   ├── mission-control.css         (REWRITE — new grid)
│   │   ├── KpiHero.tsx                 (NEW — 4 animated KPI cards)
│   │   ├── TopPostSpotlight.tsx        (NEW — viral post card)
│   │   ├── LearningLoopCard.tsx        (NEW — bandit status summary)
│   │   ├── AiInsightCard.tsx           (NEW — daily insight)
│   │   ├── PublishTimeline.tsx         (NEW — today's publishes)
│   │   ├── UpcomingQueue.tsx           (NEW — next 3 scheduled)
│   │   ├── ChannelStrip.tsx            (NEW — 5 channels + sparklines)
│   │   ├── EngagementFeed.tsx          (NEW — live comment feed)
│   │   ├── TrendRadar.tsx              (NEW — Google Trends multipliers)
│   │   ├── ContentQuality.tsx          (NEW — pipeline quality gates)
│   │   ├── PipelineCountdowns.tsx      (NEW — next run timers)
│   │   └── MonetisationCompact.tsx     (NEW — nearest threshold per channel)
│   ├── learning/
│   │   ├── Learning.tsx                (NEW — 5-tab container)
│   │   ├── LearningOverview.tsx        (NEW — status + progress)
│   │   ├── BanditArms.tsx              (NEW — per-niche arm table)
│   │   ├── RewardHistory.tsx           (NEW — time-series chart)
│   │   ├── HookClassifier.tsx          (NEW — training progress)
│   │   └── ConfigUpdates.tsx           (NEW — YAML change log)
│   ├── engagement/
│   │   ├── Engagement.tsx              (NEW — two-panel container)
│   │   ├── CommentFeed.tsx             (NEW — real comments list)
│   │   └── ReplyQueue.tsx              (NEW — pending replies)
│   ├── content-review/
│   │   ├── ContentReview.tsx           (NEW — merged unified view)
│   │   ├── ContentCard.tsx             (NEW — enriched blueprint card)
│   │   └── FocusOverlay.tsx            (NEW — full-screen review)
│   └── health/
│       └── SystemHealth.tsx            (NEW — merged health view)
├── components/
│   ├── charts/
│   │   ├── MiniSparkline.tsx           (NEW — SVG sparkline component)
│   │   └── AnimatedProgress.tsx        (NEW — animated progress bar)
│   ├── shared/
│   │   ├── AlertBanner.tsx             (NEW — dismissable error strip)
│   │   ├── ContextMenu.tsx             (NEW — right-click menu wrapper)
│   │   ├── ToxicityBadge.tsx           (NEW — safe/review/toxic pill)
│   │   └── PlatformIcon.tsx            (NEW — full-color platform icons)
│   └── ui/
│       └── (existing Radix primitives — no changes)
├── hooks/
│   ├── use-learning.ts                 (NEW — /api/v1/learning/status)
│   ├── use-engagement.ts               (NEW — /api/v1/engagement/recent)
│   ├── use-trends.ts                   (NEW — /api/v1/trends)
│   └── use-sound.ts                    (NEW — optional UI sounds)
├── lib/
│   └── sounds.ts                       (NEW — sound manager)
└── assets/
    └── sounds/                         (NEW — audio files)

dashboard/server/api/
├── trends.py                           (NEW — Google Trends cache endpoint)
└── (existing files modified for new data)
```

### Modified Files

```
dashboard/frontend/src/
├── App.tsx                             (UPDATE routes)
├── components/layout/sidebar.tsx       (UPDATE nav items)
├── api/client.ts                       (ADD learning/engagement/trends API calls)
├── api/query-keys.ts                   (ADD new query keys)
├── api/types.ts                        (ADD new type definitions)
├── hooks/use-keyboard.ts              (EXTEND shortcuts)
├── shell/command-registry.ts          (EXTEND commands)
├── design-system/tokens.css           (ADD noise texture, new animation tokens)

dashboard/server/api/
├── engagement.py                       (ADD /recent endpoint)
├── learning.py                         (already has /status — minor tweaks)
├── analytics.py                        (ADD /top-posts endpoint)
```

---

## Phase 1: Mission Control Redesign (Tasks 1-8)

### Task 1: Backend — Add trends + top-posts + engagement/recent endpoints

**Files:**
- Create: `dashboard/server/api/trends.py`
- Modify: `dashboard/server/api/analytics.py`
- Modify: `dashboard/server/api/engagement.py`
- Modify: `dashboard/server/api/__init__.py`

- [ ] **Step 1: Create trends API endpoint**

```python
# dashboard/server/api/trends.py
"""Google Trends cache data for dashboard."""
import json
import logging
from pathlib import Path

from flask import Blueprint

from server.core.responses import api_success

logger = logging.getLogger(__name__)
bp = Blueprint("trends_api", __name__, url_prefix="/api/v1/trends")

_GENLAB_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_CACHE_DIR = _GENLAB_ROOT / ".tmp" / "cache"


@bp.route("")
def get_trends():
    """Return cached Google Trends data for all niches."""
    niches = ["ai_creators", "gaming", "sports", "movies", "anime"]
    result = {}
    for niche in niches:
        cache_file = _CACHE_DIR / f"trends_{niche}.json"
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text())
                result[niche] = data[:10]
            except Exception:
                result[niche] = []
        else:
            result[niche] = []
    return api_success(data=result)
```

- [ ] **Step 2: Register trends blueprint in `__init__.py`**

Add to `dashboard/server/api/__init__.py`:
```python
from server.api.trends import bp as trends_bp
app.register_blueprint(trends_bp)
```

- [ ] **Step 3: Add top-posts endpoint to analytics.py**

Add to `dashboard/server/api/analytics.py`:
```python
@bp.route("/top-posts")
def top_posts():
    """Return top performing posts sorted by engagement."""
    client = get_sync_client()
    records = client.analytics.all(max_records=50)
    posts = []
    for r in records:
        f = r.get("fields", r)
        likes = float(f.get("likes", 0) or 0)
        if likes > 0:
            posts.append({
                "post_id": f.get("post_id", ""),
                "platform": f.get("platform", ""),
                "niche_id": f.get("niche_id", ""),
                "likes": likes,
                "comments": float(f.get("comments", 0) or 0),
                "reach": float(f.get("reach", 0) or 0),
                "collected_at": f.get("collected_at", ""),
            })
    posts.sort(key=lambda p: p["likes"], reverse=True)
    return api_success(data=posts[:20])
```

- [ ] **Step 4: Add recent-comments endpoint to engagement.py**

Add to `dashboard/server/api/engagement.py`:
```python
@bp.route("/recent")
def recent_comments():
    """Return recent Instagram comments fetched via API."""
    import os
    import requests
    from genlab_core.publishing.niche_credentials import resolve_meta_credentials

    niches = {
        "ai_creators": "17841448019867838",
        "gaming": "17841442899013893",
        "sports": "17841444443187278",
        "movies": "17841445074779760",
        "anime": "17841441780164409",
    }
    comments = []
    for niche_id, ig_id in niches.items():
        creds = resolve_meta_credentials(niche_id)
        token = creds.get("ig_access_token", "")
        if not token:
            continue
        try:
            media_resp = requests.get(
                f"https://graph.facebook.com/v21.0/{ig_id}/media",
                params={"fields": "id,comments_count", "limit": 10, "access_token": token},
                timeout=10,
            )
            for item in media_resp.json().get("data", []):
                if item.get("comments_count", 0) == 0:
                    continue
                cr = requests.get(
                    f"https://graph.facebook.com/v21.0/{item['id']}/comments",
                    params={"fields": "id,text,username,timestamp", "limit": 10, "access_token": token},
                    timeout=10,
                )
                for c in cr.json().get("data", []):
                    comments.append({
                        "niche_id": niche_id,
                        "platform": "instagram",
                        "comment_id": c["id"],
                        "text": c.get("text", ""),
                        "username": c.get("username", ""),
                        "timestamp": c.get("timestamp", ""),
                    })
        except Exception:
            continue
    comments.sort(key=lambda c: c.get("timestamp", ""), reverse=True)
    return api_success(data=comments[:30])
```

- [ ] **Step 5: Verify endpoints work**

```bash
# Restart server and test
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.genlab.review-server.plist 2>/dev/null
sleep 2
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.genlab.review-server.plist
sleep 3
# Test (with auth cookie)
source BlackboxBrief/.env
curl -sf -X POST -d "username=$REVIEW_AUTH_USER&password=$REVIEW_AUTH_PASS" -c /tmp/c.txt http://localhost:5151/login > /dev/null
curl -sf -b /tmp/c.txt http://localhost:5151/api/v1/trends | python3 -m json.tool | head -10
curl -sf -b /tmp/c.txt http://localhost:5151/api/v1/analytics/top-posts | python3 -m json.tool | head -10
curl -sf -b /tmp/c.txt http://localhost:5151/api/v1/engagement/recent | python3 -m json.tool | head -10
```

- [ ] **Step 6: Commit**

```bash
cd dashboard
git add server/api/trends.py server/api/analytics.py server/api/engagement.py server/api/__init__.py
git commit -m "feat: add trends, top-posts, and recent-comments API endpoints"
```

---

### Task 2: Frontend — API client + types + hooks for new endpoints

**Files:**
- Modify: `dashboard/frontend/src/api/client.ts`
- Modify: `dashboard/frontend/src/api/query-keys.ts`
- Modify: `dashboard/frontend/src/api/types.ts`
- Create: `dashboard/frontend/src/hooks/use-learning.ts`
- Create: `dashboard/frontend/src/hooks/use-engagement.ts`
- Create: `dashboard/frontend/src/hooks/use-trends.ts`

- [ ] **Step 1: Add types for new data**

Add to `dashboard/frontend/src/api/types.ts`:
```typescript
export interface LearningStatus {
  bandit_arms: Record<string, BanditArm[]>;
  feedback_pipeline: Record<string, number>;
  rewards_computed: number;
  avg_reward: number | null;
  max_reward: number | null;
  analytics_records: number;
  hook_classifier_progress: number;
  hook_classifier_threshold: number;
  config_update_threshold: number;
  linucb_threshold: number;
  linucb_max_plays: number;
}

export interface BanditArm {
  arm_id: string;
  alpha: number;
  beta: number;
  n_plays: number;
  mean: number;
}

export interface EngagementComment {
  niche_id: string;
  platform: string;
  comment_id: string;
  text: string;
  username: string;
  timestamp: string;
}

export interface TopPost {
  post_id: string;
  platform: string;
  niche_id: string;
  likes: number;
  comments: number;
  reach: number;
  collected_at: string;
}

export type TrendData = Record<string, string[]>;
```

- [ ] **Step 2: Add API functions to client.ts**

Add to `dashboard/frontend/src/api/client.ts`:
```typescript
export const learning = {
  status: () => get<LearningStatus>("/learning/status"),
};

export const engagement = {
  recent: () => get<EngagementComment[]>("/engagement/recent"),
  status: () => get<any>("/engagement/status"),
};

export const trends = {
  current: () => get<TrendData>("/trends"),
};

// Add to existing analytics object:
// topPosts: () => get<TopPost[]>("/analytics/top-posts"),
```

- [ ] **Step 3: Add query keys**

Add to `dashboard/frontend/src/api/query-keys.ts`:
```typescript
learning: {
  status: ["learning", "status"] as const,
},
engagement: {
  recent: ["engagement", "recent"] as const,
  status: ["engagement", "status"] as const,
},
trends: {
  current: ["trends", "current"] as const,
},
```

- [ ] **Step 4: Create hooks**

Create `dashboard/frontend/src/hooks/use-learning.ts`:
```typescript
import { useQuery } from "@tanstack/react-query";
import { learning } from "@/api/client";
import { queryKeys } from "@/api/query-keys";

export function useLearningStatus() {
  return useQuery({
    queryKey: queryKeys.learning.status,
    queryFn: learning.status,
    staleTime: 60_000,
  });
}
```

Create `dashboard/frontend/src/hooks/use-engagement.ts`:
```typescript
import { useQuery } from "@tanstack/react-query";
import { engagement } from "@/api/client";
import { queryKeys } from "@/api/query-keys";

export function useRecentComments() {
  return useQuery({
    queryKey: queryKeys.engagement.recent,
    queryFn: engagement.recent,
    staleTime: 5 * 60_000,
  });
}
```

Create `dashboard/frontend/src/hooks/use-trends.ts`:
```typescript
import { useQuery } from "@tanstack/react-query";
import { trends } from "@/api/client";
import { queryKeys } from "@/api/query-keys";

export function useTrends() {
  return useQuery({
    queryKey: queryKeys.trends.current,
    queryFn: trends.current,
    staleTime: 6 * 60 * 60_000, // 6h cache
  });
}
```

- [ ] **Step 5: Commit**

```bash
git add src/api/ src/hooks/use-learning.ts src/hooks/use-engagement.ts src/hooks/use-trends.ts
git commit -m "feat: add API client, types, and hooks for learning, engagement, trends"
```

---

### Task 3: Design System Enhancements

**Files:**
- Modify: `dashboard/frontend/src/design-system/tokens.css`
- Create: `dashboard/frontend/src/components/charts/MiniSparkline.tsx`
- Create: `dashboard/frontend/src/components/charts/AnimatedProgress.tsx`
- Create: `dashboard/frontend/src/components/shared/AlertBanner.tsx`
- Create: `dashboard/frontend/src/components/shared/ToxicityBadge.tsx`
- Create: `dashboard/frontend/src/components/shared/PlatformIcon.tsx`

- [ ] **Step 1: Add animation tokens + noise texture to tokens.css**

Add to `dashboard/frontend/src/design-system/tokens.css` inside `:root {}`:
```css
/* ── Animation ──────────────────────────────────────────── */
--stagger-delay: 50ms;
--count-up-duration: 800ms;

/* ── Noise texture ──────────────────────────────────────── */
--noise-opacity: 0.02;
```

Add after `:root {}`:
```css
body::before {
  content: '';
  position: fixed;
  inset: 0;
  background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.02'/%3E%3C/svg%3E");
  pointer-events: none;
  z-index: 9999;
  opacity: var(--noise-opacity);
}
```

- [ ] **Step 2: Create MiniSparkline component**

Create `dashboard/frontend/src/components/charts/MiniSparkline.tsx`:
```tsx
import { useMemo } from "react";

interface Props {
  data: number[];
  color: string;
  width?: number;
  height?: number;
}

export function MiniSparkline({ data, color, width = 100, height = 24 }: Props) {
  const path = useMemo(() => {
    if (!data.length) return "";
    const max = Math.max(...data, 1);
    const step = width / Math.max(data.length - 1, 1);
    return data
      .map((v, i) => `${i === 0 ? "M" : "L"} ${i * step} ${height - (v / max) * height}`)
      .join(" ");
  }, [data, width, height]);

  return (
    <svg width={width} height={height} style={{ display: "block" }}>
      <defs>
        <linearGradient id={`sg-${color.replace(/[^a-z0-9]/gi, "")}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.3" />
          <stop offset="100%" stopColor={color} stopOpacity="0.02" />
        </linearGradient>
      </defs>
      <path
        d={`${path} L ${width} ${height} L 0 ${height} Z`}
        fill={`url(#sg-${color.replace(/[^a-z0-9]/gi, "")})`}
      />
      <path d={path} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}
```

- [ ] **Step 3: Create AnimatedProgress, AlertBanner, ToxicityBadge, PlatformIcon**

Create each as a focused component file (full code in spec). Each is <30 lines.

- [ ] **Step 4: Commit**

```bash
git add src/design-system/ src/components/charts/ src/components/shared/
git commit -m "feat: design system enhancements — sparkline, progress, alert, badges"
```

---

### Task 4: Mission Control — KPI Hero Row

**Files:**
- Create: `dashboard/frontend/src/views/mission-control/KpiHero.tsx`

- [ ] **Step 1: Build KpiHero with animated count-up**

4-card grid using existing `useCountUp` hook. Each card: label, hero number (animated), delta with color.

- [ ] **Step 2: Verify in isolation**

Import into existing MissionControl temporarily to test rendering.

- [ ] **Step 3: Commit**

```bash
git add src/views/mission-control/KpiHero.tsx
git commit -m "feat: KPI hero cards with animated count-up"
```

---

### Task 5: Mission Control — Top Post + Learning + AI Insight

**Files:**
- Create: `dashboard/frontend/src/views/mission-control/TopPostSpotlight.tsx`
- Create: `dashboard/frontend/src/views/mission-control/LearningLoopCard.tsx`
- Create: `dashboard/frontend/src/views/mission-control/AiInsightCard.tsx`

- [ ] **Step 1: Build TopPostSpotlight** — thumbnail + hook + engagement stats
- [ ] **Step 2: Build LearningLoopCard** — bandit status from `useLearningStatus()`
- [ ] **Step 3: Build AiInsightCard** — gradient border, computed from arm means
- [ ] **Step 4: Commit**

---

### Task 6: Mission Control — Timeline + Queue + Channel Strip

**Files:**
- Create: `dashboard/frontend/src/views/mission-control/PublishTimeline.tsx`
- Create: `dashboard/frontend/src/views/mission-control/UpcomingQueue.tsx`
- Create: `dashboard/frontend/src/views/mission-control/ChannelStrip.tsx`

- [ ] **Step 1: Build PublishTimeline** — today's publishes with ✓/✗ per platform
- [ ] **Step 2: Build UpcomingQueue** — next 3 scheduled with inline approve
- [ ] **Step 3: Build ChannelStrip** — 5 channels with MiniSparkline + accent colors
- [ ] **Step 4: Commit**

---

### Task 7: Mission Control — Feed + Radar + Quality + Countdowns + Monetisation

**Files:**
- Create remaining Mission Control sub-components (EngagementFeed, TrendRadar, ContentQuality, PipelineCountdowns, MonetisationCompact)

- [ ] **Step 1: Build EngagementFeed** — `useRecentComments()` + ToxicityBadge
- [ ] **Step 2: Build TrendRadar** — `useTrends()` + multiplier badges
- [ ] **Step 3: Build ContentQuality** — latest run stats from pipeline API
- [ ] **Step 4: Build PipelineCountdowns** — live JS countdown timers
- [ ] **Step 5: Build MonetisationCompact** — nearest threshold per channel + AnimatedProgress
- [ ] **Step 6: Commit**

---

### Task 8: Mission Control — Assemble + Route + AlertBanner

**Files:**
- Rewrite: `dashboard/frontend/src/views/mission-control/MissionControl.tsx`
- Rewrite: `dashboard/frontend/src/views/mission-control/mission-control.css`
- Modify: `dashboard/frontend/src/components/layout/sidebar.tsx`
- Modify: `dashboard/frontend/src/App.tsx`

- [ ] **Step 1: Rewrite MissionControl.tsx** — import all sub-components into 7-row bento grid
- [ ] **Step 2: Rewrite mission-control.css** — new grid template with all areas
- [ ] **Step 3: Add AlertBanner** at top (conditional on health errors)
- [ ] **Step 4: Update sidebar nav items** — add Learning, Engagement (NEW badges), merge health
- [ ] **Step 5: Update App.tsx routes** — add `/learning`, `/engagement`, `/content`, `/health`
- [ ] **Step 6: Build frontend and test**

```bash
cd dashboard/frontend && npm run build
# Restart server
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.genlab.review-server.plist 2>/dev/null
sleep 2 && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.genlab.review-server.plist
```

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat: Mission Control redesign — 7-row bento grid with all 10 sections"
```

---

## Phase 2: Learning Intelligence + Engagement Views (Tasks 9-10)

### Task 9: Learning Intelligence View

**Files:**
- Create: `dashboard/frontend/src/views/learning/Learning.tsx`
- Create: `dashboard/frontend/src/views/learning/LearningOverview.tsx`
- Create: `dashboard/frontend/src/views/learning/BanditArms.tsx`
- Create: `dashboard/frontend/src/views/learning/RewardHistory.tsx`
- Create: `dashboard/frontend/src/views/learning/HookClassifier.tsx`
- Create: `dashboard/frontend/src/views/learning/ConfigUpdates.tsx`

- [ ] **Step 1: Build Learning.tsx** — Radix Tabs container with 5 tabs
- [ ] **Step 2: Build LearningOverview** — status card + 3 AnimatedProgress bars
- [ ] **Step 3: Build BanditArms** — expandable niche sections with arm table
- [ ] **Step 4: Build RewardHistory** — Recharts AreaChart time series
- [ ] **Step 5: Build HookClassifier** — progress indicator + empty state
- [ ] **Step 6: Build ConfigUpdates** — log table or empty state
- [ ] **Step 7: Wire route in App.tsx** — lazy import for `/learning`
- [ ] **Step 8: Build + test**
- [ ] **Step 9: Commit**

---

### Task 10: Engagement View

**Files:**
- Create: `dashboard/frontend/src/views/engagement/Engagement.tsx`
- Create: `dashboard/frontend/src/views/engagement/CommentFeed.tsx`
- Create: `dashboard/frontend/src/views/engagement/ReplyQueue.tsx`

- [ ] **Step 1: Build Engagement.tsx** — stats header + two-panel layout
- [ ] **Step 2: Build CommentFeed** — scrollable list with ToxicityBadge + PlatformIcon
- [ ] **Step 3: Build ReplyQueue** — pending replies with approve/edit/reject (or empty state)
- [ ] **Step 4: Wire route in App.tsx**
- [ ] **Step 5: Build + test**
- [ ] **Step 6: Commit**

---

## Phase 3: Content Review (Tasks 11-12)

### Task 11: Content Review — Unified View

**Files:**
- Create: `dashboard/frontend/src/views/content-review/ContentReview.tsx`
- Create: `dashboard/frontend/src/views/content-review/ContentCard.tsx`
- Create: `dashboard/frontend/src/views/content-review/FocusOverlay.tsx`

- [ ] **Step 1: Build ContentCard** — video thumb, hook, score, platform status, engagement
- [ ] **Step 2: Build ContentReview** — filter bar + card grid using existing `useBlueprints()`
- [ ] **Step 3: Build FocusOverlay** — full-screen review with keyboard nav
- [ ] **Step 4: Wire route `/content`, add redirect from old `/blueprints`, `/queue`, `/focus-review`**
- [ ] **Step 5: Commit**

### Task 12: Keyboard Nav + Context Menus

- [ ] **Step 1: Extend use-keyboard.ts** with G+L, G+E, G+C, J/K, A/R shortcuts
- [ ] **Step 2: Create ContextMenu wrapper** using Radix ContextMenu
- [ ] **Step 3: Add context menus to channel cards + content cards
- [ ] **Step 4: Commit**

---

## Phase 4: Analytics + Data Viz (Tasks 13-14)

### Task 13: Analytics Upgrade

- [ ] **Step 1: Add KPI summary row** (4 cards with real data)
- [ ] **Step 2: Upgrade charts** to use gradient fills + tooltips
- [ ] **Step 3: Add Top Posts tab** using `/api/v1/analytics/top-posts`
- [ ] **Step 4: Add By Niche tab** with per-niche drill-down
- [ ] **Step 5: Commit**

### Task 14: Premium Data Viz Components

- [ ] **Step 1: SVG sparklines** everywhere (replace ASCII)
- [ ] **Step 2: Platform donut chart** with brand colors
- [ ] **Step 3: Animated gradient fills** on area charts
- [ ] **Step 4: Commit**

---

## Phase 5: System Health + Pipeline + Schedule + Monetisation (Tasks 15-18)

### Task 15: System Health (merged)

- [ ] **Step 1: Build SystemHealth.tsx** — 3 sections (tokens, infra, agents)
- [ ] **Step 2: Wire route `/health`**, redirect from old `/channel-health`
- [ ] **Step 3: Commit**

### Task 16: Pipeline Upgrades

- [ ] **Step 1: Add arm_id + quality stats** to run details
- [ ] **Step 2: Add published post links** from run
- [ ] **Step 3: Commit**

### Task 17: Schedule Upgrades

- [ ] **Step 1: Add niche accent colors** to calendar slots
- [ ] **Step 2: Add quick-approve** from calendar cards
- [ ] **Step 3: Commit**

### Task 18: Monetisation Upgrade

- [ ] **Step 1: Full multi-threshold view** — 5 channels × 4 platforms
- [ ] **Step 2: Projected timeline** calculations
- [ ] **Step 3: Commit**

---

## Phase 6: Real-Time + Command Palette (Tasks 19-20)

### Task 19: Real-Time Pulse

- [ ] **Step 1: Extend Socket.IO events** — `engagement_new`, `publish_complete`, `learning_update`
- [ ] **Step 2: Add breathing pulse dot** in sidebar
- [ ] **Step 3: Live KPI counter updates** on socket events
- [ ] **Step 4: Engagement feed slide-in animation** for new items
- [ ] **Step 5: Commit**

### Task 20: Command Palette Extensions

- [ ] **Step 1: Add search posts** by hook text
- [ ] **Step 2: Add quick-approve** command
- [ ] **Step 3: Add recent actions** section
- [ ] **Step 4: Commit**

---

## Phase 7: Responsive + PWA + Empty States (Tasks 21-23)

### Task 21: Responsive Layout

- [ ] **Step 1: Collapsible sidebar** at `<768px`
- [ ] **Step 2: Mobile bottom tab bar** at `<640px`
- [ ] **Step 3: KPI horizontal scroll** on mobile
- [ ] **Step 4: Commit**

### Task 22: PWA

- [ ] **Step 1: Add manifest.json** + service worker
- [ ] **Step 2: Add apple-mobile-web-app meta tags**
- [ ] **Step 3: Commit**

### Task 23: Empty States

- [ ] **Step 1: Add meaningful empty states** to every card/section
- [ ] **Step 2: Ensure skeleton loading** is consistent
- [ ] **Step 3: Commit**

---

## Phase 8: Export + Sound + Final Polish (Tasks 24-26)

### Task 24: Export & Sharing

- [ ] **Step 1: Add html2canvas** for card screenshots
- [ ] **Step 2: Add CSV export** for analytics
- [ ] **Step 3: Add clipboard copy** for KPI summary
- [ ] **Step 4: Commit**

### Task 25: Sound Design

- [ ] **Step 1: Create sound manager** with Web Audio API
- [ ] **Step 2: Add sounds** for approve, reject, publish, viral, notification
- [ ] **Step 3: Add mute toggle** in Settings
- [ ] **Step 4: Commit**

### Task 26: Final Polish Pass

- [ ] **Step 1: Hover states** — subtle scale + border luminance on all cards
- [ ] **Step 2: Focus rings** — niche accent colored
- [ ] **Step 3: Page transitions** — AnimatePresence with slide + fade
- [ ] **Step 4: Card stagger** — `staggerChildren: 0.05` on all grids
- [ ] **Step 5: Full build + manual QA across all views**
- [ ] **Step 6: Final commit**

```bash
cd dashboard/frontend && npm run build
cd .. && git add -A && git commit -m "feat: dashboard v2 — world-class redesign complete"
```
