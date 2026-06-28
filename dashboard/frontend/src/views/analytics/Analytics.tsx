import { useState, useMemo } from "react";
import {
  Eye,
  Heart,
  Send,
  CheckCircle2,
  BarChart3,
  MessageCircle,
  TrendingUp,
} from "lucide-react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import { useQuery } from "@tanstack/react-query";
import { useAnalyticsOverview } from "@/hooks/use-analytics-overview";
import { getActiveNiches, getNicheInfo, NICHE_COLORS, NICHE_SHORT_LABELS } from "@/niches/registry";
import { analytics } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import { formatCompact, formatRelativeTime } from "@/lib/format";
import type {
  TimeSeriesPoint,
  PlatformMetrics,
  FunnelData,
  TopPerformer,
  TopPost,
  CrossNicheAnalytics,
} from "@/api/types";
import { PlatformIcon } from "@/components/shared/PlatformIcon";
import { PLATFORM_COLORS, PLATFORM_LABELS } from "@/lib/platforms";
import { KpiCard } from "@/components/shared/kpi-card";
import { ChartTooltip } from "@/components/charts/chart-tooltip";
import { ChartCard } from "@/components/shared/chart-card";
import { PageHeader } from "@/components/shared/page-header";
import { ErrorState } from "@/components/shared/error-state";
import { EmptyState } from "@/components/shared/empty-state";
import { AlertBanner } from "@/components/shared/AlertBanner";
import { ProgressBar } from "@/components/shared/progress-bar";
import { LoadingSkeleton } from "@/components/shared/loading-skeleton";

type WindowOption = "7d" | "14d" | "30d";
type TabOption = "overview" | "top-posts" | "compare";

function formatDateShort(dateStr: string): string {
  const d = new Date(dateStr + "T00:00:00");
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

// ── Reach Over Time ─────────────────────────────────

function ReachChart({
  data,
  nicheId,
}: {
  data: TimeSeriesPoint[];
  nicheId: string;
}) {
  if (!data.length) {
    return (
      <EmptyState
        icon={BarChart3}
        title="No published posts in this period"
        description="Run the pipeline to generate content"
      />
    );
  }

  const formattedData = data.map((d) => ({
    ...d,
    label: formatDateShort(d.date),
  }));

  // Build gradient IDs for each niche we'll render
  const gradientEntries = nicheId === "all"
    ? Object.entries(NICHE_COLORS)
    : [[nicheId, NICHE_COLORS[nicheId] ?? "#71717a"]] as [string, string][];

  return (
    <ResponsiveContainer width="100%" height={240}>
      <AreaChart data={formattedData}>
        <defs>
          {gradientEntries.map(([nId, color]) => (
            <linearGradient key={nId} id={`grad-${nId}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={color} stopOpacity={0.25} />
              <stop offset="95%" stopColor={color} stopOpacity={0.02} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid
          strokeDasharray="3 3"
          stroke="var(--border)"
          vertical={false}
        />
        <XAxis
          dataKey="label"
          tick={{ fontSize: 10, fill: "var(--text-muted)", fontFamily: "var(--font-mono)" }}
          axisLine={{ stroke: "var(--border)" }}
          tickLine={false}
        />
        <YAxis
          tickFormatter={formatCompact}
          tick={{ fontSize: 10, fill: "var(--text-muted)", fontFamily: "var(--font-mono)" }}
          axisLine={false}
          tickLine={false}
          width={40}
        />
        <Tooltip content={<ChartTooltip />} />
        {nicheId === "all" ? (
          Object.entries(NICHE_COLORS).map(([nId, color]) => (
            <Area
              key={nId}
              type="monotone"
              dataKey={`${nId}_reach`}
              name={nId.replace(/_/g, " ")}
              fill={`url(#grad-${nId})`}
              stroke={color}
              strokeWidth={2}
            />
          ))
        ) : (
          <Area
            type="monotone"
            dataKey={`${nicheId}_reach`}
            name={nicheId.replace(/_/g, " ")}
            fill={`url(#grad-${nicheId})`}
            stroke={NICHE_COLORS[nicheId] ?? "var(--text-muted)"}
            strokeWidth={2}
          />
        )}
        <Legend
          wrapperStyle={{ fontSize: 11, color: "var(--text-muted)" }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

// ── Platform Breakdown ──────────────────────────────

function PlatformBreakdown({
  data,
}: {
  data: Record<string, PlatformMetrics>;
}) {
  const entries = Object.entries(data);
  const maxReach = Math.max(...entries.map(([, v]) => v.reach), 1);

  return (
    <div>
      {entries.map(([platform, metrics], idx) => {
        const noMetrics = metrics.data_status === "no_metrics";
        const noData = metrics.data_status === "no_data";

        return (
          <div
            className={`flex items-center gap-2 py-2 ${idx > 0 ? "border-t border-border/50" : ""}`}
            key={platform}
          >
            <div className="w-20 shrink-0">
              <div className="text-sm font-medium text-text-primary">
                {PLATFORM_LABELS[platform] ?? platform}
              </div>
              <div className="text-[9px] text-text-muted">{metrics.metric_label}</div>
            </div>
            {noMetrics ? (
              <>
                <div className="flex-1 h-2 bg-bg-elevated rounded overflow-hidden opacity-30">
                  <div className="h-full rounded" style={{ width: 0 }} />
                </div>
                <span className="w-14 text-right font-mono text-[10px] text-warning shrink-0">No API data</span>
                <span className="w-[50px] text-right text-[10px] text-text-muted shrink-0">{metrics.posts} posts</span>
              </>
            ) : noData ? (
              <>
                <div className="flex-1 h-2 bg-bg-elevated rounded overflow-hidden opacity-15">
                  <div className="h-full rounded" style={{ width: 0 }} />
                </div>
                <span className="w-14 text-right font-mono text-[10px] text-warning shrink-0">{"\u2014"}</span>
                <span className="w-[50px] text-right text-[10px] text-text-muted shrink-0">0 posts</span>
              </>
            ) : (
              <>
                <div className="flex-1 h-2 bg-bg-elevated rounded overflow-hidden">
                  <div
                    className="h-full rounded transition-[width] duration-500 ease-out"
                    style={{
                      width: `${(metrics.reach / maxReach) * 100}%`,
                      background: PLATFORM_COLORS[platform] ?? "var(--text-muted)",
                    }}
                  />
                </div>
                <span className="w-14 text-right font-mono text-xs text-text-secondary shrink-0">{formatCompact(metrics.reach)}</span>
                <span className="w-[50px] text-right text-[10px] text-text-muted shrink-0">{metrics.posts} posts</span>
              </>
            )}
          </div>
        );
      })}
      {entries.length === 0 && (
        <div className="flex flex-col items-center justify-center gap-2 p-6 text-text-muted text-sm">
          No platform data yet
        </div>
      )}
    </div>
  );
}

// ── Content Funnel ──────────────────────────────────

function ContentFunnel({ data }: { data: FunnelData }) {
  const stages: Array<{ key: keyof FunnelData; label: string; color: string }> = [
    { key: "fetched", label: "Fetched", color: "var(--text-muted)" },
    { key: "filtered", label: "Filtered", color: "var(--color-blue)" },
    { key: "written", label: "Written", color: "var(--color-purple)" },
    { key: "rendered", label: "Rendered", color: "var(--color-amber)" },
    { key: "published", label: "Published", color: "var(--color-green)" },
  ];
  const maxCount = Math.max(data.fetched, 1);
  const efficiency =
    data.fetched > 0
      ? Math.round((data.published / data.fetched) * 100)
      : 0;

  return (
    <div>
      {stages.map(({ key, label, color }) => (
        <div className="flex items-center gap-2 py-1.5" key={key}>
          <span className="w-[72px] text-xs font-medium text-text-secondary shrink-0">{label}</span>
          <div className="flex-1 h-2.5 bg-bg-elevated rounded-[5px] overflow-hidden">
            <div
              className="h-full rounded-[5px] transition-[width] duration-500 ease-out"
              style={{
                width: `${(data[key] / maxCount) * 100}%`,
                background: color,
              }}
            />
          </div>
          <span className="w-8 text-right font-mono text-xs text-text-muted shrink-0">{data[key]}</span>
        </div>
      ))}
      <div className="text-xs text-green-500 font-medium mt-2 text-right">
        {efficiency}% pipeline efficiency
      </div>
    </div>
  );
}

// ── Top Performers Table ────────────────────────────

function TopPerformersTable({ performers }: { performers: TopPerformer[] }) {
  if (!performers.length) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 p-6 text-text-muted text-sm">
        No published posts yet
      </div>
    );
  }

  return (
    <table className="data-table">
      <thead>
        <tr>
          <th className="w-8">#</th>
          <th>Title / Hook</th>
          <th>Niche</th>
          <th>Platform</th>
          <th className="text-right">Reach</th>
          <th className="text-right">Eng%</th>
          <th className="text-right">Published</th>
        </tr>
      </thead>
      <tbody>
        {performers.map((p, i) => (
          <tr key={p.id}>
            <td className="text-text-muted">{i + 1}</td>
            <td>
              <div className="text-sm text-text-primary max-w-[260px] overflow-hidden text-ellipsis whitespace-nowrap">
                {p.hook_text || p.title || "\u2014"}
              </div>
              {p.title && p.title !== p.hook_text && (
                <div className="text-[10px] text-text-muted max-w-[260px] overflow-hidden text-ellipsis whitespace-nowrap">
                  {p.title.slice(0, 40)}
                </div>
              )}
            </td>
            <td>
              <span className="inline-flex items-center gap-[5px]">
                <span
                  className="size-1.5 rounded-full"
                  style={{
                    background: NICHE_COLORS[p.niche_id] ?? "var(--text-muted)",
                  }}
                />
                <span className="text-xs">
                  {{"ai_creators": "AI", "gaming": "Gaming", "sports": "Sports", "movies": "Movies", "anime": "Anime"}[p.niche_id] ?? p.niche_id}
                </span>
              </span>
            </td>
            <td>
              <span className="inline-flex items-center gap-1 text-xs">
                <span
                  className="size-1 rounded-full"
                  style={{
                    background: PLATFORM_COLORS[p.platform] ?? "var(--text-muted)",
                  }}
                />
                {PLATFORM_LABELS[p.platform] ?? p.platform}
              </span>
            </td>
            <td className="mono text-right">
              {formatCompact(p.total_reach)}
            </td>
            <td className="mono text-right">
              {p.engagement_rate != null
                ? `${(p.engagement_rate * 100).toFixed(1)}%`
                : "\u2014"}
            </td>
            <td className="mono text-right">
              {p.published_at ? formatRelativeTime(p.published_at) : "\u2014"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ── Top Posts Table ───────────────────────────────────

const NICHE_LABELS = NICHE_SHORT_LABELS;

function TopPostsTable({ posts }: { posts: TopPost[] }) {
  if (!posts.length) {
    return (
      <EmptyState
        icon={BarChart3}
        title="No engagement data yet"
        description="Posts appear here once metrics are collected"
      />
    );
  }

  return (
    <table className="data-table">
      <thead>
        <tr>
          <th className="w-8">#</th>
          <th>Niche</th>
          <th>Platform</th>
          <th className="text-right">Likes</th>
          <th className="text-right">Comments</th>
          <th className="text-right">Reach</th>
          <th className="text-right">Collected</th>
        </tr>
      </thead>
      <tbody>
        {posts.map((p, i) => (
          <tr key={`${p.post_id}-${p.platform}`}>
            <td className="text-text-muted font-mono text-xs">
              {i + 1}
            </td>
            <td>
              <span className="inline-flex items-center gap-[5px]">
                <span
                  className="size-[7px] rounded-full shrink-0"
                  style={{
                    background: NICHE_COLORS[p.niche_id] ?? "var(--text-muted)",
                  }}
                />
                <span className="text-xs">
                  {NICHE_LABELS[p.niche_id] ?? p.niche_id}
                </span>
              </span>
            </td>
            <td>
              <span className="inline-flex items-center gap-[5px]">
                <PlatformIcon platform={p.platform} size={13} />
                <span className="text-xs text-text-muted">
                  {PLATFORM_LABELS[p.platform] ?? p.platform}
                </span>
              </span>
            </td>
            <td className="mono text-right">
              {formatCompact(p.likes)}
            </td>
            <td className="mono text-right">
              {p.comments}
            </td>
            <td className="mono text-right">
              {formatCompact(p.reach)}
            </td>
            <td className="mono text-right text-text-muted">
              {p.collected_at ? formatRelativeTime(p.collected_at) : "\u2014"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ── Cross-Niche Comparison ──────────────────────────

function CrossNicheComparison() {
  const { data, isLoading } = useQuery<CrossNicheAnalytics>({
    queryKey: queryKeys.analytics.crossNiche(),
    queryFn: () => analytics.crossNiche(),
    staleTime: 300_000,
  });

  if (isLoading) return <LoadingSkeleton variant="table" rows={5} />;
  if (!data || Object.keys(data).length === 0) {
    return <EmptyState icon={BarChart3} title="No comparison data available" />;
  }

  const niches = Object.entries(data).sort((a, b) => (b[1].total_reach ?? 0) - (a[1].total_reach ?? 0));
  const maxReach = Math.max(...niches.map(([, d]) => d.total_reach ?? 0), 1);

  return (
    <ChartCard title="Cross-Niche Performance">
      <div className="overflow-x-auto">
        <table className="data-table w-full">
          <thead>
            <tr>
              <th>Channel</th>
              <th className="text-right">Reach</th>
              <th className="text-right">Likes</th>
              <th className="text-right">Posts</th>
              <th className="text-right">Pub Rate</th>
              <th className="text-right">Clicks</th>
              <th style={{ width: "25%" }}>Reach Distribution</th>
            </tr>
          </thead>
          <tbody>
            {niches.map(([nicheId, d]) => {
              const info = getNicheInfo(nicheId);
              return (
                <tr key={nicheId}>
                  <td>
                    <span className="inline-flex items-center gap-2">
                      <span className="size-2 rounded-full shrink-0" style={{ backgroundColor: info.hex }} />
                      <span className="font-medium">{info.label}</span>
                    </span>
                  </td>
                  <td className="text-right font-mono text-xs tabular-nums">{formatCompact(d.total_reach)}</td>
                  <td className="text-right font-mono text-xs tabular-nums">{formatCompact(d.total_likes)}</td>
                  <td className="text-right font-mono text-xs tabular-nums">{d.analytics_records}</td>
                  <td className="text-right font-mono text-xs tabular-nums">{d.publish_rate ?? 0}%</td>
                  <td className="text-right font-mono text-xs tabular-nums">{d.affiliate_clicks_30d ?? 0}</td>
                  <td>
                    <ProgressBar
                      value={((d.total_reach ?? 0) / maxReach) * 100}
                      color={info.hex}
                      height={6}
                      animated
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </ChartCard>
  );
}

// ── Loading Skeleton ────────────────────────────────

function AnalyticsSkeleton() {
  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div className="shimmer w-[140px] h-7" />
        <div className="flex gap-2">
          <div className="shimmer w-[120px] h-8" />
          <div className="shimmer w-[100px] h-8" />
        </div>
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
        {Array.from({ length: 4 }).map((_, i) => (
          <div className="shimmer h-[100px]" key={i} />
        ))}
      </div>
      <div className="shimmer h-[280px] mb-6" />
      <div className="two-col-layout">
        <div className="shimmer h-[200px]" />
        <div className="shimmer h-[200px]" />
      </div>
    </div>
  );
}

// ── Main View ───────────────────────────────────────

export default function Analytics() {
  const [nicheId, setNicheId] = useState("all");
  const [timeWindow, setTimeWindow] = useState<WindowOption>("7d");
  const [activeTab, setActiveTab] = useState<TabOption>("overview");

  const { data, isLoading, error, isEstimated } = useAnalyticsOverview(nicheId, timeWindow);
  const activeNiches = useMemo(() => getActiveNiches(), []);

  // Top Posts query — only fetch when the tab is active to avoid unnecessary requests
  const topPostsQuery = useQuery<TopPost[]>({
    queryKey: ["analytics", "top-posts"],
    queryFn: () => analytics.topPosts(),
    staleTime: 120_000,
    enabled: activeTab === "top-posts",
  });

  // Compute aggregate KPI totals from the top-posts data when available
  const topPosts: TopPost[] = useMemo(() => {
    const raw = topPostsQuery.data;
    if (!raw) return [];
    return Array.isArray(raw) ? raw : [];
  }, [topPostsQuery.data]);

  // Sorted top-20 by likes
  const top20Posts = useMemo(
    () => [...topPosts].sort((a, b) => b.likes - a.likes).slice(0, 20),
    [topPosts],
  );

  // Hero KPI values: prefer summary (from overview API) over topPosts aggregation
  const heroTotalLikes = data?.summary?.total_likes ?? topPosts.reduce((acc, p) => acc + (p.likes ?? 0), 0);
  const heroTotalReach = data?.summary?.total_reach ?? topPosts.reduce((acc, p) => acc + (p.reach ?? 0), 0);
  const heroTotalComments = data?.summary?.total_comments ?? topPosts.reduce((acc, p) => acc + (p.comments ?? 0), 0);
  const heroAvgEngRate = data?.summary?.avg_engagement_rate ?? (() => {
    const postsWithReach = topPosts.filter((p) => p.reach > 0);
    if (!postsWithReach.length) return 0;
    const totalEngagement = postsWithReach.reduce((acc, p) => acc + (p.likes + p.comments), 0);
    const totalReach = postsWithReach.reduce((acc, p) => acc + p.reach, 0);
    return totalReach > 0 ? (totalEngagement / totalReach) * 100 : 0;
  })();

  const windowLabel = { "7d": "7", "14d": "14", "30d": "30" }[timeWindow];

  if (isLoading) return <AnalyticsSkeleton />;

  if (error) {
    return (
      <ErrorState
        title="Could not connect to the API"
        onRetry={() => globalThis.location.reload()}
      />
    );
  }

  const summary = data?.summary;
  const byPlatform = data?.by_platform ?? {};
  const timeSeries = data?.time_series ?? [];
  const funnel = data?.funnel ?? { fetched: 0, filtered: 0, written: 0, rendered: 0, published: 0 };
  const topPerformers = data?.top_performers ?? [];

  return (
    <div>
      {/* Header */}
      <PageHeader
        title="Analytics"
        actions={
          activeTab === "overview" ? (
            <div className="flex items-center gap-2">
              <select
                className="appearance-none bg-bg-elevated border border-border rounded-lg text-text-secondary text-sm px-2.5 py-1.5 pr-7 cursor-pointer"
                value={nicheId}
                onChange={(e) => setNicheId(e.target.value)}
              >
                <option value="all">All Niches</option>
                {activeNiches.map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.displayName}
                  </option>
                ))}
              </select>
              <select
                className="appearance-none bg-bg-elevated border border-border rounded-lg text-text-secondary text-sm px-2.5 py-1.5 pr-7 cursor-pointer"
                value={timeWindow}
                onChange={(e) => setTimeWindow(e.target.value as WindowOption)}
              >
                <option value="7d">7 days</option>
                <option value="14d">14 days</option>
                <option value="30d">30 days</option>
              </select>
              <button
                className="bg-bg-elevated border border-border rounded-lg text-text-secondary text-xs px-2.5 py-1.5 cursor-pointer hover:bg-bg-raised transition-colors"
                onClick={() => {
                  import("@/lib/export").then(({ exportToCSV }) => {
                    // ``TopPerformer`` declares every key below; the
                    // earlier ``as any`` casts were unneeded.
                    exportToCSV(
                      topPerformers,
                      [
                        { key: "hook_text", header: "Hook" },
                        { key: "niche_id", header: "Niche" },
                        { key: "platform", header: "Platform" },
                        { key: "total_reach", header: "Reach" },
                        { key: "engagement_rate", header: "Eng %" },
                        { key: "published_at", header: "Published" },
                      ],
                      `genlab-analytics-${timeWindow}.csv`,
                    );
                  });
                }}
                title="Export top performers as CSV"
              >
                Export CSV
              </button>
            </div>
          ) : undefined
        }
      />

      {/* KPI Hero Row — aggregate totals from all engagement data.
       *
       * H1 audit fix (2026-03-31): the same metric name was rendered
       * with different windows on Mission Control vs. Analytics — MC
       * "TOTAL REACH" is all-time, Analytics "Total Reach" is windowed.
       * Adding the `(Nd)` suffix here removes the ambiguity. The
       * `windowLabel` already drives subtitle copy below; we now wire
       * it into the label itself so operators can't miss it. */}
      <div className="kpi-hero-grid mb-6">
        <KpiCard
          variant="hero"
          label={`Total Likes (${windowLabel}d)`}
          value={heroTotalLikes ?? 0}
          subtitle="across all niches"
          icon={Heart}
          accentColor="var(--color-rose)"
        />
        <KpiCard
          variant="hero"
          label={`Total Reach (${windowLabel}d)`}
          value={heroTotalReach ?? 0}
          subtitle="impressions served"
          icon={Eye}
          accentColor="var(--color-blue)"
        />
        <KpiCard
          variant="hero"
          label={`Total Comments (${windowLabel}d)`}
          value={heroTotalComments ?? 0}
          formatter={(n) => String(n)}
          subtitle="community interactions"
          icon={MessageCircle}
          accentColor="var(--color-purple)"
        />
        <KpiCard
          variant="hero"
          label={`Avg Eng Rate (${windowLabel}d)`}
          value={heroAvgEngRate ?? (summary?.avg_engagement_rate ?? 0)}
          formatter={(n) => `${n.toFixed(1)}%`}
          subtitle="likes + comments / reach"
          icon={TrendingUp}
          accentColor="var(--color-green)"
        />
      </div>

      {/* Estimated banner */}
      {isEstimated && activeTab === "overview" && (
        <AlertBanner
          message="Engagement metrics are estimated \u2014 insights haven't been fetched for this period yet."
          type="warning"
          dismissable
        />
      )}

      {/* Tab bar */}
      <div className="flex items-center gap-0.5 mb-6 border-b border-border">
        <button
          className={`bg-transparent border-none border-b-2 cursor-pointer text-sm font-medium px-3 py-2 -mb-px transition-colors ${
            activeTab === "overview"
              ? "text-text-primary border-b-[color:var(--niche-current,var(--color-blue))]"
              : "text-text-muted border-b-transparent hover:text-text-secondary"
          }`}
          onClick={() => setActiveTab("overview")}
        >
          Overview
        </button>
        <button
          className={`bg-transparent border-none border-b-2 cursor-pointer text-sm font-medium px-3 py-2 -mb-px transition-colors ${
            activeTab === "top-posts"
              ? "text-text-primary border-b-[color:var(--niche-current,var(--color-blue))]"
              : "text-text-muted border-b-transparent hover:text-text-secondary"
          }`}
          onClick={() => setActiveTab("top-posts")}
        >
          Top Posts
        </button>
        <button
          className={`bg-transparent border-none border-b-2 cursor-pointer text-sm font-medium px-3 py-2 -mb-px transition-colors ${
            activeTab === "compare"
              ? "text-text-primary border-b-[color:var(--niche-current,var(--color-blue))]"
              : "text-text-muted border-b-transparent hover:text-text-secondary"
          }`}
          onClick={() => setActiveTab("compare")}
        >
          Compare
        </button>
      </div>

      {/* ── Overview Tab ─────────────────────────────── */}
      {activeTab === "overview" && (
        <>
          {/* KPI Cards.
           *
           * H1 audit fix (2026-03-31): label gets the `(Nd)` window
           * suffix so the surface itself disambiguates from Mission
           * Control's "all time" labels. */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
            <KpiCard
              label={`Total Reach (${windowLabel}d)`}
              value={summary?.total_reach ?? 0}
              subtitle="across all platforms"
              icon={Eye}
            />
            <KpiCard
              label={`Avg Engagement (${windowLabel}d)`}
              value={summary?.avg_engagement_rate ?? 0}
              formatter={(n) => `${n.toFixed(1)}%`}
              subtitle="likes \u00b7 comments \u00b7 shares / reach"
              icon={Heart}
              estimated={isEstimated}
            />
            <KpiCard
              label="Posts Published"
              value={summary?.total_posts ?? 0}
              formatter={(n) => String(n)}
              subtitle={`in the last ${windowLabel} days`}
              icon={Send}
            />
            <KpiCard
              label="Publish Success"
              value={Math.round(summary?.publish_success_rate ?? 0)}
              formatter={(n) => `${n}%`}
              subtitle="of pipeline items published"
              icon={CheckCircle2}
            />
          </div>

          {/* Reach Over Time */}
          <ChartCard title="Reach Over Time">
            <ReachChart data={timeSeries} nicheId={nicheId} />
          </ChartCard>

          {/* Two-column: Platform Breakdown + Content Funnel */}
          <div className="two-col-layout">
            <ChartCard title="Platform Breakdown" className="!mb-0">
              <PlatformBreakdown data={byPlatform} />
            </ChartCard>
            <ChartCard title="Content Funnel" className="!mb-0">
              <ContentFunnel data={funnel} />
            </ChartCard>
          </div>

          {/* Top Performers */}
          <ChartCard title="Top Performers">
            <TopPerformersTable performers={topPerformers} />
          </ChartCard>
        </>
      )}

      {/* ── Top Posts Tab ─────────────────────────────── */}
      {activeTab === "top-posts" && (
        <ChartCard
          title="Top Posts by Likes"
          headerAction={
            <>
              {topPostsQuery.isLoading && (
                <div className="shimmer" style={{ width: 60, height: 14, borderRadius: 4 }} />
              )}
              {topPostsQuery.isError && (
                <span className="text-xs text-warning">
                  Could not load — showing cached data
                </span>
              )}
            </>
          }
        >
          <TopPostsTable posts={top20Posts} />
        </ChartCard>
      )}

      {/* ── Compare Tab ──────────────────────────────── */}
      {activeTab === "compare" && <CrossNicheComparison />}
    </div>
  );
}
