/**
 * Mission Control PerPlatformHealthCard (PR B, 2026-06-27).
 *
 * The existing publishing-health surface averages success rates ACROSS
 * niches × platforms, so a single (niche, platform) pair silently failing
 * for days is invisible at-a-glance: its noise gets averaged into the
 * global per-platform rate. Today's investigation surfaced ai_creators
 * Instagram failing 3 times in the last 7 days (8 in 60 days, all with
 * error 2207077) with the operator completely blind to it because:
 *   * Email = Never
 *   * Slack webhook empty
 *   * In-app notifications 99+ (operator fatigue)
 *   * The global publishing card averaged the failures into "IG looks fine"
 *
 * This card is the fine-grained matrix that surfaces those regressions:
 * one row per niche, one column per platform, each cell a 14d success-rate
 * percentage colored red / yellow / green. Tooltip shows the last failure
 * timestamp + error message + raw sample counts.
 *
 * ## Sibling cards
 *
 *   * NichePauseCard (PR #581) — operator emergency-stop UI
 *   * ComplianceStatsCard (PR after #582) — compliance signals diagnosis
 *
 *   All three share the 5-niche row layout + 60s polling + threshold-cell
 *   pattern so the operator's mental model is consistent.
 *
 * ## Severity thresholds
 *
 *   red    : success_rate_pct < 50      (active regression — investigate)
 *   yellow : 50 <= rate < 80            (degrading; monitor)
 *   green  : rate >= 80                  (healthy)
 *   grey   : insufficient data (<3 attempts in window)
 *
 *   Thresholds come from the API response (server-side constant) so
 *   future tuning doesn't need a frontend rebuild.
 *
 * ## Insufficient data
 *
 *   With our 1/day/channel cap, fewer than 3 publish attempts in the
 *   window is too noisy for red/yellow/green to be meaningful. Card
 *   renders "—" with greyed cell so operator distinguishes "no data"
 *   from "actually 0%" (which would be alarming red).
 */
import { useQuery } from "@tanstack/react-query";

import { publishingHealthPerNiche } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type {
  PerNichePlatformHealthResponse,
  PerNichePlatformHealthRow,
} from "@/api/types";
import { getNicheInfo, NICHE_IDS } from "@/niches/registry";

const WINDOW_DAYS = 14;

/** Color tone for a cell given its rate + thresholds. Pure function
 * exported for testing. */
export function classifyRate(
  rate: number | null,
  thresholds: { red_below_pct: number; yellow_below_pct: number },
): "grey" | "red" | "yellow" | "green" {
  if (rate === null) return "grey";
  if (rate < thresholds.red_below_pct) return "red";
  if (rate < thresholds.yellow_below_pct) return "yellow";
  return "green";
}

/** Truncate the error message to N chars + ellipsis for tooltip. */
function truncate(s: string | null, max = 80): string {
  if (!s) return "";
  if (s.length <= max) return s;
  return s.slice(0, max) + "…";
}

export function PerPlatformHealthCard() {
  const { data, isLoading, isError } = useQuery({
    queryKey: queryKeys.publishingHealthPerNiche.fetch(WINDOW_DAYS),
    queryFn: () => publishingHealthPerNiche.fetch(WINDOW_DAYS),
    staleTime: 60_000,
    refetchInterval: 60_000,
    retry: false,
  });

  // Build the (niche, platform) → row lookup so we can render the
  // matrix even when some cells are missing. Also gather the union of
  // platform names across all rows so we know which columns to render.
  const lookup = new Map<string, PerNichePlatformHealthRow>();
  const platformSet = new Set<string>();
  for (const r of data?.rows ?? []) {
    lookup.set(`${r.niche_id}::${r.platform}`, r);
    platformSet.add(r.platform);
  }
  // Stable column ordering — alphabetical so the layout is reproducible
  // and adding/removing platforms doesn't jiggle the headers around.
  const platforms = Array.from(platformSet).sort();

  const thresholds = data?.thresholds ?? {
    red_below_pct: 50,
    yellow_below_pct: 80,
  };

  // Count red cells across the entire matrix for the headline.
  let redCount = 0;
  let totalCellsWithData = 0;
  for (const r of data?.rows ?? []) {
    if (r.success_rate_pct === null) continue;
    totalCellsWithData++;
    if (r.success_rate_pct < thresholds.red_below_pct) redCount++;
  }

  const headlineDot =
    redCount > 0
      ? "bg-error"
      : totalCellsWithData === 0
        ? "bg-text-muted"
        : "bg-success";

  return (
    <div className="bento-card">
      <h3 className="card-title">
        Per-platform Health
        <span className="ml-2 text-[10px] text-text-muted font-normal">
          niche × platform · last {WINDOW_DAYS}d
        </span>
      </h3>

      {/* Headline — at-a-glance count of red (niche, platform) cells.
          Mirrors the sibling cards' dot-+-summary pattern so operator
          finds it in the same place. */}
      <div className="flex items-center gap-2 mb-3">
        <span className={`size-2 rounded-full ${headlineDot}`} />
        <span className="text-sm text-text-secondary">
          {isError
            ? "Health endpoint unreachable"
            : totalCellsWithData === 0
              ? "Insufficient data across all combinations"
              : redCount === 0
                ? `All ${totalCellsWithData} combinations healthy`
                : `${redCount} of ${totalCellsWithData} niche-platform combinations red`}
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
        ) : platforms.length === 0 ? (
          <div className="text-xs text-text-muted py-2">
            No publish attempts in the window — waiting for data.
          </div>
        ) : (
          <HealthMatrix
            platforms={platforms}
            lookup={lookup}
            thresholds={thresholds}
          />
        )}
      </div>

      {/* Footer — context for the thresholds + what "insufficient" means. */}
      <div className="mt-3 pt-2 border-t border-text-muted/10">
        <span className="text-[10px] text-text-muted">
          Red &lt; {thresholds.red_below_pct}% · yellow &lt;{" "}
          {thresholds.yellow_below_pct}% · grey = &lt;3 attempts in window
        </span>
      </div>
    </div>
  );
}

interface HealthMatrixProps {
  platforms: string[];
  lookup: Map<string, PerNichePlatformHealthRow>;
  thresholds: PerNichePlatformHealthResponse["thresholds"];
}

function HealthMatrix({ platforms, lookup, thresholds }: HealthMatrixProps) {
  return (
    <table
      className="w-full text-xs border-collapse"
      role="table"
      aria-label="Per-niche per-platform publishing health matrix"
    >
      <thead>
        <tr>
          <th className="text-left py-1 px-1 font-medium text-text-muted">
            Niche
          </th>
          {platforms.map((p) => (
            <th
              key={p}
              className="text-center py-1 px-1 font-medium text-text-muted capitalize"
              scope="col"
            >
              {p}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {NICHE_IDS.map((nicheId) => {
          const info = getNicheInfo(nicheId);
          return (
            <tr key={nicheId}>
              <th
                scope="row"
                className="text-left py-1 px-1 font-medium text-text-secondary whitespace-nowrap"
              >
                <span className="inline-flex items-center gap-1.5">
                  <span
                    className="size-2 rounded-full shrink-0"
                    style={{ background: info.hex }}
                  />
                  <span>{info.shortLabel}</span>
                </span>
              </th>
              {platforms.map((platform) => {
                const row = lookup.get(`${nicheId}::${platform}`);
                return (
                  <HealthCell
                    key={platform}
                    row={row}
                    thresholds={thresholds}
                  />
                );
              })}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

interface HealthCellProps {
  row: PerNichePlatformHealthRow | undefined;
  thresholds: PerNichePlatformHealthResponse["thresholds"];
}

function HealthCell({ row, thresholds }: HealthCellProps) {
  // No row at all = the niche × platform has zero rows in publishing_analytics
  // for the window. Render as insufficient-data ("—") because semantically
  // "no attempts at all" and "<3 attempts" are operator-equivalent ("we
  // can't grade you").
  if (!row || row.success_rate_pct === null) {
    return (
      <td
        className="text-center py-1 px-1 bg-text-muted/5 text-text-muted"
        data-testid="health-cell"
        data-tone="grey"
        title={
          row
            ? `Insufficient data: ${row.success_count} success / ${row.failed_count} failed`
            : "No publish attempts in window"
        }
      >
        —
      </td>
    );
  }

  const tone = classifyRate(row.success_rate_pct, thresholds);
  const cellClass = {
    red: "bg-error/15 text-error",
    yellow: "bg-warning/15 text-warning",
    green: "bg-success/15 text-success",
    grey: "bg-text-muted/5 text-text-muted",
  }[tone];

  // Tooltip surfaces the actionable detail without crowding the cell.
  // Format: last failure timestamp + first 80 chars of error + sample counts.
  const tooltipParts: string[] = [];
  tooltipParts.push(
    `${row.success_count} success / ${row.failed_count} failed`,
  );
  if (row.last_success_at) {
    tooltipParts.push(`Last success: ${row.last_success_at}`);
  }
  if (row.last_failure_at) {
    tooltipParts.push(`Last failure: ${row.last_failure_at}`);
  }
  if (row.last_failure_error) {
    tooltipParts.push(`Error: ${truncate(row.last_failure_error)}`);
  }
  const title = tooltipParts.join("\n");

  return (
    <td
      className={`text-center py-1 px-1 font-mono ${cellClass}`}
      data-testid="health-cell"
      data-tone={tone}
      title={title}
    >
      {row.success_rate_pct}%
    </td>
  );
}
