/**
 * AUTO #2 D1.4 (2026-06-15): Daily SLO badge for Mission Control.
 *
 * Surfaces the "1 reel per channel per day" SLO status across all 5
 * niches at a glance. The owner's autonomous-agent vision (CLAUDE.md)
 * commits to 1 reel/channel/day at 06:30 UTC; this card answers "are
 * we hitting it?" without the operator having to scan analytics.
 *
 * Visual language (mirrors AutoApprovalCalibrationCard for consistency):
 *   - 5 niche rows, color-coded by niche accent
 *   - Per-niche state: pending (0/1), met (1/1), violated (≥2/1)
 *   - Headline: "X / 5 niches published today"
 *   - Cap-violation rows render in red — these only appear if PR #220's
 *     scheduler-per-day-cap fix regresses; in steady state, all rows are
 *     either pending or met.
 *
 * No data path: when the overview endpoint hasn't responded yet, the
 * card shows skeleton rows; when published_today is missing (defensive
 * default), the row shows "—".
 *
 * Reads from the existing cross-niche overview endpoint — no new API
 * surface needed. The endpoint already populates per-niche
 * published_today + target_posts_per_day.
 */
import { useCrossNicheOverview } from "@/hooks/use-cross-niche-overview";
import { NICHE_HEX, NICHE_LABELS } from "@/niches/registry";

const SLO_TARGET_REELS_PER_DAY = 1;

type NicheSloState = "pending" | "met" | "violated";

function _classifyNiche(publishedToday: number): NicheSloState {
  if (publishedToday >= SLO_TARGET_REELS_PER_DAY + 1) return "violated";
  if (publishedToday >= SLO_TARGET_REELS_PER_DAY) return "met";
  return "pending";
}

function NicheRow({
  nicheId,
  publishedToday,
}: {
  nicheId: string;
  publishedToday: number;
}) {
  const state = _classifyNiche(publishedToday);
  const accent = NICHE_HEX[nicheId] ?? "#71717a";
  const label = NICHE_LABELS[nicheId] ?? nicheId;

  // Styling: pending=dim, met=green, violated=red.
  const stateStyles: Record<NicheSloState, { dot: string; text: string; tag: string }> = {
    pending: {
      dot: "bg-text-ghost",
      text: "text-text-muted",
      tag: "pending",
    },
    met: {
      dot: "bg-green-500",
      text: "text-green-400",
      tag: "met",
    },
    violated: {
      // Red because this is a cap violation — should be impossible after
      // PR #220's scheduler-per-day-cap fix. If you see this in prod,
      // either the fix regressed or the publisher's DailyCapEnforcer
      // fail-open from the SUCCESS/INSIGHTS_* bug (also fixed in #220)
      // came back.
      dot: "bg-red-500",
      text: "text-red-400",
      tag: "cap violated",
    },
  };
  const { dot, text, tag } = stateStyles[state];

  return (
    <div className="flex items-center gap-3 py-1.5">
      <span
        className="size-2 rounded-full shrink-0"
        style={{ backgroundColor: accent }}
        aria-hidden
      />
      <span className="flex-1 text-sm text-text-secondary truncate">{label}</span>
      <span className={`size-2 rounded-full ${dot}`} aria-hidden />
      <span className={`text-xs font-mono tabular-nums ${text}`}>
        {publishedToday} / {SLO_TARGET_REELS_PER_DAY}
      </span>
      <span className={`text-[10px] uppercase tracking-wide ${text} w-[80px] text-right`}>
        {tag}
      </span>
    </div>
  );
}

export function DailySloBadge() {
  const { data, isLoading, isError } = useCrossNicheOverview();

  const niches = data?.niches ?? [];
  const metCount = niches.filter(
    (n) => n.published_today >= SLO_TARGET_REELS_PER_DAY
      && n.published_today < SLO_TARGET_REELS_PER_DAY + 1,
  ).length;
  const violatedCount = niches.filter(
    (n) => n.published_today >= SLO_TARGET_REELS_PER_DAY + 1,
  ).length;
  const total = niches.length;

  // Headline color: all met = green, any violation = red, otherwise neutral.
  const headlineColor = violatedCount > 0
    ? "text-red-400"
    : metCount === total && total > 0
      ? "text-green-400"
      : "text-text-secondary";

  return (
    <div className="bento-card">
      <h3 className="card-title">
        Daily SLO
        <span className="ml-2 text-[10px] text-text-muted font-normal">
          1 reel / channel / day
        </span>
      </h3>

      {isError && (
        <div className="text-xs text-red-400 py-2">
          Overview endpoint unreachable — cannot read per-niche publish counts.
        </div>
      )}

      <div className={`mb-2 text-2xl font-bold tabular-nums ${headlineColor}`}>
        {isLoading || total === 0
          ? "—"
          : `${metCount} / ${total}`}
        <span className="ml-2 text-xs font-normal text-text-muted">
          niches published today
        </span>
      </div>

      {violatedCount > 0 && (
        <div
          role="alert"
          className="mb-2 rounded-md border border-red-500/40 bg-red-500/10 px-2 py-1.5 text-[11px] text-red-200"
        >
          ⚠ {violatedCount} niche{violatedCount > 1 ? "s" : ""} over the 1/day cap.
          Check publisher logs — PR #220 was meant to prevent this.
        </div>
      )}

      <div className="divide-y divide-border-subtle">
        {isLoading && total === 0 ? (
          // Skeleton: 5 dim rows so the card doesn't collapse height
          // while loading. Matches the eventual layout.
          Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-7 animate-pulse bg-bg-hover/30 rounded my-1" />
          ))
        ) : (
          niches.map((n) => (
            <NicheRow
              key={n.id}
              nicheId={n.id}
              publishedToday={n.published_today ?? 0}
            />
          ))
        )}
      </div>
    </div>
  );
}
