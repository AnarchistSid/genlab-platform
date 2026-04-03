import { useState, useRef, useEffect, useMemo } from "react";
import {
  Play,
  ChevronDown,
  Loader2,
  AlertCircle,
  CheckCircle2,
} from "lucide-react";
import { usePipelineMonitor, usePipelineTrigger, usePipelineRuns } from "@/hooks/use-pipeline-monitor";
import { getActiveNiches } from "@/niches/registry";
import type { NicheDefinition } from "@/niches/registry";
import type { PipelineNicheStatus, PipelineRunDetail } from "@/hooks/use-pipeline-monitor";
import { formatDuration, formatRelativeTime } from "@/lib/format";
import { PageHeader } from "@/components/shared/page-header";
import { StageWaterfall } from "./StageWaterfall";
import { RunHistoryTable } from "./RunHistoryTable";
import { PipelineLogViewer } from "./PipelineLogViewer";

function StatusBadge({ status }: { status: string }) {
  const base = "inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-0.5 rounded-full whitespace-nowrap";
  if (status === "running") {
    return (
      <span className={`${base} text-info bg-info/[0.12]`}>
        <Loader2 className="size-3 animate-spin" />
        Running
      </span>
    );
  }
  if (status === "error") {
    return (
      <span className={`${base} text-error bg-error/[0.12]`}>
        <AlertCircle className="size-3" />
        Error
      </span>
    );
  }
  return (
    <span className={`${base} text-text-muted bg-bg-elevated`}>
      <CheckCircle2 className="size-3" />
      Idle
    </span>
  );
}

function NichePipelineCard({
  niche,
  status,
}: {
  niche: NicheDefinition;
  status: PipelineNicheStatus | undefined;
}) {
  const pipelineStatus = status?.pipeline_status ?? "idle";
  const currentStage = status?.current_stage ?? null;
  const progress = status?.stage_progress;
  const lastRun = status?.last_run;
  const lastRunStages = lastRun?.stages ?? null;

  const progressPct = useMemo(() => {
    if (!progress) return 0;
    if (progress.total_stages === 0) return 0;
    const stagePct = progress.stage_index / progress.total_stages;
    const itemPct =
      progress.items_total > 0
        ? progress.items_processed / progress.items_total
        : 0;
    return Math.min(
      100,
      Math.round((stagePct + itemPct / progress.total_stages) * 100)
    );
  }, [progress]);

  return (
    <div className="bg-bg-surface border border-border rounded-lg p-4 bento-card">
      <div className="flex items-center gap-2 mb-4">
        <div
          className="w-1 h-7 rounded-sm shrink-0"
          style={{ background: niche.accentHex }}
        />
        <span className="text-lg font-semibold text-text-primary flex-1">{niche.displayName}</span>
        <StatusBadge status={pipelineStatus} />
        {pipelineStatus === "running" && progress && (
          <span className="font-mono text-xs text-text-muted">
            {formatDuration(progress.elapsed_seconds)}
          </span>
        )}
      </div>

      <StageWaterfall
        stages={niche.pipelineStages}
        currentStage={currentStage}
        lastRunStages={lastRunStages}
        accentColor={niche.accentHex}
      />

      {pipelineStatus === "running" && progress && (
        <div className="pm-progress-bar">
          <div
            className="pm-progress-fill"
            style={{
              width: `${progressPct}%`,
              background: niche.accentHex,
            }}
          />
        </div>
      )}

      {lastRun && pipelineStatus !== "running" && (
        <div className="text-xs text-text-muted flex flex-wrap gap-2 items-center">
          <span>Last run: {formatRelativeTime(lastRun.started_at || lastRun.date || "")}</span>
          <span>&middot;</span>
          <span>{formatDuration(lastRun.duration_seconds)}</span>
          <span>&middot;</span>
          <span>
            {lastRun.steps_completed}/{lastRun.total_steps} steps
          </span>
          {lastRun.errors > 0 && (
            <>
              <span>&middot;</span>
              <span className="text-error">
                {lastRun.errors} error{lastRun.errors !== 1 ? "s" : ""}
              </span>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function TriggerDropdown() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const trigger = usePipelineTrigger();
  const niches = getActiveNiches();

  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  const handleTrigger = (nicheId?: string, mode?: string) => {
    trigger.mutate({ niche_id: nicheId, mode });
    setOpen(false);
  };

  return (
    <div className="relative" ref={ref}>
      <button
        className="btn btn-primary inline-flex items-center gap-1.5"
        onClick={() => setOpen(!open)}
        disabled={trigger.isPending}
      >
        {trigger.isPending ? (
          <Loader2 className="size-3.5 animate-spin" />
        ) : (
          <Play className="size-3.5" />
        )}
        Trigger Run
        <ChevronDown className="size-3" />
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 min-w-[200px] bg-bg-surface border border-border rounded-md shadow-lg py-1 z-50">
          <button
            className="block w-full text-left px-4 py-2 text-sm text-text-secondary hover:bg-bg-elevated hover:text-text-primary transition-colors cursor-pointer border-none bg-transparent"
            onClick={() => handleTrigger()}
          >
            <strong>Full Pipeline</strong>
            <br />
            <span className="text-xs text-text-muted">
              All niches, all stages
            </span>
          </button>
          <div className="h-px bg-border mx-2 my-1" />
          {niches.map((n) => (
            <button
              key={n.id}
              className="block w-full text-left px-4 py-2 text-sm text-text-secondary hover:bg-bg-elevated hover:text-text-primary transition-colors cursor-pointer border-none bg-transparent"
              onClick={() => handleTrigger(n.id)}
            >
              <span className="inline-flex items-center gap-1.5">
                <span
                  className="size-2 rounded-full shrink-0"
                  style={{ background: n.accentHex }}
                />
                {n.displayName} only
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function PipelineMonitor() {
  const { niches: nicheStatuses, isLoading } = usePipelineMonitor();
  const { data: runsData, isLoading: runsLoading } = usePipelineRuns(20);
  const activeNiches = getActiveNiches();
  const [logNicheId, setLogNicheId] = useState<string | null>(null);

  // Build a lookup from niche id → status
  const statusMap = useMemo(() => {
    const map = new Map<string, PipelineNicheStatus>();
    for (const ns of nicheStatuses) {
      map.set(ns.id, ns);
    }
    return map;
  }, [nicheStatuses]);

  // Auto-select the running niche for logs (or first active)
  useEffect(() => {
    const running = nicheStatuses.find((n) => n.pipeline_status === "running");
    if (running) {
      setLogNicheId(running.id);
    } else if (!logNicheId && activeNiches.length > 0) {
      setLogNicheId(activeNiches[0].id);
    }
  }, [nicheStatuses, activeNiches, logNicheId]);

  const runs = useMemo(() => {
    if (!runsData) return [];
    // runsData may be { data: [...] } or an array
    const arr = Array.isArray(runsData) ? runsData : (runsData as { data?: unknown[] }).data;
    return (Array.isArray(arr) ? arr : []) as PipelineRunDetail[];
  }, [runsData]);

  return (
    <>
      <PageHeader title="Pipeline Monitor" actions={<TriggerDropdown />} />

      {isLoading ? (
        <div className="flex items-center gap-2 text-text-muted py-8">
          <Loader2 className="size-4 animate-spin" />
          Loading pipeline status...
        </div>
      ) : (
        <div className="grid gap-4">
          {activeNiches
            .filter((n) => n.pipelineStages.length > 0)
            .map((niche) => (
              <NichePipelineCard
                key={niche.id}
                niche={niche}
                status={statusMap.get(niche.id)}
              />
            ))}
        </div>
      )}

      <PipelineLogViewer nicheId={logNicheId} />

      <div className="mt-8">
        <h2 className="text-lg font-semibold text-text-primary mb-4">
          Run History
        </h2>
        <div className="bg-bg-surface border border-border rounded-lg overflow-hidden">
          <RunHistoryTable runs={runs} isLoading={runsLoading} />
        </div>
      </div>
    </>
  );
}
