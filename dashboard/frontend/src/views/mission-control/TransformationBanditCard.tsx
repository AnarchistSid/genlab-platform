/**
 * PR 14 (2026-07-05) — Intelligent Transformation sprint observability card.
 *
 * Reads /api/v1/transformation-bandit/summary and displays the top
 * arm per (niche, dimension) — so operator can eyeball whether the
 * bandit is learning consistent preferences before flipping
 * ``GENLAB_INTELLIGENT_TRANSFORM_ENABLED``.
 *
 * ## What each niche row shows
 *
 * The top-rated arm per dimension in a compact grid — 11 columns,
 * one per transformation dimension. Each cell shows the arm value
 * + rate + observation count. Cells with 0 observations render
 * "cold-start" so the operator can see which arms haven't received
 * any reward yet.
 *
 * ## Active vs observation-only badge
 *
 * Server injects ``flag_enabled`` into the payload — reflects the
 * current ``GENLAB_INTELLIGENT_TRANSFORM_ENABLED`` runtime state.
 * When true → green "active" badge; when false → amber "observation
 * only" badge. Same pattern as CounterfactualReplayCard.
 *
 * ## Cold-start
 *
 * When no arms are registered yet (pre-run of
 * scripts/register_transformation_arms.py), server returns
 * ``niches: {}``. Card renders "No transformation arms yet — run
 * scripts/register_transformation_arms.py after PR 3 deploys" so
 * the operator has a clear next action.
 */
import { useQuery } from "@tanstack/react-query";

import { transformationBandit } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type {
  TransformationArm,
  TransformationNicheSummary,
} from "@/api/types";
import { NICHE_IDS, getNicheInfo, type NicheId } from "@/niches/registry";

/** Order the dimensions display columns are shown in. Matches the
 *  reward-attribution table in
 *  ``intelligent-transformation-architecture-2026-07-05`` memory. */
const DIMENSION_ORDER = [
  "hook_framing",
  "music_mood",
  "caption_style",
  "caption_pacing",
  "caption_emphasis_color",
  "intro_animation",
  "outro_cta",
  "pan_zoom",
  "highlight_moment",
  "audio_ducking",
  "music_visual_sync",
] as const;

function topArm(arms: TransformationArm[] | undefined): TransformationArm | null {
  if (!arms || arms.length === 0) return null;
  return arms[0]; // Server returns pre-sorted by rate desc.
}

function DimensionCell({ arms }: { arms: TransformationArm[] | undefined }) {
  const arm = topArm(arms);
  if (!arm || arm.n_obs === 0) {
    return (
      <div className="flex flex-col text-[10px] text-text-muted">
        <span className="font-mono">—</span>
        <span>cold-start</span>
      </div>
    );
  }
  const displayValue =
    arm.value.length > 10 ? `${arm.value.slice(0, 10)}…` : arm.value;
  return (
    <div className="flex flex-col text-[10px]" title={arm.arm_id}>
      <span className="font-mono">{displayValue}</span>
      <span className="text-text-muted">
        {(arm.rate * 100).toFixed(0)}% · n={arm.n_obs}
      </span>
    </div>
  );
}

function NicheRow({
  nicheId,
  summary,
}: {
  nicheId: NicheId;
  summary: TransformationNicheSummary | undefined;
}) {
  const info = getNicheInfo(nicheId);
  return (
    <div className="grid grid-cols-[80px_repeat(11,minmax(60px,1fr))] items-center gap-1 border-b border-border/40 py-2">
      <span
        className="text-xs font-semibold"
        style={{ color: info.color }}
        title={info.shortLabel}
      >
        {info.shortLabel}
      </span>
      {DIMENSION_ORDER.map((dim) => (
        <DimensionCell key={dim} arms={summary?.[dim]} />
      ))}
    </div>
  );
}

export function TransformationBanditCard() {
  const { data, isLoading, isError } = useQuery({
    queryKey: queryKeys.transformationBandit.summary(),
    queryFn: () => transformationBandit.summary(),
    // 60s polling matches AutoApprovalCalibrationCard — bandit posteriors
    // change per publish + reward cycle, ~1-2 per niche per day at
    // steady state; hourly would be too laggy for the "am I learning?"
    // question this card answers.
    refetchInterval: 60 * 1000,
    staleTime: 30 * 1000,
  });

  if (isLoading) {
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="text-sm font-semibold">Transformation bandit</div>
        <div className="mt-1 text-xs text-text-muted">Loading…</div>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="text-sm font-semibold">Transformation bandit</div>
        <div className="mt-1 text-xs text-text-muted">
          No transformation arms yet — run
          <code className="mx-1 rounded bg-surface-2 px-1 py-0.5 text-[10px]">
            scripts/register_transformation_arms.py
          </code>
          after PR 3 deploys.
        </div>
      </div>
    );
  }

  const flagBadge = data.flag_enabled ? (
    <span
      className="rounded border border-success/30 bg-success/10 px-1.5 py-0.5 text-[10px] text-success"
      title="GENLAB_INTELLIGENT_TRANSFORM_ENABLED=1 — render pipeline actively applies transformations"
    >
      active
    </span>
  ) : (
    <span
      className="rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-[10px] text-warning"
      title="GENLAB_INTELLIGENT_TRANSFORM_ENABLED is off — arms populate posteriors from mock rewards, no render-side transformation applied"
    >
      observation only
    </span>
  );

  const hasAnyNiche = Object.keys(data.niches).length > 0;

  return (
    <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
      <div className="mb-2 flex items-center gap-2">
        <div className="text-sm font-semibold">Transformation bandit</div>
        {flagBadge}
      </div>
      <div className="text-xs text-text-muted">
        Top arm per (niche × dimension) · updates 60s
      </div>
      {!hasAnyNiche ? (
        <div className="mt-2 text-xs text-text-muted">
          No arms registered yet — waiting for PR 3 bootstrap script to run
        </div>
      ) : (
        <>
          <div className="mt-3 grid grid-cols-[80px_repeat(11,minmax(60px,1fr))] gap-1 border-b border-border/40 pb-1 text-[10px] text-text-muted">
            <span></span>
            {DIMENSION_ORDER.map((dim) => (
              <span key={dim} title={dim}>
                {dim.replaceAll("_", " ").slice(0, 8)}
              </span>
            ))}
          </div>
          <div>
            {NICHE_IDS.map((n) => (
              <NicheRow
                key={n}
                nicheId={n}
                summary={data.niches[n]}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
