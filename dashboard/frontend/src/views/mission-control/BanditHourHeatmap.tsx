/**
 * PR BB (2026-06-23) — Mission Control bandit hour-heatmap card.
 *
 * Operator's feedback loop for PR #487's optimal-time bandit
 * foundation. Without this card, the bandit is invisible learning
 * — arms accumulate posteriors on every publish but the operator
 * has no way to see whether the system is learning anything
 * meaningful before they flip GENLAB_OPTIMAL_TIME_BANDIT=1.
 *
 * Visual: 5 niche rows × 24-hour strips, each hour colored by
 * Beta mean (alpha / (alpha+beta)). Empty / cold-start hours
 * render as muted backgrounds; high-engagement hours render as
 * filled accent-color bars.
 *
 * Activation badge: turns green when total_observations ≥ 30
 * for a niche — same threshold pattern as the
 * AutoApprovalCalibrationCard's ready-for-enforcement gate.
 *
 * Polls every 60s. Each niche is a separate query (5 round-trips)
 * — acceptable because arms only update on publish (~1-5/niche/day)
 * and dashboard tabs collapse into the in-process cache.
 */
import { useQueries } from "@tanstack/react-query";

import { bandit } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { BanditHourEntry, BanditHourPosteriors } from "@/api/types";
import { getNicheInfo, NICHE_IDS } from "@/niches/registry";

const ACTIVATION_THRESHOLD = 30;

export function BanditHourHeatmap() {
  const queries = useQueries({
    queries: NICHE_IDS.map((nicheId) => ({
      queryKey: queryKeys.bandit.hourPosteriors(nicheId),
      queryFn: () => bandit.hourPosteriors(nicheId),
      staleTime: 60_000,
      refetchInterval: 60_000,
      retry: false,
    })),
  });

  const perNiche: Record<string, BanditHourPosteriors | undefined> = {};
  NICHE_IDS.forEach((nid, i) => {
    perNiche[nid] = queries[i].data;
  });

  const readyCount = NICHE_IDS.filter(
    (nid) => perNiche[nid]?.ready_for_activation === true,
  ).length;
  const anyLoading = queries.some((q) => q.isLoading && !q.data);

  return (
    <div className="bento-card">
      <h3 className="card-title">
        Optimal-Time Bandit
        <span className="ml-2 text-[10px] text-text-muted font-normal">
          PR #487 hour posteriors
        </span>
      </h3>

      {/* Headline — readiness count for env-flag activation */}
      <div className="flex items-center gap-2 mb-3">
        <span
          className={
            readyCount === NICHE_IDS.length
              ? "size-2 rounded-full bg-success"
              : readyCount > 0
                ? "size-2 rounded-full bg-warning"
                : "size-2 rounded-full bg-text-muted"
          }
          style={
            readyCount > 0
              ? { boxShadow: "0 0 6px rgba(34,197,94,0.4)" }
              : undefined
          }
        />
        <span className="text-sm text-text-secondary">
          {readyCount === NICHE_IDS.length
            ? "All 5 niches ready — flip GENLAB_OPTIMAL_TIME_BANDIT=1"
            : readyCount > 0
              ? `${readyCount} / ${NICHE_IDS.length} niche${readyCount === 1 ? "" : "s"} ≥ 30 obs`
              : `${NICHE_IDS.length} niches accumulating`}
        </span>
      </div>

      {/* Per-niche rows with 24-hour strips */}
      <div className="flex flex-col gap-2">
        {anyLoading ? (
          <>
            <div
              className="shimmer"
              style={{ height: 18, width: "100%", borderRadius: 4 }}
            />
            <div
              className="shimmer"
              style={{ height: 18, width: "100%", borderRadius: 4 }}
            />
            <div
              className="shimmer"
              style={{ height: 18, width: "100%", borderRadius: 4 }}
            />
          </>
        ) : (
          NICHE_IDS.map((nicheId) => {
            const info = getNicheInfo(nicheId);
            return (
              <NicheHourStrip
                key={nicheId}
                label={info.shortLabel}
                hex={info.hex}
                data={perNiche[nicheId]}
              />
            );
          })
        )}
      </div>

      {/* Footer hint */}
      <div className="mt-3 pt-2 border-t border-text-muted/10 text-[10px] text-text-muted">
        Each bar = UTC hour. Color intensity = Beta posterior mean
        (higher = stronger engagement signal). Cold-start hours render
        muted.
      </div>
    </div>
  );
}

interface NicheHourStripProps {
  label: string;
  hex: string;
  data: BanditHourPosteriors | undefined;
}

function NicheHourStrip({ label, hex, data }: NicheHourStripProps) {
  // No data → muted shape with empty bars + waiting badge
  if (!data) {
    return (
      <div className="flex items-center gap-2 text-xs">
        <span className="size-2 rounded-full shrink-0" style={{ background: hex }} />
        <span
          className="font-medium text-text-secondary shrink-0"
          style={{ width: 48 }}
        >
          {label}
        </span>
        <div className="flex-1 flex gap-px h-3 rounded overflow-hidden bg-text-muted/10" />
        <span
          className="font-mono text-[10px] text-text-muted shrink-0"
          style={{ minWidth: 56, textAlign: "right" }}
        >
          waiting
        </span>
      </div>
    );
  }

  const { hours, total_observations, ready_for_activation } = data;

  return (
    <div className="flex items-center gap-2 text-xs">
      {/* Color dot + niche label */}
      <span className="size-2 rounded-full shrink-0" style={{ background: hex }} />
      <span
        className="font-medium text-text-secondary shrink-0"
        style={{ width: 48 }}
      >
        {label}
      </span>

      {/* 24-hour strip — one micro-bar per UTC hour */}
      <div
        className="flex-1 flex gap-px h-3 rounded overflow-hidden"
        title={`Hour distribution UTC 0..23. Sum: ${total_observations} obs.`}
      >
        {hours.map((h) => (
          <HourBar key={h.hour} entry={h} hex={hex} />
        ))}
      </div>

      {/* Total observations + ready badge */}
      <span
        className={
          ready_for_activation
            ? "font-mono text-[10px] text-success shrink-0 px-1.5 py-0.5 rounded bg-success/10 border border-success/30"
            : "font-mono text-[10px] text-text-muted shrink-0"
        }
        title={
          ready_for_activation
            ? `${total_observations} obs — ready to activate`
            : `${total_observations} / ${ACTIVATION_THRESHOLD} obs needed`
        }
        style={{ minWidth: 56, textAlign: "right" }}
      >
        {ready_for_activation
          ? `READY · ${total_observations}`
          : `${total_observations}/${ACTIVATION_THRESHOLD}`}
      </span>
    </div>
  );
}

function HourBar({ entry, hex }: { entry: BanditHourEntry; hex: string }) {
  // Cold-start hour → muted background. Active hour → color
  // intensity proportional to (mean - 0.5) * 2 → 0..1 alpha.
  // Mean ≤ 0.5 (low/neutral engagement) renders dim; >0.5 renders
  // accent-colored at proportional opacity.
  let bg = "rgba(120,120,120,0.15)";
  if (entry.n_obs_est > 0) {
    const intensity = Math.max(0, Math.min(1, (entry.mean - 0.5) * 2));
    if (intensity > 0) {
      // Use the niche accent color at intensity-proportional alpha.
      // Min 0.25 so even faint signal is visible.
      const alpha = 0.25 + intensity * 0.75;
      bg = hexWithAlpha(hex, alpha);
    } else {
      // Has data but mean ≤ 0.5 — dim grey
      bg = "rgba(120,120,120,0.30)";
    }
  }

  return (
    <div
      className="flex-1 transition-colors"
      style={{ background: bg }}
      title={`UTC ${entry.hour}:00 — ${entry.n_obs_est} obs, mean ${entry.mean.toFixed(2)}`}
    />
  );
}

/** Convert "#RRGGBB" + alpha 0..1 to "rgba(r,g,b,a)" string. */
function hexWithAlpha(hex: string, alpha: number): string {
  const stripped = hex.replace("#", "");
  if (stripped.length !== 6) return `rgba(120,120,120,${alpha})`;
  const r = parseInt(stripped.slice(0, 2), 16);
  const g = parseInt(stripped.slice(2, 4), 16);
  const b = parseInt(stripped.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}
