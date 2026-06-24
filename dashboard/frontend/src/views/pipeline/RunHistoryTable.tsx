import { useState, Fragment } from "react";
import { ChevronRight, CheckCircle2, XCircle, Clock, Zap, BarChart2, Youtube } from "lucide-react";
import { NICHE_COLORS } from "@/niches/registry";
import type { PipelineRunDetail } from "@/hooks/use-pipeline-monitor";

interface Props {
  runs: PipelineRunDetail[];
  isLoading: boolean;
}

// Extra fields that may exist on a run from the raw run_report
interface RunExtra {
  arm_id?: string;
  hooks_generated?: number;
  banned_blocked?: number;
  dupes_rejected?: number;
  yt_quota_used?: number;
  yt_quota_limit?: number;
  blueprints_count?: number;
  stories_count?: number;
}

function ContentQualityPanel({ run }: { run: PipelineRunDetail & RunExtra }) {
  const hasQuality =
    run.arm_id ||
    run.hooks_generated != null ||
    run.banned_blocked != null ||
    run.dupes_rejected != null ||
    run.blueprints_count != null;

  const hasQuota = run.yt_quota_used != null;

  if (!hasQuality && !hasQuota) {
    return (
      <div className="text-xs text-text-muted italic pt-1">
        Quality stats available in next run
      </div>
    );
  }

  return (
    <div className="flex flex-wrap gap-4 pt-1 border-t border-border mt-1">
      {/* Arm ID */}
      {run.arm_id && (
        <div className="flex items-center gap-1">
          <Zap className="size-3 text-text-muted" />
          <span className="text-xs text-text-muted">
            Arm:
          </span>
          <span className="text-xs font-mono text-text-secondary bg-bg-elevated px-1.5 rounded-sm">
            {run.arm_id}
          </span>
        </div>
      )}

      {/* Content Quality */}
      {hasQuality && (
        <div className="flex items-center gap-2">
          <BarChart2 className="size-3 text-text-muted" />
          <span className="text-xs text-text-muted">
            Quality:
          </span>
          {run.hooks_generated != null && (
            <span className="text-xs text-success">
              {run.hooks_generated} hooks
            </span>
          )}
          {run.blueprints_count != null && run.hooks_generated == null && (
            <span className="text-xs text-success">
              {run.blueprints_count} blueprints
            </span>
          )}
          {run.banned_blocked != null && run.banned_blocked > 0 && (
            <span className="text-xs text-warning">
              {run.banned_blocked} banned
            </span>
          )}
          {run.dupes_rejected != null && run.dupes_rejected > 0 && (
            <span className="text-xs text-text-muted">
              {run.dupes_rejected} dupes
            </span>
          )}
        </div>
      )}

      {/* YouTube Quota */}
      {hasQuota && (
        <div className="flex items-center gap-1">
          <Youtube className="size-3" style={{ color: "#FF0000", opacity: 0.8 }} />
          <span className="text-xs text-text-muted">
            YT Quota:
          </span>
          <span
            className={`text-xs font-mono ${
              (run.yt_quota_used ?? 0) > 8000
                ? "text-error"
                : (run.yt_quota_used ?? 0) > 5000
                ? "text-warning"
                : "text-success"
            }`}
          >
            {run.yt_quota_used?.toLocaleString()}
            {run.yt_quota_limit != null && ` / ${run.yt_quota_limit.toLocaleString()}`}
          </span>
        </div>
      )}
    </div>
  );
}


function formatDuration(seconds: number): string {
  if (seconds < 1) return "<1s";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return s > 0 ? `${m}m ${s}s` : `${m}m`;
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return "\u2014";
  const d = new Date(dateStr);
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function statusIcon(status: string) {
  if (status === "completed" || status === "ok")
    return <CheckCircle2 className="size-3.5 text-success" />;
  if (status === "error" || status === "failed")
    return <XCircle className="size-3.5 text-error" />;
  return <Clock className="size-3.5 text-text-muted" />;
}

function extractNicheFromRunId(runId: string): string | null {
  for (const niche of Object.keys(NICHE_COLORS)) {
    if (runId.includes(niche)) return niche;
  }
  return null;
}

export function RunHistoryTable({ runs, isLoading }: Props) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (isLoading) {
    return (
      <div className="p-4 text-text-muted text-sm">
        Loading run history...
      </div>
    );
  }

  if (!runs || runs.length === 0) {
    return (
      <div className="p-4 text-text-muted text-sm">
        No runs recorded yet.
      </div>
    );
  }

  return (
    <table className="data-table">
      <thead>
        <tr>
          <th className="w-7" />
          <th>Run ID</th>
          <th>Type</th>
          <th>Duration</th>
          <th>Steps</th>
          <th>Status</th>
          <th>Started</th>
        </tr>
      </thead>
      <tbody>
        {runs.filter((r) => r?.run_id).map((run) => {
          const runId = run.run_id ?? "\u2014";
          const isExpanded = expandedId === runId;
          const niche = extractNicheFromRunId(runId);
          const nicheColor = niche ? NICHE_COLORS[niche] : undefined;
          const stages = run.stages ?? [];
          const hasStages = stages.length > 0;

          return (
            <Fragment key={runId}>
              <tr
                onClick={() => hasStages && setExpandedId(isExpanded ? null : runId)}
                className={hasStages ? "cursor-pointer" : "cursor-default"}
              >
                <td className="w-7 pr-0">
                  {hasStages && (
                    <ChevronRight
                      className={`size-3.5 text-text-muted transition-transform duration-150 ${
                        isExpanded ? "rotate-90" : "rotate-0"
                      }`}
                    />
                  )}
                </td>
                <td>
                  <span className="mono inline-flex items-center gap-1.5">
                    {nicheColor && (
                      <span
                        className="size-1.5 rounded-full shrink-0"
                        style={{ background: nicheColor }}
                      />
                    )}
                    <span title={runId}>{runId.slice(0, 20)}</span>
                  </span>
                </td>
                <td>
                  <span className="mono">{run.run_type || "\u2014"}</span>
                </td>
                <td className="mono">{formatDuration(run.duration_seconds ?? 0)}</td>
                <td className="mono">
                  {run.steps_completed ?? 0}/{run.total_steps ?? 0}
                </td>
                <td>
                  <span className="inline-flex items-center gap-1">
                    {statusIcon(run.status)}
                    <span className="mono">{run.status ?? "unknown"}</span>
                    {/* RENDER #2 (2026-06-13): silent stage failures.
                       Pre-fix the dashboard showed "success" while
                       individual stages had been corrupting outputs
                       (whisper_captions timing out → no moov atom, etc.).
                       Now the badge surfaces which stages failed in the
                       single status cell so the operator never trusts a
                       lying "success". Title-tooltip shows the full
                       breakdown for >2 stages. */}
                    {run.stage_failures && Object.keys(run.stage_failures).length > 0 && (
                      <span
                        className="ml-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-warning/15 text-warning border border-warning/30"
                        title={Object.entries(run.stage_failures)
                          .map(([k, v]) => `${k}: ${v}`)
                          .join("\n")}
                      >
                        {Object.entries(run.stage_failures)
                          .slice(0, 2)
                          .map(([k, v]) => `${k}:${v}`)
                          .join(" · ")}
                        {Object.keys(run.stage_failures).length > 2 && " · …"}
                      </span>
                    )}
                    {/* PR #508 (2026-06-24): structured bottleneck badge.
                       When `blueprints_pushed === 0` the genlab-core
                       run_report stage (PR #504) sets `bottleneck_stage`
                       to name which filter killed the funnel. Distinct
                       from stage_failures (which surfaces partial failures
                       on runs that DID produce blueprints) — this is the
                       "no content produced and HERE'S WHY" signal. Uses
                       error-coloured styling because zero blueprints is
                       a dark-day SLO violation, not a "partial" warning.
                       The full reason lands in the title tooltip — the
                       short stage name fits in the row without overflow. */}
                    {run.bottleneck_stage && (
                      <span
                        className="ml-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-error/15 text-error border border-error/30"
                        title={run.bottleneck_reason ?? `blocked at ${run.bottleneck_stage}`}
                      >
                        blocked: {run.bottleneck_stage}
                      </span>
                    )}
                  </span>
                </td>
                <td className="mono">{formatDate(run.started_at || run.date)}</td>
              </tr>
              {isExpanded && hasStages && (
                <tr>
                  <td colSpan={7} className="p-0">
                    <div className="bg-bg-primary p-2 px-4 rounded-sm mx-2 mb-2">
                      <div className="grid grid-cols-[1fr_auto_auto] gap-x-4 gap-y-0.5 text-xs font-mono">
                        {stages.map((stage) => (
                          <div key={stage.name} className="contents">
                            <span className="text-text-secondary">
                              {stage.label || stage.name}
                            </span>
                            <span className="text-text-muted text-right">
                              {formatDuration(stage.duration_seconds)}
                            </span>
                            <span>{statusIcon(stage.status)}</span>
                          </div>
                        ))}
                      </div>
                      <ContentQualityPanel run={run as PipelineRunDetail & RunExtra} />
                    </div>
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}
