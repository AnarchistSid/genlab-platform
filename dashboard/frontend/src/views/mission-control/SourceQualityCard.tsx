/**
 * Source-quality card on Mission Control.
 *
 * Surfaces the 22-of-30-top-sources-have-0%-claim-rate finding from
 * the 2026-06-13 autonomy deep-dive. Operator scans the table, picks
 * the worst offenders, prunes them from each niche's sources.yaml.
 *
 * Design:
 *   - Headline pill counts each health bucket (active / weak / dead / unproven)
 *   - Top N sources by fetch volume — the operator's at-a-glance "what
 *     am I paying to ingest?" view
 *   - Each row color-coded via StatusBadge so red/yellow/green is
 *     visible in 1 second
 *   - "Show all" toggles between top 10 and the full set (up to 200)
 *
 * Accessibility:
 *   - <table> semantics with proper <th scope="col">
 *   - StatusBadge components handle aria-live for status updates
 *   - Keyboard: native table semantics; toggle button is a real <button>
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  sources,
  type SourcePerformanceResponse,
  type SourceHealth,
} from "@/api/client";
import { StatusBadge } from "@/components/ui/status-badge";

const DEFAULT_VISIBLE = 10;

// Map health bucket → StatusBadge variant. Keeps the visual language
// consistent with other status indicators on Mission Control.
const HEALTH_VARIANT: Record<SourceHealth, "success" | "warning" | "error" | "neutral"> = {
  active: "success",
  weak: "warning",
  dead: "error",
  unproven: "neutral",
};

const HEALTH_LABEL: Record<SourceHealth, string> = {
  active: "active",
  weak: "weak",
  dead: "DEAD — prune",
  unproven: "new",
};

export function SourceQualityCard() {
  const [showAll, setShowAll] = useState(false);

  const { data, isLoading, isError } = useQuery<SourcePerformanceResponse>({
    queryKey: ["sources-performance", 14],
    queryFn: () => sources.performance(14, 5),
    // Source performance doesn't change minute-by-minute — refresh every
    // 5 min so the operator sees fresh signal without polling overhead.
    staleTime: 300_000,
    refetchInterval: 300_000,
    retry: false,
  });

  if (isError) {
    return (
      <div className="bento-card">
        <h3 className="card-title">Source Quality</h3>
        <p className="text-xs text-text-muted">
          Failed to load source performance data
        </p>
      </div>
    );
  }

  if (isLoading || !data) {
    return (
      <div className="bento-card">
        <h3 className="card-title">Source Quality</h3>
        <div className="space-y-1">
          {Array.from({ length: 3 }).map((_, i) => (
            <div
              key={i}
              className="shimmer"
              style={{ height: 14, width: "100%", borderRadius: 4 }}
            />
          ))}
        </div>
      </div>
    );
  }

  const visible = showAll ? data.sources : data.sources.slice(0, DEFAULT_VISIBLE);
  const overallPct = Math.round(data.claim_rate * 1000) / 10; // 1 decimal

  return (
    <div className="bento-card">
      <h3 className="card-title">
        Source Quality
        <span className="ml-2 text-[10px] text-text-muted font-normal">
          rolling {data.window_days}d
        </span>
      </h3>

      {/* Headline row — what's the overall claim rate + bucket counts */}
      <div className="flex flex-wrap items-center gap-2 mb-3 text-xs">
        <span className="text-text-secondary">
          {data.total_claimed.toLocaleString()} /{" "}
          {data.total_fetched.toLocaleString()} claimed
        </span>
        <span className="text-text-muted">·</span>
        <span className="font-mono text-text-primary">{overallPct}%</span>
        <div className="flex-1" />
        {(["active", "weak", "dead", "unproven"] as SourceHealth[]).map(
          (bucket) =>
            data.bucket_counts[bucket] > 0 && (
              <StatusBadge
                key={bucket}
                status={HEALTH_VARIANT[bucket]}
                size="xs"
                srLabel={`${data.bucket_counts[bucket]} ${bucket} sources`}
              >
                {data.bucket_counts[bucket]} {bucket}
              </StatusBadge>
            ),
        )}
      </div>

      {/* Top-N rows — operator's primary working set */}
      {visible.length === 0 ? (
        <p className="text-xs text-text-muted italic py-2">
          No sources with ≥5 fetches in the last {data.window_days}d
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="data-table text-xs">
            <thead>
              <tr>
                <th scope="col" className="text-left">Source</th>
                <th scope="col" className="text-right">Fetched</th>
                <th scope="col" className="text-right">Claimed</th>
                <th scope="col" className="text-right">Rate</th>
                <th scope="col" className="text-left">Health</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((src) => (
                <tr key={`${src.source_name}__${src.source_platform}`}>
                  <td className="text-left">
                    <span className="text-text-primary">{src.source_name}</span>
                    <span className="ml-2 text-[10px] text-text-muted font-mono">
                      {src.source_platform}
                    </span>
                  </td>
                  <td className="text-right font-mono text-text-secondary">
                    {src.fetched}
                  </td>
                  <td className="text-right font-mono text-text-secondary">
                    {src.claimed}
                  </td>
                  <td
                    className={`text-right font-mono ${
                      src.health === "dead"
                        ? "text-error"
                        : src.health === "weak"
                        ? "text-warning"
                        : src.health === "active"
                        ? "text-success"
                        : "text-text-muted"
                    }`}
                  >
                    {src.claim_pct.toFixed(1)}%
                  </td>
                  <td className="text-left">
                    <StatusBadge
                      status={HEALTH_VARIANT[src.health]}
                      size="xs"
                    >
                      {HEALTH_LABEL[src.health]}
                    </StatusBadge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data.sources.length > DEFAULT_VISIBLE && (
        <button
          type="button"
          onClick={() => setShowAll((v) => !v)}
          className="mt-2 text-xs text-text-muted hover:text-text-primary underline underline-offset-2"
        >
          {showAll
            ? `Show top ${DEFAULT_VISIBLE}`
            : `Show all ${data.sources.length}`}
        </button>
      )}
    </div>
  );
}
