/**
 * Session 3b (2026-07-01) — Trend Anticipation Accuracy card.
 *
 * Weekly Spearman-r validation: does composite_score at write time
 * actually predict the observed peak N days later?
 *
 * Companion to :file:`TrendAnticipationCard.tsx`. The two answer
 * complementary questions:
 *
 *   * ``TrendAnticipationCard``  — "what does anticipation THINK
 *     is coming next?" (daily forward-looking view)
 *   * ``TrendAnticipationAccuracyCard`` — "is anticipation actually
 *     right when we check back?" (weekly retrospective validation)
 *
 * Reads the measurement written by the weekly runner
 * (``scripts/measure_anticipation_accuracy.py``, fires Mon 05:00 UTC).
 *
 * ## Rendering rules
 *
 *   * ``spearman_r >= 0.4`` AND ``n_topics >= 5``:   green "ready"
 *     badge → operator can safely flip
 *     ``GENLAB_TREND_ANTICIPATION_ENABLED=true``
 *   * ``spearman_r > 0`` (any):                       amber "learning"
 *     badge → wait more weeks
 *   * ``spearman_r <= 0``:                             red "not
 *     predictive" badge → composite weights need re-tuning; DO
 *     NOT activate pipeline steering
 *   * ``spearman_r === null``:                         muted
 *     "no measurement yet" — runner hasn't fired or measurement
 *     was disabled
 */
import { useQuery } from "@tanstack/react-query";

import { trendAnticipation } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { AnticipationAccuracy } from "@/api/types";
import { NICHE_IDS, getNicheInfo, type NicheId } from "@/niches/registry";

const READY_R_THRESHOLD = 0.4;
const MEANINGFUL_N = 5;

function VerdictBadge({ measurement }: { measurement: AnticipationAccuracy }) {
  const { spearman_r: r, n_topics } = measurement;
  if (r === null) {
    return (
      <span className="rounded border border-text-muted/30 bg-text-muted/10 px-1.5 py-0.5 text-xs text-text-muted">
        no data
      </span>
    );
  }
  if (n_topics < MEANINGFUL_N) {
    return (
      <span className="rounded border border-text-muted/30 bg-text-muted/10 px-1.5 py-0.5 text-xs text-text-muted">
        underpowered
      </span>
    );
  }
  if (r >= READY_R_THRESHOLD) {
    return (
      <span
        className="rounded border border-success/30 bg-success/10 px-1.5 py-0.5 text-xs text-success"
        title="Anticipation is predictive — safe to activate pipeline steering"
      >
        ready
      </span>
    );
  }
  if (r > 0) {
    return (
      <span
        className="rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-xs text-warning"
        title="Anticipation has weak signal — wait for more weeks of data"
      >
        learning
      </span>
    );
  }
  return (
    <span
      className="rounded border border-danger/30 bg-danger/10 px-1.5 py-0.5 text-xs text-danger"
      title="Anticipation is NOT predicting peaks — DO NOT activate pipeline steering; composite weights need re-tuning"
    >
      not predictive
    </span>
  );
}

function NicheRow({ nicheId }: { nicheId: NicheId }) {
  const info = getNicheInfo(nicheId);
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.trendAnticipation.accuracy(nicheId),
    queryFn: () => trendAnticipation.accuracy(nicheId),
    // Weekly runner (Mon 05:00 UTC) + hourly poll = fresh state
    // on refresh without hammering the endpoint. Longer TTL than
    // TrendAnticipationCard because measurement rewrites weekly,
    // not daily.
    refetchInterval: 60 * 60 * 1000,
    staleTime: 30 * 60 * 1000,
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-between border-b border-border/40 py-2 text-sm">
        <span className="font-semibold" style={{ color: info.color }}>
          {info.short}
        </span>
        <span className="text-xs text-text-muted">Loading…</span>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex items-center justify-between border-b border-border/40 py-2 text-sm">
        <span className="font-semibold" style={{ color: info.color }}>
          {info.short}
        </span>
        <span className="text-xs text-text-muted">
          No measurement yet — weekly runner may not have fired
        </span>
      </div>
    );
  }

  const rText = data.spearman_r === null ? "—" : data.spearman_r.toFixed(2);
  const pText = data.p_value === null ? "—" : data.p_value.toFixed(2);

  return (
    <div className="flex items-center justify-between gap-2 border-b border-border/40 py-2 text-sm">
      <div className="flex items-center gap-2">
        <span className="font-semibold" style={{ color: info.color }}>
          {info.short}
        </span>
        <VerdictBadge measurement={data} />
      </div>
      <div className="flex items-center gap-3 text-xs text-text-muted">
        <span title="Spearman rank correlation">
          r=<span className="font-mono text-text-primary">{rText}</span>
        </span>
        <span title="p-value from Spearman test">
          p=<span className="font-mono">{pText}</span>
        </span>
        <span title="Number of (predicted, observed) pairs">
          N=<span className="font-mono">{data.n_topics}</span>
        </span>
      </div>
    </div>
  );
}

export function TrendAnticipationAccuracyCard() {
  return (
    <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
      <div className="mb-2 flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold">
            Trend anticipation — accuracy
          </div>
          <div className="text-xs text-text-muted">
            Weekly Spearman validation · Mondays 05:00 UTC · flip
            steering flag when r ≥ 0.4 and N ≥ 5
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
