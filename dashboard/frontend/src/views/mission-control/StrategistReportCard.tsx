/**
 * PR Strategist-2b (2026-07-01) — Mission Control Strategist report card.
 *
 * Completes the Strategist trilogy dashboard visualization. Reads the
 * latest weekly LLM Strategist report per niche via
 * `/api/v1/strategist/reports/latest` and surfaces:
 *
 *   * Detected strategic phase (BOOTSTRAP / GROWTH / OPTIMIZE /
 *     MONETIZE / DEFEND) — the primary "where is this channel in its
 *     lifecycle?" signal the operator has to reason about
 *   * Proposal count + top proposal (with risk / urgency badges) so
 *     the operator sees WHAT the Strategist is suggesting without
 *     drilling in
 *   * "Review" affordance opening an inline accept/reject panel for
 *     each proposal
 *   * Weekly-summary text (LLM-generated, human-readable digest)
 *
 * ## Design decisions
 *
 * **Latest-per-niche vs unreviewed queue**: this card shows the LATEST
 * report per niche (5 total rows). A future PR can add a dedicated
 * "unreviewed queue" view that lists all reports across time — this
 * card's job is the operator's weekly at-a-glance.
 *
 * **Cold-start path**: when `latest()` returns null (persister has no
 * rows yet — first Strategist run hasn't fired for this niche), the
 * row shows "No Strategist report yet — first run pending" instead of
 * an empty state. Same defensive pattern as
 * `SponsorshipReadinessCard` / `AutoApprovalCalibrationCard`.
 *
 * **Weekly cadence**: Strategist runs Sundays 02:00 UTC (see
 * `deploy/systemd-phase2/genlab-strategist.timer`). We poll every
 * 5 minutes — much longer than most cards because the underlying
 * data changes at most once a week, and hitting the LLM-persister
 * endpoint faster is pointless.
 *
 * **Sibling cards** — same visual language as SponsorshipReadinessCard
 * / BanditHourHeatmap / AutoApprovalCalibrationCard: 5 niche rows,
 * badge-driven state, tooltip on hover, inline actions.
 */
import { useState } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { toast } from "sonner";

import { strategist } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type {
  ProposalRisk,
  ProposalUrgency,
  StrategicPhase,
  StrategistProposal,
  StrategistReport,
} from "@/api/types";
import { NICHE_IDS, getNicheInfo, type NicheId } from "@/niches/registry";

// Phase → display label. When the Python enum grows (a 6th phase
// added), unknowns fall through to the raw string — no crash.
const PHASE_LABEL: Record<StrategicPhase, string> = {
  BOOTSTRAP: "Bootstrap",
  GROWTH: "Growth",
  OPTIMIZE: "Optimize",
  MONETIZE: "Monetize",
  DEFEND: "Defend",
};

// Phase → color. Follows the sponsorship-tier / bandit-hour heatmap
// convention: cool blues for early stages, warm success for late.
const PHASE_COLOR: Record<StrategicPhase, string> = {
  BOOTSTRAP: "bg-text-muted/10 text-text-muted border-text-muted/30",
  GROWTH: "bg-info/10 text-info border-info/30",
  OPTIMIZE: "bg-warning/10 text-warning border-warning/30",
  MONETIZE: "bg-success/10 text-success border-success/30",
  DEFEND: "bg-danger/10 text-danger border-danger/30",
};

const RISK_COLOR: Record<ProposalRisk, string> = {
  low: "text-success",
  medium: "text-warning",
  high: "text-danger",
};

const URGENCY_LABEL: Record<ProposalUrgency, string> = {
  ship_now: "Ship now",
  this_week: "This week",
  next_sprint: "Next sprint",
};

/** Format an ISO datetime as "3d ago" / "5h ago" — the same format
 *  the calibration-card / sponsorship-card use for their "last event"
 *  timestamps. Falls back to the raw string on parse failure. */
function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function TopProposalRow({ proposal }: { proposal: StrategistProposal }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="rounded border border-border/50 bg-surface-2 px-1.5 py-0.5 font-mono">
        {proposal.type}
      </span>
      <span className={`font-semibold ${RISK_COLOR[proposal.risk] ?? ""}`}>
        {proposal.risk}
      </span>
      <span className="text-text-muted">
        {URGENCY_LABEL[proposal.urgency] ?? proposal.urgency}
      </span>
      <span className="truncate text-text-muted" title={proposal.target}>
        {proposal.target}
      </span>
    </div>
  );
}

function NicheRow({ nicheId }: { nicheId: NicheId }) {
  const info = getNicheInfo(nicheId);
  const queryClient = useQueryClient();
  const [reviewOpen, setReviewOpen] = useState(false);
  const [acceptedIdxs, setAcceptedIdxs] = useState<Set<number>>(new Set());
  const [rejectedIdxs, setRejectedIdxs] = useState<Set<number>>(new Set());

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.strategist.latest(nicheId),
    queryFn: () => strategist.latest(nicheId),
    // 2026-07-14: 30min poll (was 5min). Strategist runs weekly
    // (Sunday 02:00 UTC); 5min was 2016× overpoll per refresh cycle
    // + hit the LLM-persister-heavy read path on every tick. 30min
    // is still overkill vs actual cadence but matches operator-refresh
    // rhythm without hammering the endpoint.
    refetchInterval: 30 * 60 * 1000,
    staleTime: 30 * 60 * 1000,
  });

  const reviewMutation = useMutation({
    mutationFn: (report: StrategistReport) =>
      strategist.review(report.id, {
        accepted_proposal_indices: Array.from(acceptedIdxs).sort(),
        rejected_proposal_indices: Array.from(rejectedIdxs).sort(),
      }),
    onSuccess: () => {
      toast.success(`Strategist review recorded for ${info.shortLabel}`);
      setReviewOpen(false);
      setAcceptedIdxs(new Set());
      setRejectedIdxs(new Set());
      queryClient.invalidateQueries({
        queryKey: queryKeys.strategist.latest(nicheId),
      });
    },
    onError: (err) =>
      toast.error(
        `Review failed: ${err instanceof Error ? err.message : String(err)}`,
      ),
  });

  const toggleAccept = (idx: number) => {
    setAcceptedIdxs((s) => {
      const next = new Set(s);
      if (next.has(idx)) next.delete(idx);
      else {
        next.add(idx);
        // Auto-uncheck the reject if you're accepting — a proposal
        // can't be both accepted and rejected. Same pattern the
        // /review server endpoint enforces logically.
        setRejectedIdxs((r) => {
          const rn = new Set(r);
          rn.delete(idx);
          return rn;
        });
      }
      return next;
    });
  };

  const toggleReject = (idx: number) => {
    setRejectedIdxs((s) => {
      const next = new Set(s);
      if (next.has(idx)) next.delete(idx);
      else {
        next.add(idx);
        setAcceptedIdxs((a) => {
          const an = new Set(a);
          an.delete(idx);
          return an;
        });
      }
      return next;
    });
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-between border-b border-border/40 py-2 text-sm">
        <span className="font-semibold" style={{ color: info.color }}>
          {info.shortLabel}
        </span>
        <span className="text-xs text-text-muted">Loading…</span>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex items-center justify-between border-b border-border/40 py-2 text-sm">
        <span className="font-semibold" style={{ color: info.color }}>
          {info.shortLabel}
        </span>
        <span className="text-xs text-text-muted">
          No Strategist report yet — first run pending
        </span>
      </div>
    );
  }

  const topProposal = data.proposals[0];
  return (
    <div className="border-b border-border/40 py-2">
      <div className="flex items-center justify-between gap-2 text-sm">
        <div className="flex flex-1 items-center gap-2">
          <span className="font-semibold" style={{ color: info.color }}>
            {info.shortLabel}
          </span>
          <span
            className={`rounded border px-1.5 py-0.5 text-xs font-medium ${
              PHASE_COLOR[data.detected_phase] ?? "text-text-muted"
            }`}
            title={data.phase_evidence}
          >
            {PHASE_LABEL[data.detected_phase] ?? data.detected_phase}
          </span>
          <span className="text-xs text-text-muted">
            {data.proposals.length} proposal
            {data.proposals.length === 1 ? "" : "s"} · {relativeTime(data.run_at)}
          </span>
        </div>
        {data.proposals.length > 0 && (
          <button
            onClick={() => setReviewOpen((v) => !v)}
            className="rounded border border-border/60 bg-surface-2 px-2 py-0.5 text-xs hover:bg-surface-3"
          >
            {reviewOpen ? "Close" : "Review"}
          </button>
        )}
      </div>

      {topProposal && !reviewOpen && (
        <div className="ml-2 mt-1">
          <TopProposalRow proposal={topProposal} />
        </div>
      )}

      {reviewOpen && (
        <div className="mt-2 space-y-2 rounded border border-border/50 bg-surface-2 p-2">
          <div className="text-xs text-text-muted">{data.weekly_summary}</div>
          {data.proposals.map((prop, idx) => (
            <div
              key={idx}
              className="rounded border border-border/30 bg-surface-1 p-2"
            >
              <TopProposalRow proposal={prop} />
              <div className="mt-1 text-xs text-text-primary">
                <div className="font-medium">Reasoning</div>
                <div className="text-text-muted">{prop.reasoning}</div>
              </div>
              <div className="mt-1 text-xs">
                <div className="font-medium">Expected impact</div>
                <div className="text-text-muted">{prop.expected_impact}</div>
              </div>
              <div className="mt-1 flex gap-2">
                <button
                  onClick={() => toggleAccept(idx)}
                  className={`rounded border px-2 py-0.5 text-xs ${
                    acceptedIdxs.has(idx)
                      ? "border-success bg-success/10 text-success"
                      : "border-border/50 text-text-muted hover:bg-surface-3"
                  }`}
                >
                  Accept
                </button>
                <button
                  onClick={() => toggleReject(idx)}
                  className={`rounded border px-2 py-0.5 text-xs ${
                    rejectedIdxs.has(idx)
                      ? "border-danger bg-danger/10 text-danger"
                      : "border-border/50 text-text-muted hover:bg-surface-3"
                  }`}
                >
                  Reject
                </button>
              </div>
            </div>
          ))}
          <div className="flex justify-end gap-2 pt-1">
            <button
              disabled={
                reviewMutation.isPending ||
                (acceptedIdxs.size === 0 && rejectedIdxs.size === 0)
              }
              onClick={() => reviewMutation.mutate(data)}
              className="rounded border border-info bg-info/10 px-2 py-0.5 text-xs text-info disabled:opacity-40"
            >
              {reviewMutation.isPending ? "Submitting…" : "Submit review"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export function StrategistReportCard() {
  return (
    <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
      <div className="mb-2 flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold">Strategist reports</div>
          <div className="text-xs text-text-muted">
            Weekly LLM analyst · Sundays 02:00 UTC · Accept or reject
            proposals to steer the strategy
          </div>
        </div>
      </div>
      <div>
        {NICHE_IDS.map((n) => (
          <NicheRow key={n} nicheId={n} />
        ))}
      </div>
    </div>
  );
}
