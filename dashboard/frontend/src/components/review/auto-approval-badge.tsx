/**
 * AUTO #1 (2026-06-13): Auto-approval gate verdict badge.
 *
 * Foundation for the owner's autonomous-agent vision. Surfaces the gate's
 * decision next to each blueprint in the Focus Review UI so operators
 * can A/B their own intuition against the gate before the opt-in switch
 * is flipped. Read-only — does NOT change blueprint state.
 *
 * Three visual states:
 *   - APPROVED (green): gate would auto-approve. Confidence shown.
 *   - REJECTED (red):   gate would reject. Failed checks shown in tooltip.
 *   - LOADING/ERROR:    null badge (silent) — never block the operator.
 */
import { useQuery } from "@tanstack/react-query";
import { blueprints, type AutoApprovalPreview } from "@/api/client";
import { StatusBadge } from "@/components/ui/status-badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface Props {
  blueprintId: string;
}

export function AutoApprovalBadge({ blueprintId }: Props) {
  const { data, isLoading, isError } = useQuery<AutoApprovalPreview>({
    queryKey: ["auto-approval-preview", blueprintId],
    queryFn: () => blueprints.autoApprovalPreview(blueprintId),
    // The verdict is deterministic for a given blueprint snapshot — no
    // need to refetch on focus. If the blueprint is edited, the parent
    // navigates away (different blueprintId) so the badge refetches
    // naturally.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    // Don't surface errors; absence of badge = unknown. The operator
    // makes their own decision either way.
    retry: false,
  });

  if (isLoading || isError || !data) return null;

  const confidencePct = Math.round(data.confidence * 100);
  const tooltipLines = data.reasons.slice(0, 6).join("\n");
  const headline = data.approved
    ? `WOULD AUTO-APPROVE · ${confidencePct}% confidence`
    : `WOULD REJECT · ${data.failed_checks.join(", ")}`;

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <StatusBadge
            status={data.approved ? "success" : "warning"}
            size="xs"
            icon={<span aria-hidden>{data.approved ? "✓" : "⚠"}</span>}
            srLabel={headline}
            data-testid="auto-approval-badge"
            data-approved={data.approved}
          >
            <span>{data.approved ? "would auto-approve" : "gate would reject"}</span>
            {data.approved && (
              <span className="opacity-70 ml-0.5 font-mono">{confidencePct}%</span>
            )}
          </StatusBadge>
        </TooltipTrigger>
        <TooltipContent side="bottom" className="max-w-md">
          <div className="space-y-1">
            <p className="font-semibold text-[11px]">{headline}</p>
            <p className="text-[10px] text-text-muted">
              Preview only — operator still decides. The "publish without
              manual approval" feature is opt-in (off by default).
            </p>
            {tooltipLines && (
              <pre className="text-[10px] font-mono leading-relaxed whitespace-pre-wrap pt-1 border-t border-border/30 mt-1">
                {tooltipLines}
              </pre>
            )}
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
