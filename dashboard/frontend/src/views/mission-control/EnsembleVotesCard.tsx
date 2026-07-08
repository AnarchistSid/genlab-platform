/**
 * L5 observability card (2026-07-08 audit) — ensemble vote summary.
 *
 * Renders the cross-niche summary of ensemble decision-making over
 * the last N days:
 *   * Per-component participation (voted / abstained) — component
 *     abstention > 0 is a signal that the component is missing an
 *     input feature, which is a health issue.
 *   * Recommendation distribution (auto_approve / worth_your_look
 *     / route_to_operator / …) — where the operator's leverage is.
 *
 * ## Rendering rules
 *
 *   * ``flag_enabled``          → green "active" badge — ensemble
 *                                 votes are being persisted
 *   * ``!flag_enabled``         → amber "observation only" badge —
 *                                 machinery shipped but not writing
 *   * ``data === null``         → "waiting for migration" copy — the
 *                                 endpoint's fail-open path when the
 *                                 ``ensemble_votes`` table hasn't
 *                                 been created yet on the target DB
 *   * ``total_decisions === 0`` → "No decisions in the last N days"
 *
 * ## Migration-pending vs no-decisions
 *
 * The two empty states are DIFFERENT signals and must render with
 * distinct copy: `null` = machinery not yet installed, `0` =
 * installed but idle. Operators need to distinguish these; the pin
 * tests lock the boundary.
 *
 * ## Sibling cards
 *
 * Visual language matches ``CrossNichePriorsCard`` and
 * ``FlagStateCard`` — the "active vs observation only" badge is the
 * same pattern used across every intervention-observability card.
 */
import { useQuery } from "@tanstack/react-query";

import { ensembleVotes } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { EnsembleComponentSummary } from "@/api/types";

const WINDOW_DAYS = 7;

function flagBadge(flagEnabled: boolean) {
  if (flagEnabled) {
    return (
      <span
        className="rounded border border-success/30 bg-success/10 px-1.5 py-0.5 text-xs text-success"
        data-testid="ensemble-votes-flag-badge"
        data-flag-state="active"
        title="GENLAB_ENSEMBLE_PERSIST_ENABLED is on — votes are being persisted"
      >
        active
      </span>
    );
  }
  return (
    <span
      className="rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-xs text-warning"
      data-testid="ensemble-votes-flag-badge"
      data-flag-state="observation-only"
      title="GENLAB_ENSEMBLE_PERSIST_ENABLED is off — vote persistence dormant"
    >
      observation only
    </span>
  );
}

function ComponentRow({ component }: { component: EnsembleComponentSummary }) {
  const total = component.voted + component.abstained;
  const pct = total === 0 ? 0 : Math.round(component.vote_rate * 100);
  // Any abstention is a health signal — the component is missing an
  // input feature on some fraction of decisions. Amber-tint the row
  // so an operator eyeballing the card can spot it immediately.
  const hasAbstentions = component.abstained > 0;
  return (
    <div
      className="grid grid-cols-[1fr,auto,auto] items-center gap-2 border-b border-border/40 py-1.5 text-xs"
      data-testid={`ensemble-votes-component-row-${component.component}`}
      data-has-abstentions={hasAbstentions ? "true" : "false"}
    >
      <span className="truncate font-mono text-[11px]" title={component.component}>
        {component.component}
      </span>
      <span className="font-mono text-[11px] text-text-muted">
        {component.voted}/{total}
      </span>
      <span
        className={
          hasAbstentions
            ? "rounded bg-amber-500/20 px-1.5 py-0.5 font-mono text-[10px] text-amber-400"
            : "rounded bg-emerald-500/15 px-1.5 py-0.5 font-mono text-[10px] text-emerald-400"
        }
        data-testid={`ensemble-votes-abstained-count-${component.component}`}
        title={
          hasAbstentions
            ? `${component.abstained} abstention(s) — component missing input on those decisions`
            : "No abstentions — component voted on every decision"
        }
      >
        {pct}%
      </span>
    </div>
  );
}

function RecommendationList({
  recommendations,
}: {
  recommendations: Record<string, number>;
}) {
  const entries = Object.entries(recommendations).sort(
    (a, b) => b[1] - a[1],
  );
  return (
    <div className="flex flex-col gap-0.5">
      {entries.map(([name, count]) => (
        <div
          key={name}
          className="grid grid-cols-[1fr,auto] items-center gap-2 py-0.5 text-xs"
          data-testid={`ensemble-votes-recommendation-${name}`}
        >
          <span className="truncate font-mono text-[11px]" title={name}>
            {name}
          </span>
          <span className="font-mono text-[11px] text-text-muted">
            {count}
          </span>
        </div>
      ))}
    </div>
  );
}

export function EnsembleVotesCard() {
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.ensembleVotes.summary(undefined, WINDOW_DAYS),
    queryFn: () => ensembleVotes.summary({ windowDays: WINDOW_DAYS }),
    // New decisions arrive continuously — 5-min poll keeps the card
    // fresh without hammering the endpoint. Matches other high-cadence
    // observability cards (FlagStateCard).
    refetchInterval: 5 * 60 * 1000,
    staleTime: 60 * 1000,
  });

  if (isLoading) {
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="text-sm font-semibold">Ensemble votes</div>
        <div className="mt-2 text-xs text-text-muted">Loading…</div>
      </div>
    );
  }

  // Migration-pending: the endpoint returns null when the
  // ``ensemble_votes`` table hasn't been created yet on the target DB.
  // Distinct from "0 decisions in window" — different fix path
  // (deploy migration vs wait for decisions to land).
  if (!data) {
    return (
      <div
        className="rounded-lg border border-border/60 bg-surface-1 p-3"
        data-testid="ensemble-votes-card"
      >
        <div className="mb-2">
          <div className="text-sm font-semibold">Ensemble votes</div>
          <div className="text-xs text-text-muted">
            Cross-niche vote summary · last {WINDOW_DAYS}d
          </div>
        </div>
        <div
          className="text-xs text-text-muted"
          data-testid="ensemble-votes-migration-pending"
        >
          Ensemble votes table not yet available — waiting for migration
        </div>
      </div>
    );
  }

  return (
    <div
      className="rounded-lg border border-border/60 bg-surface-1 p-3"
      data-testid="ensemble-votes-card"
    >
      <div className="mb-3 flex items-start justify-between gap-2">
        <div>
          <div className="text-sm font-semibold">Ensemble votes</div>
          <div className="text-xs text-text-muted">
            Last {data.window_days}d · {data.total_decisions} decisions
          </div>
        </div>
        {flagBadge(data.flag_enabled)}
      </div>

      {data.total_decisions === 0 ? (
        <div
          className="text-xs text-text-muted"
          data-testid="ensemble-votes-empty-state"
        >
          No decisions in the last {data.window_days} days
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <div>
            <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-text-muted">
              Per-component participation
            </div>
            <div>
              {data.components.map((c) => (
                <ComponentRow key={c.component} component={c} />
              ))}
            </div>
          </div>

          <div>
            <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-text-muted">
              Recommendation distribution
            </div>
            <RecommendationList recommendations={data.recommendations} />
          </div>
        </div>
      )}
    </div>
  );
}
