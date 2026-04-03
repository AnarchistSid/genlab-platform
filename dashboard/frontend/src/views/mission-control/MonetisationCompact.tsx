import { DollarSign } from "lucide-react";
import { useMonetisationProgress } from "@/hooks/use-monetisation";
import { useRevenueSummary } from "@/hooks/use-revenue";
import { ProgressBar } from "@/components/shared/progress-bar";
import { formatCompact } from "@/lib/format";
import { NICHE_IDS, getNicheInfo } from "@/niches/registry";

const NICHES = NICHE_IDS.map((id) => {
  const info = getNicheInfo(id);
  return { id, name: info.label, accent: info.hex };
});

export function MonetisationCompact() {
  const { data: resp } = useMonetisationProgress();
  const progressData = (resp && typeof resp === "object" && "data" in resp) ? (resp as any).data : resp;
  const { data: revenueData } = useRevenueSummary();
  const clicks = revenueData?.clicks ?? (revenueData as any)?.data?.clicks;
  const estRevenue = revenueData?.estimated_revenue_inr_30d ?? (revenueData as any)?.data?.estimated_revenue_inr_30d;

  return (
    <div className="bento-card">
      <h3 className="card-title">
        <DollarSign size={12} style={{ marginRight: 4, verticalAlign: "middle" }} />
        Monetisation
      </h3>

      <div className="flex flex-col gap-2">
        {NICHES.map((niche) => {
          // Find the nearest threshold metric for this niche
          const nicheProgress = progressData?.[niche.id];
          let nearestMetric: { label: string; value: number; target: number; pct: number } | null = null;

          if (nicheProgress) {
            // Iterate all platforms and find the metric closest to threshold
            for (const [, platProgress] of Object.entries(nicheProgress) as [string, any][]) {
              for (const metric of (platProgress?.metrics ?? [])) {
                if (metric.target_value && !metric.is_threshold_met) {
                  const pct = metric.pct_complete ?? 0;
                  if (!nearestMetric || pct > nearestMetric.pct) {
                    nearestMetric = {
                      label: metric.metric_name,
                      value: metric.current_value ?? 0,
                      target: metric.target_value,
                      pct: Math.min(100, pct),
                    };
                  }
                }
              }
            }
          }

          const pctValue = nearestMetric?.pct ?? 0;
          const valueLabel = nearestMetric
            ? `${formatCompact(nearestMetric.value)} / ${formatCompact(nearestMetric.target)}`
            : "No data";

          return (
            <div key={niche.id} className="grid grid-cols-[10px_1fr_auto_60px] items-center gap-2">
              <span
                className="niche-dot"
                style={{ backgroundColor: niche.accent }}
              />
              <span className="text-xs text-text-primary truncate">{niche.name}</span>
              <span className="text-[10px] font-mono text-text-muted text-right">{valueLabel}</span>
              <div className="min-w-[60px]">
                <ProgressBar value={pctValue} color={niche.accent} height={3} animated />
              </div>
            </div>
          );
        })}
      </div>

      {(clicks?.today != null || estRevenue != null) && (
        <div className="mt-3 pt-2 border-t border-border-subtle flex items-center justify-between text-xs text-text-muted">
          {clicks?.today != null && (
            <span>{clicks.today} clicks today</span>
          )}
          {estRevenue != null && estRevenue > 0 && (
            <span className="font-mono tabular-nums">{"\u20B9"}{estRevenue.toLocaleString()} est.</span>
          )}
        </div>
      )}
    </div>
  );
}
