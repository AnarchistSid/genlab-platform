/**
 * A + B intelligence stack observability card (2026-07-08) — top-
 * creator prior correlations (B.2) + latest upload snapshots (A.2).
 *
 * ## What each niche row shows
 *
 * One compact row per niche summarising both artifacts:
 *
 *   * ``top correlation`` — B.2's single strongest |r| feature and
 *     its Spearman value. Weekly refit; hourly poll.
 *   * ``fresh uploads`` — A.2's count of watchlist creators whose
 *     latest upload is < 24h old (i.e. actually contributes to
 *     ``_signal_creator_upload_lead`` per A.3's recency weight).
 *     Below 24h grows amber then green; above stays grey.
 *
 * ## Rendering rules
 *
 *   * ``flag_enabled`` per artifact          → green "active" badge
 *   * ``!flag_enabled``                      → amber "obs only" badge
 *   * cold-start (data=null)                 → "no {corr,uploads} yet"
 *
 * ## Independent flags
 *
 * The two data sources have SEPARATE flag env vars because the
 * runners are independent (A.2 fires 4×/day, B.2 fires weekly).
 * Either can be active while the other stays observation-only:
 *
 *   * B.2 correlations   → ``GENLAB_TOP_CREATOR_PRIORS_ENABLED``
 *   * A.2 uploads        → ``GENLAB_TOP_CREATORS_ENABLED``
 *
 * ## Sibling cards
 *
 * Same visual language as ``CounterfactualReplayCard`` —
 * per-niche row layout, mono numeric columns, per-niche staleness
 * badges. The staleness rendering matches
 * ``_signal_creator_upload_lead``'s recency-weight schedule so the
 * card doesn't lie about what A.3 will actually see when it reads
 * the same artifact.
 */
import { useQuery } from "@tanstack/react-query";

import { topCreatorPriors } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type {
  TopCreatorPriorsArtifact,
  TopCreatorUploadsArtifact,
} from "@/api/types";
import { NICHE_IDS, getNicheInfo, type NicheId } from "@/niches/registry";

function FlagChip({
  enabled,
  label,
  activeTitle,
  offTitle,
}: {
  enabled: boolean;
  label: string;
  activeTitle: string;
  offTitle: string;
}) {
  if (enabled) {
    return (
      <span
        className="rounded border border-success/30 bg-success/10 px-1 py-0.5 text-[10px] text-success"
        title={activeTitle}
      >
        {label}
      </span>
    );
  }
  return (
    <span
      className="rounded border border-warning/30 bg-warning/10 px-1 py-0.5 text-[10px] text-warning"
      title={offTitle}
    >
      {label}
    </span>
  );
}

/** Pick the strongest-signal feature — the one whose Spearman |r|
 *  is largest. Positive r means feature correlates with high view
 *  velocity; negative means low. Sign is preserved in the display so
 *  the operator sees the direction, not just magnitude. */
function topCorrelation(
  correlations: Record<string, number>,
): { feature: string; r: number } | null {
  const entries = Object.entries(correlations ?? {});
  if (entries.length === 0) return null;
  const [feature, r] = entries.reduce((best, cur) =>
    Math.abs(cur[1]) > Math.abs(best[1]) ? cur : best,
  );
  return { feature, r };
}

/** Count uploads whose recency contributes anything to A.3's
 *  composite. A.3's schedule: ``≤6h → 1.0``, ``6-24h → 1.0→0.5``,
 *  ``>24h → 0.0``. So the fresh count is everything ≤ 24h. */
function freshUploadsCount(artifact: TopCreatorUploadsArtifact): number {
  let count = 0;
  for (const creator of artifact.creators ?? []) {
    const latest = creator.uploads?.[0];
    if (latest && latest.hours_since_publish <= 24) count += 1;
  }
  return count;
}

function NicheRow({ nicheId }: { nicheId: NicheId }) {
  const info = getNicheInfo(nicheId);

  const priorsQuery = useQuery({
    queryKey: queryKeys.topCreatorPriors.latest(nicheId),
    queryFn: () => topCreatorPriors.latest(nicheId),
    // Weekly refit (Sun 04:00 UTC) + hourly poll — matches
    // CrossNichePriorsCard cadence.
    refetchInterval: 60 * 60 * 1000,
    staleTime: 30 * 60 * 1000,
  });

  const uploadsQuery = useQuery({
    queryKey: queryKeys.topCreatorPriors.uploads(nicheId),
    queryFn: () => topCreatorPriors.uploads(nicheId),
    // A.2 fires 4×/day (every 6h) — 10-min poll balances fresh-ness
    // with server load. Matches TrendAnticipationCard cadence.
    refetchInterval: 10 * 60 * 1000,
    staleTime: 5 * 60 * 1000,
  });

  const priors = priorsQuery.data as TopCreatorPriorsArtifact | null | undefined;
  const uploads = uploadsQuery.data as TopCreatorUploadsArtifact | null | undefined;

  const topCorr = priors ? topCorrelation(priors.correlations) : null;
  const freshCount = uploads ? freshUploadsCount(uploads) : 0;
  const creatorTotal = uploads ? (uploads.creators ?? []).length : 0;

  // Fresh-count color mirrors A.3's recency-weight thresholds so the
  // operator's mental model matches the composite maths.
  let freshColor = "text-text-muted";
  if (freshCount >= 3) freshColor = "text-success";
  else if (freshCount >= 1) freshColor = "text-warning";

  return (
    <div className="flex items-center justify-between gap-2 border-b border-border/40 py-2 text-sm">
      <div className="flex items-center gap-2">
        <span className="font-semibold" style={{ color: info.color }}>
          {info.shortLabel}
        </span>
        {priors ? (
          <FlagChip
            enabled={priors.flag_enabled}
            label="B.2"
            activeTitle="GENLAB_TOP_CREATOR_PRIORS_ENABLED=true — priors would be read at arm registration"
            offTitle="GENLAB_TOP_CREATOR_PRIORS_ENABLED off — correlations written but not consumed"
          />
        ) : null}
        {uploads ? (
          <FlagChip
            enabled={uploads.flag_enabled}
            label="A.2"
            activeTitle="GENLAB_TOP_CREATORS_ENABLED=true — poll runner is active"
            offTitle="GENLAB_TOP_CREATORS_ENABLED off — poll runner is a no-op"
          />
        ) : null}
      </div>
      <div className="flex items-center gap-3 text-xs">
        {topCorr ? (
          <span title={`Spearman r=${topCorr.r.toFixed(3)} for ${topCorr.feature}`}>
            <span className="text-text-muted">top=</span>
            <span className="font-mono">{topCorr.feature.slice(0, 22)}</span>{" "}
            <span
              className={`font-mono ${topCorr.r >= 0 ? "text-success" : "text-warning"}`}
            >
              {topCorr.r >= 0 ? "+" : ""}
              {topCorr.r.toFixed(2)}
            </span>
          </span>
        ) : priorsQuery.isLoading ? (
          <span className="text-text-muted">loading corr…</span>
        ) : (
          <span className="text-text-muted">no corr yet</span>
        )}
        <span title="Watchlist creators with an upload in the last 24 hours (counts toward A.3's composite)">
          <span className="text-text-muted">fresh=</span>
          <span className={`font-mono ${freshColor}`}>
            {freshCount}
            {creatorTotal > 0 ? `/${creatorTotal}` : ""}
          </span>
        </span>
      </div>
    </div>
  );
}

export function TopCreatorPriorsCard() {
  return (
    <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
      <div className="mb-2">
        <div className="text-sm font-semibold">Top-creator intelligence</div>
        <div className="text-xs text-text-muted">
          A+B stack observability · weekly Spearman correlations + 4×/day
          upload watchlist
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
