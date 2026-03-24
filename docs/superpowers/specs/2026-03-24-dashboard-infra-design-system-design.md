# Dashboard Infrastructure & Design System Upgrade

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate three competing styling approaches (per-view CSS files, inline styles, Tailwind) into one coherent system, extract shared UI primitives, and delete all dead code — so every future dashboard upgrade is fast, consistent, and maintainable.

**Date:** 2026-03-24

---

## 1. Problem Statement

The dashboard has three competing styling approaches and no shared UI primitives:

- **2,463 lines of per-view CSS** across 6 files that duplicate the same patterns
- **~470 inline `style={{}}` usages** across 30+ files (39 of which are static CSS-variable references trivially convertible to Tailwind)
- **4 different KPI card implementations** (MissionControl KpiCard, Analytics KpiCard, Analytics KpiHero, Engagement StatCard)
- **7 different error states** — same "Could not connect / Retry" pattern copy-pasted
- **3 different page header approaches** — CSS class, Tailwind utilities, inline styles
- **5 duplicate Recharts tooltip implementations**
- **5× `LABEL_STYLE` and 4× `CARD_STYLE` constants** — identical objects defined in separate Learning view files
- **5+ duplicate platform color/label maps** — no centralised platform registry
- **11 duplicate utility functions** — `formatRelative`, `formatNumber`, `CustomTooltip` defined in multiple files
- **6,168 lines of dead code** across 27 orphaned files (chart components, hooks, lib)
- **Shared components exist but are underused** — `FilterBar` in 2/16 views, `EmptyState` in 5/16
- **6 different `max-width` values** (700px–1200px) across views, duplicating Shell's own `max-w-7xl` constraint
- **6 views double-pad** content (Shell `p-6` + view's own `padding: var(--space-xl)`)
- **4 hook filenames** use camelCase while 19 use kebab-case

## 2. Scope

**In scope:**
- Styling consolidation (CSS files + inline styles → Tailwind + shared `layouts.css`)
- Shared component extraction (8 new primitives)
- Dead code deletion (27 files)
- Duplicate function/constant consolidation
- Platform registry creation
- Hook file naming convention fix
- API client type hygiene (move misplaced types, fix `any`, fix query key patterns)
- Accessibility fixes on migrated elements (aria-labels, alt tags)

**Out of scope:**
- Splitting bloated files (Analytics 876 lines, SystemHealth 753 lines) — deferred to view-specific sprints
- New features or API endpoints
- Backend changes
- Test coverage additions
- Visual redesign — the dashboard must look identical before and after

## 3. Architecture

### 3.1 Styling Strategy (Hybrid)

- **Tailwind** for layout, spacing, typography, colors, borders, radius — the majority
- **`styles/layouts.css`** (~365 lines) for patterns genuinely awkward in Tailwind: bento grid with `grid-template-areas`, stage waterfall visualization, chart tooltips, SVG score ring, log viewer, keyframe animations, data table base styles
- **`style={{}}` only** for dynamic runtime values: niche accent colors from data, computed bar widths, Recharts color props — approximately 44 remaining across the entire codebase
- **Delete** all 6 per-view CSS files (2,463 lines)

### 3.2 What Goes in `layouts.css` (~365 lines)

| Section | Lines | From | Reason |
|---------|-------|------|--------|
| Bento grid (MC) + 3 responsive breakpoints | ~60 | mission-control.css:45-103 | `grid-template-areas` with named areas + responsive rewrites |
| KPI hero grid + mobile scroll-snap | ~30 | mission-control.css:180-209 | Horizontal scroll-snap on mobile is complex in pure Tailwind |
| Chart card + tooltip | ~30 | analytics.css:145-158, 330-357 | Recharts tooltip can't be styled via Tailwind |
| Data table base | ~35 | analytics.css:294-328 + pipeline-monitor.css:394-424 | Shared `.data-table` for Analytics top-performers, Pipeline runs, Learning bandit arms |
| Two-column layout | ~15 | analytics.css:162-173 | Simple grid with responsive breakpoint |
| Progress bar fills + transitions | ~20 | monetisation-progress.css:128-153, pipeline-monitor.css:220-232 | Width transition on dynamic values |
| Stage waterfall visualization | ~85 | pipeline-monitor.css:105-216 | Complex positional layout with connectors, glow animations, pseudo-elements |
| Score ring (SVG) | ~25 | channel-health.css:38-73 | SVG circle stroke-dasharray animation |
| Log viewer | ~30 | pipeline-monitor.css:236-367 | Monospace log layout with toolbar |
| Keyframe animations | ~15 | `cardIn`, `slideIn` from MC + PQ CSS | Shared entry animations |
| AI Insight gradient border mask | ~10 | mission-control.css:397-410 | `mask-composite: exclude` pattern |
| Status dots with glow | ~10 | channel-health.css:154-164 | `box-shadow` glow pulse on status dots |

### 3.3 Per-View CSS Migration Strategy

**`mission-control.css` (808 lines → DELETE):**
- ~95 lines → `layouts.css` (bento grid, KPI grid, cardIn animation)
- ~20 lines → `globals.css` additions (`.bento-card`, `.btn-primary`, `.niche-dot`)
- ~690 lines → Tailwind classes on 12 MC sub-component `.tsx` files

**`analytics.css` (451 lines → DELETE):**
- ~45 lines → `layouts.css` (chart-card, tooltip, data-table, two-col, KPI hero card)
- ~47 lines → shared `<KpiCard>` component
- ~360 lines → Tailwind classes on Analytics.tsx + shadcn `<Select>`, `<Tabs>`, `<AlertBanner>`

**`pipeline-monitor.css` (473 lines → DELETE):**
- ~85 lines → `layouts.css` (waterfall, log viewer)
- ~275 lines → Tailwind classes on Pipeline .tsx files + shared `<StatusBadge>`, `<ProgressBar>`, shadcn `<DropdownMenu>`
- `@keyframes cardIn` deduplicated (already in MC section of layouts.css)

**`publishing-queue.css` (303 lines → DELETE):**
- ~10 lines → `layouts.css` (`slideIn` keyframe)
- ~290 lines → Tailwind classes + shared `<PageHeader>`, `<KpiCard>`, `<StatusBadge>`, `<EmptyState>`, shadcn `<Tabs>`

**`monetisation-progress.css` (256 lines → DELETE):**
- ~15 lines → `layouts.css` (progress bar with color thresholds)
- ~240 lines → Tailwind classes + shared `<PageHeader>`, `<ProgressBar>`

**`channel-health.css` (172 lines → DELETE):**
- ~25 lines → `layouts.css` (score ring, status dots)
- ~145 lines → Tailwind classes + shared `<PageHeader>`, `<ErrorState>`, `<StatusBadge>`

### 3.4 Inline Style Elimination

470 total `style={{}}` usages → 44 remaining (dynamic-only).

| File | Before | After | Remaining (why) |
|------|--------|-------|-----------------|
| Analytics.tsx | 62 | 2 | Recharts color props |
| MonetisationProgress.tsx | 51 | 3 | Niche accent colors |
| SystemHealthView.tsx | 50 | 4 | Status colors, SVG ring stroke |
| focus-mode.tsx | 47 | 2 | Niche-current color |
| RunHistoryTable.tsx | 35 | 2 | Status colors |
| BanditArms.tsx | 30 | 2 | Niche accent, bar width |
| sidebar.tsx | 25 | 10 | Niche accent, socket dot, animations |
| RewardHistory.tsx | 25 | 1 | Chart color |
| LearningOverview.tsx | 24 | 2 | Niche colors |
| HookClassifier.tsx | 23 | 1 | Chart color |
| EngagementView.tsx | 21 | 1 | Niche color |
| ConfigUpdates.tsx | 20 | 0 | — |
| ReplyQueue.tsx | 18 | 1 | Toxicity color |
| PipelineMonitor.tsx | 14 | 2 | Niche accent, glow color |
| CommentFeed.tsx | 13 | 1 | Platform color |
| LearningView.tsx | 12 | 0 | — |
| shell.tsx | 6 | 1 | Niche avatar color-mix |
| AiNewsDetailView.tsx | 13 | 0 | — |
| GamingDetailView.tsx | 12 | 0 | — |
| GenericDetailView.tsx | 13 | 0 | — |
| Others (~10 files) | 1-10 each | ~9 | Various dynamic values |

**Rule**: `style={{}}` stays ONLY for values computed from runtime data (niche accent hex, platform colors from API, computed widths/positions, Recharts customization). Static CSS variable references like `style={{ color: "var(--text-muted)" }}` become `className="text-text-muted"`.

### 3.5 Shared Components (10 new, 2 relocated)

#### 3.5.1 `components/shared/page-header.tsx` (~30 lines)

Replaces 14 different page header implementations across 11 views.

```tsx
interface PageHeaderProps {
  title: string;
  subtitle?: string;
  badge?: ReactNode;
  actions?: ReactNode;
  className?: string;
}
```

| View | Current | After |
|------|---------|-------|
| MissionControl | `.mc-header` CSS | `<PageHeader title={greeting} badge={sprint} subtitle={date} />` |
| Analytics | `.analytics-header` CSS | `<PageHeader title="Analytics" actions={<SelectGroup>} />` |
| Pipeline | `.pm-header` CSS | `<PageHeader title="Pipeline Monitor" actions={<TriggerButton>} />` |
| PublishingQueue | `.pq-header` CSS | `<PageHeader title="Publishing Queue" subtitle="..." />` |
| Monetisation | `.mp-header` CSS | `<PageHeader title="Monetisation Progress" subtitle="..." />` |
| ChannelHealth | `.ch-header` CSS | `<PageHeader title="Channel Health" subtitle="..." />` |
| Learning | inline `style={{}}` | `<PageHeader title="Learning Intelligence" subtitle="..." />` |
| Engagement | inline `style={{}}` | `<PageHeader title="Engagement" subtitle="..." />` |
| SystemHealth | inline `style={{}}` | `<PageHeader title="System Health" actions={<RefreshBtn>} />` |
| ContentReview | Tailwind classes | `<PageHeader title="Content Review" actions={<Filters>} />` |
| Schedule | Tailwind classes | `<PageHeader title="Publishing Schedule" subtitle="..." />` |

#### 3.5.2 `components/shared/kpi-card.tsx` (~55 lines)

Replaces 4 different KPI implementations.

```tsx
interface KpiCardProps {
  label: string;
  value: string | number;
  subtitle?: string;
  icon?: LucideIcon;
  accentColor?: string;
  estimated?: boolean;
  formatter?: (n: number) => string;
  animate?: boolean;
  variant?: "default" | "hero";
  className?: string;
}
```

Replaces: MC `KpiCard` (KpiHero.tsx:16), Analytics `KpiCard` (Analytics.tsx:110), Analytics `KpiHero` (Analytics.tsx:458), Engagement `StatCard` (EngagementView.tsx:26). Internalises `useCountUp` for animated values.

#### 3.5.3 `components/shared/section-header.tsx` (~15 lines)

```tsx
interface SectionHeaderProps {
  title: string;
  action?: ReactNode;
  className?: string;
}
```

Replaces `.card-title` CSS class + inline section titles in Learning/Health views.

#### 3.5.4 `components/shared/chart-card.tsx` (~25 lines)

```tsx
interface ChartCardProps {
  title: string;
  subtitle?: string;
  headerAction?: ReactNode;
  children: ReactNode;
  className?: string;
}
```

Replaces `.chart-card` CSS from analytics.css + inline-styled containers in Learning views.

#### 3.5.5 `components/shared/stat-row.tsx` (~25 lines)

```tsx
interface StatRowProps {
  label: string;
  value: string | number;
  mono?: boolean;
  valueColor?: string;
  icon?: LucideIcon;
  className?: string;
}
```

Replaces `.quality-stat-row`, `.learning-best-arm`, inline stat rows in Health/Learning.

#### 3.5.6 `components/shared/error-state.tsx` (~25 lines)

```tsx
interface ErrorStateProps {
  title?: string;
  message?: string;
  onRetry?: () => void;
  className?: string;
}
```

Includes `role="alert"` for accessibility. Replaces 7 copy-pasted error UIs across MC, Analytics, Learning, Engagement, Channel Health, Schedule, Health.

#### 3.5.7 `components/shared/loading-skeleton.tsx` (~45 lines)

```tsx
interface LoadingSkeletonProps {
  variant: "kpi-row" | "card-list" | "card-grid" | "table" | "bento";
  rows?: number;
  cols?: number;
  className?: string;
}
```

Includes `aria-busy="true"` and `aria-label="Loading"`. Replaces MC `LoadingSkeleton`, Analytics `AnalyticsSkeleton`, `.pq-loading`, `.mp-loading`, `.ch-loading`.

#### 3.5.8 `components/shared/progress-bar.tsx` (~30 lines)

```tsx
interface ProgressBarProps {
  value: number;
  color?: string;
  thresholds?: boolean;
  height?: number;
  animated?: boolean;
  label?: string;
  valueLabel?: string;
  className?: string;
}
```

Replaces 5 different bar implementations: `.platform-bar-fill`, `.funnel-bar-fill`, `.mp-bar-fill`, `.pm-progress-fill`, `.mp-widget-fill`. Also absorbs `AnimatedProgress` component's animation logic (currently separate, 38 lines).

#### 3.5.9 `components/charts/chart-tooltip.tsx` (~25 lines)

```tsx
interface ChartTooltipProps {
  active?: boolean;
  payload?: Array<{ name: string; value: number; color?: string }>;
  label?: string;
  formatter?: (value: number) => string;
}
```

Replaces 5 independent copies: Analytics `ChartTooltip`, RewardHistory `CustomTooltip`, `platform-chart` `CustomTooltip`, `status-funnel` `CustomTooltip`, `template-ranking` `CustomTooltip`.

#### 3.5.10 `lib/platforms.ts` (~30 lines)

```tsx
interface PlatformInfo {
  id: string;
  label: string;
  shortLabel: string;
  color: string;
  hex: string;
}

export const PLATFORMS: PlatformInfo[];
export function getPlatformInfo(id: string): PlatformInfo;
export const PLATFORM_COLORS: Record<string, string>;
export const PLATFORM_LABELS: Record<string, string>;
```

Single source of truth. Replaces duplicate constants in: Analytics.tsx (PLATFORM_COLORS + PLATFORM_LABELS), MonetisationProgress.tsx (PLATFORM_COLORS), SystemHealthView.tsx (PLATFORMS array), EngagementView.tsx (PLATFORMS array).

#### 3.5.11 Existing Components — Wider Adoption

| Component | Current Users | Add To |
|-----------|--------------|--------|
| `EmptyState` | 5 views | Analytics, Learning (4 tabs), Engagement, Pipeline, PQ, Monetisation, Health |
| `FilterBar` | 2 views | Engagement (replace inline FilterSelect) |
| `StatusBadge` | PQ only | Pipeline badges (`.pipe-badge-*`), Channel Health status |
| `AlertBanner` | MC only | Analytics estimated banner, Pipeline prefect banner |

### 3.6 Duplicate Function Consolidation

#### `lib/format.ts` changes:

| Keep | Delete (duplicates) |
|------|-------------------|
| `formatRelativeTime()` (line 7) | Analytics.tsx `formatRelative()` (line 63), PipelineMonitor.tsx `formatRelative()` (line 26) |
| `formatCompact()` (line 138) | MonetisationProgress.tsx `formatNumber()` (line 18), blueprint-card.tsx `formatNumber()` (line 32) |
| `formatDuration()` (line 65) | PipelineMonitor.tsx `formatElapsed()` (line 19) |
| Delete `formatCompactNumber()` (line 87) | Duplicate of `formatCompact()` — identical logic, different name |
| Keep `relativeTime()` (line 149) | Compact variant, used by different callers than `formatRelativeTime` |

#### Learning view constants:

Delete all 5 copies of `LABEL_STYLE` (LearningOverview, BanditArms, RewardHistory, HookClassifier, ConfigUpdates) — replace with existing `label-caps` Tailwind utility class from `globals.css:88`.

Delete all 4 copies of `CARD_STYLE` (LearningOverview, RewardHistory, HookClassifier, ConfigUpdates) — replace with Tailwind `bg-bg-surface border border-border rounded-lg p-4`.

#### Dead response parsers:

Delete `EngagementView.tsx:121-131` (10-line defensive parser handling 4 response shapes that the server never returns — `api_success` always wraps as `{ data: [...] }`, `unwrapResponse` strips to `[...]`).

Delete `Analytics.tsx:618-623` (6-line defensive parser for top-posts — same reason).

### 3.7 Dead Code Deletion

| Category | Files | Lines |
|----------|-------|-------|
| Per-view CSS | 6 | 2,463 |
| Dead chart components | 15 | 3,201 |
| Dead hooks (`use-analytics.ts`, `use-version-history.ts`, `use-reduced-motion.ts`) | 3 | 351 |
| Dead lib files (`slot-scoring.ts`, `search-index.ts`, `announce.ts`) | 3 | 153 |
| **Total** | **27** | **6,168** |

Dead chart components list: `audience-growth` (264), `content-performance` (347), `cost-tracker` (316), `engagement-breakdown` (118), `engagement-trends` (311), `heatmap` (159), `kpi-card` (115), `monetization-summary` (238), `platform-chart` (129), `post-tracker` (269), `status-funnel` (130), `template-ranking` (170), `token-health-panel` (199), `top-posts` (142), `virality-breakdown` (294).

Verified via tree-shaking analysis: none of these appear in the Vite build output. Only `AnimatedProgress` (6 imports) and `MiniSparkline` (1 import) survive.

### 3.8 API Client Hygiene

1. **Move types to `types.ts`**: `CrossNicheOverviewResponse` (43 lines), `DetailedHealthResponse` (18 lines), `LaunchAgentInfo` (5 lines), `NotificationPreferences` (5 lines) — currently defined in `client.ts`.

2. **Fix `any` type**: `engagementApi.status` returns `get<any>(...)` — define `EngagementStatusResponse` interface.

3. **Fix query key consistency**: `queryKeys.learning.status` and `queryKeys.trends.current` are raw arrays. Convert to factory functions `() => [...] as const` to match every other key.

4. **Hook file renames** (convention fix, kebab-case):

| Current | Renamed |
|---------|---------|
| `useAnalyticsOverview.ts` | `use-analytics-overview.ts` |
| `useCrossNicheOverview.ts` | `use-cross-niche-overview.ts` |
| `usePipelineLogs.ts` | `use-pipeline-logs.ts` |
| `usePipelineMonitor.ts` | `use-pipeline-monitor.ts` |

### 3.9 Shell Layout Fix

Remove per-view `max-width` and double-padding. Shell already wraps content in `<div className="p-6 max-w-7xl mx-auto">`.

| View | Remove | Keep |
|------|--------|------|
| MC, Analytics, Pipeline | `max-width: 1200px` | Shell's `max-w-7xl` |
| Engagement, Health | `maxWidth: 1100` + `padding: var(--space-xl)` | Shell's `p-6 max-w-7xl` |
| Learning, Monetisation | `maxWidth: 1000` + `padding: var(--space-xl)` | Shell's `p-6 max-w-7xl` |
| Publishing Queue | `max-width: 900px` | Shell's `max-w-7xl` + inner `max-w-4xl` |
| Channel Health | `max-width: 700px` | Shell's `max-w-7xl` + inner `max-w-3xl` |

Shell inline styles: convert 5 static `style={{ backgroundColor: "var(--surface-*)" }}` to Tailwind (`bg-bg-primary`, `bg-bg-surface`, `bg-bg-elevated`). Keep 1 dynamic (avatar `color-mix`).

### 3.10 Accessibility Fixes (During Migration)

- Add `aria-label` to 7 buttons in learning/engagement/health views
- Add `alt=""` to 10 `<img>` tags (decorative thumbnails)
- `<ErrorState>` includes `role="alert"`
- `<LoadingSkeleton>` includes `aria-busy="true"` and `aria-label="Loading"`
- `<ProgressBar>` includes `role="progressbar"`, `aria-valuenow`, `aria-valuemin="0"`, `aria-valuemax="100"`

## 4. File Changes Manifest

### 4.1 Create (12 files, ~640 lines)

1. `styles/layouts.css` (~365 lines)
2. `components/shared/page-header.tsx` (~30 lines)
3. `components/shared/kpi-card.tsx` (~55 lines)
4. `components/shared/section-header.tsx` (~15 lines)
5. `components/shared/chart-card.tsx` (~25 lines)
6. `components/shared/stat-row.tsx` (~25 lines)
7. `components/shared/error-state.tsx` (~25 lines)
8. `components/shared/loading-skeleton.tsx` (~45 lines)
9. `components/shared/progress-bar.tsx` (~30 lines)
10. `components/charts/chart-tooltip.tsx` (~25 lines)
11. `lib/platforms.ts` (~30 lines)
12. (No file — `lib/format.ts` consolidation is a modify)

### 4.2 Delete (27 files, 6,168 lines)

6 CSS files + 15 dead chart components + 3 dead hooks + 3 dead lib files. See §3.7.

### 4.3 Rename (4 files)

Hook convention fix. See §3.8.

### 4.4 Modify (42 files)

| File | Changes |
|------|---------|
| `styles/globals.css` | Add `@import "./layouts.css"`, add `bento-card`/`btn-primary` from MC CSS |
| `lib/format.ts` | Delete `formatCompactNumber` duplicate |
| `lib/platforms.ts` | NEW — platform registry |
| `api/client.ts` | Move 4 type defs to types.ts, fix `any` |
| `api/types.ts` | Receive moved types, add `EngagementStatusResponse` |
| `api/query-keys.ts` | Fix learning/trends to factory functions |
| `api/socket.ts` | No change (express_progress is used) |
| `components/layout/shell.tsx` | 5 static inline styles → Tailwind |
| `components/layout/sidebar.tsx` | 15 static inline styles → Tailwind (keep 10 dynamic) |
| `components/review/focus-mode.tsx` | 45 static inline styles → Tailwind (keep 2 dynamic) |
| `niches/detail-views/AiNewsDetailView.tsx` | 13 inline styles → Tailwind |
| `niches/detail-views/GamingDetailView.tsx` | 12 inline styles → Tailwind |
| `niches/detail-views/GenericDetailView.tsx` | 13 inline styles → Tailwind |
| `views/mission-control/MissionControl.tsx` | Delete CSS import, use PageHeader |
| `views/mission-control/KpiHero.tsx` | Use shared KpiCard |
| `views/mission-control/TopPostSpotlight.tsx` | CSS classes → Tailwind |
| `views/mission-control/LearningLoopCard.tsx` | CSS classes → Tailwind |
| `views/mission-control/AiInsightCard.tsx` | CSS classes → Tailwind |
| `views/mission-control/PublishTimeline.tsx` | CSS classes → Tailwind |
| `views/mission-control/UpcomingQueue.tsx` | CSS classes → Tailwind |
| `views/mission-control/ChannelStrip.tsx` | CSS classes → Tailwind |
| `views/mission-control/EngagementFeed.tsx` | CSS classes → Tailwind |
| `views/mission-control/TrendRadar.tsx` | CSS classes → Tailwind |
| `views/mission-control/ContentQuality.tsx` | CSS classes → Tailwind + shared StatRow |
| `views/mission-control/PipelineCountdowns.tsx` | CSS classes → Tailwind |
| `views/mission-control/MonetisationCompact.tsx` | CSS classes → Tailwind + shared ProgressBar |
| `views/analytics/Analytics.tsx` | Delete CSS import, local KpiCard/KpiHero/ChartTooltip/formatRelative/dead parser. Use shared components |
| `views/pipeline/PipelineMonitor.tsx` | Delete CSS import, formatRelative/formatElapsed. Use shared PageHeader/StatusBadge |
| `views/pipeline/RunHistoryTable.tsx` | Inline styles → Tailwind |
| `views/pipeline/StageWaterfall.tsx` | CSS classes reference layouts.css |
| `views/pipeline/PipelineLogViewer.tsx` | CSS classes reference layouts.css |
| `views/publishing-queue/PublishingQueue.tsx` | Delete CSS import. Tailwind + shared components |
| `views/monetisation/MonetisationProgress.tsx` | Delete CSS import/formatNumber. Tailwind + shared ProgressBar/PageHeader |
| `views/channel-health/ChannelHealth.tsx` | Delete CSS import. Tailwind + shared PageHeader/ErrorState |
| `views/learning/LearningView.tsx` | Inline → Tailwind, shared PageHeader/ErrorState |
| `views/learning/LearningOverview.tsx` | Delete LABEL_STYLE/CARD_STYLE. Tailwind + shared KpiCard/SectionHeader/ChartCard |
| `views/learning/BanditArms.tsx` | Delete LABEL_STYLE. Tailwind + shared EmptyState |
| `views/learning/RewardHistory.tsx` | Delete LABEL_STYLE/CARD_STYLE/CustomTooltip. Tailwind + shared ChartCard/ChartTooltip/KpiCard |
| `views/learning/HookClassifier.tsx` | Delete LABEL_STYLE/CARD_STYLE. Tailwind + shared ChartCard/SectionHeader |
| `views/learning/ConfigUpdates.tsx` | Delete LABEL_STYLE/CARD_STYLE. Tailwind + shared ChartCard/SectionHeader |
| `views/engagement/EngagementView.tsx` | Delete dead response parser, inline → Tailwind, shared PageHeader/ErrorState/KpiCard. Use platform registry |
| `views/content/ContentReviewView.tsx` | Adopt shared PageHeader |
| `views/schedule.tsx` | Adopt shared PageHeader/ErrorState |
| `views/health/SystemHealthView.tsx` | Inline → Tailwind, shared PageHeader/ErrorState. Use platform registry |
| `components/blueprints/blueprint-card.tsx` | Delete local `formatNumber`, import `formatCompact` |

## 5. Migration Rules

1. **Spacing/padding/margin** → Tailwind: `p-4`, `mb-6`, `gap-3`
2. **Typography** → Tailwind: `text-xs`, `font-semibold`, `tracking-tight`, `text-text-muted`
3. **Layout** → Tailwind: `flex items-center gap-2`, `grid grid-cols-4`
4. **Colors** → Tailwind semantic tokens: `text-text-primary`, `bg-bg-surface`, `border-border`
5. **Radius** → Tailwind: `rounded-lg`, `rounded-md`
6. **Complex grid areas** → `layouts.css`
7. **Transitions on data-driven widths** → `layouts.css`
8. **Recharts tooltip** → `layouts.css` (can't style via Tailwind)
9. **Keyframe animations** → `layouts.css` or `globals.css`
10. **Dynamic niche/platform colors** → keep as `style={{ color: niche.accentHex }}`
11. **`LABEL_STYLE` pattern** → existing `label-caps` class from `globals.css:88`
12. **`CARD_STYLE` pattern** → `className="bg-bg-surface border border-border rounded-lg p-4"`

## 6. Migration Order

1. Create shared components (no existing code touched yet)
2. Create `layouts.css` (extract from existing CSS files)
3. Create `lib/platforms.ts`
4. Update `globals.css` (add imports, move shared classes)
5. Consolidate `lib/format.ts` (delete duplicates)
6. API client hygiene (move types, fix `any`, fix query keys)
7. Rename 4 hook files + update imports
8. Delete 27 dead files
9. Migrate Mission Control (highest complexity — bento grid, most card types)
10. Migrate Analytics (charts, KPI cards, tables)
11. Migrate Pipeline (waterfall, log viewer)
12. Migrate Publishing Queue (card list)
13. Migrate Monetisation (progress bars)
14. Migrate Channel Health (simplest CSS file)
15. Migrate Learning views (5 files, inline → Tailwind)
16. Migrate Engagement views (3 files, inline → Tailwind)
17. Migrate System Health (inline → Tailwind)
18. Migrate remaining views (Content Review, Schedule, Shell, sidebar, focus-mode, detail views)
19. Delete 6 CSS files
20. Build verification: `npm run build` zero errors
21. Visual regression check

## 7. Quality Gates

- Zero per-view CSS files after migration
- ≤44 remaining `style={{}}` usages (all dynamic runtime values)
- Every view has `<PageHeader>`, `<ErrorState>` (or equivalent), loading state
- `npm run build` passes with zero TypeScript errors
- Visual parity: dashboard looks identical before and after
- No duplicate utility functions remain
- All platform colors/labels sourced from `lib/platforms.ts`
- All niche filter dropdowns sourced from niche registry
- Zero `LABEL_STYLE` or `CARD_STYLE` constants remain
- All `aria-label` and `alt` gaps closed (17 elements)
- Hook files all follow kebab-case convention

## 8. Net Impact

| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| Source files | ~105 | ~90 | -27 deleted, +12 created |
| Source lines | ~25,500 | ~19,200 | **-6,300** |
| Per-view CSS | 6 files, 2,463 lines | 0 | **-6 files** |
| Dead code | 21 files, 3,705 lines | 0 | **-21 files** |
| Inline styles (static) | ~470 | 0 | **-470** |
| Shared components | 8 | 20 | **+12** |
| Duplicated functions | ~25 | 0 | **-25** |
| Build CSS chunks | 7 | 1 | **-6 HTTP requests/route** |
| Accessibility gaps | 17 | 0 | **+17 fixed** |
