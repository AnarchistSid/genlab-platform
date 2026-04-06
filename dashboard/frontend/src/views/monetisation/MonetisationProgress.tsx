import { useState } from "react";
import { CheckCircle2, DollarSign, TrendingUp } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { useMonetisationProgress } from "@/hooks/use-monetisation";
import { useRevenueSummary } from "@/hooks/use-revenue";
import { getAllNiches } from "@/niches/registry";
import { ProgressBar } from "@/components/shared/progress-bar";
import { PageHeader } from "@/components/shared/page-header";
import { LoadingSkeleton } from "@/components/shared/loading-skeleton";
import { ErrorState } from "@/components/shared/error-state";
import { KpiCard } from "@/components/shared/kpi-card";
import { ChartCard } from "@/components/shared/chart-card";
import { SectionHeader } from "@/components/shared/section-header";
import { ChartTooltip } from "@/components/charts/chart-tooltip";
import { formatCompact } from "@/lib/format";
import { PLATFORM_HEX, PLATFORM_LABELS } from "@/lib/platforms";
import { analytics, revenue } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type {
  ClickTrend,
  MonetisationMetric,
  MonetisationPlatformProgress,
  MonetisationProgressData,
} from "@/api/types";

// ── Helpers ──────────────────────────────────────────────────

// Canonical monetisation thresholds per platform/metric
// Used to fill in missing target_value and build placeholder rows
const MONETISATION_THRESHOLDS: Record<string, { label: string; target: number }[]> = {
  instagram: [
    { label: "Followers", target: 10_000 },
  ],
  youtube: [
    { label: "Subscribers", target: 1_000 },
    { label: "Watch Hours (12mo)", target: 4_000 },
  ],
  facebook: [
    { label: "Page Likes", target: 5_000 },
  ],
};

/**
 * Estimate months to reach a threshold.
 * Uses delta_7d (weekly growth) extrapolated linearly.
 * Returns null when there's no meaningful growth signal.
 */
function estimateMonthsToThreshold(
  current: number | null,
  target: number | null,
  delta7d: number | null,
): number | null {
  if (current == null || target == null || current >= target) return null;
  if (delta7d == null || delta7d <= 0) return null;
  const remaining = target - current;
  const weeklyRate = delta7d;
  const weeksNeeded = remaining / weeklyRate;
  const months = weeksNeeded / 4.33; // avg weeks per month
  return Math.max(0.5, Math.round(months * 10) / 10);
}

function pctClass(pct: number | null): string {
  if (pct == null) return "text-error";
  if (pct >= 100) return "text-success";
  if (pct >= 50) return "text-warning";
  return "text-error";
}

function barClass(pct: number | null): string {
  if (pct == null) return "mp-bar-fill-low";
  if (pct >= 100) return "mp-bar-fill-high";
  if (pct >= 50) return "mp-bar-fill-mid";
  return "mp-bar-fill-low";
}

const METRIC_LABELS: Record<string, string> = {
  subscribers: "Subscribers",
  watch_hours_12mo: "Watch Hours (12mo)",
  "watch_hours_(12mo)": "Watch Hours (12mo)",
  shorts_views_90d: "Shorts Views (90d)",
  followers: "Followers",
  page_likes: "Page Likes",
  minutes_viewed_60d: "Minutes Viewed (60d)",
  dm_sends_7d: "DM Sends (7d)",
  media_count: "Total Posts",
  total_views: "Total Views",
  video_count: "Videos",
  fans: "Page Fans",
};

// ── Metric Row ──────────────────────────────────────────────

function MetricRow({
  metric,
  accentColor,
}: {
  metric: MonetisationMetric;
  accentColor?: string;
}) {
  const rawPct = metric.pct_complete;
  // API returns pct_complete as a ratio (0.0–1.0+), ProgressBar expects 0–100
  const pct = rawPct != null ? rawPct * 100 : null;
  const cappedPct = pct != null ? Math.min(pct, 100) : 0;

  // Pick bar color: use accent when available, else fallback to status colors
  const barColor =
    metric.is_threshold_met
      ? "var(--color-green)"
      : accentColor ||
        (pct == null
          ? "var(--color-red)"
          : pct >= 50
          ? "var(--color-amber)"
          : "var(--color-red)");

  const eta = estimateMonthsToThreshold(
    metric.current_value,
    metric.target_value,
    metric.delta_7d,
  );

  return (
    <div className="flex flex-col items-start gap-1">
      <div className="flex items-center gap-3 w-full">
        <span className="text-xs text-text-muted w-[140px] shrink-0">
          {METRIC_LABELS[metric.metric_name] || metric.metric_name}
        </span>
        <div className="flex-1 flex items-center gap-2">
          <div className="mp-bar-track">
            <ProgressBar value={cappedPct} color={barColor} height={6} animated />
          </div>
          {metric.is_threshold_met ? (
            <CheckCircle2 size={14} className="text-success shrink-0" />
          ) : (
            <span className={`text-xs font-semibold tabular-nums w-12 text-right shrink-0 ${pctClass(pct)}`}>
              {pct != null ? `${pct.toFixed(0)}%` : "\u2014"}
            </span>
          )}
        </div>
        <span className="text-xs text-text-muted w-[120px] text-right shrink-0 tabular-nums">
          {formatCompact(metric.current_value)}
          {metric.target_value != null && ` / ${formatCompact(metric.target_value)}`}
        </span>
      </div>
      {eta != null && (
        <span className="text-[10px] text-text-muted mt-0.5 pl-[140px]">
          <TrendingUp className="inline w-2.5 h-2.5 mr-0.5" />
          ~{eta < 1 ? "<1" : eta} month{eta !== 1 ? "s" : ""} at current rate
        </span>
      )}
    </div>
  );
}

// ── Platform Section ────────────────────────────────────────

function PlatformSection({
  platform,
  data,
}: {
  platform: string;
  data: MonetisationPlatformProgress;
}) {
  const platformColor = PLATFORM_HEX[platform];

  // Enrich metrics: ensure canonical thresholds are represented
  const canonicalThresholds = MONETISATION_THRESHOLDS[platform] ?? [];
  const enrichedMetrics = [...data.metrics];
  for (const threshold of canonicalThresholds) {
    const exists = enrichedMetrics.some(
      (m) =>
        (METRIC_LABELS[m.metric_name] || m.metric_name).toLowerCase() ===
        threshold.label.toLowerCase(),
    );
    if (!exists) {
      // Add a placeholder metric row for this threshold
      enrichedMetrics.push({
        metric_name: threshold.label.toLowerCase().replace(/\s+/g, "_"),
        current_value: null,
        target_value: threshold.target,
        pct_complete: null,
        delta_7d: null,
        days_to_threshold_est: null,
        is_threshold_met: false,
        data_source: "placeholder",
        as_of_date: null,
        error_log: null,
      });
    }
  }

  return (
    <div className="py-2 [&+&]:border-t [&+&]:border-border">
      <div className="flex items-center gap-2 text-xs font-semibold text-text-secondary uppercase tracking-wide mb-2">
        {platformColor && (
          <span
            className="inline-block size-2 rounded-full shrink-0"
            style={{ background: platformColor }}
          />
        )}
        {PLATFORM_LABELS[platform] || platform}
        {data.is_monetised && (
          <span className="text-xs font-medium px-2 py-px rounded-full bg-[color-mix(in_srgb,var(--color-green)_15%,transparent)] text-success ml-1">
            Monetised
          </span>
        )}
      </div>
      <div className="flex flex-col gap-2">
        {enrichedMetrics.map((m) => (
          <MetricRow key={m.metric_name} metric={m} accentColor={platformColor} />
        ))}
      </div>
    </div>
  );
}

// ── Projected Timeline ───────────────────────────────────────

function ProjectedTimeline({
  platforms,
  displayName,
}: {
  platforms: Record<string, MonetisationPlatformProgress>;
  displayName: string;
}) {
  // Find the most impactful unmet threshold with growth data
  const projections: Array<{ label: string; months: number }> = [];

  for (const [platform, data] of Object.entries(platforms)) {
    for (const metric of data.metrics) {
      if (metric.is_threshold_met) continue;
      const months = estimateMonthsToThreshold(
        metric.current_value,
        metric.target_value,
        metric.delta_7d,
      );
      if (months == null) continue;
      const label = `${formatCompact(metric.target_value)} ${METRIC_LABELS[metric.metric_name] || metric.metric_name} (${PLATFORM_LABELS[platform] || platform})`;
      projections.push({ label, months });
    }
  }

  if (projections.length === 0) return null;

  // Sort by closest threshold first
  projections.sort((a, b) => a.months - b.months);
  const closest = projections[0];

  return (
    <div className="flex items-center gap-1.5 px-4 py-2 border-t border-border bg-[var(--surface-0)] text-xs text-text-muted">
      <TrendingUp className="w-3 h-3 shrink-0 text-success" />
      <span>
        At current rate, <strong className="text-text-secondary">{displayName}</strong>{" "}
        reaches {closest.label} in{" "}
        <strong className="text-success">
          ~{closest.months < 1 ? "<1" : closest.months} month{closest.months !== 1 ? "s" : ""}
        </strong>
      </span>
    </div>
  );
}

// ── Niche Card ──────────────────────────────────────────────

// All canonical platforms each niche should show
const ALL_PLATFORMS = ["instagram", "youtube", "facebook"];

function NicheCard({
  nicheId,
  platforms,
}: {
  nicheId: string;
  platforms: Record<string, MonetisationPlatformProgress>;
}) {
  const allNiches = getAllNiches();
  const niche = allNiches.find((n) => n.id === nicheId);
  const displayName = niche?.displayName || nicheId;
  const accent = niche?.accentHex || "#888";

  // Ensure all 3 platforms are always shown — fill in empty ones
  const fullPlatforms: Record<string, MonetisationPlatformProgress> = {};
  for (const p of ALL_PLATFORMS) {
    fullPlatforms[p] = platforms[p] ?? { metrics: [], is_monetised: false };
  }

  const allMetrics = Object.values(fullPlatforms).flatMap((p) => p.metrics);
  const withTarget = allMetrics.filter((m) => m.target_value != null);
  const metCount = withTarget.filter((m) => m.is_threshold_met).length;
  const allMet = withTarget.length > 0 && metCount === withTarget.length;

  return (
    <div
      className="bg-bg-surface border border-border rounded-lg overflow-hidden"
      style={{ borderTopColor: accent, borderTopWidth: 3, borderTopStyle: "solid" }}
    >
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
        <span className="size-2.5 rounded-full shrink-0" style={{ backgroundColor: accent }} />
        <span className="text-sm font-semibold text-text-primary flex-1">{displayName}</span>
        <span
          className={`text-xs font-medium px-2 py-px rounded-full ${
            allMet
              ? "bg-[color-mix(in_srgb,var(--color-green)_15%,transparent)] text-success"
              : "bg-[color-mix(in_srgb,var(--color-amber)_15%,transparent)] text-warning"
          }`}
        >
          {allMet ? "All Thresholds Met" : `${metCount}/${withTarget.length} met`}
        </span>
      </div>
      <div className="px-4 py-2 pb-3">
        {Object.entries(fullPlatforms).map(([platform, data]) => (
          <PlatformSection key={platform} platform={platform} data={data} />
        ))}
      </div>
      <ProjectedTimeline platforms={fullPlatforms} displayName={displayName} />
    </div>
  );
}

// ── Affiliate Summary ───────────────────────────────────────

function AffiliateSummary() {
  const { data: monetData } = useQuery({
    queryKey: ["analytics", "monetization"],
    queryFn: () => analytics.monetization(),
    staleTime: 5 * 60_000,
  });
  const { data: revData } = useQuery({
    queryKey: ["revenue", "summary"],
    queryFn: () => revenue.summary(),
    staleTime: 5 * 60_000,
  });

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const raw = monetData as any;
  const programs = raw?.data?.active_programs ?? raw?.active_programs ?? [];
  const totalPublished = raw?.data?.total_published ?? raw?.total_published ?? 0;
  const affiliateCount = raw?.data?.posts_with_affiliate_links ?? raw?.posts_with_affiliate_links ?? 0;
  const catalogNetworks: Record<string, number> = raw?.data?.catalog_networks ?? raw?.catalog_networks ?? {};

  const clicks = revData?.clicks ?? (revData as any)?.data?.clicks ?? { today: 0, last_7d: 0, last_30d: 0 };
  const estRevenue = revData?.estimated_revenue_inr_30d ?? (revData as any)?.data?.estimated_revenue_inr_30d ?? 0;

  return (
    <div
      className="bg-bg-surface border border-border rounded-lg overflow-hidden"
      style={{ borderTopColor: "#22c55e", borderTopWidth: 3, borderTopStyle: "solid" }}
    >
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
        <DollarSign size={14} className="text-success" />
        <span className="text-sm font-semibold text-text-primary flex-1">Affiliate Revenue</span>
        <span className="text-xs font-medium px-2 py-px rounded-full bg-[color-mix(in_srgb,var(--color-amber)_15%,transparent)] text-warning">
          {programs.length} network{programs.length !== 1 ? "s" : ""}
        </span>
      </div>

      {/* KPIs row */}
      <div className="grid grid-cols-4 gap-2 px-3 py-2">
        {[
          { value: affiliateCount, label: "Posts w/ Affiliate" },
          { value: clicks.last_30d, label: "Clicks (30d)" },
          { value: totalPublished ? `${((affiliateCount / totalPublished) * 100).toFixed(0)}%` : "\u2014", label: "Affiliate Rate" },
          { value: `\u20B9${estRevenue.toFixed(0)}`, label: "Est. Revenue (30d)", color: "text-success" },
        ].map((kpi, i) => (
          <div key={i} className="text-center py-2 px-1 bg-[var(--surface-0)] rounded-lg">
            <div className={`text-lg font-bold ${kpi.color ?? "text-text-primary"}`}>{kpi.value}</div>
            <div className="text-[10px] text-text-muted">{kpi.label}</div>
          </div>
        ))}
      </div>

      {/* Network breakdown */}
      {Object.keys(catalogNetworks).length > 0 && (
        <div className="px-3 pt-1 pb-2">
          <div className="text-[11px] text-text-muted mb-1">Network Breakdown</div>
          {Object.entries(catalogNetworks).map(([net, count]) => (
            <div key={net} className="flex justify-between py-0.5 text-xs">
              <span className="text-text-secondary">{net.replace("_", " ")}</span>
              <span className="font-mono text-text-primary">{count} posts</span>
            </div>
          ))}
        </div>
      )}

      {/* Programs list */}
      {programs.length > 0 && (
        <div className="px-3 pt-1 pb-3 border-t border-border">
          <div className="text-[11px] text-text-muted mb-1 mt-1">Active Programs</div>
          {programs.map((p: { name: string; slug: string; commission: string }) => (
            <div key={p.slug} className="flex justify-between py-0.5 text-xs">
              <span className="text-text-secondary">{p.name}</span>
              {p.commission && (
                <span className="text-[10px] text-success bg-success/10 px-1.5 py-px rounded">
                  {p.commission}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Revenue Section ──────────────────────────────────────────

function RevenueSection() {
  const { data: revData } = useRevenueSummary();
  const { data: trendsData } = useQuery<ClickTrend[]>({
    queryKey: queryKeys.revenue.clickTrends(),
    queryFn: () => revenue.clickTrends(),
    staleTime: 300_000,
  });

  const clicks = revData?.clicks ?? (revData as any)?.data?.clicks;
  const byProduct: Record<string, number> = revData?.by_product ?? (revData as any)?.data?.by_product ?? {};
  const byNetwork: Record<string, number> = revData?.by_network ?? (revData as any)?.data?.by_network ?? {};
  const estRevenue = revData?.estimated_revenue_inr_30d ?? (revData as any)?.data?.estimated_revenue_inr_30d ?? 0;
  const trends = trendsData ?? [];

  // Sort products by clicks descending, take top 10
  const topProducts = Object.entries(byProduct)
    .sort(([, a], [, b]) => (b as number) - (a as number))
    .slice(0, 10);
  const maxClicks = Math.max(...topProducts.map(([, c]) => c as number), 1);

  return (
    <div className="space-y-6">
      <SectionHeader title="Affiliate Revenue" />

      {/* Revenue KPIs */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <KpiCard
          label="Clicks Today"
          value={clicks?.today ?? 0}
          formatter={(n) => String(n)}
          subtitle="affiliate link clicks"
        />
        <KpiCard
          label="Clicks 7d"
          value={clicks?.last_7d ?? 0}
          formatter={(n) => String(n)}
          subtitle="last 7 days"
        />
        <KpiCard
          label="Clicks 30d"
          value={clicks?.last_30d ?? 0}
          formatter={(n) => String(n)}
          subtitle="last 30 days"
        />
        <KpiCard
          label="Est. Revenue"
          value={estRevenue}
          formatter={(n) => `\u20B9${Math.round(n).toLocaleString()}`}
          subtitle="estimated 30-day"
          accentColor="var(--color-green)"
          variant="hero"
        />
      </div>

      {/* Click Trends Chart */}
      {trends.length > 0 && (
        <ChartCard title="Click Trends (14d)">
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={trends}>
              <XAxis
                dataKey="date"
                tickFormatter={(d) => new Date(d + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                tick={{ fontSize: 10, fill: "var(--text-muted)" }}
                axisLine={{ stroke: "var(--border)" }}
                tickLine={false}
              />
              <YAxis
                tick={{ fontSize: 10, fill: "var(--text-muted)" }}
                axisLine={false}
                tickLine={false}
                width={30}
              />
              <Tooltip content={<ChartTooltip formatter={(v) => `${v} clicks`} />} />
              <Bar dataKey="clicks" fill="var(--color-green)" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      )}

      {/* Top Products + Network Breakdown side by side */}
      <div className="two-col-layout">
        {/* Top Products */}
        <ChartCard title="Top Products">
          <div className="space-y-2">
            {topProducts.map(([slug, count]) => (
              <div key={slug} className="flex items-center gap-3">
                <span className="text-xs text-text-primary flex-1 truncate capitalize">
                  {slug.replace(/-/g, " ")}
                </span>
                <span className="text-xs font-mono text-text-muted tabular-nums w-8 text-right">{count}</span>
                <div className="w-20">
                  <ProgressBar value={(count / maxClicks) * 100} color="var(--color-green)" height={4} animated />
                </div>
              </div>
            ))}
            {topProducts.length === 0 && (
              <p className="text-sm text-text-muted py-4 text-center">No click data yet</p>
            )}
          </div>
        </ChartCard>

        {/* Network Breakdown */}
        <ChartCard title="Network Breakdown">
          <div className="space-y-3">
            {Object.entries(byNetwork).sort(([, a], [, b]) => b - a).map(([network, count]) => {
              const totalClicks = Object.values(byNetwork).reduce((s, v) => s + v, 0);
              const pct = totalClicks > 0 ? Math.round((count / totalClicks) * 100) : 0;
              return (
                <div key={network} className="space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-text-primary capitalize">{network.replace(/_/g, " ")}</span>
                    <span className="text-xs font-mono text-text-muted tabular-nums">{count} ({pct}%)</span>
                  </div>
                  <ProgressBar value={pct} color="var(--color-blue)" height={4} animated />
                </div>
              );
            })}
            {Object.keys(byNetwork).length === 0 && (
              <p className="text-sm text-text-muted py-4 text-center">No network data yet</p>
            )}
          </div>
        </ChartCard>
      </div>
    </div>
  );
}

// ── Full Page View ──────────────────────────────────────────

// All 5 niche IDs — always show all of them
const ALL_NICHE_IDS = ["ai_creators", "gaming", "sports", "movies", "anime"];

export default function MonetisationProgressView() {
  const { data: resp, isLoading, isError, refetch } = useMonetisationProgress();
  // Handle both unwrapped (direct MonetisationProgressData) and wrapped ({data: ...}) formats
  const progressData = (resp && typeof resp === "object" && "data" in resp) ? (resp as any).data : resp;
  const [triggering, setTriggering] = useState(false);

  if (isError && !progressData) {
    return (
      <div className="max-w-[1000px] mx-auto">
        <PageHeader
          title="Monetisation Progress"
          subtitle="Platform threshold tracking across all niches"
        />
        <ErrorState
          title="Unable to load monetisation data"
          message="Could not connect to the API."
          onRetry={() => void refetch()}
        />
      </div>
    );
  }

  const handleTrigger = async () => {
    setTriggering(true);
    try {
      const token = await fetch("/api/csrf-token").then((r) => r.json()).then((d) => d.csrf_token);
      await fetch("/api/v1/monetisation/trigger", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
      });
      // Wait a few seconds for background tracker to write data, then refetch
      setTimeout(() => {
        void refetch();
        setTriggering(false);
      }, 8000);
    } catch {
      setTriggering(false);
    }
  };

  return (
    <div className="max-w-[1000px] mx-auto">
      <PageHeader
        title="Monetisation Progress"
        subtitle="Platform threshold tracking across all niches"
      />

      {isLoading ? (
        <LoadingSkeleton variant="card-list" rows={3} />
      ) : (
        <>
          {/* Affiliate Revenue Summary */}
          <AffiliateSummary />

          {/* Always show all 5 niches, using API data where available */}
          <div className="flex flex-col gap-4 mt-4">
            {ALL_NICHE_IDS.map((nicheId) => (
              <NicheCard
                key={nicheId}
                nicheId={nicheId}
                platforms={progressData?.[nicheId] ?? {}}
              />
            ))}
          </div>

          {/* Affiliate Revenue Tracking */}
          <div className="mt-6">
            <RevenueSection />
          </div>

          {/* Trigger button + note when no data yet */}
          {(!progressData || Object.keys(progressData).length === 0) && (
            <div className="text-center py-6 px-4 max-w-[480px] mx-auto">
              <p className="text-text-muted mb-2 text-[0.8125rem]">
                No live metrics yet. The tracker needs per-niche platform API credentials.
              </p>
              <p className="text-text-disabled text-xs mb-4 leading-relaxed">
                Required: <code>*_YOUTUBE_REFRESH_TOKEN</code>, <code>*_META_ACCESS_TOKEN</code>, <code>*_META_IG_USER_ID</code>
              </p>
              <button
                onClick={handleTrigger}
                disabled={triggering}
                className={`px-5 py-2 rounded-lg border border-border text-[0.8125rem] font-medium ${
                  triggering
                    ? "bg-bg-elevated text-text-muted cursor-wait"
                    : "bg-[var(--niche-current)] text-white cursor-pointer"
                }`}
              >
                {triggering ? "Collecting metrics..." : "Run Tracker Now"}
              </button>
              {triggering && (
                <p className="text-text-disabled text-xs mt-2">
                  Fetching from YouTube, Facebook, and Instagram APIs...
                </p>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── Mission Control Widget ──────────────────────────────────

export function MonetisationWidget({
  data,
  index,
}: {
  data: MonetisationProgressData | undefined;
  index: number;
}) {
  if (!data || Object.keys(data).length === 0) {
    return (
      <div className="bento-card area-monetisation" style={{ animationDelay: `${index * 60}ms` }}>
        <h3 className="card-title">Monetisation</h3>
        <p className="card-body text-text-muted">
          Awaiting first tracker run
        </p>
      </div>
    );
  }

  const allNiches = getAllNiches();

  // Compute per-niche average pct
  const nicheSummaries = Object.entries(data).map(([nicheId, platforms]) => {
    const niche = allNiches.find((n) => n.id === nicheId);
    const allMetrics = Object.values(platforms).flatMap((p) => p.metrics);
    const withTarget = allMetrics.filter((m) => m.target_value != null && m.pct_complete != null);
    // pct_complete is a ratio (0.0–1.0+), convert to percentage for display
    const avgPct =
      withTarget.length > 0
        ? withTarget.reduce((sum, m) => sum + ((m.pct_complete ?? 0) * 100), 0) / withTarget.length
        : 0;
    return {
      nicheId,
      displayName: niche?.displayName || nicheId,
      accent: niche?.accentHex || "#888",
      avgPct: Math.round(avgPct),
    };
  });

  return (
    <div className="bento-card area-monetisation" style={{ animationDelay: `${index * 60}ms` }}>
      <h3 className="card-title">Monetisation</h3>
      <div className="mp-widget-grid">
        {nicheSummaries.map((ns) => (
          <div key={ns.nicheId} className="mp-widget-row">
            <span className="mp-widget-niche">{ns.displayName}</span>
            <div className="mp-widget-bar">
              <div
                className={`mp-widget-fill ${barClass(ns.avgPct)}`}
                style={{
                  width: `${Math.min(ns.avgPct, 100)}%`,
                  backgroundColor: ns.accent,
                }}
              />
            </div>
            <span className={`mp-widget-pct ${pctClass(ns.avgPct)}`}>
              {ns.avgPct}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
