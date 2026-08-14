/**
 * Phase 2.D observability card (2026-08-14) — cost budget throttle.
 *
 * Shows today's LLM spend + current throttle level + how far from
 * next threshold. If Anthropic spend spikes, optional callers
 * (strategist, LLM reviewer) auto-throttle before the credit runs
 * out — preventing the class of silent outage that caused the
 * 3-week strategist gap (07-13 → 08-09).
 *
 * ## Progress bar
 *
 * Horizontal bar with 3 threshold markers ($5, $10, $20). Fill
 * color shifts as thresholds cross:
 *   * green — under $5 (none)
 *   * amber — $5-$10 (reduce_50pct)
 *   * red — $10-$20 (pause_optional)
 *   * dark red — over $20 (emergency_shutoff)
 */
import { useQuery } from "@tanstack/react-query";

import { costBudget } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { CostThrottleLevel } from "@/api/types";

function levelClasses(l: CostThrottleLevel) {
  switch (l) {
    case "none":
      return { badge: "bg-emerald-500/20 text-emerald-300 border-emerald-500/40", bar: "bg-emerald-500/70" };
    case "reduce_50pct":
      return { badge: "bg-amber-500/20 text-amber-300 border-amber-500/40", bar: "bg-amber-500/70" };
    case "pause_optional":
      return { badge: "bg-red-500/20 text-red-300 border-red-500/40", bar: "bg-red-500/70" };
    case "emergency_shutoff":
      return { badge: "bg-red-700/30 text-red-200 border-red-700/50", bar: "bg-red-700" };
  }
}

function levelReason(l: CostThrottleLevel): string {
  switch (l) {
    case "none":
      return "All callers allowed.";
    case "reduce_50pct":
      return "Optional callers (strategist, LLM reviewer) skip 50% of calls.";
    case "pause_optional":
      return "All optional callers paused. Essential callers (writer) still allowed.";
    case "emergency_shutoff":
      return "Emergency: all LLM callers paused. Manual intervention needed.";
  }
}

export function CostBudgetCard() {
  const { data, isLoading, isError } = useQuery({
    queryKey: queryKeys.costBudget.status(),
    queryFn: () => costBudget.status(),
    refetchInterval: 60_000, // 1 min
  });

  if (isLoading) {
    return (
      <div className="rounded-lg border border-gray-800 bg-gray-900/40 p-4">
        <div className="text-xs text-gray-500">Loading cost budget…</div>
      </div>
    );
  }
  if (isError || !data) {
    return (
      <div className="rounded-lg border border-gray-800 bg-gray-900/40 p-4">
        <div className="text-xs text-red-400">Failed to load cost budget.</div>
      </div>
    );
  }

  const cls = levelClasses(data.throttle_level);
  // Fill % capped at emergency threshold for visual bar
  const fillPct = Math.min(
    100,
    (data.spend_today_usd / data.emergency_threshold) * 100,
  );

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/40 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-200">
          LLM cost budget
        </h3>
        <span className="text-[10px] text-gray-500">
          Phase 2.D — daily auto-throttle
        </span>
      </div>

      <div className="flex items-baseline justify-between mb-2">
        <span className="text-2xl font-mono text-gray-100">
          ${data.spend_today_usd.toFixed(2)}
        </span>
        <span className="text-xs text-gray-500">
          today · caps ${data.reduce_50_threshold} / $
          {data.pause_threshold} / ${data.emergency_threshold}
        </span>
      </div>

      {/* Threshold bar */}
      <div className="relative h-3 rounded overflow-hidden bg-gray-800 mb-3">
        <div
          className={`absolute inset-y-0 left-0 ${cls.bar}`}
          style={{ width: `${fillPct}%` }}
        />
        {/* Threshold ticks */}
        {[data.reduce_50_threshold, data.pause_threshold].map((t) => {
          const leftPct = (t / data.emergency_threshold) * 100;
          return (
            <div
              key={t}
              className="absolute top-0 bottom-0 w-px bg-gray-950/70"
              style={{ left: `${leftPct}%` }}
              title={`$${t}`}
            />
          );
        })}
      </div>

      <div className="flex items-center justify-between mb-2">
        <span
          className={`px-2 py-0.5 rounded border text-[11px] font-medium ${cls.badge}`}
        >
          {data.throttle_level}
        </span>
      </div>

      <div className="text-[11px] text-gray-400 leading-relaxed">
        {levelReason(data.throttle_level)}
      </div>
    </div>
  );
}
