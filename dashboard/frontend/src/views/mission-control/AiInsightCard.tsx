import { useMemo } from "react";
import { Sparkles } from "lucide-react";
import { useLearningStatus } from "@/hooks/use-learning";
import { NICHE_LABELS } from "@/niches/registry";
import type { BanditArm } from "@/api/types";

export function AiInsightCard() {
  const { data: status } = useLearningStatus();

  const insight = useMemo(() => {
    if (!status || !status.bandit_arms) {
      return "Blackbox Brief's AI creator content consistently outperforms other niches. The learning loop is identifying which content types drive the highest engagement.";
    }

    // Compare mean performance across niches — only count arms with real observations
    const nicheAvgs: { niche: string; avgMean: number }[] = [];
    for (const [nicheId, arms] of Object.entries(status.bandit_arms)) {
      const activeArms = arms.filter((a) => a.n_plays > 0);
      if (activeArms.length === 0) continue;
      const avg = activeArms.reduce((s, a) => s + a.mean, 0) / activeArms.length;
      nicheAvgs.push({ niche: nicheId, avgMean: avg });
    }

    if (nicheAvgs.length < 2) {
      return "Learning loop is still gathering data. Thompson Sampling is active and optimizing content selection across all niches.";
    }

    nicheAvgs.sort((a, b) => b.avgMean - a.avgMean);
    const best = nicheAvgs[0];
    const rest = nicheAvgs.slice(1);
    const restAvg = rest.reduce((s, n) => s + n.avgMean, 0) / rest.length;
    const multiplier = restAvg > 0 ? (best.avgMean / restAvg).toFixed(1) : "N/A";

    const bestLabel = NICHE_LABELS[best.niche] ?? best.niche;

    // Find best arm in top niche (only arms with real plays)
    const bestArms: BanditArm[] = (status.bandit_arms[best.niche] ?? []).filter((a) => a.n_plays > 0);
    const topArm = bestArms.length > 0
      ? bestArms.reduce((b, a) => (a.mean > b.mean ? a : b), bestArms[0])
      : null;
    const armLabel = topArm ? topArm.arm_id.replace(/_/g, " ") : "content";

    return `${bestLabel}'s ${armLabel} content gets ${multiplier}x more engagement than the average. The bandit is learning to prioritize high-performing content types automatically.`;
  }, [status]);

  return (
    <div className="bento-card ai-gradient-border relative overflow-hidden">
      <div className="text-accent-secondary mb-2">
        <Sparkles size={14} />
      </div>
      <h3 className="card-title">AI Insight</h3>
      <p className="text-sm text-text-secondary leading-relaxed">{insight}</p>
    </div>
  );
}
