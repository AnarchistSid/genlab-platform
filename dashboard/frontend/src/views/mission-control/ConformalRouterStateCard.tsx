/**
 * L4 observability card (2026-07-08 audit) — conformal router
 * per-niche calibration state.
 *
 * ## What the card shows
 *
 * One row per niche summarising the router's calibration fit:
 *
 *   * ready badge — green ``READY`` when ``sample_count`` clears
 *     ``min_niche_samples``, amber ``X / N samples`` countdown
 *     otherwise. The countdown format is deliberately explicit
 *     because it's the single most operator-valuable number: it
 *     tells them how many more decisions the router needs before
 *     it can be trusted.
 *   * small chips for ``α=…``, ``q̂=…``, ``n_calib=…`` — enough
 *     to eyeball whether the fit looks reasonable without opening
 *     the raw state file.
 *
 * ## Flag badge
 *
 *   * ``flag_enabled``    → green "active" badge — router is
 *                           currently steering routing decisions
 *   * ``!flag_enabled``   → amber "observation only" — state
 *                           persisted but router falls back to
 *                           operator on every decision
 *
 * ## Sibling cards
 *
 * Same visual language as ``CrossNichePriorsCard`` — grouped
 * observation surface with per-row status + a top-right flag badge.
 * The READY countdown discipline mirrors ``TopCreatorPriorsCard``'s
 * "N/M" fresh-uploads counter so operators see the same shape for
 * "waiting for enough data" across cards.
 */
import { useQuery } from "@tanstack/react-query";

import { conformalRouterState } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { ConformalRouterNicheSummary } from "@/api/types";

function ReadyBadge({
  niche,
  minSamples,
}: {
  niche: ConformalRouterNicheSummary;
  minSamples: number;
}) {
  if (niche.ready) {
    return (
      <span
        className="rounded border border-success/30 bg-success/10 px-1.5 py-0.5 text-[10px] font-mono font-medium text-success"
        data-testid={`conformal-router-ready-badge-${niche.niche_id}`}
        title="sample_count ≥ min_niche_samples — router can be trusted for this niche"
      >
        READY
      </span>
    );
  }
  return (
    <span
      className="rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-[10px] font-mono font-medium text-warning"
      data-testid={`conformal-router-countdown-${niche.niche_id}`}
      title="Samples still needed before router can route for this niche"
    >
      {niche.sample_count} / {minSamples} samples
    </span>
  );
}

function NicheRow({
  niche,
  minSamples,
}: {
  niche: ConformalRouterNicheSummary;
  minSamples: number;
}) {
  return (
    <div
      className="grid grid-cols-[auto,auto,1fr] items-center gap-2 border-b border-border/40 py-1.5 text-xs"
      data-testid={`conformal-router-niche-row-${niche.niche_id}`}
    >
      <span className="font-mono font-semibold">{niche.niche_id}</span>
      <ReadyBadge niche={niche} minSamples={minSamples} />
      <div className="flex flex-wrap justify-end gap-1.5 text-[10px]">
        <span
          className="rounded bg-zinc-800 px-1 py-0.5 font-mono text-zinc-300"
          title="Miscoverage tolerance (α) used at fit time"
        >
          α={niche.alpha.toFixed(2)}
        </span>
        <span
          className="rounded bg-zinc-800 px-1 py-0.5 font-mono text-zinc-300"
          title="Non-conformity quantile from the calibration set"
        >
          q̂={niche.q_hat.toFixed(3)}
        </span>
        <span
          className="rounded bg-zinc-800 px-1 py-0.5 font-mono text-zinc-300"
          title="Calibration-set size (n_calib)"
        >
          n_calib={niche.n_calib}
        </span>
      </div>
    </div>
  );
}

export function ConformalRouterStateCard() {
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.conformalRouterState.get(),
    queryFn: () => conformalRouterState.get(),
    // 10-min poll — nightly refit means state changes rarely; a
    // 5-min stale window keeps the card responsive after refresh.
    refetchInterval: 10 * 60 * 1000,
    staleTime: 5 * 60 * 1000,
  });

  if (isLoading) {
    // NOTE: no ``conformal-router-card`` testid on the loading
    // return — tests use ``findByTestId`` to wait for the data-
    // loaded card, and re-using the testid here would resolve
    // early against the loading skeleton and defeat the wait.
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="text-sm font-semibold">Conformal router</div>
        <div className="mt-2 text-xs text-text-muted">Loading…</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div
        className="rounded-lg border border-border/60 bg-surface-1 p-3"
        data-testid="conformal-router-card"
      >
        <div className="mb-2">
          <div className="text-sm font-semibold">Conformal router</div>
          <div className="text-xs text-text-muted">
            Per-niche q_hat calibration · flip
            GENLAB_CONFORMAL_ROUTER_ENABLED when READY
          </div>
        </div>
        <div
          className="text-xs text-text-muted"
          data-testid="conformal-router-empty-state"
        >
          No calibration yet — nightly refit may not have fired
        </div>
      </div>
    );
  }

  const flagBadge = data.flag_enabled ? (
    <span
      className="rounded border border-success/30 bg-success/10 px-1.5 py-0.5 text-xs text-success"
      data-testid="conformal-router-flag-badge"
      data-flag-state="active"
      title="GENLAB_CONFORMAL_ROUTER_ENABLED is on — router is steering routing decisions"
    >
      active
    </span>
  ) : (
    <span
      className="rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-xs text-warning"
      data-testid="conformal-router-flag-badge"
      data-flag-state="observation-only"
      title="GENLAB_CONFORMAL_ROUTER_ENABLED is off — state persisted but router falls back to operator"
    >
      observation only
    </span>
  );

  const sortedNiches = [...data.niches].sort((a, b) =>
    a.niche_id.localeCompare(b.niche_id),
  );

  return (
    <div
      className="rounded-lg border border-border/60 bg-surface-1 p-3"
      data-testid="conformal-router-card"
    >
      <div className="mb-2 flex items-start justify-between gap-2">
        <div>
          <div className="text-sm font-semibold">Conformal router</div>
          <div className="text-xs text-text-muted">
            Per-niche q_hat calibration · flip
            GENLAB_CONFORMAL_ROUTER_ENABLED when READY
          </div>
        </div>
        {flagBadge}
      </div>

      {sortedNiches.length === 0 ? (
        <div
          className="text-xs text-text-muted"
          data-testid="conformal-router-empty-state"
        >
          No calibration yet — nightly refit may not have fired
        </div>
      ) : (
        <div>
          {sortedNiches.map((niche) => (
            <NicheRow
              key={niche.niche_id}
              niche={niche}
              minSamples={data.min_niche_samples}
            />
          ))}
        </div>
      )}
    </div>
  );
}
