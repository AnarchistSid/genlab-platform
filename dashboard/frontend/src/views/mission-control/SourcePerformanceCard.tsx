/**
 * Mission Control SourcePerformanceCard (Phase 2 L1, 2026-06-30).
 *
 * Surfaces the per-source bandit reward data shipped in PR #571 +
 * activated in PR #571b. The bandit_arms table has been accumulating
 * rows like ``source:reddit:aivideo`` for weeks but the operator had
 * no UI to see "which source produces our best content?"
 *
 * ## Why a separate card from SourceDiscoveryCard
 *
 * Different signal, different question:
 *
 *   * SourceDiscoveryCard → which YouTube CHANNELS to add to
 *     sources.yaml (per-clip avg engagement on clips we used)
 *   * SourcePerformanceCard → which SOURCE NAMES the bandit favors
 *     (Beta posterior reward mean per arm_id LIKE 'source:%')
 *
 * Both useful, both per-niche, but they answer different operator
 * decisions. Showing them as separate cards lets the operator switch
 * mental modes between "should I expand my source pool?" and "is my
 * current source pool weighted correctly?"
 *
 * ## Per-niche selector + top 10
 *
 * Same shape as SourceDiscoveryCard — single niche at a time, top-N
 * arms ranked by reward_mean. A 5-niche × 10-arm grid would be 50
 * dense rows, too noisy for at-a-glance Mission Control.
 *
 * ## Insufficient data section
 *
 * Arms with n_plays < 10 surface in a separate dimmed section so the
 * operator doesn't act on noise. The bandit itself is also lukewarm
 * on under-played arms — the UI just makes that visible.
 *
 * ## Foundation deps
 *
 *   * PR #571 — bandit_arms.source_performance storage primitives
 *   * PR #571b (fa317889) — reward loop wire that populates the arms
 *   * Phase 2 L1 — /api/v1/source-performance endpoint (this commit)
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { sourcePerformanceBandit } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { SourcePerformanceArm } from "@/api/types";
import { getNicheInfo, NICHE_IDS, type NicheId } from "@/niches/registry";

const TOP_N = 20;
const MIN_PLAYS_FOR_TRUST = 10;

export function SourcePerformanceCard() {
  const [selectedNiche, setSelectedNiche] = useState<NicheId>(NICHE_IDS[0]);

  const { data, isLoading, isError } = useQuery({
    queryKey: queryKeys.sourcePerformance.list(selectedNiche, TOP_N),
    queryFn: () => sourcePerformanceBandit.list(selectedNiche, TOP_N),
    staleTime: 30_000,
    refetchInterval: 30_000,
    retry: false,
  });

  const sources = data?.sources ?? [];
  const warning = data?.warning;
  const trusted = sources.filter((s) => s.n_plays >= MIN_PLAYS_FOR_TRUST).slice(0, 10);
  const noisy = sources.filter((s) => s.n_plays < MIN_PLAYS_FOR_TRUST);

  return (
    <div className="bento-card">
      <h3 className="card-title">
        Source Performance
        <span className="ml-2 text-[10px] text-text-muted font-normal">
          bandit reward · top 10 by posterior mean
        </span>
      </h3>

      {/* Niche selector pills */}
      <div className="flex gap-1 mb-3 flex-wrap">
        {NICHE_IDS.map((nicheId) => {
          const info = getNicheInfo(nicheId);
          const active = nicheId === selectedNiche;
          return (
            <button
              key={nicheId}
              type="button"
              onClick={() => setSelectedNiche(nicheId)}
              className={`text-[10px] px-2 py-0.5 rounded border transition-colors ${
                active
                  ? "text-text-primary"
                  : "text-text-secondary hover:bg-text-muted/10"
              }`}
              style={{
                borderColor: active ? info.hex : "rgba(128,128,128,0.3)",
                background: active ? `${info.hex}20` : undefined,
              }}
            >
              {info.shortLabel}
            </button>
          );
        })}
      </div>

      {isLoading && !data ? (
        <div className="flex flex-col gap-1">
          <div className="shimmer" style={{ height: 16, width: "100%", borderRadius: 4 }} />
          <div className="shimmer" style={{ height: 16, width: "100%", borderRadius: 4 }} />
          <div className="shimmer" style={{ height: 16, width: "100%", borderRadius: 4 }} />
        </div>
      ) : isError ? (
        <span className="text-xs text-text-muted">
          Source-performance endpoint unreachable — try again shortly
        </span>
      ) : warning ? (
        <ZeroStateMessage
          message={`Tracker unavailable on this build: ${warning}`}
        />
      ) : sources.length === 0 ? (
        <ZeroStateMessage
          message={`No source-arm data for ${
            getNicheInfo(selectedNiche).shortLabel
          } yet. Arms accumulate as the reward loop fires on published blueprints.`}
        />
      ) : (
        <>
          {trusted.length > 0 ? (
            <PerformanceTable arms={trusted} />
          ) : (
            <ZeroStateMessage
              message={`No source has ≥${MIN_PLAYS_FOR_TRUST} plays for ${
                getNicheInfo(selectedNiche).shortLabel
              } yet — see "Insufficient data" below.`}
            />
          )}
          {noisy.length > 0 && (
            <details className="mt-3">
              <summary className="text-[10px] text-text-muted cursor-pointer hover:text-text-secondary">
                Insufficient data (&lt;{MIN_PLAYS_FOR_TRUST} plays) · {noisy.length} sources
              </summary>
              <div className="mt-2 opacity-50">
                <PerformanceTable arms={noisy.slice(0, 10)} />
              </div>
            </details>
          )}
        </>
      )}

      <div className="mt-3 pt-2 border-t border-text-muted/10">
        <span className="text-[10px] text-text-muted">
          Beta posterior mean (α / (α+β)) per source · 95% CI shown as
          bar width · arms updated by reward loop on published blueprints
        </span>
      </div>
    </div>
  );
}

function ZeroStateMessage({ message }: { message: string }) {
  return (
    <div className="text-xs text-text-muted py-4 text-center italic">
      {message}
    </div>
  );
}

function PerformanceTable({ arms }: { arms: SourcePerformanceArm[] }) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2 text-[9px] uppercase tracking-wide text-text-muted px-1 pb-0.5 border-b border-text-muted/10">
        <span className="flex-1">Source</span>
        <span className="font-mono text-right" style={{ minWidth: 28 }}>
          n
        </span>
        <span className="font-mono text-right" style={{ minWidth: 110 }}>
          Reward (95% CI)
        </span>
      </div>
      {arms.map((a) => (
        <PerformanceRow key={a.arm_id} arm={a} />
      ))}
    </div>
  );
}

function PerformanceRow({ arm }: { arm: SourcePerformanceArm }) {
  const pct = arm.reward_mean * 100;
  const lower = arm.reward_ci_lower * 100;
  const upper = arm.reward_ci_upper * 100;

  const title =
    `Source: ${arm.source}\n` +
    `Plays: ${arm.n_plays}\n` +
    `Reward mean: ${(arm.reward_mean * 100).toFixed(1)}%\n` +
    `95% CI: [${lower.toFixed(1)}%, ${upper.toFixed(1)}%]\n` +
    `Beta(α=${arm.alpha.toFixed(2)}, β=${arm.beta.toFixed(2)})`;

  return (
    <div
      className="flex items-center gap-2 text-xs hover:bg-text-muted/5 rounded px-1 py-0.5 transition-colors"
      title={title}
    >
      <span className="flex-1 truncate font-mono text-[10px] text-text-secondary">
        {arm.source}
      </span>
      <span
        className="font-mono text-[10px] text-text-primary text-right"
        style={{ minWidth: 28 }}
      >
        {arm.n_plays}
      </span>
      <div
        className="flex items-center gap-1 text-right"
        style={{ minWidth: 110 }}
      >
        {/* Reward bar with CI overlay. Background fill = mean,
            outline = CI bounds (visually the wider the outline, the
            more uncertain the estimate). */}
        <div
          className="relative h-3 rounded overflow-hidden bg-text-muted/10"
          style={{ flex: 1 }}
        >
          {/* CI band (lighter) */}
          <div
            className="absolute h-full bg-text-muted/30"
            style={{
              left: `${lower}%`,
              width: `${Math.max(0, upper - lower)}%`,
            }}
          />
          {/* Mean fill (solid) */}
          <div
            className="absolute h-full bg-accent"
            style={{ width: `${pct}%`, opacity: 0.8 }}
          />
        </div>
        <span className="font-mono text-[10px] text-text-primary" style={{ minWidth: 38 }}>
          {pct.toFixed(1)}%
        </span>
      </div>
    </div>
  );
}
