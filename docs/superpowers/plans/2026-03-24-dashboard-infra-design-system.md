# Dashboard Infrastructure & Design System — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate styling, extract shared components, delete dead code — 6,400 fewer lines, 10 new shared primitives, zero per-view CSS files.

**Architecture:** Hybrid Tailwind + shared `layouts.css` for complex patterns. New shared components replace 25+ duplicate implementations. Dead code deletion first, then view-by-view migration with build verification after each task.

**Tech Stack:** React 19, Tailwind CSS v4, Vite 7, TypeScript 5.9, Recharts 3, shadcn/ui (Radix), TanStack Query 5

**Spec:** `docs/superpowers/specs/2026-03-24-dashboard-infra-design-system-design.md`

**Working directory:** `/Users/anarchistsid/GenLab/dashboard/frontend`

**Build command:** `npm run build` (must pass after every task)

**Visual check:** After all tasks, open dashboard at `http://localhost:5151` — must look identical to before.

---

## Task 1: Create shared `layouts.css`

Extract CSS patterns that are genuinely awkward in Tailwind from the 6 per-view CSS files into a single shared file.

**Files:**
- Create: `src/styles/layouts.css`
- Modify: `src/styles/globals.css` (add import)

- [ ] **Step 1:** Create `src/styles/layouts.css` with sections extracted from existing CSS files per spec §3.2:
  - Bento grid from `mission-control.css:45-103` (grid-template-areas + 3 responsive breakpoints + area assignments)
  - KPI hero grid from `mission-control.css:180-209` (4-col + mobile scroll-snap)
  - Chart card + tooltip from `analytics.css:145-158, 330-357`
  - Data table from `analytics.css:294-328` merged with `pipeline-monitor.css:394-424` into single `.data-table` class
  - Two-column layout from `analytics.css:162-173`
  - Progress bar fills from `monetisation-progress.css:128-153`
  - Stage waterfall from `pipeline-monitor.css:105-216`
  - Score ring SVG from `channel-health.css:38-73`
  - Log viewer from `pipeline-monitor.css:236-367`
  - Keyframe animations: `cardIn` from `mission-control.css:120-129`, `slideIn` from `publishing-queue.css:256-259`
  - AI Insight gradient border mask from `mission-control.css:397-410`
  - Status dots with glow from `channel-health.css:154-164`

- [ ] **Step 2:** Add to `src/styles/globals.css`:
  - `@import "./layouts.css";` after the tokens import
  - Move `.bento-card`, `.btn-primary`, `.niche-dot` from `mission-control.css` into `globals.css` (they're used across multiple views)

- [ ] **Step 3:** Run `npm run build` — must pass (new CSS is imported but not yet consumed, old CSS still exists)

- [ ] **Step 4:** Commit: `feat(dashboard): create shared layouts.css with complex CSS patterns`

---

## Task 2: Create platform registry

Single source of truth for platform colors, labels, and hex values.

**Files:**
- Create: `src/lib/platforms.ts`

- [ ] **Step 1:** Create `src/lib/platforms.ts` with `PlatformInfo` interface, `PLATFORMS` array, `getPlatformInfo()`, `PLATFORM_COLORS`, `PLATFORM_LABELS` exports per spec §3.5.10. Include all 6 platforms: instagram, youtube, facebook, x_twitter, threads, tiktok.

- [ ] **Step 2:** Run `npm run build` — must pass (new file, not yet imported)

- [ ] **Step 3:** Commit: `feat(dashboard): create platform registry as single source of truth`

---

## Task 3: Create shared components

Build all 10 new shared components. None replace existing code yet — they're additive.

**Files:**
- Create: `src/components/shared/page-header.tsx`
- Create: `src/components/shared/kpi-card.tsx`
- Create: `src/components/shared/section-header.tsx`
- Create: `src/components/shared/chart-card.tsx`
- Create: `src/components/shared/stat-row.tsx`
- Create: `src/components/shared/error-state.tsx`
- Create: `src/components/shared/loading-skeleton.tsx`
- Create: `src/components/shared/progress-bar.tsx`
- Create: `src/components/charts/chart-tooltip.tsx`

- [ ] **Step 1:** Create each component per spec §3.5.1–3.5.9 interfaces. Each component:
  - Uses Tailwind classes (referencing design tokens via `@theme` mappings in `globals.css`)
  - Includes accessibility attributes (`role="alert"` on ErrorState, `aria-busy` on LoadingSkeleton, `role="progressbar"` on ProgressBar)
  - Uses `cn()` from `@/lib/utils` for className merging
  - `KpiCard` imports `useCountUp` from `@/hooks/use-count-up`
  - `ProgressBar` absorbs animation logic from `AnimatedProgress` (delayed mount + CSS transition)
  - `ChartTooltip` (in `charts/`) uses styles from `layouts.css` `.chart-tooltip` class

- [ ] **Step 2:** Run `npm run build` — must pass (new files, not yet imported by any view)

- [ ] **Step 3:** Commit: `feat(dashboard): create 10 shared UI primitives`

---

## Task 4: API client hygiene + format consolidation + hook renames

Clean up the API layer, consolidate duplicate utils, rename hooks.

**Files:**
- Modify: `src/api/client.ts` — move 4 type defs out, fix `any`
- Modify: `src/api/types.ts` — receive types, add `EngagementStatusResponse`
- Modify: `src/api/query-keys.ts` — fix `learning.status` and `trends.current` to factory functions
- Modify: `src/lib/format.ts` — delete `formatCompactNumber` (duplicate of `formatCompact`)
- Rename: `src/hooks/useAnalyticsOverview.ts` → `src/hooks/use-analytics-overview.ts`
- Rename: `src/hooks/useCrossNicheOverview.ts` → `src/hooks/use-cross-niche-overview.ts`
- Rename: `src/hooks/usePipelineLogs.ts` → `src/hooks/use-pipeline-logs.ts`
- Rename: `src/hooks/usePipelineMonitor.ts` → `src/hooks/use-pipeline-monitor.ts`
- Modify: All files importing the renamed hooks (~9 files — update import paths)

- [ ] **Step 1:** Move `CrossNicheOverviewResponse`, `DetailedHealthResponse`, `LaunchAgentInfo`, `NotificationPreferences` from `client.ts` to `types.ts`. Update imports in `client.ts` and `useCrossNicheOverview.ts`. Add `EngagementStatusResponse` interface. Fix `engagementApi.status` return type from `any` to `EngagementStatusResponse`.

- [ ] **Step 2:** In `query-keys.ts`, convert `learning.status` and `trends.current` from raw arrays to factory functions: `status: () => ["learning", "status"] as const`. Update `use-learning.ts` and `use-trends.ts` to call the factory: `queryKeys.learning.status()`.

- [ ] **Step 3:** In `lib/format.ts`, delete `formatCompactNumber()` (lines 87-91). Grep for any callers and update to `formatCompact()`.

- [ ] **Step 4:** Rename the 4 hook files (git mv). Update all import paths that reference them. Files to update:
  - `useAnalyticsOverview` importers: `Analytics.tsx`
  - `useCrossNicheOverview` importers: `MissionControl.tsx`, `KpiHero.tsx`, `AlertBanner.tsx`, `PublishingHealth.tsx`
  - `usePipelineLogs` importers: `PipelineLogViewer.tsx`
  - `usePipelineMonitor` importers: `PipelineMonitor.tsx`, `StageWaterfall.tsx`

- [ ] **Step 5:** Run `npm run build` — must pass

- [ ] **Step 6:** Commit: `refactor(dashboard): API hygiene, format consolidation, hook renames`

---

## Task 5: Delete dead code (Phase 1)

Remove 22 orphaned files that have zero imports.

**Files:**
- Delete: 15 dead chart components in `src/components/charts/` (all except `AnimatedProgress.tsx` and `MiniSparkline.tsx`)
- Delete: `src/components/charts/AnimatedProgress.tsx` (logic absorbed by shared `ProgressBar`)
- Delete: `src/hooks/use-analytics.ts`
- Delete: `src/hooks/use-version-history.ts`
- Delete: `src/hooks/use-reduced-motion.ts`
- Delete: `src/lib/slot-scoring.ts`
- Delete: `src/lib/search-index.ts`
- Delete: `src/lib/announce.ts`

- [ ] **Step 1:** Delete all 22 files listed above.

- [ ] **Step 2:** Update any files that imported `AnimatedProgress` to import `ProgressBar` from `@/components/shared/progress-bar` instead. Files: `LearningOverview.tsx`, `HookClassifier.tsx`, `ConfigUpdates.tsx`, `MonetisationProgress.tsx`, `MonetisationCompact.tsx`, `LearningLoopCard.tsx`. Adjust props: `AnimatedProgress` took `value`/`color`/`height` → `ProgressBar` takes the same (compatible interface, add `animated` prop).

- [ ] **Step 3:** Run `npm run build` — must pass

- [ ] **Step 4:** Commit: `chore(dashboard): delete 22 dead files (3,743 lines)`

---

## Task 6: Migrate Mission Control

Replace MC CSS classes with Tailwind + shared components. This is the largest single migration.

**Files:**
- Modify: `src/views/mission-control/MissionControl.tsx` — use `PageHeader`, delete CSS import
- Modify: `src/views/mission-control/KpiHero.tsx` — use shared `KpiCard`
- Modify: `src/views/mission-control/TopPostSpotlight.tsx` — CSS → Tailwind
- Modify: `src/views/mission-control/LearningLoopCard.tsx` — CSS → Tailwind
- Modify: `src/views/mission-control/AiInsightCard.tsx` — CSS → Tailwind (keep gradient from `layouts.css`)
- Modify: `src/views/mission-control/PublishTimeline.tsx` — CSS → Tailwind
- Modify: `src/views/mission-control/UpcomingQueue.tsx` — CSS → Tailwind
- Modify: `src/views/mission-control/ChannelStrip.tsx` — CSS → Tailwind
- Modify: `src/views/mission-control/EngagementFeed.tsx` — CSS → Tailwind
- Modify: `src/views/mission-control/TrendRadar.tsx` — CSS → Tailwind
- Modify: `src/views/mission-control/ContentQuality.tsx` — CSS → Tailwind + shared `StatRow`
- Modify: `src/views/mission-control/PipelineCountdowns.tsx` — CSS → Tailwind
- Modify: `src/views/mission-control/MonetisationCompact.tsx` — CSS → Tailwind
- Modify: `src/views/mission-control/PublishingHealth.tsx` — inline styles → Tailwind, use platform registry
- Modify: `src/views/mission-control/AlertBanner.tsx` — if it has inline styles, convert

- [ ] **Step 1:** In `MissionControl.tsx`: remove `import "./mission-control.css"`. Replace `mc-page` div with `className="max-w-7xl mx-auto"` (or just remove — Shell handles this). Replace `mc-header`/`mc-greeting`/`mc-sprint`/`mc-date` with shared `<PageHeader>`. Replace `mc-grid-v2` with `className="mc-grid-v2"` (class now lives in `layouts.css`). Replace `mc-error` ErrorState with shared `<ErrorState>`. Replace LoadingSkeleton with shared `<LoadingSkeleton variant="bento">`. Grid area divs (`area-kpi`, `area-spotlight`, etc.) keep their class names (defined in `layouts.css`).

- [ ] **Step 2:** In `KpiHero.tsx`: replace the local `KpiCard` component definition with import from `@/components/shared/kpi-card`. Adjust props to match shared interface.

- [ ] **Step 3:** Migrate each remaining MC sub-component: replace CSS class references (`.top-post-layout`, `.channel-strip`, `.engagement-feed`, etc.) with equivalent Tailwind classes. Use spec §3.3 as reference for MC classes. Pattern: `.top-post-layout` → `className="flex gap-4 items-start"`, `.channel-tile` → `className="bg-bg-surface border border-border rounded-lg overflow-hidden flex"`, etc.

- [ ] **Step 4:** Run `npm run build` — must pass. The old `mission-control.css` is still present (import removed, but file exists). No references should remain.

- [ ] **Step 5:** Commit: `refactor(dashboard): migrate Mission Control from CSS to Tailwind + shared components`

---

## Task 7: Migrate Analytics

**Files:**
- Modify: `src/views/analytics/Analytics.tsx`

- [ ] **Step 1:** Remove `import "./analytics.css"`. Delete local `KpiCard`, `KpiHero`, `ChartTooltip`, `formatRelative` definitions. Delete dead response parser (lines 618-623). Import shared `KpiCard`, `ChartTooltip`, `PageHeader`, `EmptyState` + `formatRelativeTime` from `@/lib/format` + `PLATFORM_COLORS`/`PLATFORM_LABELS` from `@/lib/platforms`.

- [ ] **Step 2:** Replace all CSS class references with Tailwind equivalents. Key mappings: `.analytics-page` → remove (Shell), `.analytics-header` → `<PageHeader>`, `.analytics-select` → shadcn `<Select>`, `.kpi-grid` → `className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6"`, `.kpi-hero-grid` stays (in `layouts.css`), `.chart-card` stays (in `layouts.css`), `.analytics-two-col` stays (in `layouts.css`), `.platform-row` → Tailwind flex, `.funnel-*` → Tailwind + shared `ProgressBar`, `.top-table` stays (in `layouts.css` as `.data-table`), `.estimated-banner` → shared `AlertBanner`, `.analytics-tabs` → shadcn `Tabs`, `.analytics-empty` → shared `EmptyState`.

- [ ] **Step 3:** Convert all 62 inline styles to Tailwind (keep 2 Recharts color props as `style`).

- [ ] **Step 4:** Run `npm run build` — must pass

- [ ] **Step 5:** Commit: `refactor(dashboard): migrate Analytics from CSS + inline styles to Tailwind + shared components`

---

## Task 8: Migrate Pipeline

**Files:**
- Modify: `src/views/pipeline/PipelineMonitor.tsx`
- Modify: `src/views/pipeline/RunHistoryTable.tsx`
- Modify: `src/views/pipeline/StageWaterfall.tsx`
- Modify: `src/views/pipeline/PipelineLogViewer.tsx`

- [ ] **Step 1:** In `PipelineMonitor.tsx`: remove `import "./pipeline-monitor.css"`. Delete local `formatRelative` and `formatElapsed` — import `formatRelativeTime` and `formatDuration` from `@/lib/format`. Replace `pm-page`/`pm-header`/`pm-title` with shared `PageHeader`. Replace `pipe-badge-*` with shared `StatusBadge`. Replace `niche-card` with Tailwind. `waterfall-*` classes stay (in `layouts.css`).

- [ ] **Step 2:** In `RunHistoryTable.tsx`: convert 35 inline styles to Tailwind. Use `.data-table` from `layouts.css` instead of `.run-table`.

- [ ] **Step 3:** `StageWaterfall.tsx` and `PipelineLogViewer.tsx`: classes now reference `layouts.css` automatically (imported via globals). Minimal changes needed — just verify class names match.

- [ ] **Step 4:** Run `npm run build` — must pass

- [ ] **Step 5:** Commit: `refactor(dashboard): migrate Pipeline from CSS + inline styles to Tailwind`

---

## Task 9: Migrate Publishing Queue

**Files:**
- Modify: `src/views/publishing-queue/PublishingQueue.tsx`

- [ ] **Step 1:** Remove `import "./publishing-queue.css"`. Replace `pq-page`/`pq-header`/`pq-title`/`pq-subtitle` with shared `PageHeader`. Replace `.stat-pill` with shared `KpiCard` (small variant). Replace `.pq-tabs` with shadcn `Tabs`. Replace `.post-card` + children with Tailwind flex. Replace `.queue-status-badge` with shared `StatusBadge`. Replace `.pca-btn` with Tailwind icon buttons. Replace `.bulk-bar` with Tailwind. Replace `.pq-empty` with shared `EmptyState`. Replace `.pq-loading` with shared `LoadingSkeleton variant="card-list"`.

- [ ] **Step 2:** Run `npm run build` — must pass

- [ ] **Step 3:** Commit: `refactor(dashboard): migrate Publishing Queue from CSS to Tailwind + shared components`

---

## Task 10: Migrate Monetisation

**Files:**
- Modify: `src/views/monetisation/MonetisationProgress.tsx`

- [ ] **Step 1:** Remove `import "./monetisation-progress.css"`. Delete local `formatNumber` — import `formatCompact` from `@/lib/format`. Delete local `PLATFORM_COLORS` — import from `@/lib/platforms`. Replace `mp-page`/`mp-header`/`mp-title`/`mp-subtitle` with shared `PageHeader`. Replace `.mp-bar-*` with shared `ProgressBar` (using `thresholds` prop for red/amber/green). Replace `.mp-niche-card` with Tailwind card. Convert all 51 inline styles to Tailwind (keep 3 dynamic niche colors).

- [ ] **Step 2:** Run `npm run build` — must pass

- [ ] **Step 3:** Commit: `refactor(dashboard): migrate Monetisation from CSS + inline styles to Tailwind`

---

## Task 11: Migrate Channel Health

**Files:**
- Modify: `src/views/channel-health/ChannelHealth.tsx`

- [ ] **Step 1:** Remove `import "./channel-health.css"`. Delete local `PLATFORMS` / `NICHES` constants — import from `@/lib/platforms` and `@/niches/registry`. Replace `ch-page`/`ch-header`/`ch-title`/`ch-subtitle` with shared `PageHeader` (with `max-w-3xl` inner constraint). Use `PlatformIcon` component instead of inline icon mapping. Replace `.ch-score-ring` SVG with classes from `layouts.css`. Replace `.ch-status-label`/`.ch-dot` with classes from `layouts.css` + shared `StatusBadge`. Replace `.ch-loading` with shared `LoadingSkeleton`. Error state → shared `ErrorState`.

- [ ] **Step 2:** Run `npm run build` — must pass

- [ ] **Step 3:** Commit: `refactor(dashboard): migrate Channel Health from CSS to Tailwind + shared components`

---

## Task 12: Migrate Learning views

**Files:**
- Modify: `src/views/learning/LearningView.tsx`
- Modify: `src/views/learning/LearningOverview.tsx`
- Modify: `src/views/learning/BanditArms.tsx`
- Modify: `src/views/learning/RewardHistory.tsx`
- Modify: `src/views/learning/HookClassifier.tsx`
- Modify: `src/views/learning/ConfigUpdates.tsx`

- [ ] **Step 1:** In `LearningView.tsx`: replace inline-styled page container with Tailwind. Replace inline h1/p with shared `PageHeader`. Replace inline error state with shared `ErrorState`.

- [ ] **Step 2:** In all 5 tab components: delete `LABEL_STYLE` and `CARD_STYLE` constants. Replace `style={LABEL_STYLE}` with `className="label-caps"`. Replace `style={CARD_STYLE}` with `className="bg-bg-surface border border-border rounded-lg p-4"`. Replace `style={{ ...CARD_STYLE, ... }}` spreads with Tailwind classes.

- [ ] **Step 3:** In `RewardHistory.tsx`: delete local `CustomTooltip` — import shared `ChartTooltip`. Replace `CARD_STYLE` hero cards with shared `KpiCard variant="hero"`. Wrap charts in shared `ChartCard`.

- [ ] **Step 4:** In `BanditArms.tsx`: replace inline-styled table with `.data-table` class from `layouts.css` + Tailwind. Replace empty state with shared `EmptyState`.

- [ ] **Step 5:** In `LearningOverview.tsx`: replace stat cards with shared `KpiCard`. Replace threshold rows with shared `ProgressBar`. Wrap sections in shared `ChartCard` or `SectionHeader`.

- [ ] **Step 6:** Convert all remaining inline styles across all 5 files to Tailwind (keep ~6 dynamic niche colors).

- [ ] **Step 7:** Run `npm run build` — must pass

- [ ] **Step 8:** Commit: `refactor(dashboard): migrate Learning views from inline styles to Tailwind + shared components`

---

## Task 13: Migrate Engagement views

**Files:**
- Modify: `src/views/engagement/EngagementView.tsx`
- Modify: `src/views/engagement/CommentFeed.tsx`
- Modify: `src/views/engagement/ReplyQueue.tsx`

- [ ] **Step 1:** In `EngagementView.tsx`: delete dead response parser (lines 121-131 — shapes that server never returns). Delete local `StatCard` — use shared `KpiCard`. Delete local `FilterSelect` — use shared `FilterBar` or shadcn `Select`. Delete local `NICHES`/`PLATFORMS` constants — import from registries. Replace inline page container with Tailwind. Replace inline h1/p with shared `PageHeader`. Replace inline error state with shared `ErrorState`.

- [ ] **Step 2:** Convert inline styles in `CommentFeed.tsx` (13) and `ReplyQueue.tsx` (18) to Tailwind.

- [ ] **Step 3:** Run `npm run build` — must pass

- [ ] **Step 4:** Commit: `refactor(dashboard): migrate Engagement from inline styles to Tailwind + shared components`

---

## Task 14: Migrate System Health

**Files:**
- Modify: `src/views/health/SystemHealthView.tsx`

- [ ] **Step 1:** Delete local `NICHES` and `PLATFORMS` constants — import from registries. Replace inline page container with Tailwind. Replace inline h1 with shared `PageHeader`. Replace inline error state with shared `ErrorState`. Convert 50 inline styles to Tailwind (keep 4 dynamic: status colors, SVG ring stroke).

- [ ] **Step 2:** Run `npm run build` — must pass

- [ ] **Step 3:** Commit: `refactor(dashboard): migrate System Health from inline styles to Tailwind + shared components`

---

## Task 15: Migrate remaining files

Shell, sidebar, focus-mode, detail views, command palette, schedule components, and remaining shared components.

**Files:**
- Modify: `src/components/layout/shell.tsx` — 5 static inline styles → Tailwind
- Modify: `src/components/layout/sidebar.tsx` — 15 static inline styles → Tailwind (keep 10 dynamic)
- Modify: `src/components/layout/command-palette.tsx` — 10 inline styles → Tailwind
- Modify: `src/components/review/focus-mode.tsx` — 45 static inline styles → Tailwind (keep 2 dynamic)
- Modify: `src/components/review/PlatformAdaptationsPanel.tsx` — 7 inline styles → Tailwind
- Modify: `src/components/schedule/schedule-board.tsx` — 8 inline styles → Tailwind
- Modify: `src/components/shared/AlertBanner.tsx` — 4 inline styles → Tailwind
- Modify: `src/components/shared/PlatformIcon.tsx` — review, keep dynamic styles
- Modify: `src/components/blueprints/blueprint-card.tsx` — delete local `formatNumber`, import `formatCompact`
- Modify: `src/niches/detail-views/AiNewsDetailView.tsx` — 13 inline → Tailwind
- Modify: `src/niches/detail-views/GamingDetailView.tsx` — 12 inline → Tailwind
- Modify: `src/niches/detail-views/GenericDetailView.tsx` — 13 inline → Tailwind
- Modify: `src/views/content/ContentReviewView.tsx` — adopt shared `PageHeader`
- Modify: `src/views/content/FocusOverlay.tsx` — use platform registry
- Modify: `src/views/schedule.tsx` — adopt shared `PageHeader`/`ErrorState`

- [ ] **Step 1:** Migrate Shell: convert 5 `style={{ backgroundColor: "var(--surface-*)" }}` to `className="bg-bg-primary"` / `bg-bg-surface` / `bg-bg-elevated`. Keep avatar `color-mix` as style.

- [ ] **Step 2:** Migrate sidebar: convert 15 static inline styles to Tailwind. Keep 10 dynamic (niche accent, socket dot, conditional animations).

- [ ] **Step 3:** Migrate focus-mode: convert 45 static inline styles (`var(--text-muted)` → `text-text-muted`, `var(--border)` → `border-border`, `var(--bg-base)` → `bg-bg-primary`). Keep 2 dynamic.

- [ ] **Step 4:** Migrate command-palette, PlatformAdaptationsPanel, schedule-board, AlertBanner, PlatformIcon, blueprint-card inline styles to Tailwind.

- [ ] **Step 5:** Migrate 3 niche detail views: convert all 38 inline styles to Tailwind.

- [ ] **Step 6:** Adopt `PageHeader` in ContentReviewView and Schedule. Adopt `ErrorState` in Schedule.

- [ ] **Step 7:** Use platform registry in FocusOverlay.

- [ ] **Step 8:** Run `npm run build` — must pass

- [ ] **Step 9:** Commit: `refactor(dashboard): migrate shell, sidebar, focus-mode, detail views, and remaining components`

---

## Task 16: Delete CSS files + final verification

Remove all 6 per-view CSS files (their imports were already removed in tasks 6-11).

**Files:**
- Delete: `src/views/analytics/analytics.css`
- Delete: `src/views/mission-control/mission-control.css`
- Delete: `src/views/pipeline/pipeline-monitor.css`
- Delete: `src/views/publishing-queue/publishing-queue.css`
- Delete: `src/views/monetisation/monetisation-progress.css`
- Delete: `src/views/channel-health/channel-health.css`

- [ ] **Step 1:** Delete all 6 CSS files.

- [ ] **Step 2:** Run `npm run build` — must pass with zero errors. If any file still imports a deleted CSS file, the build will fail — fix the import.

- [ ] **Step 3:** Grep for remaining issues:
  - `grep -rn "style={{" src/ --include="*.tsx" | wc -l` — should be ≤50 (all dynamic)
  - `grep -rn "LABEL_STYLE\|CARD_STYLE" src/ --include="*.tsx"` — should be 0
  - `grep -rn "\.css" src/views/ --include="*.tsx"` — should be 0 (no CSS imports in views)
  - `grep -rn "formatCompactNumber\|formatElapsed\|function formatRelative\|function formatNumber" src/ --include="*.tsx" --include="*.ts"` — should be 0

- [ ] **Step 4:** Rebuild frontend for the served dashboard:
  ```bash
  cd /Users/anarchistsid/GenLab/dashboard/frontend && npm run build
  ```

- [ ] **Step 5:** Visual verification: open `http://localhost:5151` and check each page looks identical:
  - Mission Control (bento grid, KPI cards, channel strip)
  - Analytics (charts, tables, KPI hero row)
  - Pipeline (waterfall, log viewer, run history)
  - Publishing Queue (card list, status badges, bulk actions)
  - Monetisation (progress bars, niche cards)
  - Channel Health (score ring, platform cards)
  - Learning (all 5 tabs)
  - Engagement (comment feed, filters)
  - System Health (service cards, token matrix)
  - Schedule (week + month views)
  - Content Review (card grid, focus overlay)

- [ ] **Step 6:** Commit: `chore(dashboard): delete 6 per-view CSS files (2,463 lines) — migration complete`

- [ ] **Step 7:** Final commit in dashboard submodule, then update parent:
  ```bash
  cd /Users/anarchistsid/GenLab
  git add dashboard
  git commit -m "feat(dashboard): infrastructure & design system upgrade — 6,400 lines removed, 10 shared components"
  ```
