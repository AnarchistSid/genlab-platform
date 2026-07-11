/**
 * Layer 5 attribution health card (PR #Layer5, 2026-07-11).
 *
 * Post-Markanimation observability. Consumes
 * ``/api/v1/attribution-health/stats`` (shipped in the same PR as the
 * backend endpoint) and shows per-niche ``attribution_present_pct``
 * over a rolling 24h window.
 *
 * ## What each niche row shows
 *
 * ``[niche badge] · [count] with_attribution / total_published · [pct%] · [status pill]``
 *
 * Status pill colours are driven by server-computed thresholds
 * (healthy ≥ 95%, caution 90-95%, critical < 90%, no_data when no
 * publishes in the window) so the frontend never disagrees with the
 * endpoint on classification.
 *
 * ## Overall footer
 *
 * Aggregate across all niches. Shows the same status pill so the
 * operator's eyeball reads the whole card as green/amber/red at a
 * glance without needing to sum the per-niche numbers.
 *
 * ## Poll cadence
 *
 * 60s — matches AutoApprovalCalibrationCard and other 24h-window
 * cards. The underlying data changes only on publisher fires (~5
 * times/day at 06:30 UTC), so 60s is generous; keeps the operator's
 * dashboard fresh without hammering the endpoint.
 *
 * ## Zero-state
 *
 * Server returns ``status: "unknown"`` on DB errors + ``no_data``
 * when a niche hasn't published in the window. Both render explicitly
 * so an empty card is meaningful ("nothing to report") not
 * ambiguous ("did the fetch fail?").
 */
import { useQuery } from "@tanstack/react-query";

import { attributionHealth } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { AttributionNicheRow, AttributionStatus } from "@/api/types";
import { getNicheInfo, type NicheId } from "@/niches/registry";

const STATUS_STYLE: Record<
  AttributionStatus,
  { label: string; className: string; title: string }
> = {
  healthy: {
    label: "healthy",
    className: "border-success/30 bg-success/10 text-success",
    title: "≥95% of publishes in this window carry a credit line",
  },
  caution: {
    label: "caution",
    className: "border-warning/30 bg-warning/10 text-warning",
    title: "90-95% attribution — investigate why the last few slipped",
  },
  critical: {
    label: "critical",
    className: "border-critical/30 bg-critical/10 text-critical",
    title: "<90% attribution — pipeline gap; check compliance_events",
  },
  no_data: {
    label: "no data",
    className: "border-border/30 bg-surface-2 text-text-muted",
    title: "No PUBLISHED blueprints in this window",
  },
  unknown: {
    label: "?",
    className: "border-border/30 bg-surface-2 text-text-muted",
    title:
      "Server couldn't compute — DB error or fail-open path (see logs)",
  },
};

function StatusPill({ status }: { status: AttributionStatus }) {
  const style = STATUS_STYLE[status];
  return (
    <span
      className={`rounded border px-1.5 py-0.5 text-[10px] ${style.className}`}
      title={style.title}
    >
      {style.label}
    </span>
  );
}

function NicheRow({ row }: { row: AttributionNicheRow }) {
  const info = getNicheInfo(row.niche_id as NicheId);
  return (
    <div className="flex items-center justify-between gap-2 border-b border-border/40 py-2 text-sm">
      <div className="flex items-center gap-2">
        <span className="font-semibold" style={{ color: info.color }}>
          {info.shortLabel}
        </span>
        <StatusPill status={row.status} />
      </div>
      <div className="flex items-center gap-3 text-xs">
        <span className="text-text-muted">
          <span className="font-mono">{row.with_attribution}</span>
          <span> / </span>
          <span className="font-mono">{row.total_published}</span>
        </span>
        <span className="min-w-[3.5rem] text-right font-mono">
          {row.total_published > 0 ? `${row.attribution_pct.toFixed(1)}%` : "—"}
        </span>
      </div>
    </div>
  );
}

export function AttributionHealthCard() {
  const windowHours = 24;
  const { data, isLoading, error } = useQuery({
    queryKey: queryKeys.attributionHealth.stats(windowHours),
    queryFn: () => attributionHealth.stats(windowHours),
    // 60s matches AutoApprovalCalibrationCard cadence — publisher fires
    // 5x/day so 60s is generous; keeps the operator dashboard fresh
    // without hammering the endpoint.
    refetchInterval: 60 * 1000,
    staleTime: 30 * 1000,
  });

  if (isLoading) {
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="mb-2 text-sm font-semibold">Attribution health</div>
        <div className="text-xs text-text-muted">Loading…</div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="mb-2 text-sm font-semibold">Attribution health</div>
        <div className="text-xs text-text-muted">
          Unable to load — check /api/v1/attribution-health/stats
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
      <div className="mb-2">
        <div className="text-sm font-semibold">Attribution health</div>
        <div className="text-xs text-text-muted">
          Post-Markanimation observability · rolling {data.window_hours}h ·
          healthy ≥ {data.threshold_healthy_pct.toFixed(0)}%
        </div>
      </div>
      <div>
        {data.niches.map((row) => (
          <NicheRow key={row.niche_id} row={row} />
        ))}
      </div>
      <div className="mt-2 flex items-center justify-between border-t border-border/60 pt-2 text-xs">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-text-primary">Overall</span>
          <StatusPill status={data.overall.status} />
        </div>
        <div className="flex items-center gap-3 text-text-muted">
          <span>
            <span className="font-mono">{data.overall.with_attribution}</span>
            <span> / </span>
            <span className="font-mono">{data.overall.total_published}</span>
          </span>
          <span className="min-w-[3.5rem] text-right font-mono">
            {data.overall.total_published > 0
              ? `${data.overall.attribution_pct.toFixed(1)}%`
              : "—"}
          </span>
        </div>
      </div>
    </div>
  );
}
