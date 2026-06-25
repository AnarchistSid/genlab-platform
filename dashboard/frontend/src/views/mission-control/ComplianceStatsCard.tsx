/**
 * Mission Control ComplianceStatsCard (PR after #581, 2026-06-25).
 *
 * Surfaces the Phase A compliance moat's warn-only signals to the
 * operator. Without this card, the data in compliance_events is only
 * visible via raw curl on /api/v1/compliance/events (per-row, not
 * aggregated). This card is the at-a-glance view: per-niche warn
 * count + the top firing check.
 *
 * ## What gets shown
 *
 *   Per niche:
 *     niche label · warn count (7d) · top firing event_type · severity dot
 *
 *   Headline:
 *     dot + "N warnings across 5 niches (last 7d)"
 *     dot color: green=0, amber=1-9, red=10+
 *
 * ## Sibling cards
 *
 *   * NichePauseCard (PR #581) — operator emergency-stop UI
 *   * AutoApprovalCalibrationCard (AUTO #1c, 2026-06-13) — calibration
 *     for auto-approval enforcement
 *
 *   All three share the 5-niche row layout + 60s polling + READY-on-
 *   threshold pattern so the operator's mental model is consistent.
 *
 * ## Severity heuristic
 *
 *   Per-niche dot color:
 *     warns == 0          → green   (publishing clean)
 *     1 <= warns <= 9     → amber   (monitor, no action needed)
 *     warns >= 10         → red     (investigate — likely a regression)
 *     any blocks present  → red     (enforcement fired — must investigate)
 *
 *   These thresholds are intentionally conservative for Phase A
 *   (everything is warn-only by design). When Phase B flips
 *   enforcement on, we'll re-tune.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { complianceStats } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { ComplianceNicheStats } from "@/api/types";
import { getNicheInfo, NICHE_IDS, type NicheId } from "@/niches/registry";

const WINDOW_DAYS = 7;

/** Compact labels for the event_type values from the closed enum in
 * genlab_core.compliance.events.VALID_EVENT_TYPES. The card has tight
 * row real-estate so the full names don't fit. */
const EVENT_TYPE_LABEL: Record<string, string> = {
  pre_publish_check: "pre-publish",
  ai_disclosure_added: "AI disclosure",
  copyright_flag: "copyright",
  spam_pattern_detected: "spam",
  account_health_warning: "health",
  auto_publish_block: "auto-block",
  manual_override: "override",
  unknown: "unknown",
};

function classify(stats: ComplianceNicheStats | undefined): {
  dot: string;
  tone: string;
} {
  if (!stats || stats.warns + stats.blocks === 0) {
    return { dot: "bg-success", tone: "text-success" };
  }
  if (stats.blocks > 0 || stats.warns >= 10) {
    return { dot: "bg-error", tone: "text-error" };
  }
  return { dot: "bg-warning", tone: "text-warning" };
}

export function ComplianceStatsCard() {
  const { data, isLoading, isError } = useQuery({
    queryKey: queryKeys.complianceStats.fetch(WINDOW_DAYS),
    queryFn: () => complianceStats.fetch(WINDOW_DAYS),
    staleTime: 60_000,
    refetchInterval: 60_000,
    retry: false,
  });

  const byNiche = data?.by_niche ?? {};
  const totalWarns = NICHE_IDS.reduce(
    (sum, n) => sum + (byNiche[n]?.warns ?? 0),
    0,
  );
  const totalBlocks = NICHE_IDS.reduce(
    (sum, n) => sum + (byNiche[n]?.blocks ?? 0),
    0,
  );
  const headlineDot =
    totalBlocks > 0 || totalWarns >= 10
      ? "bg-error"
      : totalWarns > 0
        ? "bg-warning"
        : "bg-success";

  return (
    <div className="bento-card">
      <h3 className="card-title">
        Compliance Signals
        <span className="ml-2 text-[10px] text-text-muted font-normal">
          Phase A warn-only · last {WINDOW_DAYS}d
        </span>
      </h3>

      <div className="flex items-center gap-2 mb-3">
        <span className={`size-2 rounded-full ${headlineDot}`} />
        <span className="text-sm text-text-secondary">
          {totalWarns === 0 && totalBlocks === 0
            ? "All niches clean"
            : totalBlocks > 0
              ? `${totalBlocks} block${totalBlocks === 1 ? "" : "s"} · ${totalWarns} warn${totalWarns === 1 ? "" : "s"}`
              : `${totalWarns} warn${totalWarns === 1 ? "" : "s"} across ${
                  NICHE_IDS.filter((n) => (byNiche[n]?.warns ?? 0) > 0).length
                } niche${
                  NICHE_IDS.filter((n) => (byNiche[n]?.warns ?? 0) > 0).length === 1 ? "" : "s"
                }`}
        </span>
      </div>

      <div className="flex flex-col gap-2">
        {isLoading && !data ? (
          <>
            <div className="shimmer" style={{ height: 14, width: "100%", borderRadius: 4 }} />
            <div className="shimmer" style={{ height: 14, width: "100%", borderRadius: 4 }} />
            <div className="shimmer" style={{ height: 14, width: "100%", borderRadius: 4 }} />
          </>
        ) : isError ? (
          <span className="text-xs text-text-muted">
            Compliance endpoint unreachable — try again shortly
          </span>
        ) : (
          NICHE_IDS.map((nicheId) => {
            const info = getNicheInfo(nicheId);
            return (
              <NicheRow
                key={nicheId}
                nicheId={nicheId}
                label={info.shortLabel}
                hex={info.hex}
                stats={byNiche[nicheId]}
              />
            );
          })
        )}
      </div>

      {/* Footer: pointer to the raw audit log + Phase A status */}
      <div className="mt-3 pt-2 border-t border-text-muted/10">
        <span className="text-[10px] text-text-muted">
          Phase A checks are observation-only · enforcement flips per (niche, check) when calibration ≥ 30
        </span>
      </div>
    </div>
  );
}

interface NicheRowProps {
  nicheId: NicheId;
  label: string;
  hex: string;
  stats: ComplianceNicheStats | undefined;
}

function NicheRow({ label, hex, stats }: NicheRowProps) {
  // Drill-down: collapsed by default. Click the row → expand to
  // show the by_event_type breakdown. Per-row state means multiple
  // rows can be expanded simultaneously (operator triaging multi-
  // niche issues sees them side-by-side).
  const [expanded, setExpanded] = useState(false);

  const { dot, tone } = classify(stats);
  const warnsText =
    !stats || stats.warns + stats.blocks === 0
      ? "clean"
      : stats.blocks > 0
        ? `${stats.warns}w / ${stats.blocks}b`
        : `${stats.warns} warn${stats.warns === 1 ? "" : "s"}`;
  const topEventLabel = stats?.top_event_type
    ? (EVENT_TYPE_LABEL[stats.top_event_type] ?? stats.top_event_type)
    : "—";

  // Row is "drillable" when there's something to show — i.e. the
  // niche actually has compliance events in the window. Zero-state
  // niches aren't clickable (nothing to see).
  const eventTypes = stats ? Object.entries(stats.by_event_type) : [];
  const isDrillable = eventTypes.length > 0;

  const title = stats
    ? `Total: ${stats.total} (warn=${stats.warns}, block=${stats.blocks}, allow=${stats.allows})\n` +
      (stats.top_event_type
        ? `Top firing: ${stats.top_event_type} × ${stats.top_event_count}`
        : "No actionable events") +
      (isDrillable ? "\nClick to see per-event-type breakdown" : "")
    : "No compliance events in window — niche is clean";

  function toggle() {
    if (isDrillable) setExpanded((x) => !x);
  }

  function handleKey(e: React.KeyboardEvent) {
    // Native button-like keyboard contract: Enter or Space toggles
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggle();
    }
  }

  return (
    <div className="flex flex-col">
      <div
        className={`flex items-center gap-2 text-xs ${
          isDrillable ? "cursor-pointer hover:bg-text-muted/5 rounded px-0.5 -mx-0.5" : ""
        }`}
        title={title}
        role={isDrillable ? "button" : undefined}
        tabIndex={isDrillable ? 0 : undefined}
        aria-expanded={isDrillable ? expanded : undefined}
        onClick={isDrillable ? toggle : undefined}
        onKeyDown={isDrillable ? handleKey : undefined}
      >
        {/* Color dot + label */}
        <span className="size-2 rounded-full shrink-0" style={{ background: hex }} />
        <span
          className="font-medium text-text-secondary shrink-0"
          style={{ width: 48 }}
        >
          {label}
        </span>

        {/* Severity dot — at-a-glance status per niche */}
        <span className={`size-1.5 rounded-full shrink-0 ${dot}`} aria-hidden />

        {/* Warn count + top firing event_type — the actionability signal */}
        <div className="flex-1 min-w-0 flex items-center gap-1.5">
          <span className={`font-mono text-[10px] ${tone}`}>{warnsText}</span>
          {stats && stats.top_event_type && (
            <span className="text-[10px] text-text-muted truncate">
              · {topEventLabel}
            </span>
          )}
        </div>

        {/* Caret chevron — only on drillable rows so operator's eye
            knows there's more to see. Rotates 90deg when expanded
            for the standard "open" affordance. */}
        {isDrillable && (
          <span
            className="text-[9px] text-text-muted shrink-0 transition-transform"
            aria-hidden
            style={{
              transform: expanded ? "rotate(90deg)" : "rotate(0deg)",
            }}
          >
            ▸
          </span>
        )}
      </div>

      {/* Drill-down expansion — by_event_type breakdown. Inline
          beneath the row (no popover positioning math) so the
          card height grows naturally. Multiple rows can expand
          simultaneously. */}
      {expanded && isDrillable && (
        <DrillDownPanel eventTypes={eventTypes} />
      )}
    </div>
  );
}

interface DrillDownPanelProps {
  /** [event_type, {warn, block, allow}] tuples from by_event_type. */
  eventTypes: Array<[string, { warn: number; block: number; allow: number }]>;
}

function DrillDownPanel({ eventTypes }: DrillDownPanelProps) {
  // Sort by descending (warn + block) so the actionable rows surface
  // first. allows-only event_types push to the bottom — operator's
  // attention should land on what needs investigation.
  const sorted = [...eventTypes].sort((a, b) => {
    const aAttn = a[1].warn + a[1].block;
    const bAttn = b[1].warn + b[1].block;
    return bAttn - aAttn;
  });

  return (
    <ul
      className="ml-7 mt-1 mb-1 pl-2 border-l border-text-muted/15 space-y-0.5"
      data-testid="drill-down-panel"
    >
      {sorted.map(([eventType, counts]) => {
        const attention = counts.warn + counts.block;
        const label = EVENT_TYPE_LABEL[eventType] ?? eventType;
        return (
          <li
            key={eventType}
            className="flex items-center gap-2 text-[10px] font-mono"
          >
            <span
              className={`shrink-0 ${attention > 0 ? "text-text-secondary" : "text-text-muted"}`}
              style={{ minWidth: 84 }}
            >
              {label}
            </span>
            {counts.warn > 0 && (
              <span className="text-warning">{counts.warn}w</span>
            )}
            {counts.block > 0 && (
              <span className="text-error">{counts.block}b</span>
            )}
            {counts.allow > 0 && (
              <span className="text-text-muted">{counts.allow}a</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}
