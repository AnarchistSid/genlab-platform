/**
 * Phase 4.A session 4 observability card (2026-08-14) — content
 * quality per-niche score aggregates.
 *
 * Shows the per-niche joint / visual / audio score means (last 7d)
 * so the operator can eyeball whether the multi-modal scorer is
 * producing sane, discriminating signal before flipping
 * ``GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED``.
 *
 * ## Reading the row colors
 *
 *   * avg_joint ≥ 0.60 → green (healthy)
 *   * 0.30 - 0.60      → amber (mixed)
 *   * < 0.30           → red (most renders quality-collapsed —
 *                        investigate before flipping the multiplier)
 *
 * ## Sibling cards
 *
 * Same visual language as CrossNichePriorsCard,
 * CompetitorDeltasCard, TopCreatorPriorsCard — completes the
 * intelligence-stack observability quartet.
 */
import { useQuery } from "@tanstack/react-query";

import { contentQuality } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { ContentQualityPerNiche } from "@/api/types";

function scoreClass(score: number | null): string {
  if (score === null) return "text-text-muted";
  if (score >= 0.6) return "text-success";
  if (score >= 0.3) return "text-warning";
  return "text-red-400";
}

function formatScore(v: number | null): string {
  if (v === null || v === undefined) return "—";
  return v.toFixed(2);
}

function NicheRow({ row }: { row: ContentQualityPerNiche }) {
  return (
    <div className="grid grid-cols-12 gap-2 border-b border-border/40 py-1.5 text-xs">
      <span className="col-span-2 font-mono font-medium">{row.niche_id}</span>
      <span className="col-span-1 text-text-muted" title="Number scored (last 7d)">
        n={row.n_scored}
      </span>
      <span
        className={`col-span-2 font-mono font-semibold ${scoreClass(row.avg_joint)}`}
        title="Weighted joint quality (visual^0.6 × audio^0.4)"
      >
        joint={formatScore(row.avg_joint)}
      </span>
      <span
        className={`col-span-2 font-mono ${scoreClass(row.avg_visual)}`}
        title="Visual: palette · motion · cuts · brand (geometric mean)"
      >
        vis={formatScore(row.avg_visual)}
      </span>
      <span
        className={`col-span-2 font-mono ${scoreClass(row.avg_audio)}`}
        title="Audio: energy variance · dialogue density · music/voice"
      >
        aud={formatScore(row.avg_audio)}
      </span>
      <span
        className="col-span-3 text-text-muted"
        title="min/max range across niche's renders"
      >
        range: {formatScore(row.min_joint)}–{formatScore(row.max_joint)}
      </span>
    </div>
  );
}

export function ContentQualityCard() {
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.contentQuality.summary(),
    queryFn: () => contentQuality.summary(),
    // Runner fires every 30 min — poll matches so operator sees
    // fresh scores within a workday of each render batch.
    refetchInterval: 30 * 60 * 1000,
    staleTime: 30 * 60 * 1000,
  });

  if (isLoading) {
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="text-sm font-semibold">Content quality scores</div>
        <div className="mt-2 text-xs text-text-muted">Loading…</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="mb-2">
          <div className="text-sm font-semibold">Content quality scores</div>
          <div className="text-xs text-text-muted">
            Multi-modal render quality · 30-min post-render scorer
          </div>
        </div>
        <div className="text-xs text-text-muted">
          No scores yet — runner may not have caught up with recent renders
        </div>
      </div>
    );
  }

  const flagBadge = data.flag_enabled ? (
    <span
      className="rounded border border-success/30 bg-success/10 px-1.5 py-0.5 text-xs text-success"
      title="GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED is on — bandit reward is being multiplied by joint_score"
    >
      active
    </span>
  ) : (
    <span
      className="rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-xs text-warning"
      title="GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED is off — scores persisted but not consumed by bandit yet"
    >
      observation only
    </span>
  );

  return (
    <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
      <div className="mb-2 flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold">
            Content quality scores {flagBadge}
          </div>
          <div className="text-xs text-text-muted">
            Per-niche joint / visual / audio (last 7d) · FFmpeg-only
            extractors
          </div>
        </div>
      </div>
      {data.per_niche.length === 0 ? (
        <div className="text-xs text-text-muted">
          No niches scored in the last 7 days.
        </div>
      ) : (
        <div>
          {data.per_niche.map((row) => (
            <NicheRow key={row.niche_id} row={row} />
          ))}
        </div>
      )}
    </div>
  );
}
