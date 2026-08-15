/**
 * Phase 5.D operator daily briefing card (2026-08-15).
 *
 * Runner fires 06:00 UTC → collects Mission Control state →
 * Anthropic Haiku writes 5 lines → row lands in operator_briefings
 * → this card renders it above every other card on the page.
 *
 * The card is intentionally the FIRST card on Mission Control:
 * it summarises everything below. Operator reads 5 lines + a "N
 * items need review" badge + drops into the drill-in card only
 * when the briefing calls out a specific one.
 */
import { useQuery } from "@tanstack/react-query";

import { operatorBriefings } from "@/api/client";
import { queryKeys } from "@/api/query-keys";

function _formatWhen(iso: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    const now = Date.now();
    const hours = Math.floor((now - d.getTime()) / (1000 * 60 * 60));
    if (hours < 1) return "just now";
    if (hours < 24) return `${hours}h ago`;
    return `${Math.floor(hours / 24)}d ago`;
  } catch {
    return iso;
  }
}

export function OperatorBriefingCard() {
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.operatorBriefings.latest(),
    queryFn: () => operatorBriefings.latest(),
    // Runner is daily; poll hourly is more than enough. Briefing
    // text is static once written.
    refetchInterval: 60 * 60 * 1000,
    staleTime: 60 * 60 * 1000,
  });

  if (isLoading) {
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="text-sm font-semibold">Operator briefing</div>
        <div className="mt-2 text-xs text-text-muted">Loading…</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="mb-2">
          <div className="text-sm font-semibold">Operator briefing</div>
          <div className="text-xs text-text-muted">
            Daily LLM-summary at 06:00 UTC — email + card
          </div>
        </div>
        <div className="text-xs text-text-muted">
          No briefing yet — runner has not fired.
        </div>
      </div>
    );
  }

  const totalPending =
    data.n_pending_flag_flips + data.n_pending_strategist_proposals;

  const emailBadge = data.email_sent ? (
    <span
      className="rounded border border-success/30 bg-success/10 px-1.5 py-0.5 text-xs text-success"
      title={`Delivered to ${data.email_recipient ?? "operator"}`}
    >
      email sent
    </span>
  ) : data.email_error ? (
    <span
      className="rounded border border-danger/30 bg-danger/10 px-1.5 py-0.5 text-xs text-danger"
      title={data.email_error}
    >
      email failed
    </span>
  ) : (
    <span
      className="rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-xs text-warning"
      title="GENLAB_OPERATOR_EMAIL not configured — card is the sole surface"
    >
      email skipped
    </span>
  );

  const reviewBadge = totalPending > 0 ? (
    <span
      className="rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-xs text-warning"
      title={`${data.n_pending_flag_flips} flag flips + ${data.n_pending_strategist_proposals} strategist proposals`}
    >
      {totalPending} to review
    </span>
  ) : (
    <span
      className="rounded border border-success/30 bg-success/10 px-1.5 py-0.5 text-xs text-success"
    >
      queue clear
    </span>
  );

  return (
    <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
      <div className="mb-2 flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold">
            Operator briefing {reviewBadge} {emailBadge}
          </div>
          <div className="text-xs text-text-muted">
            Generated {_formatWhen(data.generated_at)} · LLM cost $
            {data.llm_cost_usd.toFixed(4)}
          </div>
        </div>
      </div>
      <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-text-primary">
        {data.summary_md}
      </pre>
    </div>
  );
}
