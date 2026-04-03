import { useState, useMemo, useCallback, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  Play,
  ChevronLeft,
  ChevronRight,
  AlertCircle,
  Clock,
  CheckCircle2,
  Download,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import { DataTable, type Column } from "@/components/shared/data-table";
import { EmptyState } from "@/components/shared/empty-state";
import { usePipelineRuns, usePipelineRun } from "@/hooks/use-pipeline";
import { useSocketUpdates } from "@/hooks/use-socket";
import { formatRelativeTime, formatDuration, formatCost, getTodayISO } from "@/lib/format";
import { cn } from "@/lib/utils";
import { exportToCSV, type CSVColumn } from "@/lib/export";
import type { PipelineRun } from "@/api/types";

function SkeletonTable() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 6 }).map((_, i) => (
        <Skeleton key={i} className="h-12 w-full" />
      ))}
    </div>
  );
}

function RunDetail({ runId }: { runId: string }) {
  const { data: response, isLoading, isError } = usePipelineRun(runId);

  if (isLoading) {
    return (
      <Card className="bg-bg-elevated border-border mt-4">
        <CardContent className="pt-6 space-y-4">
          <Skeleton className="h-5 w-48" />
          <Skeleton className="h-32 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (isError || !response) {
    return (
      <Card className="bg-bg-elevated border-border mt-4">
        <CardContent className="pt-6">
          <p className="text-text-muted text-sm">
            Failed to load run details.
          </p>
        </CardContent>
      </Card>
    );
  }

  const detail = response.data;
  const steps = detail["steps"] as
    | Array<{ name: string; duration_seconds: number; status: string; error?: string }>
    | undefined;
  const errors = detail["error_details"] as
    | Array<{ step: string; message: string }>
    | undefined;

  // Get the max duration for proportional bars
  const maxStepDuration = steps
    ? Math.max(...steps.map((s) => s.duration_seconds), 1)
    : 1;

  // Build key-value pairs excluding steps/errors (shown separately)
  const kvPairs = Object.entries(detail).filter(
    ([k]) => k !== "steps" && k !== "error_details"
  );

  return (
    <Card className="bg-bg-elevated border-border mt-4">
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium text-text-primary flex items-center gap-2">
          <Play className="size-4 text-text-muted" />
          Run Details: {runId}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Key-value pairs */}
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
          {kvPairs.map(([key, value]) => (
            <div key={key}>
              <p className="text-xs font-medium text-text-muted mb-0.5">
                {key.replace(/_/g, " ")}
              </p>
              <p className="text-sm text-text-secondary font-mono break-all">
                {typeof value === "object"
                  ? JSON.stringify(value)
                  : String(value ?? "--")}
              </p>
            </div>
          ))}
        </div>

        {/* Step breakdown */}
        {steps && steps.length > 0 && (
          <>
            <Separator className="bg-border" />
            <div>
              <p className="text-xs font-medium text-text-muted mb-3">
                Step Breakdown
              </p>
              <div className="space-y-2">
                {steps.map((step, idx) => {
                  const pct =
                    maxStepDuration > 0
                      ? (step.duration_seconds / maxStepDuration) * 100
                      : 0;
                  const isError = step.status === "error" || step.status === "failed";
                  return (
                    <div key={idx} className="flex items-center gap-3">
                      <div className="w-40 shrink-0">
                        <span
                          className={cn(
                            "text-xs font-mono",
                            isError ? "text-red-400" : "text-text-secondary"
                          )}
                        >
                          {step.name}
                        </span>
                      </div>
                      <div className="flex-1 h-5 bg-bg-surface rounded overflow-hidden relative">
                        <div
                          className={cn(
                            "h-full rounded transition-all",
                            isError ? "bg-red-500/50" : "bg-accent/40"
                          )}
                          style={{ width: `${Math.max(pct, 2)}%` }}
                        />
                        <span className="absolute right-2 top-0 leading-5 text-xs text-text-muted">
                          {formatDuration(step.duration_seconds)}
                        </span>
                      </div>
                      {isError ? (
                        <AlertCircle className="size-3.5 text-red-400 shrink-0" />
                      ) : (
                        <CheckCircle2 className="size-3.5 text-green-400 shrink-0" />
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          </>
        )}

        {/* Error details */}
        {errors && errors.length > 0 && (
          <>
            <Separator className="bg-border" />
            <div>
              <p className="text-xs font-medium text-red-400 mb-2 flex items-center gap-1">
                <AlertCircle className="size-3" />
                Errors
              </p>
              <div className="space-y-2">
                {errors.map((err, idx) => (
                  <div
                    key={idx}
                    className="rounded-md border border-red-500/20 bg-red-500/5 p-3"
                  >
                    <p className="text-xs font-mono text-red-400 mb-1">
                      {err.step}
                    </p>
                    <p className="text-sm text-text-secondary">{err.message}</p>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

export default function RunsView() {
  const { id: routeId } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState<string | null>(
    routeId ?? null
  );

  // Sync route param to selected ID
  useEffect(() => {
    if (routeId) {
      setSelectedId(routeId);
    }
  }, [routeId]);

  // Real-time updates
  useSocketUpdates();

  const params = useMemo(
    () => ({ page: String(page), per_page: "20" }),
    [page]
  );
  const { data: response, isLoading, isError } = usePipelineRuns(params);

  const runs = response?.data ?? [];
  const meta = response?.meta ?? {
    page: 1,
    per_page: 20,
    total: 0,
    total_pages: 1,
  };

  const handleRowClick = useCallback(
    (row: PipelineRun) => {
      const newId = selectedId === row.run_id ? null : row.run_id;
      setSelectedId(newId);
      if (newId) {
        navigate(`/runs/${newId}`, { replace: true });
      } else {
        navigate("/runs", { replace: true });
      }
    },
    [selectedId, navigate]
  );

  const columns: Column<PipelineRun>[] = useMemo(
    () => [
      {
        key: "run_id",
        header: "Run ID",
        sortable: true,
        render: (row) => (
          <span
            className={cn(
              "font-mono text-xs",
              selectedId === row.run_id
                ? "text-accent"
                : "text-text-primary"
            )}
          >
            {row.run_id.slice(0, 12)}
          </span>
        ),
      },
      {
        key: "date",
        header: "Date",
        sortable: true,
        className: "w-28",
        render: (row) => (
          <span className="text-text-muted text-sm flex items-center gap-1">
            <Clock className="size-3" />
            {formatRelativeTime(row.date)}
          </span>
        ),
      },
      {
        key: "duration_seconds",
        header: "Duration",
        sortable: true,
        className: "w-24",
        render: (row) => (
          <span className="text-text-secondary text-sm">
            {formatDuration(row.duration_seconds)}
          </span>
        ),
      },
      {
        key: "steps_completed",
        header: "Steps",
        className: "w-20",
        render: (row) => (
          <span className="text-text-secondary text-sm">
            {row.steps_completed}/{row.total_steps}
          </span>
        ),
      },
      {
        key: "errors",
        header: "Errors",
        sortable: true,
        className: "w-20",
        render: (row) => (
          <Badge
            variant="outline"
            className={cn(
              "text-xs",
              row.errors > 0
                ? "bg-red-500/15 text-red-400 border-red-500/25"
                : "bg-green-500/15 text-green-400 border-green-500/25"
            )}
          >
            {row.errors}
          </Badge>
        ),
      },
      {
        key: "cost_estimate",
        header: "Cost",
        sortable: true,
        className: "w-20",
        render: (row) => (
          <span className="text-text-secondary text-sm font-mono">
            {formatCost(row.cost_estimate)}
          </span>
        ),
      },
    ],
    [selectedId]
  );

  if (isError) {
    return (
      <div>
        <h1 className="text-2xl font-semibold tracking-tight mb-6 text-text-primary">
          Pipeline Runs
        </h1>
        <EmptyState
          icon={Play}
          title="Failed to load runs"
          description="An error occurred while fetching pipeline runs. Please try again."
        />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
          Pipeline Runs
        </h1>
        <Button
          variant="outline"
          size="sm"
          className="h-8 gap-1.5 text-xs"
          disabled={isLoading || runs.length === 0}
          onClick={() => {
            const cols: CSVColumn<PipelineRun>[] = [
              { key: "run_id", header: "Run ID" },
              { key: "date", header: "Date" },
              { key: "duration_seconds", header: "Duration (s)" },
              { key: "steps_completed", header: "Steps Completed" },
              { key: "total_steps", header: "Total Steps" },
              { key: "errors", header: "Errors" },
              { key: "cost_estimate", header: "Cost Estimate" },
            ];
            exportToCSV(runs, cols, `pipeline-runs-${getTodayISO()}.csv`);
          }}
        >
          <Download className="size-3.5" />
          Export CSV
        </Button>
      </div>

      {/* Table */}
      {isLoading ? (
        <SkeletonTable />
      ) : runs.length === 0 ? (
        <EmptyState
          icon={Play}
          title="No pipeline runs"
          description="No pipeline runs have been recorded yet. Trigger a pipeline to see results."
        />
      ) : (
        <>
          <DataTable
            columns={columns}
            data={runs}
            onRowClick={handleRowClick}
            sortable
          />

          {/* Detail panel */}
          {selectedId && <RunDetail runId={selectedId} />}

          {/* Pagination */}
          <div className="flex items-center justify-between pt-2">
            <p className="text-sm text-text-muted">
              Page {meta.page} of {meta.total_pages} ({meta.total} total)
            </p>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={meta.page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                className={cn(
                  "h-8 gap-1",
                  meta.page <= 1 && "opacity-50"
                )}
              >
                <ChevronLeft className="size-4" />
                Prev
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={meta.page >= meta.total_pages}
                onClick={() => setPage((p) => p + 1)}
                className={cn(
                  "h-8 gap-1",
                  meta.page >= meta.total_pages && "opacity-50"
                )}
              >
                Next
                <ChevronRight className="size-4" />
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
