/**
 * W4.4 (2026-06-17): Per-niche agreement-rate trend over time.
 *
 * Complements AutoApprovalCalibrationCard:
 *   - Calibration card: single-window snapshot (ready_for_enforcement gate)
 *   - This card: per-day trend so operator sees if quality is climbing
 *     toward readiness, flat, or regressing
 *
 * Visual language:
 *   - 5 niche rows, one per niche
 *   - Inline sparkline (CSS-only — no chart lib) showing per-day rate
 *   - Overall rate over 30-day window + sample count
 *   - Color-codes the latest bin: green ≥ 0.9, amber 0.7-0.9, red < 0.7
 *
 * No data path: niches with no calibration reviews in the window show
 * "No reviews in window" — never blank.
 */
import { useQueries } from "@tanstack/react-query";
import { autoApproval, type TrackRecordBin } from "@/api/client";
import { getNicheInfo, NICHE_IDS } from "@/niches/registry";

const WINDOW_DAYS = 30;
const RATE_GREEN = 0.9;
const RATE_AMBER = 0.7;

export function TrackRecordCard() {
  const results = useQueries({
    queries: NICHE_IDS.map((nicheId) => ({
      queryKey: ["auto-approval-track-record", nicheId, WINDOW_DAYS],
      queryFn: () => autoApproval.trackRecord(nicheId, WINDOW_DAYS),
      // Match calibration card cadence for consistency
      staleTime: 60_000,
      refetchInterval: 60_000,
      retry: false,
    })),
  });

  const anyLoading = results.some((r) => r.isLoading);
  const anyError = results.some((r) => r.isError);

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
      <div className="mb-3 flex items-baseline justify-between">
        <h3 className="text-sm font-medium text-zinc-100">
          Auto-approval track record
        </h3>
        <span className="text-xs text-zinc-500">
          Last {WINDOW_DAYS} days
        </span>
      </div>

      {anyLoading && (
        <div className="text-xs text-zinc-500">Loading...</div>
      )}
      {anyError && (
        <div className="text-xs text-rose-400">
          Failed to load some niches — retrying on next poll.
        </div>
      )}

      <div className="space-y-2">
        {NICHE_IDS.map((nicheId, i) => {
          const niche = getNicheInfo(nicheId);
          const result = results[i];
          const data = result.data;
          return (
            <NicheRow
              key={nicheId}
              accent={niche.hex}
              label={niche.label}
              bins={data?.bins ?? []}
              overall={data?.overall}
            />
          );
        })}
      </div>
    </div>
  );
}

interface NicheRowProps {
  accent: string;
  label: string;
  bins: TrackRecordBin[];
  // W3 (2026-06-18): rollup now carries engagement aggregates.
  overall:
    | {
        sample_count: number;
        agreement: number;
        rate: number;
        collected_count: number;
        avg_reward_48h: number | null;
      }
    | undefined;
}

function NicheRow({ accent, label, bins, overall }: NicheRowProps) {
  const noData = !overall || overall.sample_count === 0;
  const latestRate = bins.length > 0 ? bins[bins.length - 1].rate : 0;
  const ratePillColor =
    latestRate >= RATE_GREEN
      ? "text-emerald-400"
      : latestRate >= RATE_AMBER
        ? "text-amber-400"
        : "text-rose-400";

  return (
    <div className="flex items-center gap-3 rounded border border-zinc-800/60 bg-zinc-900/40 px-3 py-2">
      {/* Accent dot */}
      <span
        className="h-2 w-2 flex-shrink-0 rounded-full"
        style={{ backgroundColor: accent }}
        aria-hidden
      />
      {/* Niche label */}
      <span className="w-28 flex-shrink-0 text-xs font-medium text-zinc-200">
        {label}
      </span>

      {noData ? (
        <span className="text-xs text-zinc-500">
          No reviews in window
        </span>
      ) : (
        <>
          {/* Inline sparkline */}
          <Sparkline bins={bins} />
          {/* Overall agreement rate */}
          <span className={`ml-auto text-xs tabular-nums ${ratePillColor}`}>
            {Math.round((overall?.rate ?? 0) * 100)}%
          </span>
          <span className="text-xs text-zinc-500 tabular-nums">
            ({overall?.sample_count ?? 0})
          </span>
          {/* W3 (2026-06-18): engagement chip. Shows reward signal
              alongside calibration agreement so the operator can spot
              "high agreement BUT low engagement" (alignment-on-garbage)
              in one glance. */}
          <EngagementChip
            collectedCount={overall?.collected_count ?? 0}
            avgReward={overall?.avg_reward_48h ?? null}
          />
        </>
      )}
    </div>
  );
}

/**
 * W3 engagement chip. Three states:
 *   - no data (collected_count === 0): muted "—" placeholder so the
 *     row stays the same width across niches (no layout shift)
 *   - has data: shows "R X.XXX (N)" — R is the avg_reward_48h to
 *     3 decimals, N is the collected platform-count. Operator
 *     scanning the column sees the magnitude immediately; the
 *     tooltip gives the full breakdown.
 */
function EngagementChip({
  collectedCount,
  avgReward,
}: {
  collectedCount: number;
  avgReward: number | null;
}) {
  if (collectedCount === 0 || avgReward === null) {
    return (
      <span
        className="text-xs text-zinc-600 tabular-nums"
        title="No 48h-window reward data yet for this window's posts"
      >
        R —
      </span>
    );
  }
  return (
    <span
      className="text-xs text-cyan-400 tabular-nums"
      title={`Mean reward_48h across ${collectedCount} collected platform-publish rows in the window`}
    >
      R {avgReward.toFixed(3)} ({collectedCount})
    </span>
  );
}

/**
 * CSS-only sparkline: one inline-flex container per bin, height
 * proportional to rate, color graduated red→amber→green. No chart
 * library required — the data is at most 30 points and we don't need
 * axes or hover.
 */
function Sparkline({ bins }: { bins: TrackRecordBin[] }) {
  const maxBins = 30;
  // Pad/trim to maxBins so the visual width is consistent across niches
  const display = bins.slice(-maxBins);

  return (
    <div
      className="flex h-6 items-end gap-px"
      style={{ width: `${maxBins * 4}px` }}
      aria-label={`Per-day agreement rate, last ${display.length} days`}
    >
      {display.map((b) => {
        const pct = Math.round(b.rate * 100);
        const colorClass =
          b.rate >= RATE_GREEN
            ? "bg-emerald-500"
            : b.rate >= RATE_AMBER
              ? "bg-amber-500"
              : "bg-rose-500";
        return (
          <div
            key={b.date}
            className={`w-1 ${colorClass} opacity-80`}
            style={{ height: `${Math.max(pct, 5)}%` }}
            title={`${b.date}: ${b.agreement}/${b.sample_count} (${pct}%)`}
          />
        );
      })}
    </div>
  );
}
