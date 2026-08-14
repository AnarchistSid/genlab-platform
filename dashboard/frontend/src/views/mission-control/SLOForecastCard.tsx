/**
 * Phase 2.C observability card (2026-08-14) — SLO forecasts.
 *
 * Predicts SLO breaches 24h ahead using EWMA-smoothed pipeline_alerts
 * time-series + linear extrapolation. Every reactive fire this month
 * (cookies, source diversity, strategist 4k) could have been surfaced
 * 24h earlier if this card existed then.
 *
 * ## Verdict ladder
 *
 *   stable        (gray)   — trend flat / decreasing
 *   watch         (blue)   — trend up 20-100%
 *   forecast_warning (amber) — forecast ≥ 2× current
 *   forecast_critical (red)  — forecast ≥ 5× current
 *
 * ## TTB (time-to-breach)
 *
 * Hours until the smoothed value would exceed 2× current at the
 * current trend. Only shown when trend is positive. Null = stable
 * or decreasing.
 */
import { useQuery } from "@tanstack/react-query";

import { sloForecasts } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { SloForecast, SloVerdict } from "@/api/types";

function verdictClasses(v: SloVerdict): string {
  switch (v) {
    case "forecast_critical":
      return "bg-red-500/20 text-red-300 border-red-500/40";
    case "forecast_warning":
      return "bg-amber-500/20 text-amber-300 border-amber-500/40";
    case "watch":
      return "bg-blue-500/15 text-blue-300 border-blue-500/40";
    case "stable":
      return "bg-emerald-500/15 text-emerald-300 border-emerald-500/40";
    case "insufficient_data":
      return "bg-gray-500/15 text-gray-500 border-gray-500/40";
  }
}

function Row({ f }: { f: SloForecast }) {
  const scope = f.niche_id ?? "all niches";
  const trendSign = f.trend_pct > 0 ? "+" : "";
  return (
    <div className="py-2 border-b border-gray-800 last:border-0">
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm text-gray-200 truncate">
            {f.check_name}
          </span>
          <span className="text-[11px] text-gray-500">· {scope}</span>
        </div>
        <span
          className={`px-2 py-0.5 rounded border text-[11px] font-medium whitespace-nowrap ${verdictClasses(
            f.verdict,
          )}`}
        >
          {f.verdict}
        </span>
      </div>
      <div className="flex items-center gap-3 text-[11px] text-gray-400 font-mono">
        <span>now {f.current_rate.toFixed(2)}/d</span>
        <span>→ 24h {f.forecast_rate.toFixed(2)}/d</span>
        <span
          className={
            f.trend_pct > 0 ? "text-red-400" : "text-emerald-400"
          }
        >
          {trendSign}
          {f.trend_pct.toFixed(0)}%
        </span>
        {f.ttb_hours !== null && (
          <span className="text-amber-400">
            breach in ~{f.ttb_hours.toFixed(0)}h
          </span>
        )}
      </div>
    </div>
  );
}

export function SLOForecastCard() {
  const { data, isLoading, isError } = useQuery({
    queryKey: queryKeys.sloForecasts.all(),
    queryFn: () => sloForecasts.fetch(),
    refetchInterval: 5 * 60_000, // 5 min
  });

  // Only surface non-stable rows (stable = nothing to see)
  const attention = (data ?? []).filter(
    (f) => f.verdict !== "stable" && f.verdict !== "insufficient_data",
  );

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/40 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-200">
          SLO forecast
        </h3>
        <span className="text-[10px] text-gray-500">
          Phase 2.C — 24h-ahead breach projection
        </span>
      </div>
      {isLoading && (
        <div className="text-xs text-gray-500 py-2">Loading…</div>
      )}
      {isError && (
        <div className="text-xs text-red-400 py-2">
          Failed to load. Runner may not have fired yet.
        </div>
      )}
      {data && attention.length === 0 && (
        <div className="text-xs text-emerald-400 py-2">
          All tracked SLOs stable — nothing forecast to breach in the
          next 24h.
        </div>
      )}
      {attention.length > 0 && (
        <div>
          {attention.map((f) => (
            <Row key={`${f.check_name}:${f.niche_id ?? ""}`} f={f} />
          ))}
        </div>
      )}
      <div className="mt-3 text-[10px] text-gray-500 leading-relaxed">
        Verdicts: <span className="text-red-400">critical</span> = 5×
        current, <span className="text-amber-400">warning</span> = 2×,
        <span className="text-blue-400 ml-1">watch</span> = 20-100%
        up.
      </div>
    </div>
  );
}
