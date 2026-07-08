/**
 * L7 observability card (2026-07-08 audit) — bandit-arm drift.
 *
 * Reads `/api/v1/drift-signals/summary`. Regressions (negative
 * z-score, endpoint sorted ASC) render prominently ABOVE improvements
 * (endpoint sorted DESC) — arms whose posterior rotted are the
 * operator's high-value signal, not arms that got lucky. Card
 * respects the endpoint's ordering verbatim.
 *
 * Display caps: 10 regressions + 5 improvements to stay scannable;
 * "showing N of M" surfaces when the endpoint returned more than the
 * card shows. Endpoint returns `data: null` before the migration
 * lands — the card distinguishes that from "empty arrays" (posteriors
 * stable) with distinct copy.
 *
 * Visual language mirrors `CrossNichePriorsCard` + `FlagStateCard` —
 * per-row layout, mono numeric columns, active vs observation-only
 * flag badge.
 */
import { useQuery } from "@tanstack/react-query";

import { driftSignals } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { DriftSignal } from "@/api/types";

const _DISPLAY_REGRESSIONS_CAP = 10;
const _DISPLAY_IMPROVEMENTS_CAP = 5;

function FlagBadge({ enabled }: { enabled: boolean }) {
  const tone = enabled
    ? "border-success/30 bg-success/10 text-success"
    : "border-warning/30 bg-warning/10 text-warning";
  const title = enabled
    ? "GENLAB_DRIFT_PERSIST_ENABLED=true — detector rows are being persisted"
    : "GENLAB_DRIFT_PERSIST_ENABLED off — signals surfaced but persistence disabled";
  return (
    <span
      className={`rounded border ${tone} px-1.5 py-0.5 text-xs`}
      data-testid="drift-signal-flag-badge"
      data-state={enabled ? "active" : "observation-only"}
      title={title}
    >
      {enabled ? "active" : "observation only"}
    </span>
  );
}

function SignalRow({
  signal,
  tone,
  testIdPrefix,
}: {
  signal: DriftSignal;
  tone: "regression" | "improvement";
  testIdPrefix: string;
}) {
  // Tone drives the z-score color. `recent → baseline` arrow always
  // reads left-to-right for temporal cues.
  const zClass = tone === "regression" ? "text-red-400" : "text-emerald-400";
  const arrow = tone === "regression" ? "↓" : "↑";
  const recentPct = (signal.recent_rate * 100).toFixed(1);
  const baselinePct = (signal.baseline_rate * 100).toFixed(1);
  const zFormatted =
    (signal.z_score >= 0 ? "+" : "") + signal.z_score.toFixed(2);
  return (
    <div
      className="grid grid-cols-[1fr,auto,auto,auto,auto] items-center gap-2 border-b border-border/40 py-1.5 text-xs"
      data-testid={`${testIdPrefix}-${signal.arm_id}`}
      data-tone={tone}
    >
      <span className="truncate font-mono text-[11px]" title={signal.arm_id}>
        {signal.arm_id}
      </span>
      <span className="rounded bg-zinc-800 px-1 py-0.5 text-[10px] text-zinc-300">
        {signal.niche_id}
      </span>
      <span className={`font-mono font-bold ${zClass}`} title="z-score vs baseline">
        z={zFormatted}
      </span>
      <span
        className="font-mono text-text-muted"
        title={`recent ${recentPct}% ${arrow} baseline ${baselinePct}%`}
      >
        {recentPct}% {arrow} {baselinePct}%
      </span>
      <span className="font-mono text-[10px] text-text-muted" title="recent_obs / baseline_obs">
        {signal.recent_obs}/{signal.baseline_obs}
      </span>
    </div>
  );
}

/** Concise horizontal per-niche summary: `Gaming: 3↓ 1↑`. Renders
 *  only niches with at least one signal — a niche with nothing
 *  interesting shouldn't burn line space. */
function NicheSummaryStrip({
  counts,
}: {
  counts: { niche_id: string; regressions: number; improvements: number }[];
}) {
  const active = counts.filter(
    (c) => c.regressions > 0 || c.improvements > 0,
  );
  if (active.length === 0) return null;
  return (
    <div className="mb-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px]">
      {active.map((c) => (
        <span
          key={c.niche_id}
          className="font-mono"
          data-testid={`drift-signal-niche-summary-${c.niche_id}`}
          title={`${c.niche_id}: ${c.regressions} regressions, ${c.improvements} improvements`}
        >
          <span className="text-text-muted">{c.niche_id}:</span>{" "}
          {c.regressions > 0 && (
            <span className="text-red-400">{c.regressions}↓</span>
          )}
          {c.regressions > 0 && c.improvements > 0 && " "}
          {c.improvements > 0 && (
            <span className="text-emerald-400">{c.improvements}↑</span>
          )}
        </span>
      ))}
    </div>
  );
}

export function DriftSignalCard() {
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.driftSignals.summary(undefined, 7, 2.0),
    queryFn: () =>
      driftSignals.summary({ windowDays: 7, minAbsZ: 2.0 }),
    // 5-min poll — detector runs are periodic, not real-time. Matches
    // FlagStateCard cadence.
    refetchInterval: 5 * 60 * 1000,
    staleTime: 60 * 1000,
  });

  if (isLoading) {
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="text-sm font-semibold">Bandit-arm drift</div>
        <div className="mt-2 text-xs text-text-muted">Loading…</div>
      </div>
    );
  }

  // `data === null` when the migration hasn't landed — the endpoint
  // signals the missing-table state via a null envelope. Show a
  // dedicated affordance rather than an empty-state (which would
  // be misleading).
  if (!data) {
    return (
      <div
        className="rounded-lg border border-border/60 bg-surface-1 p-3"
        data-testid="drift-signal-card"
      >
        <div className="mb-2">
          <div className="text-sm font-semibold">Bandit-arm drift</div>
          <div className="text-xs text-text-muted">
            Persisted regression + improvement detector
          </div>
        </div>
        <div
          className="text-xs text-text-muted"
          data-testid="drift-signal-migration-pending"
        >
          Drift signals table not yet available — waiting for migration
        </div>
      </div>
    );
  }

  const displayRegressions = data.regressions.slice(0, _DISPLAY_REGRESSIONS_CAP);
  const displayImprovements = data.improvements.slice(0, _DISPLAY_IMPROVEMENTS_CAP);
  const isEmpty = data.regressions.length === 0 && data.improvements.length === 0;

  return (
    <div
      className="rounded-lg border border-border/60 bg-surface-1 p-3"
      data-testid="drift-signal-card"
    >
      <div className="mb-2 flex items-start justify-between gap-2">
        <div>
          <div className="text-sm font-semibold">Bandit-arm drift</div>
          <div className="text-xs text-text-muted">
            Last {data.window_days}d · |z| ≥ {data.min_abs_z.toFixed(1)}
          </div>
        </div>
        <FlagBadge enabled={data.flag_enabled} />
      </div>

      <NicheSummaryStrip counts={data.counts_by_niche} />

      {isEmpty ? (
        <div
          className="text-xs text-text-muted"
          data-testid="drift-signal-empty-state"
        >
          No drift signals in window — bandit posteriors stable
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {/* Regressions section — always rendered first because the
              operator cares most about arms whose win-rate rotted. */}
          {data.regressions.length > 0 && (
            <div>
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-red-400">
                Regressions · {data.regressions.length}
              </div>
              <div>
                {displayRegressions.map((s) => (
                  <SignalRow
                    key={s.arm_id}
                    signal={s}
                    tone="regression"
                    testIdPrefix="drift-signal-regression"
                  />
                ))}
              </div>
              {data.regressions.length > _DISPLAY_REGRESSIONS_CAP && (
                <div
                  className="mt-1 text-[10px] text-text-muted"
                  data-testid="drift-signal-more-hidden"
                >
                  showing {_DISPLAY_REGRESSIONS_CAP} of{" "}
                  {data.regressions.length}
                </div>
              )}
            </div>
          )}

          {data.improvements.length > 0 && (
            <div>
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-emerald-400">
                Improvements · {data.improvements.length}
              </div>
              <div>
                {displayImprovements.map((s) => (
                  <SignalRow
                    key={s.arm_id}
                    signal={s}
                    tone="improvement"
                    testIdPrefix="drift-signal-improvement"
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
