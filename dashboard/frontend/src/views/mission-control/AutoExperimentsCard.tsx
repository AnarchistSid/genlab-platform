/**
 * Auto-experiments observability card (#9 lifecycle, 2026-07-23).
 *
 * Surfaces the OUTPUT of the auto-experiment lifecycle:
 *
 *   strategist -> testable_prediction
 *   parser     -> queues pending experiment
 *   lifecycle  -> pending -> running -> completed with reward measurement
 *
 * Card shows:
 *   1. Active-state badge (matches other lifecycle cards)
 *   2. Queue counts (pending / running / completed) — operator's
 *      "is the loop moving?" signal.
 *   3. Verdict tally (last 30d) — did the strategist's hypotheses
 *      hold up when measured?
 *   4. Top-3 most-recent completed rows with per-arm lift + verdict
 *      pill so the operator can see WHAT was tested and HOW it went
 *      without leaving Mission Control.
 *
 * Cold-start: parser is credit-gated on Anthropic + LLM judge; the
 * queue can be empty for weeks after initial rollout. Card shows
 * "No experiments yet — parser hasn't queued any" rather than an
 * error state.
 */
import { useQuery } from "@tanstack/react-query";

import { autoExperiments } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { AutoExperimentRow } from "@/api/types";

function ActiveStateBadge({ active }: { active: boolean }) {
  return active ? (
    <span
      className="rounded border border-success/30 bg-success/10 px-1.5 py-0.5 text-[10px] text-success"
      title="GENLAB_AUTO_EXPERIMENT_ENABLED is on — lifecycle timer is measuring reward per completed experiment"
    >
      active
    </span>
  ) : (
    <span
      className="rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-[10px] text-warning"
      title="GENLAB_AUTO_EXPERIMENT_ENABLED off — flag-gated OFF; lifecycle exits before touching DB"
    >
      observation only
    </span>
  );
}

/** Compact per-experiment row. Renders differently by status so the
 *  operator can scan for verdicts vs pending queue. */
function ExperimentRow({ row }: { row: AutoExperimentRow }) {
  const arms = (row.spec?.arms ?? []).slice(0, 2);
  const armSummary = arms.length ? arms.join(" vs ") : "(no arms)";
  const created = row.created_at?.slice(0, 10) ?? "—";

  if (row.status === "completed" && row.result) {
    const met = !!row.result.met_threshold;
    const suff = !!row.result.sufficient_samples;
    const lift = row.result.observed_lift;
    const verdictPill = !suff ? (
      <span
        className="rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-[10px] text-warning"
        title="Fewer than min_samples_required per arm — verdict inconclusive"
      >
        low n
      </span>
    ) : met ? (
      <span
        className="rounded border border-success/30 bg-success/10 px-1.5 py-0.5 text-[10px] text-success"
        title="observed_lift >= expected_metric_shift AND sufficient samples per arm"
      >
        met
      </span>
    ) : (
      <span
        className="rounded border border-error/30 bg-error/10 px-1.5 py-0.5 text-[10px] text-error"
        title="Sufficient samples but lift did not meet threshold"
      >
        unmet
      </span>
    );
    return (
      <div className="flex items-center justify-between border-b border-border/40 py-1.5 text-xs">
        <div className="flex items-center gap-2">
          {verdictPill}
          <span className="font-mono text-text-muted">{armSummary}</span>
        </div>
        <div className="flex items-center gap-3 text-text-muted">
          <span title="observed_lift = treatment - control">
            lift={lift !== null && lift !== undefined ? lift.toFixed(3) : "—"}
          </span>
          <span>{created}</span>
        </div>
      </div>
    );
  }

  const statusPill = (
    <span
      className="rounded border border-border/40 bg-surface-2 px-1.5 py-0.5 text-[10px] text-text-muted"
      title={
        row.status === "pending"
          ? "Queued by parser; lifecycle will start on next fire"
          : "Started; lifecycle will measure at duration expiry"
      }
    >
      {row.status}
    </span>
  );
  return (
    <div className="flex items-center justify-between border-b border-border/40 py-1.5 text-xs">
      <div className="flex items-center gap-2">
        {statusPill}
        <span className="font-mono text-text-muted">{armSummary}</span>
      </div>
      <span className="text-text-muted">{created}</span>
    </div>
  );
}

export function AutoExperimentsCard() {
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.autoExperiments.summary("all", 5),
    queryFn: () => autoExperiments.summary("all", 5),
    // Lifecycle timer fires every 6h; poll every 5 min is plenty and
    // matches the strategist card cadence.
    refetchInterval: 5 * 60 * 1000,
    staleTime: 60 * 1000,
  });

  if (isLoading) {
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="text-sm font-semibold">Auto-experiments</div>
        <div className="mt-2 text-xs text-text-muted">Loading…</div>
      </div>
    );
  }

  if (!data) {
    // Fail-open on server side returns data=null — treat as cold-start.
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="mb-1 flex items-center gap-2">
          <div className="text-sm font-semibold">Auto-experiments</div>
        </div>
        <div className="text-xs text-text-muted">
          No experiments yet — parser hasn't queued any (LLM parser + Anthropic
          credit both need to be live)
        </div>
      </div>
    );
  }

  const { counts, verdicts_last_30d, recent, active_state } = data;
  const total =
    verdicts_last_30d.met_threshold +
    verdicts_last_30d.unmet_threshold +
    verdicts_last_30d.insufficient_samples;

  return (
    <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
      <div className="mb-2 flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <div className="text-sm font-semibold">Auto-experiments</div>
            <ActiveStateBadge active={active_state === "active"} />
          </div>
          <div className="text-xs text-text-muted">
            Strategist testable_predictions -&gt; measured reward. Lifecycle
            timer every 6h (:20 UTC).
          </div>
        </div>
      </div>

      <div className="mb-3 flex items-center gap-3 text-xs text-text-muted">
        <span title="Queue depth by status">
          <span className="font-mono text-text">{counts.pending}</span> pending
          {" · "}
          <span className="font-mono text-text">{counts.running}</span> running
          {" · "}
          <span className="font-mono text-text">{counts.completed}</span>{" "}
          completed
        </span>
      </div>

      {total > 0 && (
        <div className="mb-3 flex items-center gap-3 text-xs">
          <span className="text-text-muted">Last 30d:</span>
          <span
            className="text-success"
            title="Predictions the system CONFIRMED via measurement"
          >
            met {verdicts_last_30d.met_threshold}
          </span>
          <span className="text-error" title="Predictions the system REFUTED">
            unmet {verdicts_last_30d.unmet_threshold}
          </span>
          <span
            className="text-warning"
            title="Sample size too small (< 5 per arm) to draw a verdict"
          >
            low-n {verdicts_last_30d.insufficient_samples}
          </span>
        </div>
      )}

      <div className="border-t border-border/40 pt-2">
        <div className="mb-1 text-[11px] uppercase tracking-wide text-text-muted">
          Recent
        </div>
        {recent.length === 0 ? (
          <div className="py-2 text-xs text-text-muted">
            No experiments yet
          </div>
        ) : (
          recent.map((r) => <ExperimentRow key={r.id} row={r} />)
        )}
      </div>
    </div>
  );
}
