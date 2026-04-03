import { useLearningStatus } from "@/hooks/use-learning";
import { ProgressBar } from "@/components/shared/progress-bar";
import { SectionHeader } from "@/components/shared/section-header";
import { KpiCard } from "@/components/shared/kpi-card";
import { LoadingSkeleton } from "@/components/shared/loading-skeleton";
import { getNicheInfo } from "@/niches/registry";
import type { LearningStatus } from "@/api/types";

// ── Pipeline status keys in display order ───────────────────

const PIPELINE_STATUSES: Array<{ key: string; label: string; color: string; bg: string }> = [
  { key: "awaiting_6h",   label: "6h",   color: "var(--color-cyan)",   bg: "rgba(6,182,212,0.12)" },
  { key: "awaiting_24h",  label: "24h",  color: "var(--color-blue)",   bg: "var(--color-blue-dim)" },
  { key: "awaiting_48h",  label: "48h",  color: "var(--color-amber)",  bg: "var(--color-amber-dim)" },
  { key: "awaiting_168h", label: "168h", color: "var(--color-rose)",   bg: "var(--color-red-dim)" },
  { key: "complete",      label: "Done", color: "var(--color-green)",  bg: "var(--color-green-dim)" },
];

// ── Sub-components ─────────────────────────────────────────

function AiLearnedCard({ data }: { data: LearningStatus }) {
  const nicheAvgs = Object.entries(data.bandit_arms)
    .map(([niche, arms]) => {
      const active = arms.filter(a => a.n_plays > 0);
      const avg = active.length ? active.reduce((s, a) => s + a.mean, 0) / active.length : 0;
      const bestArm = active.length ? active.reduce((b, a) => a.mean > b.mean ? a : b) : null;
      return { niche, avg, bestArm, armCount: arms.length };
    })
    .filter(n => n.avg > 0)
    .sort((a, b) => b.avg - a.avg);

  let insight: string;
  if (nicheAvgs.length < 2) {
    insight = `The learning loop has analyzed ${data.rewards_computed} posts so far. All niches are still in exploration mode — the system is testing different content types equally before making optimization decisions.`;
  } else {
    const best = nicheAvgs[0];
    const label = getNicheInfo(best.niche).label;
    const armLabel = best.bestArm?.arm_id.replace(/_/g, " ") ?? "content";
    const mode = data.linucb_max_plays >= data.linucb_threshold
      ? "now using contextual signals (time, niche, content features) to optimize selections"
      : "still in exploration mode, collecting more data before optimizing";
    insight = `${label}'s "${armLabel}" content type is currently leading. The system has analyzed ${data.rewards_computed} posts across ${nicheAvgs.length} niches and is ${mode}.`;
  }

  return (
    <div className="bg-bg-surface border border-border rounded-lg p-4">
      <SectionHeader title="What the AI Has Learned" />
      <p className="text-sm text-text-secondary leading-relaxed">{insight}</p>
    </div>
  );
}

function StatusCard({ data }: { data: LearningStatus }) {
  const isLinUCB = data.linucb_max_plays >= data.linucb_threshold && data.linucb_threshold > 0;

  return (
    <div className="bg-bg-surface border border-border rounded-lg p-4 flex items-center gap-2.5">
      <span
        className="w-2 h-2 rounded-full shrink-0"
        style={{
          background: "var(--color-green)",
          boxShadow: "0 0 6px var(--color-green)",
        }}
      />
      <span className="text-base font-semibold text-text-primary">
        {isLinUCB ? "LinUCB Active" : "Thompson Sampling Active"}
      </span>
      <span className="ml-auto text-xs text-text-muted bg-bg-elevated border border-border rounded-sm px-2 py-0.5">
        {isLinUCB ? "Contextual Bandit" : "Cold-Start Fallback"}
      </span>
    </div>
  );
}

function ThresholdsCard({ data }: { data: LearningStatus }) {
  const linucbPct = data.linucb_threshold > 0
    ? Math.min(100, (data.linucb_max_plays / data.linucb_threshold) * 100)
    : 0;
  const classifierPct = data.hook_classifier_threshold > 0
    ? Math.min(100, (data.hook_classifier_progress / data.hook_classifier_threshold) * 100)
    : 0;
  const configPct = data.config_update_threshold > 0
    ? Math.min(100, (data.rewards_computed / data.config_update_threshold) * 100)
    : 0;

  const rows = [
    {
      label: "LinUCB",
      sublabel: `${data.linucb_max_plays} of ${data.linucb_threshold} observations \u2014 after ${data.linucb_threshold}, content selection adapts to context`,
      pct: linucbPct,
      color: "var(--color-blue)",
    },
    {
      label: "Hook Classifier",
      sublabel: `${data.hook_classifier_progress} of ${data.hook_classifier_threshold} examples \u2014 after ${data.hook_classifier_threshold}, hooks are pre-scored before publishing`,
      pct: classifierPct,
      color: "var(--color-purple)",
    },
    {
      label: "Config Updates",
      sublabel: `${data.rewards_computed} of ${data.config_update_threshold} rewards \u2014 auto-tunes scoring weights`,
      pct: configPct,
      color: "var(--color-amber)",
    },
  ];

  return (
    <div className="bg-bg-surface border border-border rounded-lg p-4">
      <SectionHeader title="Threshold Progress" />
      <div className="flex flex-col gap-3.5">
        {rows.map((row) => (
          <div key={row.label}>
            <div className="flex justify-between items-baseline mb-1.5">
              <span className="text-sm text-text-secondary font-medium">
                {row.label}
              </span>
              <span className="text-xs text-text-muted">
                {row.pct.toFixed(0)}%
              </span>
            </div>
            <ProgressBar value={row.pct} color={row.color} height={5} animated />
            <p className="text-xs text-text-muted mt-1 leading-relaxed">
              {row.sublabel}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

function SummaryStatsRow({ data }: { data: LearningStatus }) {
  const stats = [
    {
      label: "Posts Analyzed",
      value: data.rewards_computed.toLocaleString(),
    },
    {
      label: "Avg Content Score",
      value: data.avg_reward != null ? `${(data.avg_reward * 100).toFixed(1)} / 100` : "\u2014",
      accentColor: "var(--color-green)",
    },
    {
      label: "Best Content Score",
      value: data.max_reward != null ? `${(data.max_reward * 100).toFixed(1)} / 100` : "\u2014",
      accentColor: "var(--color-cyan)",
    },
    {
      label: "Engagement Records",
      value: data.analytics_records.toLocaleString(),
    },
  ];

  return (
    <div className="grid grid-cols-4 gap-3">
      {stats.map((stat) => (
        <KpiCard
          key={stat.label}
          label={stat.label}
          value={stat.value}
          animate={false}
          accentColor={"accentColor" in stat ? stat.accentColor : undefined}
        />
      ))}
    </div>
  );
}

function FeedbackPipeline({ data }: { data: LearningStatus }) {
  const pipeline = data.feedback_pipeline;

  return (
    <div className="bg-bg-surface border border-border rounded-lg p-4">
      <SectionHeader title="Feedback Pipeline" />
      <div className="flex flex-wrap gap-2">
        {PIPELINE_STATUSES.map(({ key, label, color, bg }) => {
          const count = pipeline[key] ?? 0;
          return (
            <div
              key={key}
              className="flex items-center gap-1.5 rounded-full px-2.5 py-1"
              style={{
                background: bg,
                border: `1px solid ${color}33`,
              }}
            >
              <span className="text-xs font-semibold" style={{ color }}>
                {label}
              </span>
              <span className="text-sm font-bold tabular-nums" style={{ color }}>
                {count}
              </span>
            </div>
          );
        })}

        {Object.keys(pipeline).filter(
          (k) => !PIPELINE_STATUSES.some((s) => s.key === k)
        ).map((key) => (
          <div
            key={key}
            className="flex items-center gap-1.5 bg-bg-elevated border border-border rounded-full px-2.5 py-1"
          >
            <span className="text-xs font-semibold text-text-muted">
              {key}
            </span>
            <span className="text-sm font-bold text-text-secondary">
              {pipeline[key]}
            </span>
          </div>
        ))}

        {Object.keys(pipeline).length === 0 && (
          <span className="text-sm text-text-ghost">
            No feedback tasks yet
          </span>
        )}
      </div>
    </div>
  );
}

// ── Main export ────────────────────────────────────────────

export function LearningOverview() {
  const { data, isLoading } = useLearningStatus();

  if (isLoading || !data) {
    return <LoadingSkeleton variant="card-list" rows={4} />;
  }

  return (
    <div className="flex flex-col gap-4">
      <AiLearnedCard data={data} />
      <StatusCard data={data} />
      <ThresholdsCard data={data} />
      <SummaryStatsRow data={data} />
      <FeedbackPipeline data={data} />
    </div>
  );
}
