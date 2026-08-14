/**
 * Phase 4.E session 3 observability card (2026-08-14) — content
 * ideation pool depth per niche.
 *
 * Shows how many LLM-generated ideas are pending / consumed /
 * expired per niche, plus the rollout flag state. Reading this
 * card lets the operator judge:
 *
 *   * Is the ideator producing enough ideas?
 *   * Is the promoter consuming them?
 *   * Should rollout % be raised (or rolled back)?
 *
 * ## Rendering
 *
 * Per-niche row: pending / consumed / expired counts + latest
 * batch timestamp. Rollout % + flag badge in the header.
 *
 * Sibling to ContentQualityCard / TopCreatorPriorsCard — same
 * observation-vs-active flag-badge pattern.
 */
import { useQuery } from "@tanstack/react-query";

import { ideationPool } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { IdeationPoolPerNiche } from "@/api/types";

function NicheRow({ row }: { row: IdeationPoolPerNiche }) {
  const consumptionPct =
    row.total > 0 ? (row.consumed / row.total) * 100 : 0;
  return (
    <div className="grid grid-cols-12 gap-2 border-b border-border/40 py-1.5 text-xs">
      <span className="col-span-2 font-mono font-medium">{row.niche_id}</span>
      <span
        className="col-span-2 font-mono text-success"
        title="Pending ideas — ready for promoter to consume"
      >
        pending={row.pending}
      </span>
      <span
        className="col-span-2 font-mono text-text-muted"
        title="Consumed — already materialized into blueprints"
      >
        consumed={row.consumed}
      </span>
      <span
        className="col-span-2 font-mono text-text-muted"
        title="Expired — >30d without being consumed"
      >
        expired={row.expired}
      </span>
      <span
        className="col-span-2 font-mono"
        title={`${consumptionPct.toFixed(0)}% of ideas ever generated have been consumed`}
      >
        {consumptionPct.toFixed(0)}% used
      </span>
      <span className="col-span-2 text-text-muted truncate" title="Latest batch created">
        {row.latest_batch_at ? row.latest_batch_at.slice(0, 10) : "—"}
      </span>
    </div>
  );
}

export function IdeationPoolCard() {
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.ideationPool.summary(),
    queryFn: () => ideationPool.summary(),
    // Promoter fires every 6h; 15-min poll gives near-real-time
    // visibility into consumption.
    refetchInterval: 15 * 60 * 1000,
    staleTime: 15 * 60 * 1000,
  });

  if (isLoading) {
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="text-sm font-semibold">Content ideation pool</div>
        <div className="mt-2 text-xs text-text-muted">Loading…</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="mb-2">
          <div className="text-sm font-semibold">Content ideation pool</div>
          <div className="text-xs text-text-muted">
            Weekly LLM-generated content ideas · Sun 05:30 UTC
          </div>
        </div>
        <div className="text-xs text-text-muted">
          No ideas yet — weekly ideator runner may not have fired
        </div>
      </div>
    );
  }

  const flagBadge = data.flag_enabled ? (
    <span
      className="rounded border border-success/30 bg-success/10 px-1.5 py-0.5 text-xs text-success"
      title={`GENLAB_IDEATION_POOL_ENABLED on with ROLLOUT_PCT=${data.rollout_pct}% — deterministic per-niche dice picks which niches promote from pool`}
    >
      active {data.rollout_pct}%
    </span>
  ) : (
    <span
      className="rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-xs text-warning"
      title="GENLAB_IDEATION_POOL_ENABLED is off — ideas persist but promoter doesn't fire"
    >
      observation only
    </span>
  );

  return (
    <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
      <div className="mb-2 flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold">
            Content ideation pool {flagBadge}
          </div>
          <div className="text-xs text-text-muted">
            LLM-generated ideas per niche · seeded on trend + competitor +
            style + persona signals
          </div>
        </div>
      </div>
      {data.per_niche.length === 0 ? (
        <div className="text-xs text-text-muted">
          No niches have pool rows yet.
        </div>
      ) : (
        <div>
          {data.per_niche.map((row) => (
            <NicheRow key={row.niche_id} row={row} />
          ))}
        </div>
      )}
    </div>
  );
}
