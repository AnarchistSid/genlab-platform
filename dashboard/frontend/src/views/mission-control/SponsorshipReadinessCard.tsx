/**
 * PR T (2026-06-23) — Mission Control sponsorship-readiness card.
 *
 * Operator-leverage dimension opener. Surfaces, at a glance, which of
 * the 5 channels are sponsor-pitchable this week vs need more growth.
 *
 * The autonomy synthesis identified that the operator (9 IG followers,
 * 7800 monthly views, code intelligence already mid-tier creator quality)
 * is bottlenecked on REACH and on sponsor outreach. This card surfaces
 * the "is X channel pitchable?" signal that the operator currently has
 * to compute manually by cross-referencing several screens.
 *
 * Tier semantics (matched by the SponsorshipTier enum):
 *   eligible_now      — at least one platform's monetisation threshold
 *                       crossed. Green. Pitch sponsors now.
 *   within_2_months   — nearest unmet metric ≤ 60d with positive 7d
 *                       velocity. Amber. Sponsors care if outreach
 *                       starts soon.
 *   within_6_months   — nearest unmet metric 60-180d out. Blue. Track.
 *   tracking          — far away or flat velocity. Muted. Patience.
 *
 * No-data path: when monetisationprogress is empty (cold start,
 * DATABASE_URL unset on prod, audience-collector launchd job hasn't
 * fired yet), each niche row still renders with "no data yet" rather
 * than a blank card — same defensive pattern as the calibration card.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";

import { outreach, sponsorship } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type {
  SponsorshipNicheReadiness,
  SponsorshipPrimaryMetric,
  SponsorshipTier,
} from "@/api/types";
import { ProgressBar } from "@/components/shared/progress-bar";
import { getNicheInfo, NICHE_IDS, type NicheId } from "@/niches/registry";

const TIER_LABEL: Record<SponsorshipTier, string> = {
  eligible_now: "Pitch now",
  within_2_months: "≤ 2 mo",
  within_6_months: "≤ 6 mo",
  tracking: "Tracking",
};

const TIER_COLOR: Record<SponsorshipTier, string> = {
  // Use the existing card-system color vars (matches the calibration
  // card's bar-color pattern). bg classes drive the badge fill; text
  // classes keep contrast legible.
  eligible_now: "bg-success/10 text-success border-success/30",
  within_2_months: "bg-warning/10 text-warning border-warning/30",
  within_6_months: "bg-info/10 text-info border-info/30",
  tracking: "bg-text-muted/10 text-text-muted border-text-muted/30",
};

// Sort order so eligible-now niches surface first inside the card —
// the operator's eye lands on the actionable rows immediately.
const TIER_RANK: Record<SponsorshipTier, number> = {
  eligible_now: 0,
  within_2_months: 1,
  within_6_months: 2,
  tracking: 3,
};

export function SponsorshipReadinessCard() {
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.sponsorship.readiness(),
    queryFn: () => sponsorship.readiness(),
    staleTime: 60_000,
    refetchInterval: 60_000,
    retry: false,
  });

  const perNiche = data ?? {};
  const eligibleNow = NICHE_IDS.filter(
    (n) => perNiche[n]?.tier === "eligible_now",
  ).length;

  return (
    <div className="bento-card">
      <h3 className="card-title">
        Sponsorship Readiness
        <span className="ml-2 text-[10px] text-text-muted font-normal">
          pitchable this week
        </span>
      </h3>

      {/* Headline — at-a-glance pitchable count */}
      <div className="flex items-center gap-2 mb-3">
        <span
          className={
            eligibleNow === NICHE_IDS.length
              ? "size-2 rounded-full bg-success"
              : eligibleNow > 0
                ? "size-2 rounded-full bg-warning"
                : "size-2 rounded-full bg-text-muted"
          }
          style={
            eligibleNow > 0
              ? { boxShadow: "0 0 6px rgba(34,197,94,0.4)" }
              : undefined
          }
        />
        <span className="text-sm text-text-secondary">
          {eligibleNow === NICHE_IDS.length
            ? "All 5 niches pitch-ready"
            : eligibleNow > 0
              ? `${eligibleNow} / ${NICHE_IDS.length} niche${eligibleNow === 1 ? "" : "s"} pitch-ready`
              : `${NICHE_IDS.length} niches tracking`}
        </span>
      </div>

      {/* Per-niche rows, sorted by tier (eligible first) */}
      <div className="flex flex-col gap-2">
        {isLoading && !data ? (
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
          [...NICHE_IDS]
            .sort((a, b) => {
              const ra = TIER_RANK[perNiche[a]?.tier ?? "tracking"];
              const rb = TIER_RANK[perNiche[b]?.tier ?? "tracking"];
              return ra - rb;
            })
            .map((nicheId) => {
              const info = getNicheInfo(nicheId);
              return (
                <NicheRow
                  key={nicheId}
                  nicheId={nicheId}
                  label={info.shortLabel}
                  hex={info.hex}
                  readiness={perNiche[nicheId]}
                />
              );
            })
        )}
      </div>

      {/* PR X (2026-06-23): portfolio link closes the discoverability
          gap — PRs #482/#483 shipped /media-kit routes with no nav
          entry from the dashboard. Footer link opens the all-5-niches
          printable in a new tab so operator doesn't lose Mission
          Control state. */}
      <div className="mt-3 pt-2 border-t border-text-muted/10 flex items-center justify-end">
        <a
          href="/media-kit/all"
          target="_blank"
          rel="noopener noreferrer"
          className="text-[10px] text-text-secondary hover:text-text-primary transition-colors"
          title="Open the multi-niche portfolio kit in a new tab"
        >
          View portfolio →
        </a>
      </div>
    </div>
  );
}

interface NicheRowProps {
  /** Stable niche identifier (used by the Copy-pitch fetch + the
   * media-kit URL). */
  nicheId: NicheId;
  label: string;
  hex: string;
  readiness: SponsorshipNicheReadiness | undefined;
}

function NicheRow({ nicheId, label, hex, readiness }: NicheRowProps) {
  // No data yet → muted row with "—" placeholders. Same defensive
  // shape as AutoApprovalCalibrationCard's NicheRow.
  if (!readiness) {
    return (
      <div className="flex items-center gap-2 text-xs">
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
        <div className="flex-1 min-w-0">
          <ProgressBar value={0} color="var(--text-muted)" height={4} />
        </div>
        <span
          className="font-mono text-text-muted shrink-0 text-right"
          style={{ width: 36 }}
        >
          —
        </span>
        <span
          className="font-mono text-[10px] text-text-muted shrink-0 px-1.5 py-0.5"
          style={{ minWidth: 64, textAlign: "right" }}
          title="no monetisation data yet — audience-collector pending"
        >
          no data
        </span>
      </div>
    );
  }

  const { tier, nearest_threshold_days: nearestDays, platforms } = readiness;
  const primary: SponsorshipPrimaryMetric | null =
    pickHeadlinePrimaryMetric(platforms);
  const pct = primary?.pct_complete ?? null;
  const pctDisplay = pct === null ? "—" : `${Math.round(pct)}%`;

  // Bar color matches the tier rather than the pct — visual continuity
  // with the badge means operator's eye groups niches by "actionable
  // state" not by "raw percentage".
  const barColor =
    tier === "eligible_now"
      ? "var(--color-green)"
      : tier === "within_2_months"
        ? "var(--color-amber)"
        : tier === "within_6_months"
          ? "var(--color-info, #3b82f6)"
          : "var(--text-muted)";

  const tierBadgeTitle =
    tier === "eligible_now"
      ? "At least one platform monetised — sponsors will respect the audience number."
      : tier === "within_2_months"
        ? `Nearest threshold ${nearestDays}d away with positive 7d velocity.`
        : tier === "within_6_months"
          ? `Nearest threshold ${nearestDays}d away with positive 7d velocity.`
          : "Far from threshold or flat 7d velocity — patience.";

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

      {/* Primary-metric progress bar (closest platform-metric) */}
      <div className="flex-1 min-w-0">
        <ProgressBar
          value={Math.min(100, Math.max(0, pct ?? 0))}
          color={barColor}
          height={4}
        />
      </div>

      {/* Primary-metric pct */}
      <span
        className="font-mono text-text-primary shrink-0 text-right"
        style={{ width: 36 }}
      >
        {pctDisplay}
      </span>

      {/* Tier badge */}
      <span
        className={`font-mono text-[10px] shrink-0 px-1.5 py-0.5 rounded border ${TIER_COLOR[tier]}`}
        title={tierBadgeTitle}
        style={{ minWidth: 64, textAlign: "right" }}
      >
        {TIER_LABEL[tier]}
      </span>

      {/* PR W: Copy pitch action — generates outreach template +
          writes body to clipboard. The single button is the entire
          new operator-leverage surface from this row. */}
      <CopyPitchButton nicheId={nicheId} />

      {/* PR X: View kit — opens per-niche printable kit in new tab.
          Sibling to CopyPitchButton; together they make a row a
          two-button operator-leverage micro-surface (Copy text + View
          full kit). Both share the same nicheId source-of-truth. */}
      <a
        href={`/media-kit/${nicheId}`}
        target="_blank"
        rel="noopener noreferrer"
        className="text-[10px] px-1.5 py-0.5 rounded border border-text-muted/30 text-text-secondary hover:bg-text-muted/10 shrink-0 inline-flex items-center justify-center"
        title={`Open the ${label} media kit in a new tab`}
        style={{ minWidth: 36 }}
      >
        Kit
      </a>
    </div>
  );
}

/**
 * Fetches the outreach template on click and writes the body to the
 * clipboard. Lazy fetch (only fires when operator clicks) so we don't
 * burn an HTTP call per niche row at card mount.
 *
 * Feedback: toast on success / failure. Disabled state during the
 * fetch so the operator doesn't double-click and get two
 * notifications.
 */
function CopyPitchButton({ nicheId }: { nicheId: NicheId }) {
  const [busy, setBusy] = useState(false);

  async function handleClick(e: React.MouseEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    try {
      const tpl = await outreach.template(nicheId);
      const text = `Subject: ${tpl.subject}\n\n${tpl.body}`;
      // Modern Clipboard API. Fails silently on insecure contexts
      // (HTTP without localhost) — we catch + toast in that case.
      await navigator.clipboard.writeText(text);
      toast.success("Pitch copied — paste into email/Slack");
    } catch (err) {
      // Common failure: clipboard denied permission, or fetch failed.
      // Surface a single non-blocking toast; operator can retry.
      toast.error(
        err instanceof Error
          ? `Copy failed: ${err.message}`
          : "Copy failed — try again",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={busy}
      className="text-[10px] px-1.5 py-0.5 rounded border border-text-muted/30 text-text-secondary hover:bg-text-muted/10 disabled:opacity-50 disabled:cursor-not-allowed shrink-0"
      title="Generate outreach pitch and copy to clipboard"
      style={{ minWidth: 44 }}
    >
      {busy ? "…" : "Copy"}
    </button>
  );
}

/**
 * Pick the single most-informative primary metric across all platforms
 * for the niche-row's headline progress bar.
 *
 * Rules — mirror the server's per-platform _compute_platform_summary
 * but applied across platforms:
 *   1. If any platform is monetised, prefer that platform's primary
 *      (the operator's eye should land on the "we crossed the line"
 *      surface first).
 *   2. Otherwise prefer the platform with the highest pct_complete
 *      on its primary metric — closest miss.
 */
function pickHeadlinePrimaryMetric(
  platforms: Record<string, { is_monetised: boolean; primary_metric: SponsorshipPrimaryMetric | null }>,
): SponsorshipPrimaryMetric | null {
  const entries = Object.values(platforms).filter((p) => p.primary_metric);
  if (entries.length === 0) return null;

  const monetised = entries.find((p) => p.is_monetised);
  if (monetised?.primary_metric) return monetised.primary_metric;

  return entries.reduce(
    (best, p) => {
      const bestPct = best.primary_metric?.pct_complete ?? -1;
      const candPct = p.primary_metric?.pct_complete ?? -1;
      return candPct > bestPct ? p : best;
    },
    entries[0],
  ).primary_metric;
}
