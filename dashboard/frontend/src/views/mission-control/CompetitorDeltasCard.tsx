/**
 * Phase 3.A observability card (2026-08-14) — competitor content
 * deltas.
 *
 * Shows the top-N top-tier competitor uploads per niche that
 * outperformed our niche-median YouTube views by ≥1.5x over the
 * last 7 days. Gives the operator a "validate before flip" surface:
 * they can eyeball whether the delta data is trustworthy enough to
 * feed into strategist ``competitor_context`` before flipping
 * ``GENLAB_COMPETITOR_CONTEXT_ENABLED``.
 *
 * ## Rendering rules
 *
 *   * ``flag_enabled``       → green "active" badge — strategist is
 *                              consuming these deltas as context
 *   * ``!flag_enabled``      → amber "observation only" badge —
 *                              deltas persisted but not consumed
 *   * ``rows.length === 0``  → "No deltas above 5x floor yet"
 *   * data === null           → "No competitor deltas yet — daily
 *                              runner may not have fired"
 *
 * ## Sibling cards
 *
 * Same visual language as ``CrossNichePriorsCard`` +
 * ``TopCreatorPriorsCard`` — the three form the intelligence-stack
 * observability trio. All use the "observation only / active" flag-
 * badge pattern.
 *
 * ## Data-quality signal
 *
 * When ``our_reference_view_count`` is near-zero (1-5) the delta
 * ratios balloon into the thousands or millions. That's mathematically
 * correct but rendered with a subtle warning tint so the operator
 * knows the baseline is thin — flip the strategist wire only after
 * our metric collection produces a real baseline.
 */
import { useQuery } from "@tanstack/react-query";

import { competitorDeltas } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { CompetitorDeltaRow } from "@/api/types";

// Group rows by niche. Preserves incoming order within each group
// (server already sorted by delta_ratio DESC).
function groupByNiche(rows: CompetitorDeltaRow[]) {
  const map = new Map<string, CompetitorDeltaRow[]>();
  for (const r of rows) {
    const arr = map.get(r.niche_id) ?? [];
    arr.push(r);
    map.set(r.niche_id, arr);
  }
  return map;
}

function formatViews(v: number | null): string {
  if (v === null || v === undefined) return "—";
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`;
  return String(v);
}

function formatRatio(r: number | null): string {
  if (r === null || r === undefined) return "n/a";
  if (r >= 1000) return `${Math.round(r / 1000).toLocaleString()}Kx`;
  if (r >= 100) return `${Math.round(r)}x`;
  return `${r.toFixed(1)}x`;
}

function DeltaRow({ row }: { row: CompetitorDeltaRow }) {
  const url = `https://www.youtube.com/watch?v=${row.competitor_video_id}`;
  const title = row.competitor_title ?? "(untitled)";
  // Thin-baseline warning: near-zero our_reference makes delta ratios
  // mathematically absurd. Tint the row so operator knows.
  const thinBaseline =
    row.our_reference_view_count !== null &&
    row.our_reference_view_count < 10;
  return (
    <div
      className={`grid grid-cols-12 gap-2 border-b border-border/40 py-1.5 text-xs ${
        thinBaseline ? "opacity-70" : ""
      }`}
    >
      <span className="col-span-3 font-mono font-medium truncate">
        {row.competitor_channel_label ?? row.competitor_channel_id.slice(0, 12)}
      </span>
      <a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className="col-span-6 truncate text-text-muted hover:text-text-default"
        title={title}
      >
        {title}
      </a>
      <span
        className="col-span-1 text-right font-mono"
        title="Competitor view count"
      >
        {formatViews(row.competitor_view_count)}
      </span>
      <span
        className="col-span-2 text-right font-mono font-semibold"
        title={
          thinBaseline
            ? "Delta ratio is inflated by near-zero baseline — flag data quality before flip"
            : "Competitor views ÷ our niche-median YouTube views (last 7d)"
        }
      >
        {formatRatio(row.delta_ratio)}
      </span>
    </div>
  );
}

export function CompetitorDeltasCard() {
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.competitorDeltas.latest(5, 15),
    queryFn: () => competitorDeltas.latest({ minRatio: 5, limit: 15 }),
    // 6h poll matches CrossNichePriorsCard cadence — the runner
    // fires daily (09:30 UTC), so 6h gives operators a fresh view
    // within a workday of the daily run without hammering the
    // endpoint.
    refetchInterval: 6 * 60 * 60 * 1000,
    staleTime: 6 * 60 * 60 * 1000,
  });

  if (isLoading) {
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="text-sm font-semibold">Competitor content deltas</div>
        <div className="mt-2 text-xs text-text-muted">Loading…</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="mb-2">
          <div className="text-sm font-semibold">Competitor content deltas</div>
          <div className="text-xs text-text-muted">
            Daily competitor-vs-us delta refit · 09:30 UTC
          </div>
        </div>
        <div className="text-xs text-text-muted">
          No competitor deltas yet — daily runner may not have fired
        </div>
      </div>
    );
  }

  const byNiche = groupByNiche(data.rows);
  const flagBadge = data.flag_enabled ? (
    <span
      className="rounded border border-success/30 bg-success/10 px-1.5 py-0.5 text-xs text-success"
      title="GENLAB_COMPETITOR_CONTEXT_ENABLED is on — strategist is consuming these deltas as context"
    >
      active
    </span>
  ) : (
    <span
      className="rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-xs text-warning"
      title="GENLAB_COMPETITOR_CONTEXT_ENABLED is off — deltas persisted but not consumed by strategist yet"
    >
      observation only
    </span>
  );

  return (
    <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
      <div className="mb-2 flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold">
            Competitor content deltas {flagBadge}
          </div>
          <div className="text-xs text-text-muted">
            Top-tier creators' recent uploads vs our niche-median reach ·
            outperforming ≥5x
          </div>
        </div>
      </div>
      {byNiche.size === 0 ? (
        <div className="text-xs text-text-muted">
          No competitor uploads met the 5x floor over the last poll.
        </div>
      ) : (
        <div>
          {Array.from(byNiche.entries()).map(([nicheId, rows]) => (
            <div key={nicheId} className="mb-3 last:mb-0">
              <div className="mb-1 text-[11px] font-medium uppercase text-text-muted">
                {nicheId}{" "}
                <span className="ml-1 font-normal">
                  · {rows.length} row{rows.length === 1 ? "" : "s"}
                </span>
              </div>
              {rows.slice(0, 3).map((r) => (
                <DeltaRow key={r.competitor_video_id} row={r} />
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
