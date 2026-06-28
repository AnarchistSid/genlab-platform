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
};
