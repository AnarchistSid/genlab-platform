/**
 * Intervention 2 observability card (2026-07-01) — cross-niche
 * transferred priors.
 *
 * Shows each style-suffix's transferred (α, β) alongside the
 * empirical (μ, σ², N) that produced it. Gives the operator a
 * "validate before flip" surface: they can eyeball whether the
 * transferred priors look reasonable before flipping
 * ``GENLAB_CROSS_NICHE_TRANSFER_ENABLED=true``.
 *
 * ## Rendering rules
 *
 *   * ``flag_enabled``          → green "active" badge — new arms
 *                                 are inheriting these priors now
 *   * ``!flag_enabled``         → amber "observation only" badge —
 *                                 priors written but not consumed
 *   * empty priors dict         → "no transferable priors" copy
 *                                 (all styles below the min-obs
 *                                 threshold, or first refit hasn't
 *                                 fired)
 *   * cold-start (data=null)    → "No priors yet — weekly runner
 *                                 may not have fired"
 *
 * ## Sibling cards
 *
 * Same visual language as ``TrendAnticipationAccuracyCard`` — both
 * are periodic-refit surfaces that surface a moment-in-time
 * statistical fit. Both use the same "observation only / active"
 * flag-badge pattern.
 */
import { useQuery } from "@tanstack/react-query";

import { crossNicheTransfer } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { CrossNichePrior } from "@/api/types";

function PriorRow({ prior }: { prior: CrossNichePrior }) {
  const observedMean = (prior.observed_rate_mean * 100).toFixed(1);
  const observedVar = prior.observed_rate_variance.toFixed(4);
  const effectiveN = (prior.alpha + prior.beta).toFixed(1);
  return (
    <div className="grid grid-cols-6 gap-2 border-b border-border/40 py-1.5 text-xs">
      <span className="font-mono font-medium">{prior.style}</span>
      <span className="text-text-muted" title="μ (cross-niche mean rate)">
        μ={observedMean}%
      </span>
      <span
        className="text-text-muted"
        title="σ² (cross-niche variance of observed rates)"
      >
        σ²={observedVar}
      </span>
      <span className="text-text-muted" title="Contributing niches">
        N={prior.n_niches}
      </span>
      <span title="Beta prior α (moment-matched)">
        α=<span className="font-mono">{prior.alpha.toFixed(2)}</span>
      </span>
      <span title="Beta prior β (moment-matched)">
        β=<span className="font-mono">{prior.beta.toFixed(2)}</span>
        <span className="ml-1 text-text-muted">(N={effectiveN})</span>
      </span>
    </div>
  );
}

export function CrossNichePriorsCard() {
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.crossNicheTransfer.priors(),
    queryFn: () => crossNicheTransfer.priors(),
    // 2026-07-14: 6h poll (was 1h). Rewrite cadence is WEEKLY
    // (Mon 05:30 UTC); 1h was 168× overpoll per refresh cycle. 6h
    // still gives operators a fresh view within a workday of the
    // Monday run without hammering the endpoint.
    refetchInterval: 6 * 60 * 60 * 1000,
    staleTime: 6 * 60 * 60 * 1000,
  });

  if (isLoading) {
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="text-sm font-semibold">Cross-niche priors</div>
        <div className="mt-2 text-xs text-text-muted">Loading…</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="mb-2">
          <div className="text-sm font-semibold">Cross-niche priors</div>
          <div className="text-xs text-text-muted">
            Weekly hierarchical Bayes refit · Mondays 05:30 UTC
          </div>
        </div>
        <div className="text-xs text-text-muted">
          No priors yet — weekly runner may not have fired
        </div>
      </div>
    );
  }

  const priorEntries = Object.values(data.priors ?? {});
  const flagBadge = data.flag_enabled ? (
    <span
      className="rounded border border-success/30 bg-success/10 px-1.5 py-0.5 text-xs text-success"
      title="GENLAB_CROSS_NICHE_TRANSFER_ENABLED is on — new arms are inheriting these priors"
    >
      active
    </span>
  ) : (
    <span
      className="rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-xs text-warning"
      title="GENLAB_CROSS_NICHE_TRANSFER_ENABLED is off — priors persisted but not consumed"
    >
      observation only
    </span>
  );

  return (
    <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
      <div className="mb-2 flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold">
            Cross-niche priors {flagBadge}
          </div>
          <div className="text-xs text-text-muted">
            Weekly hierarchical Bayes refit · empirical Bayes moment
            match · new arms inherit these
          </div>
        </div>
      </div>
      {priorEntries.length === 0 ? (
        <div className="text-xs text-text-muted">
          No transferable priors — no style met the min-observation
          threshold (≥5 obs on ≥2 niches)
        </div>
      ) : (
        <div>
          {priorEntries
            .sort((a, b) => b.n_niches - a.n_niches)
            .map((p) => (
              <PriorRow key={p.style} prior={p} />
            ))}
        </div>
      )}
    </div>
  );
}
