import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, BarChart3 } from "lucide-react";
import { useLearningStatus } from "@/hooks/use-learning";
import { getNicheInfo } from "@/niches/registry";
import { EmptyState } from "@/components/shared/empty-state";
import { LoadingSkeleton } from "@/components/shared/loading-skeleton";
import type { BanditArm } from "@/api/types";

// ── Helpers ─────────────────────────────────────────────────

function getNicheConfig(nicheId: string) {
  const info = getNicheInfo(nicheId);
  return { accent: info.color, name: info.label };
}

// ── Sub-components ─────────────────────────────────────────

function MeanBar({ mean, color }: { mean: number; color: string }) {
  const pct = Math.min(100, mean * 100);
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1 bg-bg-elevated rounded-sm overflow-hidden">
        <div
          className="h-full rounded-sm transition-[width] duration-700 ease-out"
          style={{
            width: `${pct}%`,
            background: color,
            minWidth: pct > 0 ? 3 : 0,
          }}
        />
      </div>
      <span className="text-xs text-text-secondary tabular-nums min-w-[44px] text-right">
        {(mean * 100).toFixed(1)}%
      </span>
    </div>
  );
}

function ArmsTable({ arms, accent }: { arms: BanditArm[]; accent: string }) {
  if (arms.length === 0) {
    return (
      <div className="py-6 px-4 text-center text-sm text-text-ghost">
        No observations yet for this niche
      </div>
    );
  }

  const cols = ["Arm ID", "Alpha", "Beta", "Plays", "Mean"];

  return (
    <div className="overflow-x-auto">
      <table className="data-table w-full">
        <thead>
          <tr>
            {cols.map((col, i) => (
              <th
                key={col}
                className={`label-caps px-3 py-1.5 whitespace-nowrap border-b border-border ${i === 0 ? "text-left" : "text-right"}`}
              >
                {col}
              </th>
            ))}
            <th className="label-caps px-3 py-1.5 text-left border-b border-border w-[30%]">
              Distribution
            </th>
          </tr>
        </thead>
        <tbody>
          {arms.map((arm, idx) => (
            <tr
              key={arm.arm_id}
              className={idx % 2 !== 0 ? "bg-white/[0.015]" : ""}
            >
              <td
                className="px-3 py-2 text-sm text-text-primary font-mono max-w-[220px] overflow-hidden text-ellipsis whitespace-nowrap"
                title={arm.arm_id}
              >
                {arm.arm_id}
              </td>
              <td className="px-3 py-2 text-sm text-text-secondary text-right tabular-nums">
                {arm.alpha.toFixed(2)}
              </td>
              <td className="px-3 py-2 text-sm text-text-secondary text-right tabular-nums">
                {arm.beta.toFixed(2)}
              </td>
              <td className="px-3 py-2 text-sm text-text-secondary text-right tabular-nums">
                {arm.n_plays.toLocaleString()}
              </td>
              <td
                className="px-3 py-2 text-sm text-right tabular-nums"
                style={{
                  color: idx === 0 ? accent : "var(--text-secondary)",
                  fontWeight: idx === 0 ? 600 : 400,
                }}
              >
                {(arm.mean * 100).toFixed(1)}%
              </td>
              <td className="px-3 py-2">
                <MeanBar mean={arm.mean} color={accent} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function NicheSection({
  nicheId,
  arms,
}: {
  nicheId: string;
  arms: BanditArm[];
}) {
  const [expanded, setExpanded] = useState(true);
  const { accent, name } = getNicheConfig(nicheId);

  const sortedArms = useMemo(
    () => [...arms].sort((a, b) => b.mean - a.mean),
    [arms]
  );

  const totalPlays = arms.reduce((s, a) => s + a.n_plays, 0);

  return (
    <div className="bg-bg-surface border border-border rounded-lg overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-2.5 px-4 py-3 bg-transparent border-none cursor-pointer transition-colors hover:bg-bg-hover"
        style={{
          borderBottom: expanded ? "1px solid var(--border)" : "none",
        }}
      >
        <span
          className="w-2 h-2 rounded-full shrink-0"
          style={{ background: accent }}
        />
        <span className="text-base font-semibold text-text-primary flex-1 text-left">
          {name}
          <span className="ml-2 text-xs text-text-ghost font-normal">
            {nicheId}
          </span>
        </span>
        <span className="text-xs text-text-muted mr-2">
          {arms.length} arms &middot; {totalPlays.toLocaleString()} plays
        </span>
        {expanded ? (
          <ChevronDown size={14} className="text-text-ghost shrink-0" />
        ) : (
          <ChevronRight size={14} className="text-text-ghost shrink-0" />
        )}
      </button>

      {/* Table */}
      {expanded && <ArmsTable arms={sortedArms} accent={accent} />}
    </div>
  );
}

// ── Main export ────────────────────────────────────────────

export function BanditArms() {
  const { data, isLoading } = useLearningStatus();

  const nicheEntries = useMemo((): Array<[string, BanditArm[]]> => {
    if (!data) return [];
    return Object.entries(data.bandit_arms ?? {});
  }, [data]);

  if (isLoading) {
    return <LoadingSkeleton variant="card-list" rows={3} />;
  }

  if (nicheEntries.length === 0) {
    return (
      <EmptyState
        icon={BarChart3}
        title="No bandit arms recorded yet"
        description="Arms will appear once the pipeline has run at least once per niche."
      />
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {nicheEntries.map(([nicheId, arms]) => (
        <NicheSection key={nicheId} nicheId={nicheId} arms={arms} />
      ))}
    </div>
  );
}
