/**
 * Query key factory — single source of truth for all TanStack Query keys.
 *
 * Use hierarchical keys so `invalidateQueries({ queryKey: ["pipeline"] })`
 * correctly invalidates all pipeline-related queries.
 */
export const queryKeys = {
  blueprints: {
    all: () => ["blueprints"] as const,
    list: (params?: Record<string, string>) =>
      ["blueprints", params] as const,
    detail: (id: string) => ["blueprints", id] as const,
  },
  stories: {
    all: () => ["stories"] as const,
    list: (params?: Record<string, string>) =>
      ["stories", params] as const,
    detail: (id: string) => ["stories", "detail", id] as const,
  },
  pipeline: {
    all: () => ["pipeline"] as const,
    globalStatus: () => ["pipeline", "status"] as const,
    status: (nicheId: string) =>
      ["pipeline", nicheId, "status"] as const,
    list: (params?: Record<string, string>) =>
      ["pipeline", "runs", params] as const,
    runs: (nicheId: string, limit?: number) =>
      ["pipeline", nicheId, "runs", limit] as const,
    runDetail: (id: string) =>
      ["pipeline", "runs", id] as const,
    qualityStats: () => ["pipeline", "quality-stats"] as const,
  },
  schedule: {
    all: () => ["schedule"] as const,
    range: (from: string, to: string) =>
      ["schedule", from, to] as const,
    coverage: (from: string, to: string) =>
      ["schedule", "coverage", from, to] as const,
  },
  analytics: {
    all: () => ["analytics"] as const,
    section: (nicheId: string, section: string, params?: Record<string, string>) =>
      ["analytics", nicheId, section, params] as const,
    publishing: (params?: Record<string, string>) =>
      ["analytics", "publishing", params] as const,
    content: (params?: Record<string, string>) =>
      ["analytics", "content", params] as const,
    pipeline: (params?: Record<string, string>) =>
      ["analytics", "pipeline", params] as const,
    heatmap: (params?: Record<string, string>) =>
      ["analytics", "heatmap", params] as const,
    funnel: () => ["analytics", "funnel"] as const,
    dailyCosts: (params?: Record<string, string>) =>
      ["analytics", "daily-costs", params] as const,
    engagement: (params?: Record<string, string>) =>
      ["analytics", "engagement", params] as const,
    engagementSummary: (params?: Record<string, string>) =>
      ["analytics", "engagement-summary", params] as const,
    contentPerformance: (params?: Record<string, string>) =>
      ["analytics", "content-performance", params] as const,
    trends: (params?: Record<string, string>) =>
      ["analytics", "trends", params] as const,
    audience: (params?: Record<string, string>) =>
      ["analytics", "audience", params] as const,
    viralityBreakdown: (params?: Record<string, string>) =>
      ["analytics", "virality-breakdown", params] as const,
    monetization: (params?: Record<string, string>) =>
      ["analytics", "monetization", params] as const,
    demographics: () => ["analytics", "demographics"] as const,
    trends7d: () => ["analytics", "trends-7d"] as const,
    overview: (nicheId: string, window: string) =>
      ["analytics", "overview", nicheId, window] as const,
    crossNiche: () => ["analytics", "cross-niche"] as const,
  },
  review: {
    queue: (nicheId: string) => ["review-queue", nicheId] as const,
  },
  crossNiche: {
    all: () => ["cross-niche"] as const,
    overview: () => ["cross-niche", "overview"] as const,
    analytics: (window: string) =>
      ["cross-niche", "analytics", window] as const,
  },
  queue: {
    all: () => ["queue"] as const,
    list: (nicheId: string, status?: string) =>
      ["queue", nicheId, status] as const,
    stats: (nicheId: string) => ["queue", "stats", nicheId] as const,
  },
  monetisation: {
    progress: () => ["monetisation", "progress"] as const,
  },
  sponsorship: {
    readiness: () => ["sponsorship", "readiness"] as const,
    recentTransitions: (windowHours: number) =>
      ["sponsorship", "recent-transitions", windowHours] as const,
  },
  mediaKit: {
    get: (nicheId: string) => ["media-kit", nicheId] as const,
    all: () => ["media-kit", "_all"] as const,
  },
  bandit: {
    hourPosteriors: (nicheId: string) =>
      ["bandit", "hour-posteriors", nicheId] as const,
  },
  strategist: {
    /** PR Strategist-2b (2026-07-01) — per-niche latest report cache key.
     *  Card invalidates on review-mutation success so the "unreviewed"
     *  badge count updates without a full re-poll. */
    latest: (nicheId: string) => ["strategist", "latest", nicheId] as const,
    unreviewed: (limit: number) =>
      ["strategist", "unreviewed", limit] as const,
  },
  crossNicheTransfer: {
    /** Intervention 2 observability (2026-07-01) — cross-niche
     *  priors cache key. Rewrites weekly (Mon 05:30 UTC); hourly
     *  poll matches. */
    priors: () => ["cross-niche-transfer", "priors"] as const,
  },
  topCreatorPriors: {
    /** B.2 + B.3 observability (2026-07-08) — A+B intelligence
     *  stack cache keys. Two independent artifact sources with
     *  independent flag gates + cadences. Niche-scoped because
     *  each niche has its own artifacts (unlike cross-niche
     *  transfer which is global). */
    latest: (nicheId: string) =>
      ["top-creator-priors", "latest", nicheId] as const,
    uploads: (nicheId: string) =>
      ["top-creator-priors", "uploads", nicheId] as const,
  },
  transformationBandit: {
    /** PR 14 (2026-07-05) — transformation-arm summary cache key.
     *  Reads live from bandit_arms (no artifact); 60s poll matches
     *  the AutoApprovalCalibrationCard cadence. */
    summary: () => ["transformation-bandit", "summary"] as const,
  },
  productBandit: {
    /** L3 PR 9 (2026-07-07) — product-arm summary cache key.
     *  Reads live from bandit_arms (arm_type='product'); 60s poll
     *  matches the TransformationBanditCard cadence. */
    summary: () => ["product-bandit", "summary"] as const,
    /** L3 PR 12a (2026-07-07) — per-niche selector divergence
     *  stats cache key. Rolling 7-day window; 60s poll. */
    divergenceStats: (nicheId: string, windowDays: number) =>
      ["product-bandit", "divergence-stats", nicheId, windowDays] as const,
  },
  attributionHealth: {
    /** Layer 5 attribution health (PR #Layer5, 2026-07-11) cache key.
     *  Backend query is a single window-scoped aggregation — cache
     *  by window_hours so different window selectors don't collide. */
    stats: (windowHours: number) =>
      ["attribution-health", "stats", windowHours] as const,
  },
  counterfactualReplay: {
    /** Intervention 7 observability (2026-07-01) — per-niche latest
     *  replay artifact. Monthly rewrite; daily poll is plenty. */
    latest: (nicheId: string) =>
      ["counterfactual-replay", "latest", nicheId] as const,
  },
  rewardAudit: {
    /** Phase 0.C observability (2026-08-14) — reward signal health
     *  per niche×platform. Backed by learning/reward-audit endpoint.
     *  Aggregated snapshot; 60s poll matches other Mission Control
     *  learning cards. */
    all: () => ["reward-audit", "all"] as const,
  },
  classifierQuality: {
    /** Phase 1.C observability (2026-08-14) — per-(source, name)
     *  verdict mix over 30d. Backed by learning/classifier-quality.
     *  Slow-moving aggregate; 5-min poll is plenty. */
    all: () => ["classifier-quality", "all"] as const,
  },
  autoExperiments: {
    /** #9 lifecycle observability (2026-07-23) — per-niche recent
     *  experiments + verdicts. Lifecycle timer fires every 6h; a
     *  5-minute poll is plenty of freshness. Keyed by niche_id and
     *  limit so the queue-depth and results views can coexist. */
    summary: (nicheId: string, limit: number) =>
      ["auto-experiments", "summary", nicheId, limit] as const,
  },
  trendAnticipation: {
    /** Intervention 5 Session 3 (2026-07-01) — per-niche latest artifact
     *  cache key. Polled every 10 minutes; artifact rewrites daily at
     *  03:30 UTC. */
    latest: (nicheId: string) =>
      ["trend-anticipation", "latest", nicheId] as const,
    /** Session 3b — per-niche accuracy measurement cache key.
     *  Polled hourly; artifact rewrites weekly on Monday 05:00 UTC. */
    accuracy: (nicheId: string) =>
      ["trend-anticipation", "accuracy", nicheId] as const,
  },
  revenue: {
    summary: () => ["revenue", "summary"] as const,
    clickTrends: () => ["revenue", "click-trends"] as const,
  },
  publishingMetrics: () => ["metrics", "publishing"] as const,
  publishingAlerts: () => ["alerts", "publishing"] as const,
  criticalAlerts: () => ["alerts", "critical"] as const,
  channelHealth: () => ["channel-health"] as const,
  config: {
    sources: (nicheId?: string) => ["config", "sources", nicheId] as const,
    sourceFilters: (nicheId?: string) => ["config", "source-filters", nicheId] as const,
    scheduleSlots: (nicheId?: string) => ["config", "schedule-slots", nicheId] as const,
    scoring: (nicheId?: string) => ["config", "scoring", nicheId] as const,
    templates: (nicheId?: string) => ["config", "templates", nicheId] as const,
  },
  learning: {
    status: () => ["learning", "status"] as const,
    hookClassifierStatus: () => ["learning", "hook-classifier-status"] as const,
    configUpdates: () => ["learning", "config-updates"] as const,
  },
  engagement: {
    recent: () => ["engagement", "recent"] as const,
    status: () => ["engagement", "status"] as const,
  },
  trends: {
    current: () => ["trends", "current"] as const,
  },
  health: () => ["health"] as const,
  healthDetailed: () => ["health", "detailed"] as const,
  tokenHealth: () => ["token-health"] as const,
  platformPosts: {
    tiktok: () => ["platform-posts", "tiktok"] as const,
    threads: () => ["platform-posts", "threads"] as const,
  },
  /** Operator emergency-stop primitive — list of active niche pauses.
   * Consumed by the Mission Control NichePauseCard at 60s polling. */
  schedulingPauses: {
    list: () => ["scheduling", "pauses"] as const,
  },
  /** Compliance event aggregation (Mission Control ComplianceStatsCard). */
  complianceStats: {
    fetch: (windowDays: number) => ["compliance", "stats", windowDays] as const,
  },
  /** Per-(niche, platform) publishing health matrix
   * (Mission Control PerPlatformHealthCard, PR B). */
  publishingHealthPerNiche: {
    fetch: (days: number) => ["publishing", "per-niche-health", days] as const,
  },
  /** Per-(niche, base_arm) bandit-platform-divergence rows
   * (Mission Control BanditPlatformDivergenceCard, PR AG). */
  banditPlatformDivergence: {
    fetch: (nicheId: string | undefined, minNPlays: number) =>
      [
        "learning",
        "bandit-platform-divergence",
        nicheId ?? "all",
        minNPlays,
      ] as const,
  },
  /** Source-discovery proposer rankings (per-niche source channels). */
  sourceDiscovery: {
    proposals: (nicheId: string, windowDays: number) =>
      ["source-discovery", "proposals", nicheId, windowDays] as const,
  },
  /** Source-performance bandit-arms data (per-niche per-source). */
  sourcePerformance: {
    list: (nicheId: string, topN: number) =>
      ["source-performance", nicheId, topN] as const,
  },
  /** L11 (2026-07-08 audit) — aggregated GENLAB_* flag states.
   *  Refreshes on demand (env doesn't change without SSH); a slow
   *  poll is fine. */
  flagState: {
    get: () => ["flag-state"] as const,
  },
  /** L4 (2026-07-08 audit) — Bayesian gate posterior state. */
  bayesianGateState: {
    get: () => ["bayesian-gate-state"] as const,
  },
  /** L4 (2026-07-08 audit) — conformal router state. */
  conformalRouterState: {
    get: () => ["conformal-router-state"] as const,
  },
  /** L5 (2026-07-08 audit) — ensemble vote summary. Keyed by
   *  window params so switching niche/window doesn't collide. */
  ensembleVotes: {
    summary: (nicheId: string | undefined, windowDays: number | undefined) =>
      ["ensemble-votes", "summary", nicheId ?? "all", windowDays ?? 7] as const,
  },
  /** L7 (2026-07-08 audit) — drift signal summary. Same key
   *  discipline as ensembleVotes. */
  driftSignals: {
    summary: (
      nicheId: string | undefined,
      windowDays: number | undefined,
      minAbsZ: number | undefined,
    ) =>
      [
        "drift-signals",
        "summary",
        nicheId ?? "all",
        windowDays ?? 7,
        minAbsZ ?? 2.0,
      ] as const,
  },
};
