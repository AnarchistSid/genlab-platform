export interface PaginatedResponse<T> {
  data: T[];
  meta: {
    page: number;
    per_page: number;
    total: number;
    total_pages: number;
  };
}

export interface SingleResponse<T> {
  data: T;
}

export interface Blueprint {
  id: string;
  candidate_id?: string;
  status: "INTEL_READY" | "DRAFTED" | "VISUAL_READY" | "PUBLISHED" | "ARCHIVED" | "PUBLISH_FAILED" | "ERROR";
  format?: string;
  hook_text?: string;
  title?: string;
  video_url?: string;
  caption?: string;
  cta?: string;
  hashtags?: string;
  template_id?: string;
  story_id?: string;
  niche_id?: string;
  scheduled_for?: string | null;
  /**
   * Set true by `mark_cap_violations` (server-side, dashboard/server/core/
   * publishing_queue.py) when this blueprint shares a (niche, date)
   * bucket with another scheduled post that the publisher's
   * `DailyCapEnforcer` will pick first. The 2nd, 3rd, ... post in such
   * a bucket will silently skip at publish-time with
   * `[daily_cap] daily cap reached`. The earliest in the bucket has
   * this field absent/false. UI renders a warning badge so the
   * operator can re-schedule or accept the silent skip.
   */
  cap_violation?: boolean;
  visual_paths?: string | null;
  landscape_video_url?: string | null;
  video_duration?: number | null;
  slide_previews?: Array<{ url: string }> | null;
  platform_publish_status?: Record<string, string> | null;
  youtube_content?: Record<string, unknown> | null;
  twitter_content?: Record<string, unknown> | null;
  facebook_content?: Record<string, unknown> | null;
  all_media_urls?: string[];
  platform_publish_details?: Record<string, Record<string, unknown>>;
  priority_score?: number;
  generation_cost_usd?: number;
  action_taken?: string | null;
  feedback_issue?: string | null;
  feedback_notes?: string | null;
  reviewed_at?: string | null;
  created_at?: string;
  ig_reach?: number;
  ig_likes?: number;
  ig_comments?: number;
  yt_views?: number;
  yt_likes?: number;
  yt_comments?: number;
  engagement_rate?: number;
  insights_collected_at?: string;
}

export interface Story {
  id: string;
  story_id?: string;
  title?: string;
  source?: string;
  url?: string;
  published_date?: string;
  score?: number;
  cluster_id?: string | null;
  summary?: string;
}

export interface ScheduleSlot {
  time: string;
  blueprint: Blueprint | null;
  status: "published" | "scheduled" | "empty";
  niche_id?: string;
  video_url?: string;
  title?: string;
}

export interface ScheduleDay {
  date: string;
  slots: ScheduleSlot[];
  coverage: number;
}

export interface PipelineRun {
  run_id: string;
  date: string;
  run_type?: string;
  duration_seconds: number;
  steps_completed?: number;
  total_steps?: number;
  errors: number;
  status?: string;
  cost_estimate: number;
  cost_breakdown?: Record<string, number>;
  // RENDER #2 (2026-06-13): per-stage silent-failure attribution.
  // Empty {} or undefined means all tracked stages clean. Non-empty
  // means status was promoted from "success" to "partial" — render a
  // badge per entry (e.g. "whisper_captions: 2") so operators see
  // what specifically failed without grepping the journal.
  stage_failures?: Record<string, number>;
}

export interface DailyCost {
  date: string;
  total_cost: number;
  run_count: number;
  breakdown: Record<string, number>;
  run_types: Record<string, number>;
}

export interface PostEngagement {
  id: string;
  blueprint_id?: string;
  platform?: string;
  impressions?: number;
  reach?: number;
  engagement?: number;
  likes?: number;
  comments?: number;
  saved?: number;
  shares?: number;
  plays?: number;
  viral_score?: number;
  engagement_rate?: number;
  save_rate?: number;
  share_rate?: number;
  play_rate?: number;
  fetched_at?: string;
  fetch_window?: string;
}

export interface PlatformEngagementSummary {
  platform: string;
  total_posts: number;
  avg_engagement_rate: number;
  avg_viral_score: number;
  total_impressions: number;
  total_reach: number;
  total_likes: number;
  total_comments: number;
  total_shares: number;
  total_saves: number;
}

export interface PublishingRecord {
  id: string | number;
  blueprint_id?: string;
  platform?: string;
  status?: string;
  published_at?: string;
  post_id?: string;
  insight_error_type?: string;
}

export interface EngagementRecord {
  id: string | number;
  platform?: string;
  blueprint_id?: string;
  impressions?: number;
  reach?: number;
  likes?: number;
  comments?: number;
  shares?: number;
  saved?: number;
  plays?: number;
  engagement_rate?: number;
  viral_score?: number;
  save_rate?: number;
  share_rate?: number;
  play_rate?: number;
  fetched_at?: string;
  fetch_window?: string;
  story_title?: string;
  format?: string;
  published_at?: string;
}

export interface EngagementSummary {
  platform: string;
  total_posts: number;
  avg_engagement_rate: number;
  avg_viral_score: number;
  total_impressions: number;
  total_reach: number;
  total_likes: number;
  total_comments: number;
  total_shares: number;
  total_saves: number;
}

export interface FunnelStage {
  stage: string;
  count: number;
}

export interface HeatmapCell {
  day: string;
  hour: number;
  count: number;
}

export interface Notification {
  id: string;
  type: string;
  title: string;
  body: string;
  read: boolean;
  created_at: string;
  entity_id?: string;
}

// ── Sponsorship Readiness ──────────────────────────────────
// PR T (2026-06-23) — operator-leverage opener. The Mission Control
// SponsorshipReadinessCard polls /api/v1/sponsorship/readiness every
// 60s and tiers each niche so the operator sees "which channels can
// I pitch sponsors on this week?" without computing it by hand.

export type SponsorshipTier =
  | "eligible_now"
  | "within_2_months"
  | "within_6_months"
  | "tracking";

export interface SponsorshipPrimaryMetric {
  metric_name: string;
  pct_complete: number | null;
  current_value: number | null;
  target_value: number | null;
  days_to_threshold_est: number | null;
  delta_7d: number | null;
  is_threshold_met: boolean;
}

export interface SponsorshipPlatformSummary {
  is_monetised: boolean;
  primary_metric: SponsorshipPrimaryMetric | null;
  metric_count: number;
}

export interface SponsorshipNicheReadiness {
  tier: SponsorshipTier;
  nearest_threshold_days: number | null;
  platforms: Record<string, SponsorshipPlatformSummary>;
}

export type SponsorshipReadinessData = Record<string, SponsorshipNicheReadiness>;

// ── Media Kit ──────────────────────────────────────────────
// PR U (2026-06-23) — operator-deliverable media kit. The MediaKit
// route renders this with print-friendly CSS so the operator can
// Cmd+P → "Save as PDF" → email to a brand.

export interface MediaKitAudienceEntry {
  /** Platform identifier, e.g. "youtube", "instagram". */
  platform: string;
  /** The headline-metric name (subscribers / followers / fans). */
  metric_name: string;
  current_value: number | null;
  delta_7d: number | null;
  is_threshold_met: boolean;
}

export interface MediaKitData {
  niche_id: string;
  tier: SponsorshipTier;
  nearest_threshold_days: number | null;
  /** Ordered list — sorted DESC by current_value. The list shape
   * (not a dict) is deliberate: it survives Flask's JSON_SORT_KEYS. */
  audience: MediaKitAudienceEntry[];
  monetised_platforms: string[];
  /** ISO-8601 UTC timestamp of generation. */
  generated_at: string;
}

/** Per-niche slice inside the portfolio kit. Same shape as
 * MediaKitData minus the top-level generated_at (the portfolio's
 * generated_at is at the envelope level). */
export interface MediaKitPortfolioNiche {
  niche_id: string;
  tier: SponsorshipTier;
  nearest_threshold_days: number | null;
  audience: MediaKitAudienceEntry[];
  monetised_platforms: string[];
}

// PR W (2026-06-23) — Outreach template generator.
// Ready-to-send sponsor outreach pre-drafted per-niche; the
// SponsorshipReadinessCard's "Copy pitch" button calls this
// endpoint and writes the body to the clipboard.

export interface OutreachAudienceEntry {
  platform: string;
  metric_name: string;
  current_value: number | null;
  delta_7d: number | null;
  is_threshold_met: boolean;
}

export interface OutreachTemplate {
  niche_id: string;
  tier: SponsorshipTier;
  /** Tier-aware subject ("Sponsorship opportunity" vs "Quick intro"). */
  subject: string;
  /** Full email/DM body with [BRAND] + [NAME] placeholders. */
  body: string;
  /** Relative URL — the frontend can prefix with origin if needed. */
  media_kit_url: string;
  /** Top-3 platforms for inline preview without re-fetch. */
  audience_summary: OutreachAudienceEntry[];
}

export interface MediaKitPortfolioData {
  /** Always all 5 niches, in stable order. Cold-start niches appear
   * with tier=tracking + empty audience rather than being omitted. */
  niches: MediaKitPortfolioNiche[];
  summary: {
    eligible_now_count: number;
    monetised_platforms_total: number;
  };
  generated_at: string;
}

export interface BlueprintUpdatedEvent {
  id: string;
  status: string;
  platform_publish_status?: Record<string, string>;
}

export interface PipelineProgressEvent {
  run_id: string;
  niche_id?: string;
  pipeline_status?: string;
  current_stage?: string | null;
  stage_index?: number;
  total_stages?: number;
  items_processed?: number;
  items_total?: number;
  elapsed_seconds?: number;
  /** Legacy fields from express pipeline events */
  step?: string;
  step_index?: number;
  total_steps?: number;
  status?: string;
  message?: string;
}

// ── Content Performance ─────────────────────────────────────

export interface FormatPerformance {
  format: string;
  total_posts: number;
  avg_engagement_rate: number;
  avg_viral_score: number;
  total_impressions: number;
  total_reach: number;
}

export interface TemplatePerformance {
  template_id: string;
  total_posts: number;
  avg_engagement_rate: number;
  avg_viral_score: number;
}

export interface TopicPerformance {
  topic_category: string;
  total_posts: number;
  avg_engagement_rate: number;
  avg_viral_score: number;
}

export interface ContentPerformanceData {
  by_format: FormatPerformance[];
  by_template: TemplatePerformance[];
  by_topic: TopicPerformance[];
}

// ── Engagement Trends ───────────────────────────────────────

export interface TrendDataPoint {
  date: string;
  platform: string;
  avg_engagement_rate: number;
  total_impressions: number;
  total_reach: number;
  total_likes: number;
  total_shares: number;
  post_count: number;
}

// ── Audience Growth ─────────────────────────────────────────

export interface AudienceSnapshot {
  id: string | number;
  platform: string;
  date: string;
  followers: number;
  following?: number;
  posts_count?: number;
  subscriber_count?: number;
  fetched_at?: string;
}

// ── Virality Breakdown ──────────────────────────────────────

export interface ViralitySignalWeights {
  [signal: string]: number;
}

export interface ViralPost {
  id: string | number;
  blueprint_id?: string;
  platform?: string;
  viral_score: number;
  engagement_rate?: number;
  impressions?: number;
  story_title?: string;
  format?: string;
}

export interface ViralityBreakdownData {
  weights: Record<string, ViralitySignalWeights>;
  top_posts: ViralPost[];
}

// ── Monetization ────────────────────────────────────────────

export interface ClickTrend {
  date: string;
  clicks: number;
}

// ── Demographics ────────────────────────────────────────────

export interface YouTubeDemographics {
  fetched_at: string;
  channel_id: string;
  age_gender: {
    age_groups: Record<string, number>;
    genders: Record<string, number>;
    period: string;
    total_rows: number;
  } | null;
  countries: {
    countries: Array<{
      country_code: string;
      views: number;
      estimated_minutes_watched: number;
    }>;
    period: string;
  } | null;
}

// ── Monetization ────────────────────────────────────────────

/**
 * Wire shape of GET /api/v1/analytics/monetization (R-06 audit
 * 2026-06-18: the type used to declare a config/metrics shape that
 * the server never actually returned — see dashboard/server/api/
 * analytics.py:1430). The wire shape comes wrapped in
 * ``{status, data: {...}}`` by ``api_success``; the inner ``data``
 * object is what callers see.
 */
export interface MonetizationActiveProgram {
  name: string;
  slug: string;
  commission: string;
  cta_text?: string;
}

export interface MonetizationData {
  active_programs: MonetizationActiveProgram[];
  total_programs: number;
  posts_with_affiliate_links: number;
  posts_with_newsletter_cta: number;
  total_published: number;
  catalog_networks: Record<string, number>;
}

/** Envelope wrapper that ``analytics.monetization`` returns from the API. */
export interface MonetizationDataEnvelope {
  data: MonetizationData;
}

// ── Analytics Overview (Phase 5) ────────────────────────────

export interface AnalyticsBestPost {
  id: string;
  title: string;
  hook_text: string;
  total_reach: number;
  engagement_rate: number | null;
  platform: string;
  published_at: string;
}

export interface AnalyticsSummary {
  total_reach: number;
  total_posts: number;
  total_likes?: number;
  total_comments?: number;
  avg_engagement_rate: number | null;
  publish_success_rate: number;
  best_post: AnalyticsBestPost | null;
}

export interface PlatformMetrics {
  reach: number;
  posts: number;
  avg_engagement_rate: number | null;
  metric_label: string;
  data_status?: "available" | "no_metrics" | "no_data";
}

export interface TimeSeriesPoint {
  date: string;
  total_reach: number;
  posts: number;
  instagram_reach: number;
  youtube_reach: number;
  x_reach: number;
  facebook_reach: number;
  tiktok_reach: number;
  threads_reach: number;
  ai_creators_reach: number;
  gaming_reach: number;
}

export interface FunnelData {
  fetched: number;
  filtered: number;
  written: number;
  rendered: number;
  published: number;
}

export interface TopPerformer {
  id: string;
  title: string;
  hook_text: string;
  niche_id: string;
  platform: string;
  total_reach: number;
  engagement_rate: number | null;
  published_at: string;
}

// ── Publishing Queue ────────────────────────────────────────

export type QueueStatus =
  | "PENDING_APPROVAL"
  | "APPROVED"
  | "HELD"
  | "PUBLISHED"
  | "PUBLISH_FAILED";

export interface QueueItem extends Blueprint {
  queue_status: QueueStatus;
}

export interface QueueStats {
  pending: number;
  approved: number;
  held: number;
  published: number;
  failed: number;
  total: number;
}

export type ChannelStatus = "ok" | "degraded" | "error" | "not_configured";

export interface ChannelHealth {
  instagram: ChannelStatus;
  youtube: ChannelStatus;
  x_twitter: ChannelStatus;
  facebook: ChannelStatus;
  tiktok: ChannelStatus;
  threads: ChannelStatus;
}

// ── Token Health ────────────────────────────────────────────

export interface PlatformTokenHealth {
  platform: string;
  token_status?: "ok" | "refresh_soon" | "critical" | "missing" | "unknown";
  access_token_status?: "ok" | "refresh_soon" | "critical" | "missing" | "unknown";
  access_token_expires_in_hours?: number | null;
  refresh_token_status?: "ok" | "refresh_soon" | "critical";
  refresh_token_expires_in_days?: number | null;
  expires_in_days?: number | null;
  last_refreshed?: string | null;
  audit_approved?: boolean;
}

export interface TokenHealthResponse {
  platforms: PlatformTokenHealth[];
  checked_at: string;
}

// ── Monetisation Progress ────────────────────────────────────

export interface MonetisationMetric {
  metric_name: string;
  current_value: number | null;
  target_value: number | null;
  pct_complete: number | null;
  delta_7d: number | null;
  days_to_threshold_est: number | null;
  is_threshold_met: boolean;
  data_source: string;
  as_of_date: string | null;
  error_log: string | null;
}

export interface MonetisationPlatformProgress {
  metrics: MonetisationMetric[];
  is_monetised: boolean;
}

/** niche_id → platform → progress */
export type MonetisationProgressData = Record<
  string,
  Record<string, MonetisationPlatformProgress>
>;

// ── Analytics Overview (Phase 5) ────────────────────────────

export interface AnalyticsOverviewResponse {
  window: string;
  niche_id: string;
  is_estimated: boolean;
  platform_data_status?: Record<string, "available" | "no_metrics" | "no_data">;
  summary: AnalyticsSummary;
  by_platform: Record<string, PlatformMetrics>;
  time_series: TimeSeriesPoint[];
  funnel: FunnelData;
  top_performers: TopPerformer[];
}

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
  /** Total reward-bearing PF rows in the last 30 days across all niches. */
  config_update_progress: number;
  /** Niches whose 30-day reward-bearing PF count >= MIN_DATA_POINTS. */
  niches_at_config_quota: number;
  linucb_threshold: number;
  linucb_max_plays: number;
}

export interface HookClassifierNicheStatus {
  trained: boolean;
  n_examples?: number;
  pos_rate?: number;
  n_features?: number;
}

export interface ConfigUpdateRow {
  id: string;
  niche_id: string;
  file_path: string;
  field: string;
  old_value: string | null;
  new_value: string | null;
  reason: string | null;
  n_records: number | null;
  applied_at: string;
  dry_run: boolean;
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
  media_id?: string;
}

export interface TopPost {
  post_id: string;
  platform: string;
  niche_id: string;
  likes: number;
  comments: number;
  reach: number;
  collected_at: string;
  hook_text?: string;
  title?: string;
}

export type TrendData = Record<string, string[]>;

// ── Cross-Niche Overview ────────────────────────────────────

export interface CrossNicheOverviewResponse {
  generated_at: string;
  prefect_connected?: boolean;
  global: {
    total_pending_review: number;
    total_published_today: number;
    agents_running: number;
    platform_health: Record<string, "ok" | "degraded" | "error" | "not_configured">;
    total_archived?: number;
    auto_archive_today?: {
      total: number;
      by_reason: Record<string, number>;
      pass_rate: number | null;
      pass_rate_note?: string;
    };
    total_reach?: number;
    total_likes?: number;
    total_comments?: number;
    configured_platform_count?: number;
  };
  niches: Array<{
    id: string;
    display_name: string;
    accent_hex: string;
    pipeline_status: "running" | "idle" | "error";
    current_stage: string | null;
    last_run_at: string | null;
    pending_review: number;
    published_today: number;
    archived_today?: number;
    target_posts_per_day: number;
    best_performer_today: {
      title: string;
      score: number;
      platform: string;
    } | null;
  }>;
  niche_daily_reach?: Record<string, Array<{ date: string; reach: number }>>;
  schedule_today: Array<{
    id?: string;
    slot_time_ist: string;
    niche_id: string;
    title: string;
    status: "published" | "scheduled" | "in-progress" | "failed";
    platforms: string[];
    platform_results?: Record<string, string>;
  }>;
}

// ── Detailed Health ─────────────────────────────────────────

export interface LaunchAgentInfo {
  status: string;
  pid?: number | null;
  label?: string;
}

export interface DetailedHealthResponse {
  services?: {
    redis?: { status: string; response_time_ms?: number };
    prefect?: { status: string; url?: string };
    dashboard?: { status: string };
    cloudflare_tunnel?: { status: string };
    postgres?: { status: string; response_time_ms?: number };
  };
  postgres?: { status: string; response_time_ms?: number };
  last_run?: Record<string, string | null>;
  error_rate_24h?: number;
  disk_usage?: { used_gb?: number; total_gb?: number; pct?: number };
  engagement_pollers?: Record<string, string>;
  launch_agents?: Record<string, LaunchAgentInfo>;
  storage_backend?: string;
  token_matrix?: Record<string, Record<string, string>>;
  checked_at?: string;
}

// ── Notification Preferences ────────────────────────────────

export interface NotificationPreferences {
  slack_webhook_url: string;
  email_digest: "daily" | "weekly" | "never";
  enabled_types: string[];
}

// ── Engagement Status ───────────────────────────────────────

// Shape returned by GET /api/v1/engagement/status — counts of
// pending/replied/failed/skipped engagement queue rows per niche
// plus a totals roll-up.
export interface EngagementStatusResponse {
  by_niche: Record<
    string,
    {
      pending: number;
      replied: number;
      failed: number;
      skipped: number;
    }
  >;
  totals: {
    pending: number;
    replied: number;
    failed: number;
    skipped: number;
  };
  replied_today_by_platform: Record<string, number>;
}

// ── Cross-Niche Analytics ────────────────────────────────────

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

// ── Quality Stats ───────────────────────────────────────────

export interface QualityStats {
  hooks_generated: number;
  qc_passed: number;
  qc_failed: number;
  qc_total: number;
  videos_validated: number;
  videos_fixed: number;
}
