import { useMemo } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";
import { useLearningStatus } from "@/hooks/use-learning";
import { getNicheInfo } from "@/niches/registry";
import { KpiCard } from "@/components/shared/kpi-card";
import { ChartCard } from "@/components/shared/chart-card";
import { ChartTooltip } from "@/components/charts/chart-tooltip";
import { SectionHeader } from "@/components/shared/section-header";
import { LoadingSkeleton } from "@/components/shared/loading-skeleton";

// ── Per-niche distribution bar chart ──────────────────────

function NicheDistribution({
  data,
}: {
  data: Array<{ nicheId: string; name: string; accent: string; plays: number }>;
}) {
  if (data.length === 0) return null;

  const maxPlays = Math.max(...data.map((d) => d.plays), 1);

  return (
    <div className="bg-bg-surface border border-border rounded-lg p-4">
      <SectionHeader title="Plays by Niche" />
      <div className="flex flex-col gap-2.5">
        {data.map((row) => (
          <div key={row.nicheId} className="flex items-center gap-3">
            <div className="flex items-center gap-2 min-w-[140px]">
              <span
                className="w-2 h-2 rounded-full shrink-0"
                style={{ background: row.accent }}
              />
              <span className="text-sm text-text-secondary">
                {row.name}
              </span>
            </div>
            <div className="flex-1 h-1.5 bg-bg-elevated rounded-sm overflow-hidden">
              <div
                className="h-full rounded-sm transition-[width] duration-700 ease-out"
                style={{
                  width: `${(row.plays / maxPlays) * 100}%`,
                  background: row.accent,
                  minWidth: row.plays > 0 ? 3 : 0,
                }}
              />
            </div>
            <span className="text-sm text-text-muted min-w-[50px] text-right tabular-nums">
              {row.plays.toLocaleString()}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Bar chart for niche comparison ────────────────────────

function NicheBarChart({
  data,
}: {
  data: Array<{ name: string; plays: number; accent: string }>;
}) {
  if (data.length === 0 || data.every((d) => d.plays === 0)) return null;

  return (
    <ChartCard title="Arm Plays Distribution">
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={data} margin={{ top: 4, right: 4, left: -16, bottom: 0 }}>
          <XAxis
            dataKey="name"
            tick={{ fill: "var(--text-muted)", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tick={{ fill: "var(--text-muted)", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            content={<ChartTooltip formatter={(v) => `${v} plays`} />}
            cursor={{ fill: "rgba(255,255,255,0.04)" }}
          />
          <Bar dataKey="plays" radius={[3, 3, 0, 0]}>
            {data.map((entry) => (
              <Cell key={entry.name} fill={entry.accent} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

// ── Main export ────────────────────────────────────────────

export function RewardHistory() {
  const { data, isLoading } = useLearningStatus();

  const { nicheData, chartData } = useMemo(() => {
    if (!data) return { nicheData: [], chartData: [] };

    const arms = data.bandit_arms ?? {};
    const nData: Array<{ nicheId: string; name: string; accent: string; plays: number }> = [];

    for (const [nicheId, nicheArms] of Object.entries(arms)) {
      const info = getNicheInfo(nicheId);
      const cfg = { accent: info.hex, name: info.label };
      const totalPlays = nicheArms.reduce((s, a) => s + a.n_plays, 0);
      nData.push({ nicheId, name: cfg.name, accent: cfg.accent, plays: totalPlays });
    }

    nData.sort((a, b) => b.plays - a.plays);

    const cData = nData.map((d) => ({
      name: d.name.split(" ")[0], // Shortened for chart axis
      plays: d.plays,
      accent: d.accent,
    }));

    return { nicheData: nData, chartData: cData };
  }, [data]);

  if (isLoading) return <LoadingSkeleton variant="card-list" rows={3} />;
  if (!data) return null;

  return (
    <div className="flex flex-col gap-4">
      {/* Hero numbers */}
      <div className="grid grid-cols-3 gap-3">
        <KpiCard
          label="Total Rewards"
          value={data.rewards_computed}
          variant="hero"
        />
        <KpiCard
          label="Avg Reward"
          value={data.avg_reward != null ? data.avg_reward.toFixed(4) : "\u2014"}
          variant="hero"
          animate={false}
        />
        <KpiCard
          label="Max Reward"
          value={data.max_reward != null ? data.max_reward.toFixed(4) : "\u2014"}
          variant="hero"
          animate={false}
        />
      </div>

      {nicheData.length > 0 && (
        <>
          <NicheDistribution data={nicheData} />
          <NicheBarChart data={chartData} />
        </>
      )}
    </div>
  );
}
