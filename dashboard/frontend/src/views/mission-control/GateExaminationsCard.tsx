/**
 * Gate examinations diagnostic card (2026-07-23).
 *
 * Reveals WHICH of the 5 auto-approval-gate checks is the ratchet's
 * blocker per niche, and BY HOW MUCH. On prod 2026-07-23 the AUTO
 * #2 auto-approver examines but rejects nearly every blueprint
 * (composite_score ≥ 0.3 threshold rejects everything). Without
 * this card, operator sees `examined=1 approved=0 rejected=1` in
 * journalctl and doesn't know what to tune.
 *
 * Per-niche row shows:
 *   1. Approval rate (approved / examinations) as a small pill.
 *   2. Top failing check name — the tuning target.
 *   3. Current-vs-suggested threshold when the top check has raw
 *      score data captured in extra JSONB (composite_score /
 *      virality_score today; more can be enriched in the auto-
 *      approver wire without a card change).
 *   4. "n=" count so operator can tell "1 sample" from "50 samples"
 *      at a glance.
 *
 * Cold-start: table empty on fresh deploy. Card renders
 * "waiting for first examination" per row until the auto-approver
 * has fired at least once with the wire enabled.
 */
import { useQuery } from "@tanstack/react-query";

import { autoApproval } from "@/api/client";
import { getNicheInfo, NICHE_IDS, type NicheId } from "@/niches/registry";

export function GateExaminationsCard() {
  const { data, isLoading } = useQuery({
    queryKey: ["auto-approval-gate-examinations"],
    // 7-day rolling window. Auto-approver fires every 30 min in prod,
    // so ~336 fires/week = decent sample even at 1 examination/fire.
    queryFn: () => autoApproval.gateExaminations(7),
    // Poll every 2 min — data refreshes each auto-approver fire; a
    // faster poll wastes API calls.
    refetchInterval: 2 * 60 * 1000,
    staleTime: 60 * 1000,
    retry: false,
  });

  return (
    <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
      <div className="mb-2">
        <div className="text-sm font-semibold">Gate examinations</div>
        <div className="text-xs text-text-muted">
          Which AUTO #2 gate check is the ratchet blocker · rolling 7d
        </div>
      </div>

      {isLoading && !data ? (
        <div className="text-xs text-text-muted">Loading…</div>
      ) : !data ? (
        <div className="text-xs text-text-muted">
          Diagnostic layer not yet writing — first auto-approver fire
          will populate.
        </div>
      ) : (
        <div>
          {NICHE_IDS.map((n) => (
            <NicheRow key={n} nicheId={n as NicheId} row={data.niches?.[n]} />
          ))}
        </div>
      )}
    </div>
  );
}

interface NicheRowProps {
  nicheId: NicheId;
  row: import("@/api/client").GateExaminations | undefined;
}

function NicheRow({ nicheId, row }: NicheRowProps) {
  const info = getNicheInfo(nicheId);

  // No data path: card row still renders so operator sees an empty
  // slot for every niche (consistent grid).
  if (!row || row.examinations === 0) {
    return (
      <div className="flex items-center justify-between border-b border-border/40 py-1.5 text-xs">
        <span className="font-semibold" style={{ color: info.hex }}>
          {info.shortLabel}
        </span>
        <span className="text-text-muted">no examinations yet</span>
      </div>
    );
  }

  const approvalRate = Math.round(row.approval_rate * 100);
  const rateColor =
    approvalRate >= 50
      ? "text-success"
      : approvalRate >= 10
      ? "text-warning"
      : "text-error";

  return (
    <div className="border-b border-border/40 py-1.5 text-xs">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-semibold" style={{ color: info.hex }}>
            {info.shortLabel}
          </span>
          <span className={`font-mono ${rateColor}`}>
            {approvalRate}%
          </span>
          <span className="text-text-muted">
            ({row.approved}/{row.examinations})
          </span>
        </div>
        {row.top_failing_check && (
          <span
            className="font-mono text-text-muted"
            title={`Failed check counts: ${JSON.stringify(row.failed_check_counts)}`}
          >
            blocker: {row.top_failing_check}
          </span>
        )}
      </div>

      {/* Threshold-suggestion sub-row. Only renders when the top
          failing check has raw scores captured — composite_score /
          virality_score today. Confidence badge downgrades to "low"
          when n<5 so operator doesn't act on a percentile computed
          from 2 samples. */}
      {row.threshold_suggestion && (
        <div className="mt-1 flex items-center gap-2 text-[11px] text-text-muted">
          <span
            className={
              row.threshold_suggestion.confidence === "high"
                ? "rounded border border-success/30 bg-success/10 px-1.5 py-0.5 text-success"
                : row.threshold_suggestion.confidence === "medium"
                ? "rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-warning"
                : "rounded border border-border/40 bg-surface-2 px-1.5 py-0.5"
            }
            title={row.threshold_suggestion.rationale}
          >
            tune ({row.threshold_suggestion.confidence})
          </span>
          <span>
            {row.threshold_suggestion.check}:{" "}
            <span className="font-mono">
              {row.threshold_suggestion.current_threshold?.toFixed(3) ?? "—"}
            </span>{" "}
            →{" "}
            <span className="font-mono text-warning">
              {row.threshold_suggestion.suggested_threshold?.toFixed(3) ?? "—"}
            </span>{" "}
            <span
              title={`n=${row.threshold_suggestion.n_samples} rejected samples in window`}
            >
              (~{row.threshold_suggestion.weekly_unlock_estimate ?? "?"}/wk,{" "}
              {row.threshold_suggestion.would_unlock_count ?? 0} in window)
            </span>
          </span>
        </div>
      )}
    </div>
  );
}
