import { usePublishingMetrics } from "@/hooks/use-publishing-metrics";
import { getPlatformInfo } from "@/lib/platforms";

interface MetricsData {
  success_rate_7d: Record<string, number>;
  platform_status: Record<string, string>;
  by_niche: Record<string, { success: number; failed: number; rate: number }>;
  daily_trend: Array<{ date: string; ok: number; fail: number }>;
  error_distribution: Record<string, number>;
}

const STATUS_COLORS: Record<string, string> = {
  green: "bg-green-500",
  yellow: "bg-amber-500",
  red: "bg-red-500",
  grey: "bg-zinc-600",
};

export function PublishingHealth() {
  const { data: raw, isLoading } = usePublishingMetrics();
  const data = raw as MetricsData | undefined;

  if (isLoading) {
    return (
      <div className="bento-card animate-pulse">
        <div className="h-4 w-32 bg-bg-elevated rounded mb-3" />
        <div className="flex gap-3 mb-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-3 w-12 bg-bg-elevated rounded" />
          ))}
        </div>
        <div className="flex items-end gap-0.5 h-8">
          {[1, 2, 3, 4, 5, 6, 7].map((i) => (
            <div key={i} className="flex-1 h-4 bg-bg-elevated rounded-sm" />
          ))}
        </div>
      </div>
    );
  }

  if (!data) return null;

  // Calculate overall success rate
  const totalOk = Object.values(data.by_niche).reduce((s, n) => s + n.success, 0);
  const totalFail = Object.values(data.by_niche).reduce((s, n) => s + n.failed, 0);
  const overallRate = totalOk + totalFail > 0 ? Math.round((totalOk / (totalOk + totalFail)) * 100) : 0;

  return (
    <div className="bento-card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="card-title" style={{ marginBottom: 0 }}>Publishing Health</h3>
        <span
          className={`text-lg font-bold tabular-nums ${overallRate >= 80 ? "text-green-400" : overallRate >= 50 ? "text-amber-400" : "text-red-400"}`}
        >
          {overallRate}%
        </span>
      </div>

      {/* Platform status indicators */}
      <div className="flex gap-3 mb-3 flex-wrap">
        {Object.entries(data.platform_status).map(([platform, status]) => (
          <div key={platform} className="flex items-center gap-1.5">
            <span className={`w-2 h-2 rounded-full ${STATUS_COLORS[status] || STATUS_COLORS.grey}`} />
            <span className="text-xs text-text-muted">{getPlatformInfo(platform).shortLabel}</span>
            <span className="text-xs text-text-ghost">{data.success_rate_7d[platform] ?? 0}%</span>
          </div>
        ))}
      </div>

      {/* Mini sparkline (7-day trend) */}
      {data.daily_trend.length > 0 && (
        <div className="flex items-end gap-0.5 h-8">
          {data.daily_trend.map((d, i) => {
            const total = d.ok + d.fail;
            const rate = total > 0 ? d.ok / total : 0;
            const height = Math.max(4, rate * 32);
            return (
              <div
                key={i}
                className={`flex-1 rounded-sm ${rate >= 0.8 ? "bg-green-500/60" : rate >= 0.5 ? "bg-amber-500/60" : "bg-red-500/60"}`}
                style={{ height: `${height}px` }}
                title={`${d.date}: ${d.ok} ok, ${d.fail} fail`}
              />
            );
          })}
        </div>
      )}

      {/* Error distribution */}
      {Object.values(data.error_distribution).some((v) => v > 0) && (
        <div className="flex gap-2 mt-3 text-xs text-text-ghost flex-wrap">
          {Object.entries(data.error_distribution)
            .filter(([, v]) => v > 0)
            .map(([cls, cnt]) => (
              <span key={cls} className="bg-bg-elevated px-1.5 py-0.5 rounded">
                {cls}: {cnt}
              </span>
            ))}
        </div>
      )}
    </div>
  );
}
