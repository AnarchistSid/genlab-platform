/**
 * Phase 0.C observability card (2026-08-14) — reward signal health.
 *
 * Answers the operator question: "is the bandit actually learning
 * something on each platform, or is the reward signal Goodhart-broken?"
 *
 * ## Per-niche row
 *
 * Verdict badge: `healthy` (≥3 platforms with meaningful spread),
 * `partial` (1-2 platforms), `broken` (0). Codifies the Phase 0.A
 * pre-fix state — YT/IG/Threads all clustered near reward=0 while FB
 * had real spread — so operator can spot regression at a glance.
 *
 * ## Per-platform mini-cell
 *
 * Compact chip: platform name + `n·avg·stddev` + colored status pill.
 * Status:
 *   * `healthy` (green) — stddev ≥ 0.05, bandit can distinguish arms
 *   * `weak` (amber) — samples exist but stddev < 0.05, Goodhart-mode
 *   * `stale` (red) — latest reward > 48h old (metric collector broken)
 *   * `cold` (gray) — fewer than 3 samples (cold-start, not diagnostic)
 *
 * ## Cadence
 *
 * 60s polling matches other Mission Control learning cards
 * (`AutoApprovalCalibrationCard`, `SponsorshipReadinessCard`).
 * pending_feedback.reward_48h is written continuously as posts age
 * past 48h; a 60s stale card would only ever miss a burst of new
 * rewards by a minute.
 */
import { useQuery } from "@tanstack/react-query";

import { rewardAudit } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type {
  RewardAuditNiche,
  RewardAuditPlatform,
  RewardNicheVerdict,
  RewardSignalStatus,
} from "@/api/types";
import { getNicheInfo, type NicheId } from "@/niches/registry";

function verdictClasses(v: RewardNicheVerdict): string {
  switch (v) {
    case "healthy":
      return "bg-emerald-500/20 text-emerald-300 border-emerald-500/40";
    case "partial":
      return "bg-amber-500/20 text-amber-300 border-amber-500/40";
    case "broken":
      return "bg-red-500/20 text-red-300 border-red-500/40";
  }
}

function signalClasses(s: RewardSignalStatus): string {
  switch (s) {
    case "healthy":
      return "bg-emerald-500/15 text-emerald-300";
    case "weak":
      return "bg-amber-500/15 text-amber-300";
    case "stale":
      return "bg-red-500/15 text-red-300";
    case "cold":
      return "bg-gray-500/15 text-gray-400";
  }
}

function PlatformChip({ p }: { p: RewardAuditPlatform }) {
  const compact = `${p.n_rewards_7d}·${p.avg.toFixed(2)}±${p.stddev.toFixed(2)}`;
  return (
    <div className="inline-flex items-center gap-1.5 text-xs">
      <span className="text-gray-400 min-w-[60px]">{p.platform}</span>
      <span className="font-mono text-gray-300">{compact}</span>
      <span
        className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${signalClasses(
          p.signal_status,
        )}`}
      >
        {p.signal_status}
      </span>
    </div>
  );
}

function NicheRow({ niche }: { niche: RewardAuditNiche }) {
  const info = getNicheInfo(niche.niche_id as NicheId);
  const label = info?.label ?? niche.niche_id;
  return (
    <div className="border-b border-gray-800 last:border-0 py-2">
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-sm text-gray-200">{label}</span>
        <span
          className={`px-2 py-0.5 rounded border text-[11px] font-medium ${verdictClasses(
            niche.verdict,
          )}`}
        >
          {niche.verdict}
        </span>
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1">
        {niche.platforms.length === 0 ? (
          <span className="text-xs text-gray-500 italic">
            No rewards collected in the last 7 days.
          </span>
        ) : (
          niche.platforms.map((p) => (
            <PlatformChip key={p.platform} p={p} />
          ))
        )}
      </div>
    </div>
  );
}

export function RewardSignalAuditCard() {
  const { data, isLoading, isError } = useQuery({
    queryKey: queryKeys.rewardAudit.all(),
    queryFn: () => rewardAudit.fetch(),
    refetchInterval: 60_000, // 60s, matches other learning cards
  });

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/40 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-200">
          Reward signal audit
        </h3>
        <span className="text-[10px] text-gray-500">
          Phase 0.C — Goodhart-check per niche×platform
        </span>
      </div>
      {isLoading && (
        <div className="text-xs text-gray-500 py-2">Loading…</div>
      )}
      {isError && (
        <div className="text-xs text-red-400 py-2">
          Failed to load. Endpoint may be unavailable.
        </div>
      )}
      {data && data.length === 0 && (
        <div className="text-xs text-gray-500 py-2">
          No niches returned. Reward pipeline may be silent.
        </div>
      )}
      {data && data.length > 0 && (
        <div>
          {data.map((niche) => (
            <NicheRow key={niche.niche_id} niche={niche} />
          ))}
        </div>
      )}
      <div className="mt-3 text-[10px] text-gray-500 leading-relaxed">
        <span className="font-semibold text-gray-400">Compact:</span> n · avg ±
        stddev.
        <span className="ml-2 text-emerald-400">healthy</span> = stddev ≥ 0.05.
        <span className="ml-2 text-amber-400">weak</span> = Goodhart-mode.
        <span className="ml-2 text-red-400">stale</span> = latest reward &gt;
        48h old.
        <span className="ml-2 text-gray-400">cold</span> = &lt; 3 samples.
      </div>
    </div>
  );
}
