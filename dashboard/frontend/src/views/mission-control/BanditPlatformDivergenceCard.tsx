/**
 * Mission Control BanditPlatformDivergenceCard (PR AG, 2026-06-29).
 *
 * PR #641 (shipped + activated 2026-06-29) writes per-platform bandit
 * arms (``content_type__platform``) alongside the base ``content_type``
 * arm. Starting today, every metric_collector reward update writes
 * BOTH the base arm AND the platform-specific variant.
 *
 * After ~2 weeks of accumulation, the per-platform arms should DIVERGE
 * from the base — that's the WHOLE point of the per-platform split:
 * the bandit learns that sports prefers different hook types on YT vs
 * IG, ai_creators prefers different hook types on YT vs IG, etc.
 *
 * Without this surface, the operator has no way to see whether the
 * per-platform mechanism is producing meaningful signal vs collapsing
 * back to the base arm (which would mean the split is wasted complexity).
 *
 * ## Sibling cards
 *
 *   * NichePauseCard / ComplianceStatsCard / PerPlatformHealthCard —
 *     all use 60s polling + threshold-driven highlight + zero-state
 *     copy. Same visual language so the operator's mental model is
 *     consistent.
 *   * BanditHourHeatmap (PR BB) — sibling bandit surface (per-hour
 *     posteriors). This card is per-(niche, base_arm); that one is
 *     per-(niche, hour). Together they cover both axes the operator
 *     reasons about ("which content types?" + "which hours?").
 *
 * ## Star threshold
 *
 *   The card stars rows where ``|IG mean - YT mean| * 100 ≥
 *   thresholds.meaningful_delta_pct`` (default 5%). A 5% gap between
 *   the IG and YT posteriors is operator-meaningful: it's the
 *   difference between "treat both platforms the same" and "shift the
 *   content strategy to favour the better-performing platform." Below
 *   5% the noise floor dominates and the operator shouldn't act.
 *
 * ## Cold-start handling
 *
 *   When a variant has ``n_plays < thresholds.cold_start_below_n``
 *   (default 5), the card renders a ``(cold)`` indicator next to the
 *   variant's mean. The mean is still shown (cold-start posterior is
 *   the Beta(1,1) prior + a few observations — it's data, not noise),
 *   but the operator should weight it less when comparing.
 *
 *   The backend already filters rows where ALL THREE variants are
 *   cold, so this card only shows partial-cold rows.
 *
 * ## Empty state
 *
 *   "Per-platform bandit arms accumulate posteriors over ~2 weeks.
 *   Currently no arms have ≥5 obs per platform variant. Come back
 *   after 2026-07-13."
 *
 *   The date is computed from PR #641's activation date (2026-06-29)
 *   + 14 days. Operator gets a concrete "when to check back" not a
 *   vague "be patient."
 */
import { useQuery } from "@tanstack/react-query";

import { banditPlatformDivergence } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type {
  BanditPlatformDivergenceResponse,
  BanditPlatformDivergenceRow,
} from "@/api/types";
import { getNicheInfo } from "@/niches/registry";

const DEFAULT_MIN_N_PLAYS = 5;

// PR #641 activated 2026-06-29 — operator-meaningful divergence
// expected after ~2 weeks of natural accumulation.
const ACCUMULATION_HORIZON = "2026-07-13";

/** Format a posterior mean as a 2-dp string. Pure for testing. */
export function formatMean(m: number | null): string {
  if (m === null) return "—";
  return m.toFixed(2);
}

/** Determine whether a row's IG↔YT delta crosses the "meaningful"
 * threshold (star it on the card). Pure for testing. */
export function hasMeaningfulDelta(
  row: BanditPlatformDivergenceRow,
  meaningfulDeltaPct: number,
): boolean {
  if (row.ig_yt_delta === null) return false;
  return row.ig_yt_delta * 100 >= meaningfulDeltaPct;
}

/** Whether the given variant's n_plays falls below the cold-start floor. */
export function isCold(n: number, coldBelow: number): boolean {
  return n < coldBelow;
}

export function BanditPlatformDivergenceCard() {
  const { data, isLoading, isError } = useQuery({
    queryKey: queryKeys.banditPlatformDivergence.fetch(
      undefined,
      DEFAULT_MIN_N_PLAYS,
    ),
    queryFn: () =>
      banditPlatformDivergence.fetch({ min_n_plays: DEFAULT_MIN_N_PLAYS }),
    staleTime: 60_000,
    refetchInterval: 60_000,
    retry: false,
  });

  const thresholds = data?.thresholds ?? {
    meaningful_delta_pct: 5.0,
    cold_start_below_n: 5,
  };
  const rows = data?.rows ?? [];

  // Count rows that cross the meaningful-delta threshold for the
  // headline summary.
  const meaningfulCount = rows.filter((r) =>
    hasMeaningfulDelta(r, thresholds.meaningful_delta_pct),
  ).length;

  const headlineDot =
    isError
      ? "bg-error"
      : meaningfulCount > 0
        ? "bg-success"
        : rows.length === 0
          ? "bg-text-muted"
          : "bg-warning";

  return (
    <div className="bento-card">
      <h3 className="card-title">
        Bandit per-platform divergence
        <span className="ml-2 text-[10px] text-text-muted font-normal">
          niche · arm · base vs IG / YT
        </span>
      </h3>

      {/* Headline — at-a-glance count of arms crossing the meaningful
          delta threshold. Mirrors sibling cards' dot+summary pattern. */}
      <div className="flex items-center gap-2 mb-3">
        <span className={`size-2 rounded-full ${headlineDot}`} />
        <span className="text-sm text-text-secondary">
          {isError
            ? "Divergence endpoint unreachable"
            : rows.length === 0
              ? "No qualifying arms yet"
              : `${meaningfulCount} of ${rows.length} arms show meaningful (≥${thresholds.meaningful_delta_pct}%) per-platform divergence`}
        </span>
      </div>

      <div className="overflow-x-auto">
        {isLoading && !data ? (
          <div className="flex flex-col gap-2">
            <div
              className="shimmer"
              style={{ height: 14, width: "100%", borderRadius: 4 }}
            />
            <div
              className="shimmer"
              style={{ height: 14, width: "100%", borderRadius: 4 }}
            />
            <div
              className="shimmer"
              style={{ height: 14, width: "100%", borderRadius: 4 }}
            />
          </div>
        ) : rows.length === 0 ? (
          <div className="text-xs text-text-muted py-2">
            Per-platform bandit arms accumulate posteriors over ~2 weeks.
            Currently no arms have ≥{thresholds.cold_start_below_n} obs per
            platform variant. Come back after {ACCUMULATION_HORIZON}.
          </div>
        ) : (
          <DivergenceTable rows={rows} thresholds={thresholds} />
        )}
      </div>

      {/* Footer — threshold legend so the star icon is unambiguous. */}
      <div className="mt-3 pt-2 border-t border-text-muted/10">
        <span className="text-[10px] text-text-muted">
          ⭐ |IG − YT| ≥ {thresholds.meaningful_delta_pct}% · (cold) = &lt;
          {thresholds.cold_start_below_n} obs · means are posterior alpha/
          (alpha+beta)
        </span>
      </div>
    </div>
  );
}

interface DivergenceTableProps {
  rows: BanditPlatformDivergenceRow[];
  thresholds: BanditPlatformDivergenceResponse["thresholds"];
}

function DivergenceTable({ rows, thresholds }: DivergenceTableProps) {
  return (
    <table
      className="w-full text-xs border-collapse"
      role="table"
      aria-label="Per-niche per-arm bandit divergence by platform"
    >
      <thead>
        <tr>
          <th className="text-left py-1 px-1 font-medium text-text-muted">
            Niche · Arm
          </th>
          <th className="text-right py-1 px-1 font-medium text-text-muted">
            Base
          </th>
          <th className="text-right py-1 px-1 font-medium text-text-muted">
            IG
          </th>
          <th className="text-right py-1 px-1 font-medium text-text-muted">
            YT
          </th>
          <th className="text-right py-1 px-1 font-medium text-text-muted">
            IG↔YT
          </th>
          <th className="text-center py-1 px-1 font-medium text-text-muted">
            ★
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <DivergenceRow
            key={`${r.niche_id}::${r.base_arm_id}`}
            row={r}
            thresholds={thresholds}
          />
        ))}
      </tbody>
    </table>
  );
}

interface DivergenceRowProps {
  row: BanditPlatformDivergenceRow;
  thresholds: BanditPlatformDivergenceResponse["thresholds"];
}

function DivergenceRow({ row, thresholds }: DivergenceRowProps) {
  const meaningful = hasMeaningfulDelta(row, thresholds.meaningful_delta_pct);
  const igCold = isCold(row.instagram_n_plays, thresholds.cold_start_below_n);
  const ytCold = isCold(row.youtube_n_plays, thresholds.cold_start_below_n);
  const info = getNicheInfo(row.niche_id);

  // Format delta as ±N% with one decimal. Null → "—".
  const deltaText =
    row.ig_yt_delta === null
      ? "—"
      : `+${(row.ig_yt_delta * 100).toFixed(1)}%`;

  // Tooltip surfaces the raw counts + signed deltas from base so the
  // operator can drill in without a separate inspector.
  const tooltipParts: string[] = [];
  tooltipParts.push(
    `base n=${row.base_n_plays} · IG n=${row.instagram_n_plays} · YT n=${row.youtube_n_plays}`,
  );
  if (row.ig_delta_from_base !== null) {
    const sign = row.ig_delta_from_base >= 0 ? "+" : "";
    tooltipParts.push(
      `IG vs base: ${sign}${(row.ig_delta_from_base * 100).toFixed(1)}%`,
    );
  }
  if (row.yt_delta_from_base !== null) {
    const sign = row.yt_delta_from_base >= 0 ? "+" : "";
    tooltipParts.push(
      `YT vs base: ${sign}${(row.yt_delta_from_base * 100).toFixed(1)}%`,
    );
  }
  const tooltip = tooltipParts.join("\n");

  return (
    <tr data-testid="divergence-row" data-meaningful={meaningful} title={tooltip}>
      <th
        scope="row"
        className="text-left py-1 px-1 font-medium text-text-secondary whitespace-nowrap"
      >
        <span className="inline-flex items-center gap-1.5">
          <span
            className="size-2 rounded-full shrink-0"
            style={{ background: info.hex }}
          />
          <span>
            <span className="text-text-muted">{info.shortLabel}</span> ·{" "}
            <span className="font-mono">{row.base_arm_id}</span>
          </span>
        </span>
      </th>
      <td className="text-right py-1 px-1 font-mono text-text-secondary">
        {formatMean(row.base_mean)}
      </td>
      <td
        className="text-right py-1 px-1 font-mono"
        data-testid="ig-cell"
        data-cold={igCold}
      >
        <span>{formatMean(row.instagram_mean)}</span>
        {igCold && row.instagram_mean !== null && (
          <span className="ml-1 text-[9px] text-text-muted">(cold)</span>
        )}
      </td>
      <td
        className="text-right py-1 px-1 font-mono"
        data-testid="yt-cell"
        data-cold={ytCold}
      >
        <span>{formatMean(row.youtube_mean)}</span>
        {ytCold && row.youtube_mean !== null && (
          <span className="ml-1 text-[9px] text-text-muted">(cold)</span>
        )}
      </td>
      <td
        className={`text-right py-1 px-1 font-mono ${
          meaningful ? "text-success" : "text-text-secondary"
        }`}
      >
        {deltaText}
      </td>
      <td
        className="text-center py-1 px-1"
        data-testid="star-cell"
        data-star={meaningful}
      >
        {meaningful ? <span title="Operator-meaningful divergence">⭐</span> : null}
      </td>
    </tr>
  );
}
