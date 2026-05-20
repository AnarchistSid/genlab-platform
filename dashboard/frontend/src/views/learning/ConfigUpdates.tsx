import { useMemo } from "react";
import { useConfigUpdates, useLearningStatus } from "@/hooks/use-learning";
import { ProgressBar } from "@/components/shared/progress-bar";
import { SectionHeader } from "@/components/shared/section-header";
import { LoadingSkeleton } from "@/components/shared/loading-skeleton";
import { getNicheInfo } from "@/niches/registry";

// ── Sub-components ─────────────────────────────────────────

function ThresholdProgress({
  progress,
  threshold,
  nichesAtQuota,
}: {
  progress: number;
  threshold: number;
  nichesAtQuota: number;
}) {
  const pct = threshold > 0 ? Math.min(100, (progress / threshold) * 100) : 0;
  const remaining = Math.max(0, threshold - progress);
  const ready = nichesAtQuota >= 5;

  return (
    <div className="bg-bg-surface border border-border rounded-lg p-4">
      <SectionHeader title="Progress Toward Auto-Update" />

      <div className="flex justify-between items-baseline mb-2.5">
        <span className="text-base text-text-secondary font-medium">
          Reward-bearing PF rows (30d) across all niches
        </span>
        <span
          className="text-base font-bold tabular-nums"
          style={{ color: ready ? "var(--color-green)" : "var(--color-amber)" }}
        >
          {progress.toLocaleString()} / {threshold.toLocaleString()}
        </span>
      </div>

      <ProgressBar
        value={pct}
        color={ready ? "var(--color-green)" : "var(--color-amber)"}
        height={6}
        animated
      />

      <div className="mt-2.5 text-xs text-text-ghost">
        {ready ? (
          <span style={{ color: "var(--color-green)" }}>
            All 5 niches at quota — auto-tuner runs weekly (Mon 09:00 UTC)
          </span>
        ) : (
          <span>
            <strong className="text-text-secondary">{nichesAtQuota} / 5</strong> niches at
            per-niche quota (20 reward-bearing rows in last 30 days).{" "}
            {remaining > 0
              ? `${remaining.toLocaleString()} more row(s) total needed`
              : `Waiting on per-niche distribution`}
            .
          </span>
        )}
      </div>
    </div>
  );
}

function UpdateHistory() {
  const { data: resp, isLoading } = useConfigUpdates();
  const rows = resp?.updates ?? [];

  if (isLoading) {
    return <LoadingSkeleton variant="card-list" rows={1} />;
  }

  if (rows.length === 0) {
    return (
      <div className="bg-bg-surface border border-border rounded-lg p-4">
        <div className="text-center py-8 px-4">
          <div className="w-12 h-12 rounded-lg bg-bg-elevated mx-auto mb-4 flex items-center justify-center text-[22px]">
            ⚙️
          </div>
          <div className="text-base text-text-secondary font-semibold mb-2">
            No automatic config updates yet
          </div>
          <div className="text-sm text-text-ghost max-w-[440px] mx-auto leading-relaxed">
            The weekly auto-tuner (Mon 09:00 UTC) writes posting-schedule and
            hook-ratio changes to <code className="px-1 py-0.5 rounded bg-bg-elevated">config_updates</code>{" "}
            once per-niche reward data crosses the gate. Changes will appear
            here with old → new values.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-bg-surface border border-border rounded-lg p-4">
      <SectionHeader title="Update History" />
      <div className="border border-border-subtle rounded-md overflow-hidden">
        <table className="data-table w-full">
          <thead>
            <tr>
              {["Date", "Niche", "File", "Field", "Old → New", "Records"].map(
                (col, i) => (
                  <th
                    key={col}
                    className={`label-caps px-3 py-2 bg-bg-elevated border-b border-border ${i >= 4 ? "text-right" : "text-left"}`}
                  >
                    {col}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const info = r.niche_id
                ? getNicheInfo(r.niche_id)
                : { label: "—", hex: "var(--text-muted)" };
              return (
                <tr key={r.id} className={r.dry_run ? "opacity-60" : ""}>
                  <td className="px-3 py-2 text-xs text-text-muted tabular-nums whitespace-nowrap">
                    {new Date(r.applied_at).toLocaleString(undefined, {
                      month: "short",
                      day: "numeric",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                    {r.dry_run && (
                      <span className="ml-1.5 text-[10px] uppercase text-text-ghost border border-border-subtle px-1 rounded">
                        dry
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-xs">
                    <span
                      className="inline-flex items-center gap-1.5"
                      style={{ color: info.hex }}
                    >
                      <span
                        className="w-1.5 h-1.5 rounded-full"
                        style={{ background: info.hex }}
                      />
                      {info.label}
                    </span>
                  </td>
                  <td
                    className="px-3 py-2 text-xs text-text-secondary font-mono max-w-[180px] overflow-hidden text-ellipsis whitespace-nowrap"
                    title={r.file_path}
                  >
                    {r.file_path.split("/").pop()}
                  </td>
                  <td className="px-3 py-2 text-xs text-text-secondary font-mono">
                    {r.field}
                  </td>
                  <td className="px-3 py-2 text-xs text-text-primary tabular-nums">
                    <span className="text-text-muted">{r.old_value ?? "—"}</span>
                    <span className="mx-1.5 text-text-ghost">→</span>
                    <span style={{ color: "var(--color-green)" }}>
                      {r.new_value ?? "—"}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-xs text-text-muted text-right tabular-nums">
                    {r.n_records ?? "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Main export ────────────────────────────────────────────

export function ConfigUpdates() {
  const { data: resp, isLoading } = useLearningStatus();

  const { progress, threshold, nichesAtQuota } = useMemo(() => {
    if (!resp) {
      return { progress: 0, threshold: 100, nichesAtQuota: 0 };
    }
    return {
      progress: resp.config_update_progress ?? 0,
      threshold: resp.config_update_threshold ?? 100,
      nichesAtQuota: resp.niches_at_config_quota ?? 0,
    };
  }, [resp]);

  if (isLoading) return <LoadingSkeleton variant="card-list" rows={2} />;

  return (
    <div className="flex flex-col gap-4">
      <ThresholdProgress
        progress={progress}
        threshold={threshold}
        nichesAtQuota={nichesAtQuota}
      />
      <UpdateHistory />
    </div>
  );
}
