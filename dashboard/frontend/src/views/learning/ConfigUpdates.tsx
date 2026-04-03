import { useMemo } from "react";
import { useLearningStatus } from "@/hooks/use-learning";
import { ProgressBar } from "@/components/shared/progress-bar";
import { SectionHeader } from "@/components/shared/section-header";
import { LoadingSkeleton } from "@/components/shared/loading-skeleton";

// ── Sub-components ─────────────────────────────────────────

function ThresholdProgress({
  rewards,
  threshold,
}: {
  rewards: number;
  threshold: number;
}) {
  const pct = threshold > 0 ? Math.min(100, (rewards / threshold) * 100) : 0;
  const remaining = Math.max(0, threshold - rewards);

  return (
    <div className="bg-bg-surface border border-border rounded-lg p-4">
      <SectionHeader title="Progress Toward Auto-Update" />

      <div className="flex justify-between items-baseline mb-2.5">
        <span className="text-base text-text-secondary font-medium">
          Completed Feedback Tasks
        </span>
        <span
          className="text-base font-bold tabular-nums"
          style={{ color: pct >= 100 ? "var(--color-green)" : "var(--color-amber)" }}
        >
          {rewards.toLocaleString()} / {threshold.toLocaleString()}
        </span>
      </div>

      <ProgressBar
        value={pct}
        color={pct >= 100 ? "var(--color-green)" : "var(--color-amber)"}
        height={6}
        animated
      />

      <div className="mt-2.5 text-xs text-text-ghost">
        {pct >= 100 ? (
          <span style={{ color: "var(--color-green)" }}>
            Threshold reached — automatic config updates are active
          </span>
        ) : (
          <span>
            {remaining.toLocaleString()} more completed feedback task{remaining !== 1 ? "s" : ""} needed per platform
          </span>
        )}
      </div>
    </div>
  );
}

function ConfigEmptyState({ threshold }: { threshold: number }) {
  return (
    <div className="bg-bg-surface border border-border rounded-lg p-4">
      <div className="text-center py-8 px-4">
        <div className="w-12 h-12 rounded-lg bg-bg-elevated mx-auto mb-4 flex items-center justify-center text-[22px]">
          ⚙️
        </div>
        <div className="text-base text-text-secondary font-semibold mb-2">
          No automatic config updates yet
        </div>
        <div className="text-sm text-text-ghost max-w-[420px] mx-auto leading-relaxed">
          The system will begin updating posting schedules and content ratios after{" "}
          <strong className="text-text-muted">
            {threshold} completed feedback tasks per platform
          </strong>
          . Once active, this table will show each change with the old and new values.
        </div>
      </div>
    </div>
  );
}

function FutureTablePreview() {
  const columns = ["Date", "File", "Field", "Old Value", "New Value"];

  return (
    <div className="bg-bg-surface border border-border rounded-lg p-4">
      <SectionHeader title="Update History (Future)" />
      <div className="border border-border-subtle rounded-md overflow-hidden">
        <table className="data-table w-full">
          <thead>
            <tr>
              {columns.map((col, i) => (
                <th
                  key={col}
                  className={`label-caps px-3 py-2 bg-bg-elevated border-b border-border ${i >= 3 ? "text-right" : "text-left"}`}
                >
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr>
              <td
                colSpan={5}
                className="py-7 px-4 text-center text-sm text-text-disabled"
              >
                Config changes will appear here once auto-updates begin
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Main export ────────────────────────────────────────────

export function ConfigUpdates() {
  const { data: resp, isLoading } = useLearningStatus();

  const { rewards, threshold } = useMemo(() => {
    if (!resp) return { rewards: 0, threshold: 50 };
    return {
      rewards: resp.rewards_computed,
      threshold: resp.config_update_threshold,
    };
  }, [resp]);

  if (isLoading) return <LoadingSkeleton variant="card-list" rows={2} />;

  return (
    <div className="flex flex-col gap-4">
      <ThresholdProgress rewards={rewards} threshold={threshold} />
      <ConfigEmptyState threshold={threshold} />
      <FutureTablePreview />
    </div>
  );
}
