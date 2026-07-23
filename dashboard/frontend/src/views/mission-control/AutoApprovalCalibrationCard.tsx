/**
 * AUTO #1c (2026-06-13): Mission Control calibration card.
 *
 * Surfaces the per-niche AutoApprovalGate accuracy in real time. Operator
 * watches this card to see when each niche crosses the AUTO #2 readiness
 * threshold (≥30 samples + ≥90% agreement). When all niches are green,
 * the "enable auto-publish" toggle (AUTO #2) becomes defensible.
 *
 * Visual language:
 *   - 5 niche rows, color-coded by niche accent
 *   - Sample count + agreement rate per niche
 *   - Bar fills toward 100%, turns green at ≥90%
 *   - Sample count badge: dim until ≥30, then "ready" badge
 *
 * No data path: when DATABASE_URL is unset on prod or no reviews have
 * happened yet, all rows show "0 samples · waiting" — never a blank card.
 */
import { useQuery } from "@tanstack/react-query";
import { autoApproval, type CalibrationStats } from "@/api/client";
import { getNicheInfo, NICHE_IDS } from "@/niches/registry";
import { ProgressBar } from "@/components/shared/progress-bar";

const READY_THRESHOLD_RATE = 0.9;
const READY_THRESHOLD_SAMPLES = 30;

export function AutoApprovalCalibrationCard() {
  // PR #392: single batch query returning all 5 niches in ONE HTTP +
  // ONE SQL request. Pre-PR used `useQueries` with 5 parallel per-niche
  // fetches every 60s = 300 round-trips/hour. Now 60/hour.
  const { data, isLoading } = useQuery({
    queryKey: ["auto-approval-calibration-all"],
    queryFn: () => autoApproval.calibrationStatsAll(),
    // Refresh every 60s — calibration data builds up slowly (~5 ops/day)
    // so faster polling burns API calls for no signal
    staleTime: 60_000,
    refetchInterval: 60_000,
    retry: false,
  });

  // 2026-07-23: parallel outcome-based readiness signal — see
  // outcome_readiness.py docstring for why. Operator ratchet has been
  // stuck 24 days because auto-approver approves without operator
  // review; this signal validates the gate from reward_48h outcomes
  // instead. Poll cadence 5min matches the calibration cadence — data
  // updates once per 48h reward window fill anyway.
  const { data: outcomeData } = useQuery({
    queryKey: ["auto-approval-outcome-readiness-all"],
    queryFn: () => autoApproval.outcomeReadiness(),
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
    retry: false,
  });

  const anyLoading = isLoading;
  const perNiche = data?.niches ?? {};
  const outcomePerNiche = outcomeData?.niches ?? {};
  const readyCount = NICHE_IDS.filter(
    (nicheId) => perNiche[nicheId]?.ready_for_enforcement === true,
  ).length;
  const outcomeReadyCount = NICHE_IDS.filter(
    (nicheId) => outcomePerNiche[nicheId]?.ready === true,
  ).length;

  return (
    <div className="bento-card">
      <h3 className="card-title">
        Auto-Approval Calibration
        <span className="ml-2 text-[10px] text-text-muted font-normal">
          rolling 7d
        </span>
      </h3>

      {/* Headline status — at-a-glance "how close are we?" */}
      <div className="flex items-center gap-2 mb-3">
        <span
          className={
            readyCount === NICHE_IDS.length
              ? "size-2 rounded-full bg-success"
              : readyCount > 0
              ? "size-2 rounded-full bg-warning"
              : "size-2 rounded-full bg-text-muted"
          }
          style={
            readyCount > 0
              ? { boxShadow: "0 0 6px rgba(34,197,94,0.4)" }
              : undefined
          }
        />
        <span className="text-sm text-text-secondary">
          {readyCount === NICHE_IDS.length
            ? "All niches ready for AUTO #2"
            : `${readyCount} / ${NICHE_IDS.length} niches above threshold`}
        </span>
      </div>

      {/* Outcome-based readiness sub-badge — 2026-07-23.
          Independent signal from reward_48h. Renders below the
          headline so operator sees both signals side by side.
          Copy is deliberately compact — this is a "peek" signal
          during the observation-only rollout; a full row per niche
          would out-shout the operator-agreement primary. */}
      {outcomeData && (
        <div
          className="mb-3 flex items-center gap-2 text-xs text-text-muted"
          title={
            "Fraction of auto-approved posts whose reward_48h cleared " +
            "a low-bar threshold. Separate from operator-agreement; " +
            "readiness observed but not yet wired to advance the ladder."
          }
        >
          <span className="rounded border border-border/40 bg-surface-2 px-1.5 py-0.5 text-[10px]">
            outcome
          </span>
          <span>
            {outcomeReadyCount} / {NICHE_IDS.length} niches ·{" "}
            {NICHE_IDS.map((nid) => {
              const r = outcomePerNiche[nid];
              const info = getNicheInfo(nid);
              const rate = r?.outcome_good_rate ?? 0;
              return (
                <span
                  key={nid}
                  className="mr-2 font-mono"
                  style={{ color: info.hex }}
                  title={`${info.shortLabel}: ${r?.outcome_good_count ?? 0}/${r?.sample_count ?? 0} good outcomes (rate ${(rate * 100).toFixed(0)}%, threshold ${((r?.threshold ?? 0.05) * 100).toFixed(0)}%)`}
                >
                  {info.shortLabel[0]}
                  {(rate * 100).toFixed(0)}
                </span>
              );
            })}
          </span>
        </div>
      )}

      {/* Per-niche rows */}
      <div className="flex flex-col gap-2">
        {anyLoading && !data ? (
          // Initial load — show skeleton instead of empty rows
          <>
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
          </>
        ) : (
          NICHE_IDS.map((nicheId) => {
            const info = getNicheInfo(nicheId);
            const stats = perNiche[nicheId];
            return (
              <NicheRow
                key={nicheId}
                label={info.shortLabel}
                hex={info.hex}
                stats={stats}
              />
            );
          })
        )}
      </div>
    </div>
  );
}

interface NicheRowProps {
  label: string;
  hex: string;
  stats: CalibrationStats | undefined;
}

function NicheRow({ label, hex, stats }: NicheRowProps) {
  const samples = stats?.sample_count ?? 0;
  const rate = stats?.agreement_rate ?? 0;
  const ratePct = Math.round(rate * 100);
  const ready = stats?.ready_for_enforcement ?? false;

  // Bar shows agreement rate; goes green at the ≥90% threshold
  const barColor = ready
    ? "var(--color-green)"
    : rate >= 0.7
    ? "var(--color-amber)"
    : "var(--text-muted)";

  // Sample count tooltip explains why a niche isn't "ready" despite high rate
  const samplesBadgeTitle =
    samples < READY_THRESHOLD_SAMPLES
      ? `${samples}/${READY_THRESHOLD_SAMPLES} samples needed (rate must hold ≥${
          READY_THRESHOLD_RATE * 100
        }%)`
      : `${samples} samples — passed minimum`;

  return (
    <div className="flex items-center gap-2 text-xs">
      {/* Color dot + label */}
      <span
        className="size-2 rounded-full shrink-0"
        style={{ background: hex }}
      />
      <span
        className="font-medium text-text-secondary shrink-0"
        style={{ width: 48 }}
      >
        {label}
      </span>

      {/* Agreement-rate bar */}
      <div className="flex-1 min-w-0">
        <ProgressBar value={ratePct} color={barColor} height={4} />
      </div>

      {/* Rate % */}
      <span
        className="font-mono text-text-primary shrink-0 text-right"
        style={{ width: 36 }}
      >
        {samples > 0 ? `${ratePct}%` : "—"}
      </span>

      {/* Sample count + ready indicator */}
      <span
        className={
          ready
            ? "font-mono text-[10px] text-success shrink-0 px-1.5 py-0.5 rounded bg-success/10 border border-success/30"
            : "font-mono text-[10px] text-text-muted shrink-0"
        }
        title={samplesBadgeTitle}
        style={{ minWidth: 38, textAlign: "right" }}
      >
        {ready ? `READY · ${samples}` : `${samples}/${READY_THRESHOLD_SAMPLES}`}
      </span>
    </div>
  );
}
