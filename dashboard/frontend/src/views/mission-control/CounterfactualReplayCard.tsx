/**
 * Intervention 7 observability card (2026-07-01) — counterfactual
 * replay results.
 *
 * The DR estimator (commit 82708606) runs monthly, persists per-arm
 * IPS + doubly-robust reward estimates to a JSON artifact. Nothing
 * on the dashboard reads that artifact yet — this card closes that
 * gap.
 *
 * ## What each niche row shows
 *
 * The top DR arm (highest ``dr_reward``, falling back to
 * ``ips_reward`` when DR is null) with a compact "n=42 · ips=0.32 ·
 * dr=0.35" label. The "n" is n_with_reward — how many decisions
 * actually closed their 48h window and contributed to the estimator.
 *
 * ## DR-mode badge
 *
 * The runner emits ``dr_enabled: bool`` in the artifact. When true
 * → green "DR active" badge; when false → amber "IPS only"  (the
 * runner's stub mode — DR fields are all null). Same "active vs
 * observation only" pattern the other observability cards use.
 *
 * ## Cold-start
 *
 * The monthly cadence means new-install operators may see "No
 * replay yet — monthly runner (1st @ 04:30 UTC) hasn't fired" for
 * up to 30 days. This is normal; the message tells the operator
 * when to expect data.
 */
import { useQuery } from "@tanstack/react-query";

import { counterfactualReplay } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { CounterfactualReplayArm } from "@/api/types";
import { NICHE_IDS, getNicheInfo, type NicheId } from "@/niches/registry";

/** Pick the arm to feature in the compact row: highest DR reward
 *  when DR is populated, else highest IPS reward as fallback. Skip
 *  arms with fewer than 3 rewarded decisions — those estimates
 *  are too noisy to feature. */
function topArm(
  arms: CounterfactualReplayArm[],
): CounterfactualReplayArm | null {
  const scored = arms.filter((a) => a.n_with_reward >= 3);
  if (scored.length === 0) return null;
  scored.sort((a, b) => {
    const aScore = a.dr_reward ?? a.ips_reward ?? -Infinity;
    const bScore = b.dr_reward ?? b.ips_reward ?? -Infinity;
    return bScore - aScore;
  });
  return scored[0];
}

function NicheRow({ nicheId }: { nicheId: NicheId }) {
  const info = getNicheInfo(nicheId);
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.counterfactualReplay.latest(nicheId),
    queryFn: () => counterfactualReplay.latest(nicheId),
    // Monthly artifact — daily poll is plenty. Longer than the other
    // observability cards because the underlying data changes at most
    // once a month.
    refetchInterval: 24 * 60 * 60 * 1000,
    staleTime: 6 * 60 * 60 * 1000,
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-between border-b border-border/40 py-2 text-sm">
        <span className="font-semibold" style={{ color: info.color }}>
          {info.shortLabel}
        </span>
        <span className="text-xs text-text-muted">Loading…</span>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex items-center justify-between border-b border-border/40 py-2 text-sm">
        <span className="font-semibold" style={{ color: info.color }}>
          {info.shortLabel}
        </span>
        <span className="text-xs text-text-muted">
          No replay yet — monthly runner may not have fired
        </span>
      </div>
    );
  }

  const arm = topArm(data.per_arm);
  const drBadge = data.dr_enabled ? (
    <span
      className="rounded border border-success/30 bg-success/10 px-1.5 py-0.5 text-[10px] text-success"
      title="GENLAB_COUNTERFACTUAL_REPLAY_ENABLED is on — DR fields are populated with Ridge reward model output"
    >
      DR
    </span>
  ) : (
    <span
      className="rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-[10px] text-warning"
      title="DR flag off — dr_reward fields are null (stub mode); only IPS values are meaningful"
    >
      IPS
    </span>
  );

  return (
    <div className="flex items-center justify-between gap-2 border-b border-border/40 py-2 text-sm">
      <div className="flex items-center gap-2">
        <span className="font-semibold" style={{ color: info.color }}>
          {info.shortLabel}
        </span>
        {drBadge}
        <span className="text-xs text-text-muted">
          window={data.window_days}d · n_decisions={data.n_decisions_total}
        </span>
      </div>
      <div className="flex items-center gap-3 text-xs">
        {arm ? (
          <>
            <span className="font-mono text-text-muted" title={arm.arm_id}>
              {arm.arm_id.length > 30
                ? `${arm.arm_id.slice(0, 30)}…`
                : arm.arm_id}
            </span>
            <span title="Top arm — Naive / IPS / DR reward">
              <span className="text-text-muted">n=</span>
              <span className="font-mono">{arm.n_with_reward}</span>{" "}
              <span className="text-text-muted">ips=</span>
              <span className="font-mono">
                {arm.ips_reward !== null ? arm.ips_reward.toFixed(2) : "—"}
              </span>{" "}
              <span className="text-text-muted">dr=</span>
              <span className="font-mono">
                {arm.dr_reward !== null && arm.dr_reward !== undefined
                  ? arm.dr_reward.toFixed(2)
                  : "—"}
              </span>
            </span>
          </>
        ) : (
          <span className="text-text-muted">
            No arm cleared the n≥3 threshold
          </span>
        )}
      </div>
    </div>
  );
}

export function CounterfactualReplayCard() {
  return (
    <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
      <div className="mb-2">
        <div className="text-sm font-semibold">Counterfactual replay</div>
        <div className="text-xs text-text-muted">
          Monthly IPS + doubly-robust reward estimates · 1st @ 04:30 UTC
        </div>
      </div>
      <div>
        {NICHE_IDS.map((n) => (
          <NicheRow key={n} nicheId={n} />
        ))}
      </div>
    </div>
  );
}
