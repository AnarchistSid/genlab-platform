/**
 * L3 PR 9 (2026-07-07) — Monetization Layer 3 sprint observability card.
 *
 * Reads /api/v1/product-bandit/summary and displays the top-N products
 * per niche by Beta posterior mean — so the operator can eyeball
 * whether the product bandit is learning consistent preferences
 * before flipping ``GENLAB_PRODUCT_SELECTOR_ENABLED``.
 *
 * ## What each niche row shows
 *
 * Top-5 products per niche with rate + n_obs. Cold-start arms
 * (n_obs=0) render dimmed so the operator can quickly see which
 * niches have accumulated click signal vs which are still cold.
 *
 * ## Active vs observation-only badge
 *
 * Server injects ``flag_enabled`` — reflects the current
 * ``GENLAB_PRODUCT_SELECTOR_ENABLED`` runtime state. When true → green
 * "active" badge; when false → amber "observation only". Same pattern
 * as TransformationBanditCard.
 *
 * ## Cold-start
 *
 * When no arms are registered yet (pre-register_product_arms.py run),
 * server returns ``niches: {}``. Card renders instructions pointing
 * to the register script so the operator has a clear next action.
 */
import { useQueries, useQuery } from "@tanstack/react-query";

import { productBandit } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type {
  DivergenceStats,
  ProductArm,
  ProductNicheSummary,
} from "@/api/types";
import { NICHE_IDS, getNicheInfo, type NicheId } from "@/niches/registry";

/** Truncate long product slugs (e.g. "prime-day-wireless-earbuds") for
 *  the compact card layout. Full slug is available on hover via title. */
function truncateSlug(slug: string, maxLen = 20): string {
  return slug.length > maxLen ? `${slug.slice(0, maxLen)}…` : slug;
}

function ProductCell({ arm }: { arm: ProductArm }) {
  const displayName = truncateSlug(arm.slug);
  const isCold = arm.n_obs === 0;
  return (
    <div
      className={
        isCold
          ? "flex flex-col text-[10px] text-text-muted opacity-60"
          : "flex flex-col text-[10px]"
      }
      title={arm.arm_id}
    >
      <span className="font-mono">{displayName}</span>
      <span className="text-text-muted">
        {isCold ? "cold-start" : `${(arm.rate * 100).toFixed(0)}% · n=${arm.n_obs}`}
      </span>
    </div>
  );
}

function DivergenceBadge({ stats }: { stats: DivergenceStats | undefined }) {
  /**
   * L3 PR 12a — per-niche selector-vs-matcher agreement badge.
   *
   * Three visual states:
   * - Empty (sample_count=0): dimmed placeholder — expected when
   *   GENLAB_PRODUCT_SELECTOR_ENABLED hasn't been flipped yet
   * - Accumulating: shows sample_count / 30 progress toward ready
   * - Ready: green pill with agreement rate — L3 PR 12b's auto-flip
   *   will consult this state per niche
   */
  if (!stats || stats.sample_count === 0) {
    return (
      <span className="text-[10px] text-text-muted/40" title="No divergence data yet — flip GENLAB_PRODUCT_SELECTOR_ENABLED to start collecting">
        no data
      </span>
    );
  }
  const rate = (stats.agreement_rate * 100).toFixed(0);
  if (stats.ready_for_enforcement) {
    return (
      <span
        className="rounded border border-success/30 bg-success/10 px-1.5 py-0.5 text-[10px] text-success"
        title={`${stats.agreement_count}/${stats.sample_count} agreement over ${stats.window_days}d — ready for L3 PR 12b enforcement`}
      >
        {rate}% · READY
      </span>
    );
  }
  return (
    <span
      className="text-[10px] text-text-muted"
      title={`${stats.agreement_count}/${stats.sample_count} agreement over ${stats.window_days}d — need 30 samples + 90% for ready`}
    >
      {rate}% · {stats.sample_count}/30
    </span>
  );
}

function NicheRow({
  nicheId,
  arms,
  divergenceStats,
}: {
  nicheId: NicheId;
  arms: ProductNicheSummary | undefined;
  divergenceStats: DivergenceStats | undefined;
}) {
  const info = getNicheInfo(nicheId);

  // 5-slot grid — mirror the top-N cap in the server's _shape_response.
  // Fewer arms are rendered as empty cells; the grid stays consistent
  // so niche rows align visually.
  const paddedArms: (ProductArm | undefined)[] = [
    ...(arms ?? []),
    ...Array(Math.max(0, 5 - (arms?.length ?? 0))).fill(undefined),
  ].slice(0, 5);

  return (
    <div className="grid grid-cols-[80px_repeat(5,minmax(90px,1fr))_100px] items-center gap-1 border-b border-border/40 py-2">
      <span
        className="text-xs font-semibold"
        style={{ color: info.color }}
        title={info.shortLabel}
      >
        {info.shortLabel}
      </span>
      {paddedArms.map((arm, idx) =>
        arm ? (
          <ProductCell key={arm.arm_id} arm={arm} />
        ) : (
          <div key={`empty-${idx}`} className="text-[10px] text-text-muted/40">
            —
          </div>
        ),
      )}
      <div className="text-right">
        <DivergenceBadge stats={divergenceStats} />
      </div>
    </div>
  );
}

const DIVERGENCE_WINDOW_DAYS = 7;

export function ProductBanditCard() {
  const { data, isLoading, isError } = useQuery({
    queryKey: queryKeys.productBandit.summary(),
    queryFn: () => productBandit.summary(),
    // 60s polling matches TransformationBanditCard + AutoApproval.
    // Product-bandit updates land in bandit_arms via the daily
    // click-rewards timer (06:00 UTC), so at steady state posteriors
    // change ~once per day per arm — 60s is quick enough for the
    // operator's "am I learning?" question without wasted requests.
    refetchInterval: 60 * 1000,
    staleTime: 30 * 1000,
  });

  // L3 PR 12a — batch-fetch per-niche divergence stats. Five parallel
  // requests share the 60s cadence with the summary poll. Each fails
  // open server-side (sample_count=0 when DB unreachable) so we can
  // safely default `undefined` to "no data yet" in the render.
  const divergenceQueries = useQueries({
    queries: NICHE_IDS.map((n) => ({
      queryKey: queryKeys.productBandit.divergenceStats(n, DIVERGENCE_WINDOW_DAYS),
      queryFn: () => productBandit.divergenceStats(n, DIVERGENCE_WINDOW_DAYS),
      refetchInterval: 60 * 1000,
      staleTime: 30 * 1000,
    })),
  });
  const divergenceByNiche = Object.fromEntries(
    NICHE_IDS.map((n, i) => [n, divergenceQueries[i]?.data]),
  ) as Record<NicheId, DivergenceStats | undefined>;

  if (isLoading) {
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="text-sm font-semibold">Product bandit</div>
        <div className="mt-1 text-xs text-text-muted">Loading…</div>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="text-sm font-semibold">Product bandit</div>
        <div className="mt-1 text-xs text-text-muted">
          No product arms yet — run
          <code className="mx-1 rounded bg-surface-2 px-1 py-0.5 text-[10px]">
            scripts/register_product_arms.py
          </code>
          after the L3 PR 1 migration deploys.
        </div>
      </div>
    );
  }

  const flagBadge = data.flag_enabled ? (
    <span
      className="rounded border border-success/30 bg-success/10 px-1.5 py-0.5 text-[10px] text-success"
      title="GENLAB_PRODUCT_SELECTOR_ENABLED=true — matcher wire is logging divergences but still returns matcher's pick; PR 12 will flip enforcement"
    >
      active
    </span>
  ) : (
    <span
      className="rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-[10px] text-warning"
      title="GENLAB_PRODUCT_SELECTOR_ENABLED is off — arms accumulate reward from click-rewards timer, but selector is not consulted at match time"
    >
      observation only
    </span>
  );

  const hasAnyNiche = Object.keys(data.niches).length > 0;

  return (
    <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
      <div className="mb-2 flex items-center gap-2">
        <div className="text-sm font-semibold">Product bandit</div>
        {flagBadge}
      </div>
      <div className="text-xs text-text-muted">
        Top 5 products + selector-vs-matcher agreement · updates 60s
      </div>
      {!hasAnyNiche ? (
        <div className="mt-2 text-xs text-text-muted">
          No product arms registered yet — waiting for
          scripts/register_product_arms.py to run on prod
        </div>
      ) : (
        <div className="mt-3">
          {NICHE_IDS.map((n) => (
            <NicheRow
              key={n}
              nicheId={n}
              arms={data.niches[n]}
              divergenceStats={divergenceByNiche[n]}
            />
          ))}
        </div>
      )}
    </div>
  );
}
