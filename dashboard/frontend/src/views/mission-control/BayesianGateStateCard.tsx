/**
 * L4 observability card (2026-07-08 audit) — Bayesian LR gate
 * posterior state.
 *
 * Shows each niche's posterior weight vector alongside the training
 * set size that produced it. Gives the operator a "validate before
 * flip" surface: they can eyeball whether the fit looks reasonable
 * before flipping ``GENLAB_BAYESIAN_GATE_ENABLED=true``.
 *
 * ## Rendering rules
 *
 *   * ``flag_enabled``          → green "active" badge — the gate is
 *                                 consuming these weights now
 *   * ``!flag_enabled``         → amber "observation only" badge —
 *                                 posterior fit but not consumed
 *   * ``niches.length === 0``   → "No posterior yet — nightly refit
 *                                 may not have fired" (do NOT render
 *                                 an empty row list)
 *
 * ## Row density
 *
 * Only the top-3 features by |weight| are rendered inline — full
 * vector lives in the row tooltip. Rationale: 15-dim contexts make
 * inline rendering unreadable; the top-magnitude features are the
 * ones the operator cares about when eyeballing "does the fit look
 * reasonable?".
 *
 * ## Sibling cards
 *
 * Same visual language as ``CrossNichePriorsCard`` — grouped-list
 * card with per-row badges + a flag-state header chip. Both are
 * periodic-refit surfaces surfacing a moment-in-time statistical
 * fit under the "observation only / active" flag-badge pattern.
 */
import { useQuery } from "@tanstack/react-query";

import { bayesianGateState } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { BayesianGateNicheSummary } from "@/api/types";

/** Render the whole feature vector as a single "name=weight" string —
 *  used for the row tooltip. */
function formatFullVector(niche: BayesianGateNicheSummary): string {
  const pairs: string[] = [];
  const len = Math.min(niche.feature_names.length, niche.mean.length);
  for (let i = 0; i < len; i++) {
    pairs.push(`${niche.feature_names[i]}=${niche.mean[i].toFixed(3)}`);
  }
  return pairs.join(", ");
}

/** Pick the top-N features by |weight|, preserving original zip
 *  between feature_names and mean. Guards against length mismatch
 *  (defensive — endpoint contract says they match). */
function topFeaturesByMagnitude(
  niche: BayesianGateNicheSummary,
  topN: number,
): { name: string; weight: number }[] {
  const len = Math.min(niche.feature_names.length, niche.mean.length);
  const entries: { name: string; weight: number }[] = [];
  for (let i = 0; i < len; i++) {
    entries.push({ name: niche.feature_names[i], weight: niche.mean[i] });
  }
  entries.sort((a, b) => Math.abs(b.weight) - Math.abs(a.weight));
  return entries.slice(0, topN);
}

function NicheRow({ niche }: { niche: BayesianGateNicheSummary }) {
  const top = topFeaturesByMagnitude(niche, 3);
  const fullVector = formatFullVector(niche);
  return (
    <div
      className="grid grid-cols-[auto,auto,1fr] items-center gap-2 border-b border-border/40 py-1.5 text-xs"
      data-testid={`bayesian-gate-niche-row-${niche.niche_id}`}
      title={fullVector}
    >
      <span className="font-mono font-medium">{niche.niche_id}</span>
      <span
        className="inline-flex items-center rounded bg-zinc-500/15 px-1.5 py-0.5 text-[10px] font-mono text-zinc-400"
        title="Training set size (rows fed into the posterior fit)"
      >
        N={niche.n_rows}
      </span>
      <span className="min-w-0 truncate text-text-muted">
        {top.map((f, idx) => (
          <span key={f.name} className="font-mono">
            {idx > 0 && <span className="text-text-muted/50"> · </span>}
            {f.name}=<span>{f.weight.toFixed(3)}</span>
          </span>
        ))}
      </span>
    </div>
  );
}

export function BayesianGateStateCard() {
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.bayesianGateState.get(),
    queryFn: () => bayesianGateState.get(),
    // Posterior refits are nightly — 10 min poll is generous but
    // matches sibling `TrendAnticipationCard` cadence for the
    // same class of periodic-artifact surface.
    refetchInterval: 10 * 60 * 1000,
    staleTime: 5 * 60 * 1000,
  });

  if (isLoading || !data) {
    // Keep the loading + cold-start states testid-free — the pin
    // tests assert that finding `bayesian-gate-card` means we're
    // past the fetch. FlagStateCard uses the same pattern.
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="text-sm font-semibold">Bayesian gate posterior</div>
        {isLoading ? (
          <div className="mt-2 text-xs text-text-muted">Loading…</div>
        ) : (
          <>
            <div className="text-xs text-text-muted">
              Per-niche weight vector · flip GENLAB_BAYESIAN_GATE_ENABLED
              when the fit looks reasonable
            </div>
            <div
              className="mt-2 text-xs text-text-muted"
              data-testid="bayesian-gate-empty-state"
            >
              No posterior yet — nightly refit may not have fired
            </div>
          </>
        )}
      </div>
    );
  }

  const niches = [...data.niches].sort((a, b) =>
    a.niche_id.localeCompare(b.niche_id),
  );

  const flagBadge = data.flag_enabled ? (
    <span
      className="rounded border border-success/30 bg-success/10 px-1.5 py-0.5 text-xs text-success"
      title="GENLAB_BAYESIAN_GATE_ENABLED is on — the gate is consuming these weights"
      data-testid="bayesian-gate-flag-badge"
      data-flag-state="active"
    >
      active
    </span>
  ) : (
    <span
      className="rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-xs text-warning"
      title="GENLAB_BAYESIAN_GATE_ENABLED is off — posterior fit but not consumed"
      data-testid="bayesian-gate-flag-badge"
      data-flag-state="observation-only"
    >
      observation only
    </span>
  );

  return (
    <div
      className="rounded-lg border border-border/60 bg-surface-1 p-3"
      data-testid="bayesian-gate-card"
    >
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm font-semibold">Bayesian gate posterior</div>
          <div className="text-xs text-text-muted">
            Per-niche weight vector · flip GENLAB_BAYESIAN_GATE_ENABLED when
            the fit looks reasonable
          </div>
        </div>
        {flagBadge}
      </div>
      {niches.length === 0 ? (
        <div className="text-xs text-text-muted" data-testid="bayesian-gate-empty-state">
          No posterior yet — nightly refit may not have fired
        </div>
      ) : (
        <div>
          {niches.map((n) => (
            <NicheRow key={n.niche_id} niche={n} />
          ))}
        </div>
      )}
    </div>
  );
}
