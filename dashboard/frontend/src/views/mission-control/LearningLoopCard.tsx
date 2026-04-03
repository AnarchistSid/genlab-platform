import { useMemo } from "react";
import { useLearningStatus } from "@/hooks/use-learning";
import { ProgressBar } from "@/components/shared/progress-bar";

export function LearningLoopCard() {
  const { data, isLoading } = useLearningStatus();

  const stats = useMemo(() => {
    if (!data) {
      return {
        rewards: 15,
        bestArm: "ai_creators/tool_demo",
        bestMean: 32.2,
        avgReward: null as number | null,
        maxReward: null as number | null,
        linucbProgress: 0,
        xgbProgress: 0,
        configProgress: 0,
      };
    }

    // Find the best arm across all niches
    let bestArm = "\u2014";
    let bestMean = 0;
    for (const [nicheId, arms] of Object.entries(data.bandit_arms)) {
      for (const arm of arms) {
        if (arm.mean > bestMean) {
          bestMean = arm.mean;
          bestArm = `${nicheId}/${arm.arm_id}`;
        }
      }
    }

    const linucbProgress = data.linucb_max_plays > 0
      ? Math.min(100, (data.rewards_computed / data.linucb_max_plays) * 100)
      : 0;
    const xgbProgress = data.hook_classifier_threshold > 0
      ? Math.min(100, (data.hook_classifier_progress / data.hook_classifier_threshold) * 100)
      : 0;
    const configProgress = data.config_update_threshold > 0
      ? Math.min(100, (data.rewards_computed / data.config_update_threshold) * 100)
      : 0;

    return {
      rewards: data.rewards_computed,
      bestArm,
      bestMean: bestMean * 100,
      avgReward: data.avg_reward,
      maxReward: data.max_reward,
      linucbProgress,
      xgbProgress,
      configProgress,
    };
  }, [data]);

  if (isLoading) {
    return (
      <div className="bento-card">
        <h3 className="card-title">Learning Loop</h3>
        <div className="shimmer" style={{ height: 14, width: "60%", borderRadius: 4, marginBottom: 12 }} />
        <div className="shimmer" style={{ height: 10, width: "80%", borderRadius: 4 }} />
      </div>
    );
  }

  return (
    <div className="bento-card">
      <h3 className="card-title">Learning Loop</h3>

      {/* Status line */}
      <div className="flex items-center gap-2 mb-3">
        <span
          className="size-2 rounded-full bg-success"
          style={{ boxShadow: "0 0 6px rgba(34,197,94,0.4)" }}
        />
        <span className="text-sm text-text-secondary">
          Thompson active &middot; {stats.rewards} rewards
        </span>
      </div>

      {/* Best arm */}
      <div className="flex justify-between items-baseline mb-2">
        <span className="text-xs text-text-muted">Best arm</span>
        <span className="font-mono text-xs text-text-primary">
          {stats.bestArm}
          <span className="ml-2 text-success font-medium">{stats.bestMean.toFixed(1)}%</span>
        </span>
      </div>

      {/* Reward stats */}
      {stats.avgReward != null && (
        <div className="flex justify-between items-baseline mb-3">
          <span className="text-xs text-text-muted">Avg / Max reward</span>
          <span className="font-mono text-xs text-text-secondary">
            {stats.avgReward.toFixed(2)} / {stats.maxReward?.toFixed(2) ?? "\u2014"}
          </span>
        </div>
      )}

      {/* Thresholds */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-medium text-text-muted w-9 shrink-0">LinUCB</span>
          <div className="flex-1">
            <ProgressBar value={stats.linucbProgress} color="var(--color-blue)" height={3} animated />
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-medium text-text-muted w-9 shrink-0">XGB</span>
          <div className="flex-1">
            <ProgressBar value={stats.xgbProgress} color="var(--color-purple)" height={3} animated />
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-medium text-text-muted w-9 shrink-0">Config</span>
          <div className="flex-1">
            <ProgressBar value={stats.configProgress} color="var(--color-amber)" height={3} animated />
          </div>
        </div>
      </div>
    </div>
  );
}
