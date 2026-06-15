/**
 * D2.6 (2026-06-15, AUTO #2 runbook): ScoringExplainer panel.
 *
 * Shows the component-by-component breakdown of how the AutoApprovalGate
 * arrived at its verdict for a blueprint. The badge alone tells the
 * operator "would auto-approve · 78%" — this panel answers "why?".
 *
 * Five rows (one per gate check):
 *   has_video, has_hook, qc_passed, composite_score, virality_score
 *
 * For each: PASS/FAIL/UNKNOWN pill + raw value (where available) +
 * the threshold the check is comparing against.
 *
 * Read-only — never mutates blueprint state. Shares the
 * /api/v1/blueprints/<id>/auto-approval-preview endpoint with the
 * AutoApprovalBadge, so loading the panel is a no-op cache hit when
 * the badge is already mounted.
 */
import { useQuery } from "@tanstack/react-query";
import { blueprints, type AutoApprovalPreview } from "@/api/client";
import { Check, X, HelpCircle, ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

interface Props {
  blueprintId: string;
}

// The gate's defaults, mirrored from
// genlab_core/scheduling/auto_approval_gate.py — keep in lockstep.
// Surface them in the panel so operators see what threshold drove the
// FAIL verdict.
const COMPOSITE_MIN = 0.3;
const VIRALITY_MIN = 0.05;

type CheckRow = {
  key: string;
  label: string;
  status: "pass" | "fail" | "unknown";
  rawDisplay: string;
  thresholdHint: string;
};

function buildRows(p: AutoApprovalPreview): CheckRow[] {
  const passed = new Set(p.passed_checks);
  const failed = new Set(p.failed_checks);
  const m = p.raw_metrics;

  const status = (key: string): CheckRow["status"] => {
    if (passed.has(key)) return "pass";
    if (failed.has(key)) return "fail";
    return "unknown";
  };

  const num = (v: number | null | undefined, places = 2): string => {
    if (v === null || v === undefined) return "—";
    return v.toFixed(places);
  };

  return [
    {
      key: "has_video",
      label: "Has video",
      status: status("has_video"),
      rawDisplay: m === undefined ? "—" : m.has_video ? "yes" : "no",
      thresholdHint: "must have at least one visual_paths entry",
    },
    {
      key: "has_hook",
      label: "Has hook",
      status: status("has_hook"),
      rawDisplay: m === undefined ? "—" : m.has_hook ? "yes" : "no",
      thresholdHint: "hook OR title must be non-empty",
    },
    {
      key: "qc_passed",
      label: "QC passed",
      status: status("qc_passed"),
      rawDisplay:
        m === undefined ? "—" : m.validation_status ?? "unset",
      thresholdHint: "validation_status must be 'passed' or absent",
    },
    {
      key: "composite_score",
      label: "Composite score",
      status: status("composite_score"),
      rawDisplay: num(m?.composite_score),
      thresholdHint: `≥ ${COMPOSITE_MIN.toFixed(2)} (or unset for cold-start)`,
    },
    {
      key: "virality_score",
      label: "Virality score",
      status: status("virality_score"),
      rawDisplay: num(m?.virality_score),
      thresholdHint: `≥ ${VIRALITY_MIN.toFixed(2)} (or unset for cold-start)`,
    },
  ];
}

function StatusPill({ status }: { status: CheckRow["status"] }) {
  const className =
    status === "pass"
      ? "bg-green-700/30 text-green-300 border-green-700/50"
      : status === "fail"
      ? "bg-red-700/30 text-red-300 border-red-700/50"
      : "bg-zinc-700/40 text-zinc-400 border-zinc-700/60";
  const Icon = status === "pass" ? Check : status === "fail" ? X : HelpCircle;
  const text = status === "pass" ? "PASS" : status === "fail" ? "FAIL" : "?";
  return (
    <span
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-mono font-semibold rounded border ${className}`}
    >
      <Icon className="h-2.5 w-2.5" />
      {text}
    </span>
  );
}

export function ScoringExplainer({ blueprintId }: Props) {
  const [open, setOpen] = useState(false);

  const { data, isLoading, isError } = useQuery<AutoApprovalPreview>({
    queryKey: ["auto-approval-preview", blueprintId],
    queryFn: () => blueprints.autoApprovalPreview(blueprintId),
    // Same cache contract as AutoApprovalBadge — verdict is deterministic
    // for a snapshot.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  });

  if (isLoading || isError || !data) {
    // Silent — the panel is supplementary. Surface nothing rather than
    // a placeholder that would clutter the detail view.
    return null;
  }

  const rows = buildRows(data);
  const confidencePct = Math.round(data.confidence * 100);
  const summary = data.approved
    ? `Gate would APPROVE · ${confidencePct}% confidence`
    : `Gate would REJECT · ${data.failed_checks.length} check(s) failed`;

  return (
    <div
      className="rounded border border-border/40 bg-bg-elevated/40"
      data-testid="scoring-explainer"
    >
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-3 py-2 text-left hover:bg-bg-elevated/70"
        aria-expanded={open}
      >
        <span className="text-xs font-semibold">
          <span className={data.approved ? "text-green-400" : "text-amber-400"}>
            {summary}
          </span>
        </span>
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 text-text-muted" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-text-muted" />
        )}
      </button>

      {open && (
        <div className="border-t border-border/30 px-3 py-2">
          <table className="w-full text-[11px]">
            <thead className="text-text-muted">
              <tr>
                <th className="text-left font-normal pb-1">Check</th>
                <th className="text-left font-normal pb-1">Status</th>
                <th className="text-left font-normal pb-1">Value</th>
                <th className="text-left font-normal pb-1">Threshold</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key} className="border-t border-border/20">
                  <td className="py-1.5 font-medium">{r.label}</td>
                  <td className="py-1.5">
                    <StatusPill status={r.status} />
                  </td>
                  <td className="py-1.5 font-mono text-text-muted">
                    {r.rawDisplay}
                  </td>
                  <td className="py-1.5 text-[10px] text-text-muted">
                    {r.thresholdHint}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {data.reasons.length > 0 && (
            <div className="pt-2 mt-2 border-t border-border/20">
              <div className="text-[10px] uppercase tracking-wide text-text-muted mb-1">
                Gate reasons
              </div>
              <ul className="text-[11px] space-y-0.5 list-disc list-inside">
                {data.reasons.map((r, i) => (
                  <li key={i} className="text-text-muted">
                    {r}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <p className="text-[10px] text-text-muted pt-2 mt-2 border-t border-border/20">
            Preview only — operator decision is what publishes.
          </p>
        </div>
      )}
    </div>
  );
}
