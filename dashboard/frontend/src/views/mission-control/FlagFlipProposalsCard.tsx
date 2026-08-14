/**
 * Phase 5.C session 2 observability + operator-override card
 * (2026-08-14) — pending autonomous flag-flip proposals.
 *
 * The autonomous_flag_manager runner writes proposals here after
 * evaluating rollout/enablement evidence gates. Each proposal ages
 * for the configured override window (default 24h) at which point
 * the runner's --apply pass writes the new value to /opt/genlab/.env
 * and marks the proposal 'applied'.
 *
 * Operator override: clicking Reject flips the row to status
 * 'rejected' with the supplied reason so it never auto-applies.
 *
 * Sibling of AutoApprovalCalibrationCard (AUTO #1c) and
 * AutonomousReviewerStatusCard — same active-vs-observation flag
 * pattern; here the flag *is* the subject.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { flagFlipProposals } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { FlagFlipProposal } from "@/api/types";

function ProposalRow({
  row,
  onReject,
  isRejecting,
}: {
  row: FlagFlipProposal;
  onReject: (id: string, reason: string) => void;
  isRejecting: boolean;
}) {
  const applyBadge = row.auto_apply_eligible ? (
    <span
      className="rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-xs text-warning"
      title="Age >= override window AND confidence >= threshold. Next --apply pass will write .env."
    >
      apply eligible
    </span>
  ) : (
    <span
      className="rounded border border-border/40 px-1.5 py-0.5 text-xs text-text-muted"
      title={`Age ${row.age_hours.toFixed(1)}h — auto-apply in ${row.hours_until_auto_apply.toFixed(1)}h`}
    >
      in {row.hours_until_auto_apply.toFixed(1)}h
    </span>
  );

  return (
    <div className="border-b border-border/40 py-2 text-xs">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <span className="font-mono font-medium">{row.flag_name}</span>
            {applyBadge}
          </div>
          <div className="mt-0.5 font-mono text-text-muted">
            {row.from_state} → {row.to_state} · confidence{" "}
            {(row.confidence * 100).toFixed(0)}%
          </div>
          <div className="mt-0.5 text-text-muted">{row.rationale}</div>
        </div>
        <button
          type="button"
          className="rounded border border-danger/40 bg-danger/10 px-2 py-1 text-xs text-danger hover:bg-danger/20 disabled:opacity-50"
          disabled={isRejecting}
          onClick={() => {
            const reason = window.prompt(
              `Reject flip of ${row.flag_name}?\nReason:`,
              "",
            );
            if (reason && reason.trim()) {
              onReject(row.id, reason.trim());
            }
          }}
          title="Override — mark proposal rejected so it never auto-applies"
        >
          Reject
        </button>
      </div>
    </div>
  );
}

export function FlagFlipProposalsCard() {
  const qc = useQueryClient();
  const [feedback, setFeedback] = useState<string | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.flagFlipProposals.pending(),
    queryFn: () => flagFlipProposals.pending(),
    // Runner is daily; 15-min poll is enough to catch fresh
    // proposals + tick the countdown to auto-apply.
    refetchInterval: 15 * 60 * 1000,
    staleTime: 15 * 60 * 1000,
  });

  const rejectMutation = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      flagFlipProposals.reject(id, reason),
    onSuccess: () => {
      setFeedback("Rejected — proposal will not auto-apply.");
      qc.invalidateQueries({
        queryKey: queryKeys.flagFlipProposals.pending(),
      });
    },
    onError: (err: Error) => {
      setFeedback(`Reject failed: ${err.message}`);
    },
  });

  if (isLoading) {
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="text-sm font-semibold">Autonomous flag flips</div>
        <div className="mt-2 text-xs text-text-muted">Loading…</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="mb-2">
          <div className="text-sm font-semibold">Autonomous flag flips</div>
          <div className="text-xs text-text-muted">
            Proposer runs daily 07:00 UTC · auto-applies after 24h
          </div>
        </div>
        <div className="text-xs text-text-muted">
          No pending proposals — evidence gates not met yet.
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
      <div className="mb-2 flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold">Autonomous flag flips</div>
          <div className="text-xs text-text-muted">
            Override window {data.override_window_hours}h · min confidence{" "}
            {(data.confidence_threshold * 100).toFixed(0)}%
          </div>
        </div>
      </div>
      {feedback && (
        <div className="mb-2 text-xs text-text-muted">{feedback}</div>
      )}
      {data.rows.length === 0 ? (
        <div className="text-xs text-text-muted">
          No pending proposals — evidence gates not met yet.
        </div>
      ) : (
        <div>
          {data.rows.map((row) => (
            <ProposalRow
              key={row.id}
              row={row}
              isRejecting={rejectMutation.isPending}
              onReject={(id, reason) =>
                rejectMutation.mutate({ id, reason })
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}
