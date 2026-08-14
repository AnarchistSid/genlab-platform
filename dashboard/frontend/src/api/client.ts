import type {
  PaginatedResponse,
  SingleResponse,
  Blueprint,
  Story,
  ScheduleDay,
  PipelineRun,
  DailyCost,
  PublishingRecord,
  EngagementRecord,
  EngagementSummary,
  FunnelStage,
  HeatmapCell,
  ContentPerformanceData,
  TrendDataPoint,
  AudienceSnapshot,
  ViralityBreakdownData,
  MonetizationDataEnvelope,
  BanditHourPosteriors,
  MediaKitData,
  MediaKitPortfolioData,
  MonetisationProgressData,
  OutreachTemplate,
  PortfolioOutreachTemplate,
  RecentTransitionsResponse,
  SponsorshipReadinessData,
  YouTubeDemographics,
  AnalyticsOverviewResponse,
  QueueItem,
  QueueStats,
  ChannelHealth,
  TokenHealthResponse,
  ConfigUpdateRow,
  HookClassifierNicheStatus,
  LearningStatus,
  EngagementComment,
  TopPost,
  TrendData,
  CrossNicheOverviewResponse,
  CrossNicheAnalytics,
  DetailedHealthResponse,
  NotificationPreferences,
  EngagementStatusResponse,
  QualityStats,
  ClickTrend,
  NichePauseRecord,
  ComplianceStatsResponse,
  PerNichePlatformHealthResponse,
  BanditPlatformDivergenceResponse,
  SourceDiscoveryProposalsResponse,
  SourcePerformanceBanditResponse,
} from "./types";

const BASE = "/api/v1";
let csrfToken: string | null = null;
let csrfTokenTimestamp = 0;
const CSRF_TOKEN_TTL = 30 * 60 * 1000; // 30 minutes

async function getCsrfToken(): Promise<string> {
  const now = Date.now();
  if (csrfToken && (now - csrfTokenTimestamp) < CSRF_TOKEN_TTL) {
    return csrfToken;
  }
  const resp = await fetch("/api/csrf-token");
  const data = (await resp.json()) as { csrf_token: string };
  csrfToken = data.csrf_token;
  csrfTokenTimestamp = Date.now();
  return csrfToken;
}

function clearCsrfToken(): void {
  csrfToken = null;
  csrfTokenTimestamp = 0;
}

async function fetchWithRetry(
  url: string,
  options: RequestInit,
  retries = 2,
  delay = 1000,
  timeout = 30000,
): Promise<Response> {
  for (let attempt = 0; attempt <= retries; attempt++) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);
    try {
      const resp = await fetch(url, { ...options, signal: controller.signal });
      clearTimeout(timeoutId);
      // Don't retry client errors (4xx) except 429
      if (resp.ok || (resp.status >= 400 && resp.status < 500 && resp.status !== 429)) {
        return resp;
      }
      // Retry on 5xx and 429
      if (attempt < retries) {
        await new Promise(r => setTimeout(r, delay * (attempt + 1)));
        continue;
      }
      return resp;
    } catch (err) {
      clearTimeout(timeoutId);
      // Network error or abort — retry
      if (attempt < retries) {
        await new Promise(r => setTimeout(r, delay * (attempt + 1)));
        continue;
      }
      throw err;
    }
  }
  throw new Error("Retry exhausted");
}

async function unwrapResponse<T>(resp: Response): Promise<T> {
  const body = await resp.json();
  // Handle both old format (raw data) and new format (wrapped envelope)
  if (body && typeof body === 'object' && 'status' in body) {
    if (body.status === 'error') {
      throw new Error((body as Record<string, unknown>).error as string || (body as Record<string, unknown>).message as string || 'Request failed');
    }
    return (body as Record<string, unknown>).data as T;
  }
  // Old format — return as-is
  return body as T;
}

async function get<T>(path: string, params?: Record<string, string>): Promise<T> {
  const url = params
    ? `${BASE}${path}?${new URLSearchParams(params).toString()}`
    : `${BASE}${path}`;
  let resp: Response;
  try {
    resp = await fetchWithRetry(url, {});
  } catch (err) {
    clearCsrfToken();
    throw err;
  }
  if (!resp.ok) {
    clearCsrfToken();
    const body = await resp.json().catch(() => ({})) as Record<string, unknown>;
    throw new Error((body.error as string) || resp.statusText);
  }
  return unwrapResponse<T>(resp);
}

async function mutate<T>(method: string, path: string, body?: unknown): Promise<T> {
  const token = await getCsrfToken();
  let resp: Response;
  try {
    resp = await fetchWithRetry(`${BASE}${path}`, {
      method,
      headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    clearCsrfToken();
    throw err;
  }
  // Retry once with a fresh CSRF token on 403 (token may be stale after server restart)
  if (resp.status === 403) {
    clearCsrfToken();
    const freshToken = await getCsrfToken();
    let retry: Response;
    try {
      retry = await fetchWithRetry(`${BASE}${path}`, {
        method,
        headers: { "Content-Type": "application/json", "X-CSRF-Token": freshToken },
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch (err) {
      clearCsrfToken();
      throw err;
    }
    if (!retry.ok) {
      clearCsrfToken();
      const retryBody = await retry.json().catch(() => ({})) as Record<string, unknown>;
      throw new Error((retryBody.error as string) || retry.statusText);
    }
    return unwrapResponse<T>(retry);
  }
  if (!resp.ok) {
    clearCsrfToken();
    const respBody = await resp.json().catch(() => ({})) as Record<string, unknown>;
    throw new Error((respBody.error as string) || resp.statusText);
  }
  return unwrapResponse<T>(resp);
}

export const blueprints = {
  list: (params?: Record<string, string>) =>
    get<PaginatedResponse<Blueprint>>("/blueprints", params),
  get: (id: string) =>
    get<SingleResponse<Blueprint>>(`/blueprints/${id}`),
  reviewQueue: (params?: Record<string, string>) =>
    get<{ data: Blueprint[]; meta: { total: number; niche_id: string; fallback: boolean } }>("/blueprints/review-queue", params),
  review: (id: string, body: { action: string; issue?: string; notes?: string }) =>
    mutate<{ status: string }>("POST", `/blueprints/${id}/review`, body),
  reviewAction: (id: string, body: { action: string; issue?: string; notes?: string }) =>
    mutate<{ status: string; action: string; id: string }>("PATCH", `/blueprints/${id}/review-action`, body),
  batchReview: (body: { ids: string[]; action: string }) =>
    mutate<{ data: Array<{ id: string; status: string }> }>("POST", "/blueprints/batch-review", body),
  reschedule: (id: string, scheduledFor: string) =>
    mutate<{ status: string }>("PATCH", `/blueprints/${id}/schedule`, { scheduled_for: scheduledFor }),
  updateContent: (id: string, body: Partial<Pick<Blueprint, "hook_text" | "caption" | "hashtags">>) =>
    mutate<{ status: string }>("PATCH", `/blueprints/${id}/content`, body),
  approveAndSchedule: (id: string) =>
    mutate<{ status: string; scheduled_for: string }>("POST", `/blueprints/${id}/approve-and-schedule`, {}),
  batchApproveSchedule: (body: { ids: string[] }) =>
    mutate<{ data: Array<{ id: string; scheduled_for: string }> }>("POST", "/blueprints/batch-approve-schedule", body),
  // AUTO #1 (2026-06-13): preview the auto-approval gate's decision for a
  // blueprint. Read-only — does NOT approve or change state. The dashboard
  // surfaces this as a "would auto-approve" badge so operators can see
  // what the gate would do before the opt-in switch is flipped.
  autoApprovalPreview: (id: string) =>
    get<AutoApprovalPreview>(`/blueprints/${id}/auto-approval-preview`),
};

// AUTO #1: response shape from /api/v1/blueprints/<id>/auto-approval-preview.
// `would_publish` is the immutable contract bit — always false from this
// endpoint. Mirrors AutoApprovalDecision in genlab-core.
export interface AutoApprovalPreview {
  record_id: string;
  approved: boolean;
  confidence: number;       // 0.0..1.0
  passed_checks: string[];
  failed_checks: string[];
  reasons: string[];
  would_publish: false;     // pinned literal — preview never publishes
  preview_only: true;
  // D2.6 (2026-06-15, AUTO #2 runbook): raw scores the gate consulted.
  // Optional because older cached responses may not carry them;
  // ScoringExplainer handles the absent-field case gracefully.
  raw_metrics?: {
    composite_score: number | null;
    virality_score: number | null;
    validation_status: string | null;
    has_video: boolean;
    has_hook: boolean;
  };
  // Intervention 6 (2026-07-01): ensemble decision-making payload.
  // Optional because the server may not populate it on older cached
  // rows AND because the ensemble is env-flag-gated OFF by default
  // (renders as disabled state). The EnsembleBadge component reads
  // this to surface `worth_your_look` disagreements to the operator.
  ensemble?: {
    score: number | null;
    disagreement: number;
    n_voters: number;
    recommendation:
      | "auto_approve"
      | "auto_reject"
      | "worth_your_look"
      | "route_to_operator"
      | "disabled"
      | "insufficient_data"
      | "error";
    votes: Array<{
      component: string;
      score: number | null;
      weight: number;
      reason: string;
    }>;
    reasons: string[];
    error?: string;
  };
}

// AUTO #1b: stats response from /api/v1/auto-approval/calibration-stats.
// Surfaces per-niche agreement rate + confusion matrix so the dashboard
// can show when each niche crosses the AUTO #2 readiness threshold.
export interface CalibrationStats {
  niche_id: string;
  window_days: number;
  sample_count: number;
  agreement_count: number;
  agreement_rate: number;        // 0.0..1.0
  confusion_matrix: {
    true_positives: number;
    true_negatives: number;
    false_positives: number;
    false_negatives: number;
  };
  ready_for_enforcement: boolean; // ≥30 samples AND ≥90% agreement
}

// W4.4 (2026-06-17): per-day agreement-rate trend for the operator's
// readiness assessment. Complements calibration-stats (single-window
// snapshot) by surfacing whether the niche's quality is improving,
// flat, or regressing day over day.
export interface TrackRecordBin {
  date: string;       // ISO date "YYYY-MM-DD"
  sample_count: number;
  agreement: number;
  rate: number;       // 0..1
  // W3 (2026-06-18): engagement enrichment via pending_feedback join.
  // ``collected_count`` is the number of platforms where reward_48h is
  // populated (post-publish 48h window closed + Insights wrote back).
  // ``avg_reward_48h`` is the mean across collected; null when
  // collected_count === 0 (distinguishes "no data yet" from
  // "engagement = 0").
  collected_count: number;
  avg_reward_48h: number | null;
}

export interface TrackRecordResponse {
  niche_id: string;
  window_days: number;
  bin_days: number;
  bins: TrackRecordBin[];
  overall: {
    sample_count: number;
    agreement: number;
    rate: number;
    // W3 (2026-06-18): rollup engagement signal across the window.
    // Weighted-avg: sum(per_bin collected * avg) / sum(per_bin collected).
    collected_count: number;
    avg_reward_48h: number | null;
  };
}

/**
 * Batch response from /auto-approval/track-record-all (PR #394).
 * Collapses 5 parallel per-niche TrackRecord fetches into one HTTP request.
 */
export interface TrackRecordAllResponse {
  window_days: number;
  bin_days: number;
  niches: Record<string, TrackRecordResponse>;
}

/**
 * Batch response from /auto-approval/calibration-stats-all.
 * Shipped 2026-06-20 to collapse the AutoApprovalCalibrationCard's 5
 * parallel per-niche fetches into one. See PR #392.
 */
export interface CalibrationStatsAllResponse {
  window_days: number;
  niches: Record<string, CalibrationStats>;
}

/**
 * Per-niche outcome-based readiness verdict (2026-07-23). Independent
 * signal from operator-agreement; unblocks the AUTO #2 ratchet when
 * the operator hasn't clicked in weeks (which is the current prod
 * state). Renders as a sub-badge on AutoApprovalCalibrationCard.
 */
export interface OutcomeReadiness {
  niche_id: string;
  window_days: number;
  sample_count: number;
  outcome_good_count: number;
  outcome_good_rate: number; // 0..1
  threshold: number;         // reward_48h low bar
  ready: boolean;
  degraded?: boolean;
  degraded_reason?: string;
}

export interface OutcomeReadinessAllResponse {
  window_days: number;
  niches: Record<string, OutcomeReadiness>;
  degraded?: boolean;
  degraded_reason?: string;
}

/**
 * Per-check score distribution over rejected blueprints, restricted
 * to the top failing check. Populated from
 * gate_examinations.extra.{check_name}.
 */
export interface GateScoreDistribution {
  check: string;
  min: number | null;
  p25: number | null;
  p50: number | null;
  p75: number | null;
  max: number | null;
  n: number;
}

export interface GateThresholdSuggestion {
  check: string;
  current_threshold: number | null;
  suggested_threshold: number | null;
  would_unlock_count: number | null;
  weekly_unlock_estimate: number | null;
  confidence: "low" | "medium" | "high";
  n_samples: number;
  rationale: string;
}

/**
 * Per-niche gate examination breakdown (2026-07-23). Diagnostic
 * layer that surfaces WHICH gate check is the AUTO #2 ratchet's
 * blocker + BY HOW MUCH.
 */
export interface GateExaminations {
  niche_id: string;
  window_days: number;
  examinations: number;
  approved: number;
  rejected: number;
  approval_rate: number;
  distinct_blueprints: number;
  failed_check_counts: Record<string, number>;
  top_failing_check: string | null;
  score_distribution: GateScoreDistribution | null;
  threshold_suggestion: GateThresholdSuggestion | null;
}

export interface GateExaminationsAllResponse {
  window_days: number;
  niches: Record<string, GateExaminations>;
  degraded?: boolean;
  degraded_reason?: string;
}

export const autoApproval = {
  calibrationStats: (nicheId: string, windowDays = 7) =>
    get<CalibrationStats>("/auto-approval/calibration-stats", {
      niche_id: nicheId,
      window_days: String(windowDays),
    }),
  /**
   * Batch variant — returns all 5 niches' calibration stats in one HTTP +
   * one SQL query. Used by the Mission Control calibration card.
   */
  calibrationStatsAll: (windowDays = 7) =>
    get<CalibrationStatsAllResponse>("/auto-approval/calibration-stats-all", {
      window_days: String(windowDays),
    }),
  /**
   * Outcome-based readiness — parallel signal to calibration-stats.
   * Returns all 5 niches in one request when niche_id is omitted.
   */
  outcomeReadiness: (windowDays = 14) =>
    get<OutcomeReadinessAllResponse>("/auto-approval/outcome-readiness", {
      window_days: String(windowDays),
    }),
  /**
   * Gate examination breakdown — reveals which of the 5 gate checks
   * is the ratchet's blocker per niche. Returns null score
   * distribution + suggestion until at least 1 rejected blueprint
   * has captured its raw composite/virality values (post-2026-07-23
   * commit; each auto-approver fire enriches the data).
   */
  gateExaminations: (windowDays = 7) =>
    get<GateExaminationsAllResponse>("/auto-approval/gate-examinations", {
      window_days: String(windowDays),
    }),
  trackRecord: (nicheId: string, windowDays = 30, binDays = 1) =>
    get<TrackRecordResponse>("/auto-approval/track-record", {
      niche_id: nicheId,
      window_days: String(windowDays),
      bin_days: String(binDays),
    }),
  /**
   * Batch variant — returns all 5 niches' track-record in one HTTP.
   * Server-side still runs 5 queries (per-niche binning isn't trivially
   * SQL-batchable) but the HTTP fan-out collapses 5x. See PR #394.
   */
  trackRecordAll: (windowDays = 30, binDays = 1) =>
    get<TrackRecordAllResponse>("/auto-approval/track-record-all", {
      window_days: String(windowDays),
      bin_days: String(binDays),
    }),
};

// Source-quality dashboard surface (2026-06-13).
// Operator uses this to prune sources with 0% claim rate over the
// rolling window — the deep-dive found 22 of 30 top sources produce
// nothing despite being fetched daily.
export type SourceHealth = "active" | "weak" | "dead" | "unproven";

export interface SourcePerformanceRow {
  source_name: string;
  source_platform: string;
  fetched: number;
  claimed: number;
  claim_pct: number;        // 0..100
  distinct_niches: number;
  latest_fetch: string | null;
  health: SourceHealth;
}

export interface SourcePerformanceResponse {
  window_days: number;
  total_fetched: number;
  total_claimed: number;
  claim_rate: number;       // 0..1
  bucket_counts: Record<SourceHealth, number>;
  sources: SourcePerformanceRow[];
}

// ── M-19 / M-20 / M-21: per-niche source-edit endpoints ─────────
//
// Mounted under `/api/v1/config/sources/...` (NOT `/api/v1/sources/...`
// — config_routes.py owns the ruamel round-trip for sources.yaml;
// sources.py is read-only performance data).
export interface YoutubeChannelEntry {
  url: string;
  name: string;
}

export interface RssFeedEntry {
  url: string;
  name: string;
  max_age_hours: number;
}

export interface SubredditEntry {
  name: string;
  // Read-only metadata that some niches store on each entry; the
  // write-path only takes ``name``, so these are optional/nullable.
  min_score?: number | null;
  time_filter?: string | null;
  content_type?: string | null;
}

// ── AUTO #2: per-niche auto_publish rollout_pct slider ──────
//
// Read returns all 4 fields (display context); write only accepts
// rollout_pct per the runbook (enabled flip stays git-commit-only).
export interface AutoPublishPolicy {
  enabled: boolean;
  min_confidence: number;
  max_approvals_per_pass: number;
  rollout_pct: number;        // 0.0..1.0
}

/**
 * Batch response from /config/auto-publish-all.
 * Shipped 2026-06-20 to collapse RolloutPctSlider's 5 parallel
 * per-niche fetches into one. See PR #393.
 */
export interface AutoPublishAllResponse {
  niches: Record<string, AutoPublishPolicy>;
}

export const autoPublish = {
  get: (nicheId: string) =>
    get<{ niche_id: string; auto_publish: AutoPublishPolicy }>(
      "/config/auto-publish",
      { niche_id: nicheId },
    ),
  /**
   * Batch variant — returns all 5 niches' auto_publish in one HTTP
   * request. Used by RolloutPctSlider on Mission Control.
   */
  getAll: () => get<AutoPublishAllResponse>("/config/auto-publish-all"),
  setRolloutPct: (nicheId: string, rolloutPct: number) =>
    mutate<{ niche_id: string; updated: { rollout_pct: number } }>(
      "PATCH",
      `/config/auto-publish?niche_id=${encodeURIComponent(nicheId)}`,
      { rollout_pct: rolloutPct },
    ),
};

export const sources = {
  performance: (days = 14, minFetched = 5) =>
    get<SourcePerformanceResponse>("/sources/performance", {
      days: String(days),
      min_fetched: String(minFetched),
    }),

  listYoutubeChannels: (nicheId: string) =>
    get<{ niche_id: string; youtube_channels: YoutubeChannelEntry[] }>(
      "/config/sources/youtube-channels",
      { niche_id: nicheId },
    ),
  addYoutubeChannel: (nicheId: string, entry: YoutubeChannelEntry) =>
    mutate<{ niche_id: string; added: YoutubeChannelEntry }>(
      "POST",
      `/config/sources/youtube-channels?niche_id=${encodeURIComponent(nicheId)}`,
      entry,
    ),
  // Backend takes ``url`` as a QUERY param (not body) — see
  // config_routes.py:remove_youtube_channel.
  removeYoutubeChannel: (nicheId: string, url: string) =>
    mutate<{ niche_id: string; removed: YoutubeChannelEntry }>(
      "DELETE",
      `/config/sources/youtube-channels?niche_id=${encodeURIComponent(nicheId)}&url=${encodeURIComponent(url)}`,
    ),

  // M-20: rss_feeds_extra
  listRssFeeds: (nicheId: string) =>
    get<{ niche_id: string; rss_feeds_extra: RssFeedEntry[] }>(
      "/config/sources/rss-feeds",
      { niche_id: nicheId },
    ),
  addRssFeed: (nicheId: string, entry: Omit<RssFeedEntry, "max_age_hours"> & { max_age_hours?: number }) =>
    mutate<{ niche_id: string; added: RssFeedEntry }>(
      "POST",
      `/config/sources/rss-feeds?niche_id=${encodeURIComponent(nicheId)}`,
      entry,
    ),
  removeRssFeed: (nicheId: string, url: string) =>
    mutate<{ niche_id: string; removed: { url: string } }>(
      "DELETE",
      `/config/sources/rss-feeds?niche_id=${encodeURIComponent(nicheId)}&url=${encodeURIComponent(url)}`,
    ),

  // M-21: reddit.subreddits
  listSubreddits: (nicheId: string) =>
    get<{ niche_id: string; subreddits: SubredditEntry[] }>(
      "/config/sources/subreddits",
      { niche_id: nicheId },
    ),
  addSubreddit: (nicheId: string, name: string) =>
    mutate<{ niche_id: string; added: { name: string } }>(
      "POST",
      `/config/sources/subreddits?niche_id=${encodeURIComponent(nicheId)}`,
      { name },
    ),
  removeSubreddit: (nicheId: string, name: string) =>
    mutate<{ niche_id: string; removed: { name: string } }>(
      "DELETE",
      `/config/sources/subreddits?niche_id=${encodeURIComponent(nicheId)}&name=${encodeURIComponent(name)}`,
    ),
};

export const stories = {
  list: (params?: Record<string, string>) =>
    get<PaginatedResponse<Story>>("/stories", params),
  get: (id: string) =>
    get<SingleResponse<Story>>(`/stories/${id}`),
};

export const schedule = {
  get: (from: string, to: string) =>
    get<{ data: ScheduleDay[] }>("/schedule", { from, to }),
  reorder: (body: { blueprint_id: string; to_slot: string; to_date?: string }) =>
    mutate<{ status: string }>("PATCH", "/schedule/reorder", body),
  coverage: (from: string, to: string) =>
    get<{ data: Array<{ date: string; coverage: number }> }>("/schedule/coverage", { from, to }),
};

export const analytics = {
  overview: (params?: Record<string, string>) =>
    get<AnalyticsOverviewResponse>("/analytics/overview", params),
  publishing: (params?: Record<string, string>) =>
    get<{ data: PublishingRecord[] }>("/analytics/publishing", params),
  content: (params?: Record<string, string>) =>
    get<{ data: Array<{ template_id: string; total: number; published: number }> }>("/analytics/content", params),
  pipeline: (params?: Record<string, string>) =>
    get<{ data: PipelineRun[] }>("/analytics/pipeline", params),
  dailyCosts: (params?: Record<string, string>) =>
    get<{ data: DailyCost[] }>("/analytics/pipeline", { ...params, aggregate: "day" }),
  heatmap: (params?: Record<string, string>) =>
    get<{ data: HeatmapCell[] }>("/analytics/heatmap", params),
  funnel: (params?: Record<string, string>) =>
    get<{ data: FunnelStage[]; total_cost: number }>("/analytics/funnel", params),
  engagement: (params?: Record<string, string>) =>
    get<{ data: EngagementRecord[] }>("/analytics/engagement", params),
  engagementSummary: (params?: Record<string, string>) =>
    get<{ data: EngagementSummary[]; excluded_count?: number; total_records?: number; data_quality?: string }>("/analytics/engagement/summary", params),
  contentPerformance: (params?: Record<string, string>) =>
    get<ContentPerformanceData>("/analytics/content-performance", params),
  trends: (params?: Record<string, string>) =>
    get<{ data: TrendDataPoint[] }>("/analytics/trends", params),
  audience: (params?: Record<string, string>) =>
    get<{ data: AudienceSnapshot[]; message?: string }>("/analytics/audience", params),
  viralityBreakdown: (params?: Record<string, string>) =>
    get<ViralityBreakdownData>("/analytics/virality-breakdown", params),
  monetization: (params?: Record<string, string>) =>
    get<MonetizationDataEnvelope>("/analytics/monetization", params),
  demographics: () =>
    get<{ data: YouTubeDemographics | null; message?: string }>("/analytics/demographics"),
  topPosts: () =>
    get<TopPost[] | { posts: TopPost[]; total: number }>("/analytics/top-posts").then(
      (d) => (Array.isArray(d) ? d : d?.posts ?? []),
    ) as Promise<TopPost[]>,
  crossNiche: () => get<CrossNicheAnalytics>("/analytics/cross-niche"),
};

export const revenue = {
  summary: () =>
    get<{
      clicks: { today: number; last_7d: number; last_30d: number };
      by_product: Record<string, number>;
      by_niche: Record<string, number>;
      by_network: Record<string, number>;
      estimated_revenue_inr_30d: number;
      actual_revenue_inr_30d?: number;
    }>("/revenue/summary"),
  clickTrends: () => get<ClickTrend[]>("/revenue/click-trends"),
};

export const pipeline = {
  status: () =>
    get<{ data: { last_run: PipelineRun | null; health: string } }>("/pipeline/status"),
  runs: (params?: Record<string, string>) =>
    get<PaginatedResponse<PipelineRun>>("/pipeline/runs", params),
  run: (id: string) =>
    get<{ data: Record<string, unknown> }>(`/pipeline/runs/${id}`),
  trigger: (params?: { niche_id?: string; mode?: string }) =>
    mutate<{ status: string; run_id: string }>("POST", "/pipeline/trigger", params ?? {}),
  logs: (params: { niche_id: string; limit?: string; after_ts?: string }) =>
    get<{ data: Array<{ ts: string; level: string; logger: string; message: string; run_id: string; niche_id: string; stage: string | null }> }>("/pipeline/logs", params),
  qualityStats: () => get<QualityStats>("/pipeline/quality-stats"),
};

export const config = {
  sources: (params?: { niche_id?: string }) =>
    get<{ data: unknown }>("/config/sources", params),
  templates: (params?: { niche_id?: string }) =>
    get<{ data: Array<{ id: string } & Record<string, unknown>> }>("/config/templates", params),
  scheduleSlots: (params?: { niche_id?: string }) =>
    get<{ data: { slots: string[]; timezone: string } }>("/config/schedule-slots", params),
  scoring: (params?: { niche_id?: string }) =>
    get<{ data: unknown }>("/config/scoring", params),
};

export const notifications = {
  getPreferences: () =>
    get<{ data: NotificationPreferences }>("/settings/notifications"),
  savePreferences: (prefs: NotificationPreferences) =>
    mutate<{ status: string }>("POST", "/settings/notifications", prefs),
};

export const crossNiche = {
  overview: () =>
    get<CrossNicheOverviewResponse>("/cross-niche/overview"),
};

export const queue = {
  list: (params?: Record<string, string>) =>
    get<{ data: QueueItem[]; meta: { total: number; niche_id: string } }>("/queue", params),
  stats: (params?: Record<string, string>) =>
    get<{ data: QueueStats }>("/queue/stats", params),
  approve: (id: string, body?: { notes?: string }) =>
    mutate<{ status: string }>("POST", `/queue/${id}/approve`, body),
  hold: (id: string, body?: { reason?: string }) =>
    mutate<{ status: string }>("POST", `/queue/${id}/hold`, body),
  release: (id: string) =>
    mutate<{ status: string }>("POST", `/queue/${id}/release`, {}),
  unschedule: (id: string) =>
    mutate<{ status: string }>("POST", `/queue/${id}/unschedule`, {}),
  archive: (id: string) =>
    mutate<{ status: string }>("POST", `/queue/${id}/archive`, {}),
};

export const channelHealth = {
  get: () => get<{ data: ChannelHealth }>("/channel-health"),
};

export const tokenHealth = {
  get: async (): Promise<TokenHealthResponse> => {
    const resp = await fetchWithRetry("/api/token-health", {});
    if (!resp.ok) throw new Error(resp.statusText);
    return unwrapResponse<TokenHealthResponse>(resp);
  },
  refresh: async (platform: string): Promise<{ status: string }> => {
    const token = await getCsrfToken();
    const resp = await fetchWithRetry(`/api/token-health/refresh/${platform}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
    });
    if (!resp.ok) throw new Error(resp.statusText);
    return unwrapResponse<{ status: string }>(resp);
  },
};

export const platformPosts = {
  tiktok: (params?: Record<string, string>) =>
    get<{ data: Array<Record<string, unknown>>; meta: { total: number; audit_approved: boolean; note: string | null } }>("/platforms/tiktok/posts", params),
  threads: (params?: Record<string, string>) =>
    get<{ data: Array<Record<string, unknown>>; meta: { total: number } }>("/platforms/threads/posts", params),
};

export const monetisation = {
  progress: () =>
    get<MonetisationProgressData | { data: MonetisationProgressData }>(
      "/monetisation/progress",
    ).then((d): MonetisationProgressData => {
      // Handle both wrapped {data: {...}} and direct {...} formats.
      // Tightened from the prior ``(d as any).data`` cast: narrow via
      // a typed envelope check.
      const envelope = d as { data?: MonetisationProgressData };
      if (
        envelope &&
        typeof envelope === "object" &&
        "data" in envelope &&
        envelope.data &&
        typeof envelope.data === "object"
      ) {
        return envelope.data;
      }
      return d as MonetisationProgressData;
    }),
};

// PR W (2026-06-23) — Outreach template generator client.
// Used by the SponsorshipReadinessCard's "Copy pitch" button. Server
// produces tier-aware subject + body + media-kit-url for one niche;
// the frontend writes body to clipboard and toasts confirmation.
export const outreach = {
  template: (nicheId: string) =>
    get<OutreachTemplate | { data: OutreachTemplate }>(
      "/sponsorship/outreach-template",
      { niche: nicheId },
    ).then((d): OutreachTemplate => {
      const envelope = d as { data?: OutreachTemplate };
      if (
        envelope &&
        typeof envelope === "object" &&
        "data" in envelope &&
        envelope.data &&
        typeof envelope.data === "object"
      ) {
        return envelope.data;
      }
      return d as OutreachTemplate;
    }),
  /** PR AA (2026-06-23): cross-channel portfolio pitch generator.
   * Used by the SponsorshipReadinessCard footer's "Copy portfolio"
   * button — closes the asymmetry where per-niche has Copy+Kit but
   * portfolio only had View. */
  templateAll: () =>
    get<PortfolioOutreachTemplate | { data: PortfolioOutreachTemplate }>(
      "/sponsorship/outreach-template/_all",
    ).then((d): PortfolioOutreachTemplate => {
      const envelope = d as { data?: PortfolioOutreachTemplate };
      if (
        envelope &&
        typeof envelope === "object" &&
        "data" in envelope &&
        envelope.data &&
        typeof envelope.data === "object"
      ) {
        return envelope.data;
      }
      return d as PortfolioOutreachTemplate;
    }),
};

// PR BB (2026-06-23) — bandit hour-posteriors for Mission Control
// heatmap. Operator feedback loop for PR #487's optimal-time bandit.
export const bandit = {
  hourPosteriors: (nicheId: string) =>
    get<BanditHourPosteriors | { data: BanditHourPosteriors }>(
      "/bandit/hour-posteriors",
      { niche: nicheId },
    ).then((d): BanditHourPosteriors => {
      const envelope = d as { data?: BanditHourPosteriors };
      if (
        envelope &&
        typeof envelope === "object" &&
        "data" in envelope &&
        envelope.data &&
        typeof envelope.data === "object"
      ) {
        return envelope.data;
      }
      return d as BanditHourPosteriors;
    }),
};

// PR JJ (2026-06-23) — Phase 2 of PR #496. Reads recent tier
// transitions for the SponsorshipReadinessCard's "Recent activity"
// footer. Backend writes on every /readiness fetch (every 60s);
// this client polls /recent-transitions to surface them.
export const transitions = {
  recent: (windowHours = 24) =>
    get<RecentTransitionsResponse | { data: RecentTransitionsResponse }>(
      "/sponsorship/recent-transitions",
      { window_hours: String(windowHours) },
    ).then((d): RecentTransitionsResponse => {
      const envelope = d as { data?: RecentTransitionsResponse };
      if (
        envelope &&
        typeof envelope === "object" &&
        "data" in envelope &&
        envelope.data &&
        typeof envelope.data === "object"
      ) {
        return envelope.data;
      }
      return d as RecentTransitionsResponse;
    }),
};

// PR T (2026-06-23) — Sponsorship Readiness API client.
// Reads /api/v1/sponsorship/readiness which decorates the underlying
// monetisationprogress data with per-niche tiers and primary-metric
// summaries. The Mission Control SponsorshipReadinessCard polls this
// every 60s via the use-sponsorship-readiness hook.
export const sponsorship = {
  readiness: () =>
    get<SponsorshipReadinessData | { data: SponsorshipReadinessData }>(
      "/sponsorship/readiness",
    ).then((d): SponsorshipReadinessData => {
      // Same envelope-unwrap pattern as monetisation.progress — server
      // returns {data: {...}} from api_success but some callers also
      // accept the direct shape; tolerate either.
      const envelope = d as { data?: SponsorshipReadinessData };
      if (
        envelope &&
        typeof envelope === "object" &&
        "data" in envelope &&
        envelope.data &&
        typeof envelope.data === "object"
      ) {
        return envelope.data;
      }
      return d as SponsorshipReadinessData;
    }),
};

// PR U (2026-06-23) + PR V (2026-06-23) — Media Kit API client.
// Single-niche endpoint + portfolio (multi-niche) endpoint.
export const mediaKit = {
  get: (nicheId: string) =>
    get<MediaKitData | { data: MediaKitData }>(`/media-kit/${nicheId}`).then(
      (d): MediaKitData => {
        const envelope = d as { data?: MediaKitData };
        if (
          envelope &&
          typeof envelope === "object" &&
          "data" in envelope &&
          envelope.data &&
          typeof envelope.data === "object"
        ) {
          return envelope.data;
        }
        return d as MediaKitData;
      },
    ),
  /** Portfolio kit covering all 5 niches in stable order. Used for
   * cross-channel sponsor pitches. */
  all: () =>
    get<MediaKitPortfolioData | { data: MediaKitPortfolioData }>(
      "/media-kit/_all",
    ).then((d): MediaKitPortfolioData => {
      const envelope = d as { data?: MediaKitPortfolioData };
      if (
        envelope &&
        typeof envelope === "object" &&
        "data" in envelope &&
        envelope.data &&
        typeof envelope.data === "object"
      ) {
        return envelope.data;
      }
      return d as MediaKitPortfolioData;
    }),
};

export const learning = {
  status: () => get<LearningStatus>("/learning/status"),
  hookClassifierStatus: () =>
    get<Record<string, HookClassifierNicheStatus>>("/learning/hook-classifier-status"),
  configUpdates: () =>
    get<{ updates: ConfigUpdateRow[] }>("/learning/config-updates"),
};

export const engagementApi = {
  recent: () =>
    get<{ comments: EngagementComment[]; total: number }>("/engagement/recent").then(
      (d) => (Array.isArray(d) ? d : d?.comments ?? []),
    ) as Promise<EngagementComment[]>,
  status: () => get<EngagementStatusResponse>("/engagement/status"),
};

export const trendsApi = {
  current: () => get<TrendData>("/trends"),
};

export const health = async (): Promise<Record<string, unknown>> => {
  const resp = await fetch("/api/health");
  if (!resp.ok) throw new Error(resp.statusText);
  return unwrapResponse<Record<string, unknown>>(resp);
};

export const healthDetailed = {
  get: () => get<DetailedHealthResponse>("/health/detailed"),
};

// Publishing alerts & metrics
export const alerts = {
  publishing: () => get<Record<string, unknown>>("/alerts/publishing"),
  system: () => get<Record<string, unknown>>("/alerts/system"),
  critical: () => get<Record<string, unknown>>("/alerts/critical"),
};

export const metrics = {
  publishing: () => get<Record<string, unknown>>("/metrics/publishing"),
};

// PR after #586 — Source-discovery proposer ranks the source channels
// our trending fetcher pulled clips from, ordered by per-clip avg
// engagement. Foundation (channel_id column + producer wire) shipped
// in PR #586; this client wraps the proposer endpoint.
export const sourceDiscovery = {
  proposals: (nicheId: string, windowDays: number = 30, minClips: number = 2, topN: number = 10) =>
    get<
      | SourceDiscoveryProposalsResponse
      | { data: SourceDiscoveryProposalsResponse }
    >("/source-discovery/proposals", {
      niche_id: nicheId,
      window_days: String(windowDays),
      min_clips: String(minClips),
      top_n: String(topN),
    }).then((d): SourceDiscoveryProposalsResponse => {
      const envelope = d as { data?: SourceDiscoveryProposalsResponse };
      if (
        envelope &&
        typeof envelope === "object" &&
        "data" in envelope &&
        envelope.data &&
        typeof envelope.data === "object"
      ) {
        return envelope.data;
      }
      return d as SourceDiscoveryProposalsResponse;
    }),
};

// Phase 2 L1 — Source-performance bandit-arms data. Surfaces the
// per-source Beta posteriors collected by source_performance.py +
// activated in PR #571b (commit fa317889). Lets operators answer
// "does YouTube showcase outperform news?" without writing SQL.
//
// NOTE: distinct from the older `sources.performance` (above), which
// surfaces /sources/performance — the per-source fetch-vs-claim
// diagnostic. This one is the bandit-arms reward signal.
export const sourcePerformanceBandit = {
  list: (nicheId: string, topN: number = 20) =>
    get<
      | SourcePerformanceBanditResponse
      | { data: SourcePerformanceBanditResponse }
    >("/source-performance", {
      niche_id: nicheId,
      top_n: String(topN),
    }).then((d): SourcePerformanceBanditResponse => {
      const envelope = d as { data?: SourcePerformanceBanditResponse };
      if (
        envelope &&
        typeof envelope === "object" &&
        "data" in envelope &&
        envelope.data &&
        typeof envelope.data === "object"
      ) {
        return envelope.data;
      }
      return d as SourcePerformanceBanditResponse;
    }),
};

// PR after #581 — Compliance stats aggregation for Mission Control.
// Wraps /api/v1/compliance/stats which aggregates compliance_events
// rows over a [1, 90] day window via the events.stats_by_niche
// library helper.
export const complianceStats = {
  fetch: (windowDays = 7) =>
    get<ComplianceStatsResponse | { data: ComplianceStatsResponse }>(
      "/compliance/stats",
      { window_days: String(windowDays) },
    ).then((d): ComplianceStatsResponse => {
      const envelope = d as { data?: ComplianceStatsResponse };
      if (
        envelope &&
        typeof envelope === "object" &&
        "data" in envelope &&
        envelope.data &&
        typeof envelope.data === "object"
      ) {
        return envelope.data;
      }
      return d as ComplianceStatsResponse;
    }),
};

// PR B (2026-06-27) — Per-niche per-platform publishing-health matrix.
// Wraps /api/v1/publishing/per-niche-platform-health. Powers the
// Mission Control PerPlatformHealthCard. The existing global health
// view averages across niches × platforms and hides per-niche
// regressions (see today's investigation of ai_creators IG silent
// failures); this endpoint surfaces them at-a-glance.
export const publishingHealthPerNiche = {
  fetch: (days = 14) =>
    get<
      PerNichePlatformHealthResponse | { data: PerNichePlatformHealthResponse }
    >("/publishing/per-niche-platform-health", { days: String(days) }).then(
      (d): PerNichePlatformHealthResponse => {
        const envelope = d as { data?: PerNichePlatformHealthResponse };
        if (
          envelope &&
          typeof envelope === "object" &&
          "data" in envelope &&
          envelope.data &&
          typeof envelope.data === "object"
        ) {
          return envelope.data;
        }
        return d as PerNichePlatformHealthResponse;
      },
    ),
};

// PR AG (2026-06-29) — Per-platform bandit divergence client.
// Wraps /api/v1/learning/bandit-platform-divergence. Powers the
// Mission Control BanditPlatformDivergenceCard. Surfaces whether
// PR #641's per-platform bandit split is producing meaningful
// divergence vs collapsing back to the base arm. Operator-meaningful
// data window: ~2 weeks of accumulation from PR #641 activation.
export const banditPlatformDivergence = {
  fetch: (params: { niche_id?: string; min_n_plays?: number } = {}) => {
    const q: Record<string, string> = {};
    if (params.niche_id) q.niche_id = params.niche_id;
    if (params.min_n_plays != null) q.min_n_plays = String(params.min_n_plays);
    return get<
      | BanditPlatformDivergenceResponse
      | { data: BanditPlatformDivergenceResponse }
    >("/learning/bandit-platform-divergence", q).then(
      (d): BanditPlatformDivergenceResponse => {
        const envelope = d as { data?: BanditPlatformDivergenceResponse };
        if (
          envelope &&
          typeof envelope === "object" &&
          "data" in envelope &&
          envelope.data &&
          typeof envelope.data === "object"
        ) {
          return envelope.data;
        }
        return d as BanditPlatformDivergenceResponse;
      },
    );
  },
};

// PR after #580 — Scheduling Pauses API client.
// Wraps the GET/POST/DELETE endpoints shipped in PR #577. The
// NichePauseCard on Mission Control consumes these via TanStack
// Query for the list + invalidation flow.
//
// SR-F: the backend filters list responses by the operator's
// allowlist and rejects out-of-allowlist mutations with 403. We
// surface those errors via toast in the consumer.
export const schedulingPauses = {
  list: () =>
    get<NichePauseRecord[] | { data: NichePauseRecord[] }>(
      "/scheduling/pauses",
    ).then((d): NichePauseRecord[] => {
      const envelope = d as { data?: NichePauseRecord[] };
      if (
        envelope &&
        typeof envelope === "object" &&
        "data" in envelope &&
        Array.isArray(envelope.data)
      ) {
        return envelope.data;
      }
      return (Array.isArray(d) ? d : []) as NichePauseRecord[];
    }),
  /**
   * Pause a niche. Backend UPSERT — re-posting the same niche updates
   * the existing pause's reason / paused_until.
   *
   * @param nicheId — must be in the operator's SR-F allowlist or 403
   * @param reason — required by the backend (audit log enforcement)
   * @param pausedUntil — optional ISO-8601 timestamp; null = indefinite
   */
  pause: (
    nicheId: string,
    reason: string,
    pausedUntil?: string | null,
  ) =>
    mutate<{ niche_id: string; paused_until: string | null; reason: string; paused_by: string }>(
      "POST",
      "/scheduling/pauses",
      {
        niche_id: nicheId,
        reason,
        paused_until: pausedUntil ?? null,
      },
    ),
  /** Idempotent — returns 200 even when no pause existed. */
  unpause: (nicheId: string) =>
    mutate<{ niche_id: string; paused: false }>(
      "DELETE",
      `/scheduling/pauses/${encodeURIComponent(nicheId)}`,
    ),
};

// ───────────────────────────────────────────────────────────────
// PR Strategist-2b (2026-07-01) — Strategist API client.
//
// Reads the weekly LLM Strategist reports persisted by PR Strategist-1
// / -1b and lets the operator accept/reject individual proposals.
// The `unwrap` helper mirrors the `outreach` module's pattern —
// server returns `{status, data}` on success; we peel `data`
// off so callers work with plain objects.
// ───────────────────────────────────────────────────────────────

/** Peel a server envelope of shape `{status, data}` or return the
 *  raw object if the envelope is absent. Same defensive pattern
 *  used across sponsorship / bandit / outreach clients. */
function unwrapEnvelope<T>(response: unknown): T | null {
  if (response && typeof response === "object" && "data" in response) {
    const envelope = response as { data?: T | null };
    return envelope.data ?? null;
  }
  return response as T;
}

// ───────────────────────────────────────────────────────────────
// Intervention 5 Session 3 (2026-07-01) — Trend Anticipation client.
// ───────────────────────────────────────────────────────────────

// ───────────────────────────────────────────────────────────────
// Layer 5 attribution health (PR #Layer5, 2026-07-11) client.
// ───────────────────────────────────────────────────────────────

export const attributionHealth = {
  /** Fetch per-niche attribution stats over a rolling window.
   *  Ships observation-only — response drives the AttributionHealthCard's
   *  status colours + operator eyeball on ``attribution_present_pct``. */
  stats: (windowHours: number = 24) =>
    get<{
      data: import("./types").AttributionHealthResponse;
    }>("/attribution-health/stats", { window_hours: String(windowHours) }).then(
      (d) => unwrapEnvelope<import("./types").AttributionHealthResponse>(d),
    ),
};

export const counterfactualReplay = {
  /** Fetch the latest replay artifact for one niche. Returns null
   *  when the monthly runner hasn't fired yet (first-of-month
   *  cadence — up to 30 days between rewrites). */
  latest: (nicheId: string) =>
    get<{
      data: import("./types").CounterfactualReplayArtifact | null;
    }>("/counterfactual-replay/latest", { niche_id: nicheId }).then((d) =>
      unwrapEnvelope<import("./types").CounterfactualReplayArtifact>(d),
    ),
};

// 2026-08-14: Phase 0.C — reward signal audit.
export const rewardAudit = {
  /** Per-niche×platform reward distribution + health verdict. Backs
   *  RewardSignalAuditCard on Mission Control. */
  fetch: () =>
    get<{
      data: import("./types").RewardAuditNiche[] | null;
    }>("/learning/reward-audit").then((d) =>
      unwrapEnvelope<import("./types").RewardAuditNiche[]>(d),
    ),
};

// 2026-08-14: Phase 1.C — classifier quality (meta-learning).
export const classifierQuality = {
  /** Per-(source, name) verdict mix over 30d. Backs
   *  ClassifierQualityCard on Mission Control. */
  fetch: () =>
    get<{
      data: import("./types").ClassifierQualityRow[] | null;
    }>("/learning/classifier-quality").then((d) =>
      unwrapEnvelope<import("./types").ClassifierQualityRow[]>(d),
    ),
};

// 2026-08-14: Phase 2.C — SLO forecasts.
export const sloForecasts = {
  /** All current forecasts. Backs SLOForecastCard on Mission Control. */
  fetch: () =>
    get<{
      data: import("./types").SloForecast[] | null;
    }>("/monitoring/slo-forecasts").then((d) =>
      unwrapEnvelope<import("./types").SloForecast[]>(d),
    ),
};

// 2026-08-14: Phase 2.D — cost budget status.
export const costBudget = {
  /** Today's LLM spend + current throttle level. Backs
   *  CostBudgetCard on Mission Control. */
  status: () =>
    get<{
      data: import("./types").CostBudgetStatus | null;
    }>("/cost-budget/status").then((d) =>
      unwrapEnvelope<import("./types").CostBudgetStatus>(d),
    ),
};

export const autoExperiments = {
  /** Fetch the lifecycle summary — recent experiments + per-status
   *  counts + last-30d verdict tally. Returns null on any server-side
   *  error (fail-open). Cold-start scenario (empty queue) is a
   *  populated envelope with zero counts, not null. */
  summary: (nicheId: string, limit: number = 20) =>
    get<{
      data: import("./types").AutoExperimentsSummary | null;
    }>("/auto-experiments/summary", {
      niche_id: nicheId,
      limit: String(limit),
    }).then((d) =>
      unwrapEnvelope<import("./types").AutoExperimentsSummary>(d),
    ),
};

export const crossNicheTransfer = {
  /** Fetch the current cross-niche transferred priors. Returns null
   *  when the weekly runner hasn't fired yet (first Monday after
   *  enable). Frontend renders "No priors yet" defensively. */
  priors: () =>
    get<{
      data: import("./types").CrossNichePriorsArtifact | null;
    }>("/cross-niche-transfer/priors").then((d) =>
      unwrapEnvelope<import("./types").CrossNichePriorsArtifact>(d),
    ),
};

// ───────────────────────────────────────────────────────────────
// Phase 3.A observability (2026-08-14) — competitor content deltas.
// Reads the read-only endpoint served by
// dashboard/server/api/competitor_deltas.py. Same envelope shape
// as crossNicheTransfer.
// ───────────────────────────────────────────────────────────────

export const contentQuality = {
  /** Fetch per-niche aggregates from content_quality_scores.
   *  Returns null on cold-start or DB failure — card shows
   *  "no scores yet" copy. */
  summary: () =>
    get<{
      data: import("./types").ContentQualitySummary | null;
    }>("/content-quality/summary").then((d) =>
      unwrapEnvelope<import("./types").ContentQualitySummary>(d),
    ),
};

export const ideationPool = {
  /** Fetch per-niche pool depth + flag/rollout state. Returns
   *  null on cold-start (no ideas persisted yet). */
  summary: () =>
    get<{
      data: import("./types").IdeationPoolSummary | null;
    }>("/ideation-pool/summary").then((d) =>
      unwrapEnvelope<import("./types").IdeationPoolSummary>(d),
    ),
};

export const competitorDeltas = {
  /** Fetch top competitor-vs-us deltas filtered by min_ratio.
   *  Returns null when: DB unset OR cold-start OR no rows meet
   *  min_ratio floor. Frontend renders "No competitor deltas yet"
   *  copy on null. */
  latest: (params: { minRatio?: number; limit?: number } = {}) => {
    const q: Record<string, string> = {};
    if (params.minRatio !== undefined) q.min_ratio = String(params.minRatio);
    if (params.limit !== undefined) q.limit = String(params.limit);
    return get<{
      data: import("./types").CompetitorDeltasArtifact | null;
    }>("/competitor-deltas/latest", q).then((d) =>
      unwrapEnvelope<import("./types").CompetitorDeltasArtifact>(d),
    );
  },
};

// ───────────────────────────────────────────────────────────────
// B.2 + B.3 observability (2026-07-08) — top-creator prior client.
// Two endpoints because the two runners have independent flag
// gates + cadences (see the API blueprint docstring).
// ───────────────────────────────────────────────────────────────

export const topCreatorPriors = {
  /** Fetch B.2's most recent correlations for one niche. 8-day
   *  fallback window on the server side. Returns null when nothing
   *  in window (weekly runner hasn't fired or all niches skipped). */
  latest: (nicheId: string) =>
    get<{
      data: import("./types").TopCreatorPriorsArtifact | null;
    }>("/top-creator-priors/latest", { niche_id: nicheId }).then((d) =>
      unwrapEnvelope<import("./types").TopCreatorPriorsArtifact>(d),
    ),

  /** Fetch A.2's most recent uploads snapshot for one niche. 1-day
   *  fallback window (today or yesterday) — matches A.3's recency-
   *  weight zeroing at >24h. Returns null when nothing today or
   *  yesterday. */
  uploads: (nicheId: string) =>
    get<{
      data: import("./types").TopCreatorUploadsArtifact | null;
    }>("/top-creator-priors/uploads", { niche_id: nicheId }).then((d) =>
      unwrapEnvelope<import("./types").TopCreatorUploadsArtifact>(d),
    ),
};

// ───────────────────────────────────────────────────────────────
// L11 (2026-07-08 audit) — flag-state aggregator client.
// Reads /api/v1/flags/state. Case-sensitivity warnings for
// mis-set env values are the primary operator value.
// ───────────────────────────────────────────────────────────────

export const flagState = {
  /** Fetch the aggregated GENLAB_* flag state. Always returns a
   *  non-null payload — the endpoint's fail-open path returns the
   *  full catalog with `status="dormant"` when no env vars are set. */
  get: () =>
    get<{
      data: import("./types").FlagStatePayload;
    }>("/flags/state").then((d) =>
      unwrapEnvelope<import("./types").FlagStatePayload>(d),
    ),
};

// ───────────────────────────────────────────────────────────────
// L4 (2026-07-08 audit) — Bayesian gate + conformal router clients.
// Each surfaces the "observation-only" per-niche state snapshot
// before the consumer wire flip. Both endpoints fail-open on cold
// start (empty niches list), so callers should render "no niches
// yet" defensively when `niches.length === 0`.
// ───────────────────────────────────────────────────────────────

export const bayesianGateState = {
  /** Fetch the per-niche posterior summary. Never returns null —
   *  cold start returns `{niches: []}` with `flag_enabled` still
   *  populated from the runtime env. */
  get: () =>
    get<{
      data: import("./types").BayesianGateStatePayload;
    }>("/bayesian-gate/state").then((d) =>
      unwrapEnvelope<import("./types").BayesianGateStatePayload>(d),
    ),
};

export const conformalRouterState = {
  /** Fetch the per-niche conformal router state. Same cold-start
   *  contract as bayesianGateState — `niches: []` when nothing
   *  has been fit yet. */
  get: () =>
    get<{
      data: import("./types").ConformalRouterStatePayload;
    }>("/conformal-router/state").then((d) =>
      unwrapEnvelope<import("./types").ConformalRouterStatePayload>(d),
    ),
};

// ───────────────────────────────────────────────────────────────
// L5 (2026-07-08 audit) — ensemble votes summary client.
// niche_id is optional; omit to aggregate across all niches.
// ───────────────────────────────────────────────────────────────

export const ensembleVotes = {
  /** Fetch per-component participation + recommendation distribution.
   *  Returns null when the migration hasn't landed yet on the target
   *  DB (endpoint's fail-open path — see `test_query_failure_returns_
   *  pending_migration_message`). */
  summary: (params: { nicheId?: string; windowDays?: number } = {}) => {
    const query: Record<string, string> = {};
    if (params.nicheId) query.niche_id = params.nicheId;
    if (params.windowDays) query.window_days = String(params.windowDays);
    return get<{
      data: import("./types").EnsembleVotesSummaryPayload | null;
    }>("/ensemble-votes/summary", query).then((d) =>
      unwrapEnvelope<import("./types").EnsembleVotesSummaryPayload>(d),
    );
  },
};

// ───────────────────────────────────────────────────────────────
// L7 (2026-07-08 audit) — drift signals summary client.
// Regressions capped at 20, improvements at 10 (server-side).
// ───────────────────────────────────────────────────────────────

export const driftSignals = {
  /** Fetch recent regressions + improvements + per-niche counts.
   *  Returns null when the migration hasn't landed yet. */
  summary: (params: {
    nicheId?: string;
    windowDays?: number;
    minAbsZ?: number;
  } = {}) => {
    const query: Record<string, string> = {};
    if (params.nicheId) query.niche_id = params.nicheId;
    if (params.windowDays) query.window_days = String(params.windowDays);
    if (params.minAbsZ !== undefined) query.min_abs_z = String(params.minAbsZ);
    return get<{
      data: import("./types").DriftSignalsSummaryPayload | null;
    }>("/drift-signals/summary", query).then((d) =>
      unwrapEnvelope<import("./types").DriftSignalsSummaryPayload>(d),
    );
  },
};

// ───────────────────────────────────────────────────────────────
// PR 14 (2026-07-05) — Transformation bandit observability client.
// ───────────────────────────────────────────────────────────────

export const transformationBandit = {
  /** Fetch per-niche transformation-bandit summary. Returns null
   *  when no arms are registered yet (pre-PR-3-run install) or
   *  DB unavailable. Frontend renders "No transformation arms yet". */
  summary: () =>
    get<{
      data: import("./types").TransformationBanditSummary | null;
    }>("/transformation-bandit/summary").then((d) =>
      unwrapEnvelope<import("./types").TransformationBanditSummary>(d),
    ),
};

export const productBandit = {
  /** L3 PR 9 (2026-07-07) — per-niche product-bandit summary.
   *  Returns null when no product arms are registered yet
   *  (pre-register_product_arms.py run) or DB unavailable. Frontend
   *  renders "No product arms yet". Endpoint filters bandit_arms to
   *  arm_type='product' and groups by niche → top-5 by posterior
   *  mean. */
  summary: () =>
    get<{
      data: import("./types").ProductBanditSummary | null;
    }>("/product-bandit/summary").then((d) =>
      unwrapEnvelope<import("./types").ProductBanditSummary>(d),
    ),

  /** L3 PR 12a (2026-07-07) — per-niche selector-vs-matcher
   *  agreement stats. Called once per niche_id per card refresh.
   *  Fails-open server-side to sample_count=0 when DB unavailable
   *  or DATABASE_URL unset (no error path visible to caller). */
  divergenceStats: (nicheId: string, windowDays = 7) =>
    get<{
      data: import("./types").DivergenceStats;
    }>(
      `/product-bandit/divergence-stats?niche_id=${encodeURIComponent(nicheId)}&window_days=${windowDays}`,
    ).then((d) => unwrapEnvelope<import("./types").DivergenceStats>(d)),
};

export const trendAnticipation = {
  /** Fetch today's anticipation ranking for one niche. Returns null
   *  when the runner hasn't fired yet (cold start day, first run
   *  after enable). Frontend renders that as "No ranking yet". */
  latest: (nicheId: string) =>
    get<{
      data: import("./types").TrendAnticipationArtifact | null;
    }>("/trend-anticipation/latest", { niche_id: nicheId }).then(
      (d) =>
        unwrapEnvelope<import("./types").TrendAnticipationArtifact>(d),
    ),

  /** Session 3b: fetch this week's accuracy measurement for one
   *  niche. Returns null when the weekly runner hasn't fired yet
   *  or when the measurement is env-flag-disabled. */
  accuracy: (nicheId: string) =>
    get<{
      data: import("./types").AnticipationAccuracy | null;
    }>("/trend-anticipation/accuracy", { niche_id: nicheId }).then(
      (d) => unwrapEnvelope<import("./types").AnticipationAccuracy>(d),
    ),
};

export const strategist = {
  /** Fetch the latest Strategist report for a niche.
   *  Returns null when the persister has no rows yet (cold start —
   *  first Strategist run hasn't fired for this niche). */
  latest: (nicheId: string) =>
    get<{ data: import("./types").StrategistReport | null }>(
      "/strategist/reports/latest",
      { niche_id: nicheId },
    ).then((d) => unwrapEnvelope<import("./types").StrategistReport>(d)),

  /** Fetch the queue of reports the operator hasn't reviewed yet.
   *  Used by a "you have N unreviewed strategist reports" badge in
   *  a future card iteration; the initial card iteration surfaces
   *  only the latest-per-niche via `latest()` above. */
  unreviewed: (limit = 20) =>
    get<{ data: import("./types").StrategistReport[] }>(
      "/strategist/reports/unreviewed",
      { limit: String(limit) },
    ).then(
      (d) => unwrapEnvelope<import("./types").StrategistReport[]>(d) ?? [],
    ),

  /** Record operator's accept/reject decision on a report's proposals.
   *  Both lists optional per server contract — empty lists mean
   *  "dismissed the whole report without acting". */
  review: (
    reportId: string,
    body: import("./types").StrategistReviewRequest,
  ) =>
    mutate<{ status: string }>(
      "POST",
      `/strategist/reports/${encodeURIComponent(reportId)}/review`,
      body,
    ),
};
