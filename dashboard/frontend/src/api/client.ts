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
  MonetizationData,
  MonetisationProgressData,
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
  };
}

export const autoApproval = {
  calibrationStats: (nicheId: string, windowDays = 7) =>
    get<CalibrationStats>("/auto-approval/calibration-stats", {
      niche_id: nicheId,
      window_days: String(windowDays),
    }),
  trackRecord: (nicheId: string, windowDays = 30, binDays = 1) =>
    get<TrackRecordResponse>("/auto-approval/track-record", {
      niche_id: nicheId,
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

// ── M-19: youtube_channels source-edit endpoints ────────────────
//
// Mounted under `/api/v1/config/sources/youtube-channels` (NOT
// `/api/v1/sources/...` — config_routes.py owns the ruamel round-trip
// for sources.yaml; sources.py is read-only performance data).
export interface YoutubeChannelEntry {
  url: string;
  name: string;
}

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
    get<MonetizationData>("/analytics/monetization", params),
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
    get<MonetisationProgressData | { data: MonetisationProgressData }>("/monetisation/progress").then(
      (d) => {
        // Handle both wrapped {data: {...}} and direct {...} formats
        if (d && typeof d === "object" && "data" in d && typeof (d as any).data === "object") {
          return (d as { data: MonetisationProgressData }).data;
        }
        return d as MonetisationProgressData;
      },
    ),
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
