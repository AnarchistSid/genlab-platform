/**
 * Intervention 5 Session 3 (2026-07-01) — Trend Anticipation card.
 *
 * Mission Control at-a-glance surface for the daily trend-anticipation
 * artifact. Each niche row shows the top-scored anticipated topic,
 * its composite score, and a small badge for the current pipeline
 * state ("observation only" vs "steering").
 *
 * ## The four signals
 *
 * The card renders a compact "signal strip" per topic — four small
 * bars in fixed slot order:
 *
 *   1. search_velocity  — pytrends 2nd derivative (Session 1)
 *   2. creator_pickup   — YouTube search totalResults (Session 2)
 *   3. social_velocity  — Reddit karma-per-hour (Session 2)
 *   4. news_lead        — Google News article count (Session 3)
 *
 * A null signal renders as a hollow slot; a populated signal renders
 * as a filled bar with height proportional to value. This makes it
 * obvious at a glance which signals are active AND which topics have
 * the "all four agreeing" pattern the anticipation intent is
 * looking for.
 *
 * ## Sibling cards
 *
 * Same visual language as ``StrategistReportCard`` / ``SponsorshipReadinessCard``
 * / ``AutoApprovalCalibrationCard``: 5 niche rows, badge-driven state,
 * cold-start message, subtle poll cadence. Placed next to
 * StrategistReportCard on Mission Control — both surface daily/weekly
 * meta-signals the operator reasons about at the same cadence.
 */
import { useQuery } from "@tanstack/react-query";

import { trendAnticipation } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { AnticipationScore } from "@/api/types";
import { NICHE_IDS, getNicheInfo, type NicheId } from "@/niches/registry";

const SIGNAL_LABELS = [
  "SV",
  "CP",
  "SoV",
  "NL",
] as const;

const SIGNAL_KEYS = [
  "search_velocity",
  "creator_pickup",
  "social_velocity",
  "news_lead",
] as const;

const SIGNAL_TOOLTIPS: Record<string, string> = {
  search_velocity: "Search velocity (pytrends 2nd derivative)",
  creator_pickup: "Creator pickup (YouTube search results, last 7d)",
  social_velocity: "Social velocity (Reddit karma/hour)",
  news_lead: "News lead (Google News articles, last 3d)",
};

function SignalStrip({
  signals,
}: {
  signals: AnticipationScore["signals"];
}) {
  return (
    <div className="flex items-center gap-0.5">
      {SIGNAL_KEYS.map((key, i) => {
        const value = signals[key];
        const label = SIGNAL_LABELS[i];
        if (value === null) {
          return (
            <div
              key={key}
              title={`${SIGNAL_TOOLTIPS[key]} — no data`}
              className="h-3 w-2 rounded-sm border border-border/40 bg-transparent"
              data-testid={`signal-${key}-null`}
            />
          );
        }
        const heightPct = Math.max(15, Math.round(value * 100));
        return (
          <div
            key={key}
            title={`${SIGNAL_TOOLTIPS[key]} — ${(value * 100).toFixed(0)}%`}
            className="flex h-3 w-2 flex-col justify-end overflow-hidden rounded-sm border border-border/40"
            data-testid={`signal-${key}-value`}
          >
            <div
              className="w-full bg-info"
              style={{ height: `${heightPct}%` }}
              aria-label={`${label} ${(value * 100).toFixed(0)}%`}
            />
          </div>
        );
      })}
    </div>
  );
}

/** "3h ahead" / "at peak" / "6h past" — the same relativeTime idiom
 *  the Strategist card uses but for a signed "hours until peak"
 *  integer instead of a past-time timestamp. */
function formatPeakEta(hoursAhead: number | null): string {
  if (hoursAhead === null) return "—";
  if (hoursAhead === 0) return "at peak";
  if (hoursAhead > 0) return `${hoursAhead}h ahead`;
  return `${Math.abs(hoursAhead)}h past`;
}

function NicheRow({ nicheId }: { nicheId: NicheId }) {
  const info = getNicheInfo(nicheId);
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.trendAnticipation.latest(nicheId),
    queryFn: () => trendAnticipation.latest(nicheId),
    // Artifact rewrites daily at 03:30 UTC — every 10 minutes is
    // a compromise between "fresh state on Mission Control refresh"
    // and "don't spam the endpoint." Would be 30-60 min if we had
    // WebSocket push, which we don't.
    refetchInterval: 10 * 60 * 1000,
    staleTime: 5 * 60 * 1000,
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

  if (!data || data.ranking.length === 0) {
    return (
      <div className="flex items-center justify-between border-b border-border/40 py-2 text-sm">
        <span className="font-semibold" style={{ color: info.color }}>
          {info.shortLabel}
        </span>
        <span className="text-xs text-text-muted">
          No ranking today — runner may not have fired yet
        </span>
      </div>
    );
  }

  const top = data.ranking[0];
  const scorePct = Math.round(top.composite_score * 100);
  const confidencePct = Math.round(top.confidence * 4);

  return (
    <div className="border-b border-border/40 py-2">
      <div className="flex items-center justify-between gap-2 text-sm">
        <div className="flex flex-1 items-center gap-2 truncate">
          <span
            className="font-semibold"
            style={{ color: info.color }}
          >
            {info.shortLabel}
          </span>
          <SignalStrip signals={top.signals} />
          <span className="truncate" title={top.topic}>
            {top.topic}
          </span>
        </div>
        <div className="flex items-center gap-2 text-xs text-text-muted">
          <span className="font-mono">{scorePct}%</span>
          <span>{formatPeakEta(top.anticipated_peak_hours_ahead)}</span>
          <span title={`Signals active: ${confidencePct}/4`}>
            {confidencePct}/4
          </span>
        </div>
      </div>
    </div>
  );
}

export function TrendAnticipationCard() {
  return (
    <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
      <div className="mb-2 flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold">Trend anticipation</div>
          <div className="text-xs text-text-muted">
            Daily ranking of upcoming topics · 03:30 UTC ·{" "}
            <span title="GENLAB_TREND_ANTICIPATION_ENABLED gates pipeline steering">
              observation only until flag flipped
            </span>
          </div>
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
