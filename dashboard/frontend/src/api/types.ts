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

// ── Tier Transitions (PR #496 + PR JJ frontend, 2026-06-23) ────
// Recent tier-change events for the SponsorshipReadinessCard's
// "Recent activity" footer. Backend (PR #496) writes on every
// /readiness fetch; frontend polls /recent-transitions to surface.

export interface RecentTierTransition {
  niche_id: string;
  /** New tier the niche transitioned TO. */
  tier: SponsorshipTier;
  /** Prior tier; null for first-ever record for this niche. */
  prev_tier: SponsorshipTier | null;
  nearest_threshold_days: number | null;
  /** ISO-8601 UTC of the transition. */
  observed_at: string | null;
}

export interface RecentTransitionsResponse {
  window_hours: number;
  /** Ordered DESC by observed_at, max 100. */
  transitions: RecentTierTransition[];
}

// ── Bandit Hour Posteriors ─────────────────────────────────
// PR BB (2026-06-23) — Mission Control hour-heatmap card. Operator
// feedback loop for PR #487's optimal-time bandit foundation.

export interface BanditHourEntry {
  /** UTC hour 0..23. */
  hour: number;
  /** Aggregated Beta(alpha) across all platforms for this niche+hour. */
  alpha_sum: number;
  /** Aggregated Beta(beta) across all platforms. */
  beta_sum: number;
  /** alpha+beta-2 baseline — proxy for observation count. */
  n_obs_est: number;
  /** alpha / (alpha+beta), or 0.5 for cold-start hours. */
  mean: number;
}

export interface BanditHourPosteriors {
  niche_id: string;
  /** Always 24 entries (hour 0..23), even when most are cold-start. */
  hours: BanditHourEntry[];
  total_observations: number;
  /** True when total_observations ≥ 30 — same threshold as the
   * AutoApprovalCalibrationCard's readiness gate. Operator's signal
   * to flip GENLAB_OPTIMAL_TIME_BANDIT=1. */
  ready_for_activation: boolean;
}

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

/** PR CC (2026-06-23) — top-performing post entry in the kit. */
export interface MediaKitTopPost {
  platform: string;
  post_id: string;
  /** Stable public URL. Platforms with derivable URLs: YT, FB, X,
   * Threads, TikTok (PR #493). IG deferred to v2. */
  post_url: string;
  /** PR GG (2026-06-23) — thumbnail URL when derivable from post_id
   * alone. YT only at v1 (i.ytimg.com/vi/{id}/maxresdefault.jpg).
   * Frontend renders thumbnail when present, text-row fallback
   * when null. Other platforms need Graph API calls — deferred. */
  thumbnail_url: string | null;
  /** ISO-8601 UTC. */
  published_at: string | null;
  views: number;
  likes: number;
  comments: number;
  /** Composite engagement = views + likes*5 + comments*10. */
  score: number;
}

export interface MediaKitData {
  niche_id: string;
  tier: SponsorshipTier;
  nearest_threshold_days: number | null;
  /** Ordered list — sorted DESC by current_value. The list shape
   * (not a dict) is deliberate: it survives Flask's JSON_SORT_KEYS. */
  audience: MediaKitAudienceEntry[];
  monetised_platforms: string[];
  /** PR CC (2026-06-23) — top 3 recent posts by composite engagement.
   * Empty when no qualifying posts (cold start) or fetch fails. */
  top_posts: MediaKitTopPost[];
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
  /** PR CC (2026-06-23) — per-niche top posts inside the portfolio. */
  top_posts: MediaKitTopPost[];
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

/** Per-niche slice surfaced inside the portfolio outreach template
 * (PR AA). Just enough for the frontend to render a per-niche
 * inline preview if it wants — the body string is already a fully-
 * composed cross-channel pitch. */
export interface PortfolioOutreachNiche {
  niche_id: string;
  tier: SponsorshipTier;
  audience_summary: OutreachAudienceEntry[];
}

export interface PortfolioOutreachTemplate {
  /** Subject — "Cross-channel sponsorship opportunity" when
   * eligible_now_count ≥ 1, else "Quick intro: 5-channel video portfolio". */
  subject: string;
  /** Full email/DM body with [BRAND] + [NAME] placeholders. */
  body: string;
  /** Always /media-kit/all — the portfolio kit. */
  media_kit_url: string;
  /** How many niches are tier=eligible_now (0..5). Drives CTA choice. */
  eligible_now_count: number;
  /** Always 5 with the current niche registry. */
  niche_count: number;
  /** Per-niche slices in stable order. */
  niches: PortfolioOutreachNiche[];
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
    // PR #510 (2026-06-24): structured split of the historical
    // ``total_pending_review`` bucket. DRAFTED → render_retry (needs
    // pipeline re-run, not operator click); VISUAL_READY-with-NULL-
    // action → operator_review (needs operator click). The union
    // stays in ``total_pending_review`` for backward compat. Prefer
    // ``total_operator_review`` for "X needs your attention" banners
    // — it filters out the render-retry items the operator can't act
    // on directly.
    total_render_retry?: number;
    total_operator_review?: number;
    oldest_operator_review_age_hours?: number | null;
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
    // PR #554 (2026-06-25, SR-F wire pass 14 consumer): the server
    // sets these when the current operator has a niche allowlist
    // configured (REVIEW_NICHE_ALLOWLIST_<USER> env). The shape
    // below has been carve-out filtered to the operator's scope:
    //
    //   * niches[]            → trimmed to allowlist niches only
    //   * schedule_today[]    → trimmed by niche_id
    //   * niche_daily_reach{} → trimmed by key
    //   * total_*             → recomputed from filtered niches
    //   * total_reach         → reconstructed from 14d sparkline
    //                           (vs global 30d); _sr_f_note flags
    //                           this limitation
    //   * total_likes/comments → fail-secure to 0 (no per-niche
    //                            breakdown in scope; PR #553 deferred
    //                            per-request filtered SQL)
    //
    // When undefined / false the operator is unrestricted and the
    // response is the original full overview shape — backward compat
    // for admin sessions. The ScopedAccessBanner consumes these.
    _sr_f_scoped?: boolean;
    _sr_f_note?: string;
  };
  niches: Array<{
    id: string;
    display_name: string;
    accent_hex: string;
    pipeline_status: "running" | "idle" | "error";
    current_stage: string | null;
    last_run_at: string | null;
    pending_review: number;
    // PR #510: structured per-niche split. ``render_retry_count`` =
    // DRAFTED count (different action: pipeline retry, not click).
    // ``operator_review_count`` = VISUAL_READY+NULL action count.
    render_retry_count?: number;
    operator_review_count?: number;
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

// ── Source-Discovery Proposer (Mission Control, PR after #586) ──

/**
 * One ranked source-channel suggestion returned by
 * /api/v1/source-discovery/proposals.
 *
 * Mirrors genlab_core.source_discovery.ChannelSuggestion (frozen
 * dataclass) — values are read-only on the frontend; mutations
 * would go to a separate "add to sources.yaml" endpoint (future PR).
 */
export interface SourceChannelProposal {
  source_channel_id: string;
  clips_used: number;
  total_engagement: number;
  avg_engagement: number;
  /** ISO-8601 datetime or null. */
  last_used_at: string | null;
  /** Reserved for a future PR that resolves YouTube channel display
   *  names via the Data API. */
  channel_name: string | null;
  sample_blueprints: string[];
}

export interface SourceDiscoveryProposalsResponse {
  niche_id: string;
  window_days: number;
  min_clips: number;
  proposals: SourceChannelProposal[];
  /** Present when the core build lacks the source_discovery module
   *  (pre-PR-586 deployment) — card renders zero-state. */
  warning?: string;
}

// ── Compliance Stats (Mission Control dashboard, PR after #581) ──

/**
 * Per-niche aggregation row from /api/v1/compliance/stats.
 *
 * Mirrors the shape returned by genlab_core.compliance.events.stats_by_niche.
 * Niches with zero events in the window are absent from the parent dict —
 * the card fills the gap with a zero-state row.
 */
export interface ComplianceNicheStats {
  total: number;
  warns: number;
  blocks: number;
  allows: number;
  /** Most-frequent warn/block event_type, or null when only allows. */
  top_event_type: string | null;
  /** Count of warn+block for top_event_type (the operator-attention metric). */
  top_event_count: number;
  by_event_type: Record<
    string,
    { warn: number; block: number; allow: number }
  >;
}

export interface ComplianceStatsResponse {
  window_days: number;
  by_niche: Record<string, ComplianceNicheStats>;
}

// ── Per-niche per-platform publishing health (PR B, 2026-06-27) ──

/**
 * One row of the publishing-health matrix: one (niche, platform) pair.
 *
 * Mirrors the rows produced by
 * ``server.core.publishing_health_per_niche.fetch_per_niche_platform_health``.
 *
 * Fields:
 *   * success_rate_pct = round(100 * success / (success+failed), 1) when
 *     sample count >= 3; null otherwise (insufficient-data → "—" cell).
 *   * last_failure_error is the FULL error_message from the latest
 *     FAILED publish; the frontend truncates for tooltip display.
 */
export interface PerNichePlatformHealthRow {
  niche_id: string;
  platform: string;
  success_count: number;
  failed_count: number;
  /** Null when sample count < 3 (insufficient data); card renders "—". */
  success_rate_pct: number | null;
  /** ISO-8601 datetime or null. */
  last_success_at: string | null;
  /** ISO-8601 datetime or null. */
  last_failure_at: string | null;
  /** Full error_message; frontend truncates for tooltip. */
  last_failure_error: string | null;
}

/**
 * Response shape from /api/v1/publishing/per-niche-platform-health.
 *
 * thresholds is server-supplied so the card doesn't hardcode them.
 * If we ever tune the thresholds, the card picks it up via the API
 * without a frontend rebuild.
 */
export interface PerNichePlatformHealthResponse {
  rows: PerNichePlatformHealthRow[];
  thresholds: {
    red_below_pct: number;
    yellow_below_pct: number;
  };
  window_days: number;
}

// ── Per-platform bandit divergence (PR AG, 2026-06-29) ──

/**
 * One row of the bandit-divergence matrix: one (niche, base_arm) pair
 * with its base posterior + per-platform variant posteriors + the deltas.
 *
 * Mirrors the rows produced by
 * ``server.core.bandit_platform_divergence_pg.fetch_per_platform_divergence``.
 *
 * Fields:
 *   * *_mean values are Thompson posterior means (alpha / (alpha+beta));
 *     null when no row exists in bandit_arms for that variant.
 *   * *_n_plays is the bandit observation count; 0 when no row exists.
 *   * ig_yt_delta is |instagram_mean - youtube_mean|. Null if either side
 *     missing; frontend renders "—". Star the row when ≥ thresholds
 *     .meaningful_delta_pct / 100.
 *   * ig_delta_from_base / yt_delta_from_base are SIGNED (positive when
 *     the variant outperforms the base on that platform).
 */
export interface BanditPlatformDivergenceRow {
  niche_id: string;
  base_arm_id: string;
  /** alpha / (alpha+beta) for the base arm. */
  base_mean: number | null;
  base_n_plays: number;
  /** alpha / (alpha+beta) for the per-platform variant (null when no row). */
  instagram_mean: number | null;
  instagram_n_plays: number;
  /** alpha / (alpha+beta) for the per-platform variant (null when no row). */
  youtube_mean: number | null;
  youtube_n_plays: number;
  /** Absolute |IG - YT|, [0, 1]. Null if either side missing. */
  ig_yt_delta: number | null;
  /** Signed IG - base, [-1, 1]. Null if either side missing. */
  ig_delta_from_base: number | null;
  /** Signed YT - base, [-1, 1]. Null if either side missing. */
  yt_delta_from_base: number | null;
}

/**
 * Response shape from /api/v1/learning/bandit-platform-divergence.
 *
 * thresholds is server-supplied so the card doesn't hardcode them.
 * If we ever tune (say, raise the meaningful delta to 7%), the card
 * picks it up via the API without a frontend rebuild.
 */
export interface BanditPlatformDivergenceResponse {
  rows: BanditPlatformDivergenceRow[];
  /** Canonical platform list (mirrors list_split_platforms()). */
  platforms: string[];
  thresholds: {
    /** Star rows where ig_yt_delta * 100 ≥ this value. */
    meaningful_delta_pct: number;
    /** Variants with n_plays < this render with "(cold)" badge. */
    cold_start_below_n: number;
  };
  /** Window descriptor — currently always "all_time" since bandit
   *  posteriors are cumulative. */
  window: string;
}

// ── Scheduling Pauses (operator emergency-stop, PR after #579) ──

/**
 * A single active niche_pauses row.
 *
 * Mirrors the genlab_core.scheduling.niche_pause.NichePause dataclass.
 * paused_until: null means indefinite — operator must explicitly
 * unpause. A future timestamp means auto-expiry; the sweeper
 * (PR #580) will GC expired rows.
 */
export interface NichePauseRecord {
  niche_id: string;
  /** ISO-8601 datetime, or null for indefinite pauses. */
  paused_until: string | null;
  /** Operator-supplied (or auto-pause-generated) audit reason. */
  reason: string;
  /** Username from session, or 'system:account_health_check' for
   *  auto-pauses. Null on pre-PR-577 rows that lacked the column. */
  paused_by: string | null;
  /** First-ever pause timestamp (preserved across re-pauses by the
   *  ON CONFLICT clause — repause does NOT reset this). */
  created_at: string | null;
}
