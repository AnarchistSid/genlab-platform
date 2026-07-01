/**
 * Intervention 6 badge (2026-07-01) — ensemble decision surface.
 *
 * Companion to :file:`auto-approval-badge.tsx`. Reads the ``ensemble``
 * payload from ``/api/v1/blueprints/<id>/auto-approval-preview`` and
 * surfaces the ensemble's ``recommendation`` next to the rule-gate
 * badge on Focus Review.
 *
 * Why a separate badge (not extending AutoApprovalBadge)
 * ======================================================
 *
 * The two verdicts are INDEPENDENT: the rule gate is a fast heuristic
 * over 5 checks; the ensemble is a weighted vote across the bandit,
 * hook classifier, Bayesian gate, conformal router, and (optionally)
 * an LLM judge. Combining them into one badge would collapse the
 * disagreement signal — the operator would lose the ability to see
 * "rule gate says approve, ensemble says worth-your-look because
 * three components disagree."
 *
 * When to render
 * ==============
 *
 *   * ``recommendation === "worth_your_look"``  → prominent amber badge.
 *     This is THE signal the ensemble was built to surface — it means
 *     the components disagree with each other enough that the rule
 *     gate's confident-wrong risk is elevated. Operator should look.
 *   * ``recommendation === "auto_approve"``  → subtle green badge.
 *     Redundant with the rule gate on happy path but confirms
 *     agreement.
 *   * ``recommendation === "auto_reject"``  → subtle red badge. Same
 *     idea — confirming disagreement with the rule gate is out of
 *     scope for this badge, that would need its own compare view.
 *   * ``recommendation === "disabled" | "insufficient_data" | "error"``
 *     → NO badge. Ensemble is off / not enough data / broke silently;
 *     the operator's UI shouldn't be cluttered with "not applicable"
 *     signals.
 */
import { blueprints, type AutoApprovalPreview } from "@/api/client";
import { StatusBadge } from "@/components/ui/status-badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useQuery } from "@tanstack/react-query";

interface Props {
  blueprintId: string;
}

// Recommendation → status color. The status-badge component maps
// "warning" → amber, "success" → green, "error"/"danger" → red.
const RECOMMENDATION_STATUS: Record<
  string,
  "warning" | "success" | "error" | null
> = {
  worth_your_look: "warning",
  auto_approve: "success",
  auto_reject: "error",
  // These four all → null (no badge rendered). Explicit rather than
  // implicit-default so a future recommendation label added to the
  // Python enum is easy to route.
  route_to_operator: null,
  disabled: null,
  insufficient_data: null,
  error: null,
};

const HEADLINES: Record<string, string> = {
  worth_your_look: "ENSEMBLE: worth your look",
  auto_approve: "ENSEMBLE: auto-approve",
  auto_reject: "ENSEMBLE: auto-reject",
};

const BODY_COPY: Record<string, string> = {
  worth_your_look:
    "Components disagree — the rule gate's confident-wrong risk is elevated here. This is why the ensemble exists.",
  auto_approve:
    "All voting components agree on approve. Ensemble confirms the rule gate's verdict.",
  auto_reject:
    "All voting components agree on reject. Ensemble confirms the rule gate's verdict.",
};

export function EnsembleBadge({ blueprintId }: Props) {
  const { data, isLoading, isError } = useQuery<AutoApprovalPreview>({
    queryKey: ["auto-approval-preview", blueprintId],
    queryFn: () => blueprints.autoApprovalPreview(blueprintId),
    // Deterministic verdict per snapshot — no need to refetch on
    // focus. Same policy as AutoApprovalBadge (they share the
    // cache key so both badges hit the same request).
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  });

  if (isLoading || isError || !data) return null;
  const ens = data.ensemble;
  if (!ens) return null;
  const status = RECOMMENDATION_STATUS[ens.recommendation] ?? null;
  if (status === null) return null;

  const scorePct =
    ens.score !== null ? `${Math.round(ens.score * 100)}%` : "—";
  const disagreementPct = `${Math.round(ens.disagreement * 100)}%`;
  const headline = HEADLINES[ens.recommendation] ?? ens.recommendation;
  const bodyCopy = BODY_COPY[ens.recommendation] ?? "";

  // Per-component vote lines for the tooltip. Truncate reasons at
  // 40 chars to keep the tooltip narrow on wide monitors.
  const voteLines = ens.votes
    .filter((v) => v.score !== null || v.reason)
    .map((v) => {
      const scoreStr =
        v.score !== null ? `${(v.score * 100).toFixed(0)}%` : "abstain";
      const reason = v.reason.length > 40 ? `${v.reason.slice(0, 40)}…` : v.reason;
      return `${v.component.padEnd(18)} ${scoreStr.padStart(8)}  ${reason}`;
    })
    .join("\n");

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <StatusBadge
            status={status}
            size="xs"
            icon={<span aria-hidden>Σ</span>}
            srLabel={headline}
            data-testid="ensemble-badge"
            data-recommendation={ens.recommendation}
          >
            <span>{ens.recommendation.replace(/_/g, " ")}</span>
            <span className="opacity-70 ml-0.5 font-mono">{scorePct}</span>
          </StatusBadge>
        </TooltipTrigger>
        <TooltipContent side="bottom" className="max-w-md">
          <div className="space-y-1">
            <p className="font-semibold text-[11px]">{headline}</p>
            <p className="text-[10px] text-text-muted">{bodyCopy}</p>
            <p className="text-[10px] text-text-muted">
              Score {scorePct} · Disagreement {disagreementPct} ·{" "}
              {ens.n_voters} voter{ens.n_voters === 1 ? "" : "s"}
            </p>
            {voteLines && (
              <pre className="text-[10px] font-mono leading-relaxed whitespace-pre-wrap pt-1 border-t border-border/30 mt-1">
                {voteLines}
              </pre>
            )}
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
