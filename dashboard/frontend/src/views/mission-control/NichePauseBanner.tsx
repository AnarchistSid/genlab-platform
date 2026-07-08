/**
 * Mission Control top-surface niche-pause banner (H3, 2026-07-08 audit).
 *
 * The gap H3 flagged: `NichePauseCard` lives below-the-fold on 1440p.
 * If a niche was paused for a copyright strike or shadowban recovery,
 * the operator would spend 30 minutes debugging "why isn't gaming
 * publishing?" before scrolling to find the pause row.
 *
 * This banner surfaces the same data at the top of Mission Control,
 * above the KPI hero but below the CriticalAlertsBanner so the
 * operator's eye lands on infrastructure alerts first, then on the
 * "paused niches" reminder, then on the KPIs.
 *
 * Design decisions worth flagging
 * -------------------------------
 * * **Renders NOTHING when nothing is paused.** No visual noise in
 *   the healthy path. The banner is a reminder, not a status line.
 * * **60s poll matches `NichePauseCard`** so the two surfaces never
 *   disagree — same `queryKey` reuses the same cache entry.
 * * **Anchor jump to the pause card** instead of duplicating the
 *   pause/resume actions here. The banner tells you *what*; you
 *   scroll to the card to fix it. Keeps the banner narrow.
 */
import { useQuery } from "@tanstack/react-query";
import { PauseCircle } from "lucide-react";

import { schedulingPauses } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import { getNicheInfo } from "@/niches/registry";

/** ID of the pause card's outer element — used so the banner can
 * scroll the operator to the actionable surface. Keep in sync with
 * the id="…" on the `NichePauseCard` root; both live in the same
 * dir so drift is unlikely, but if you rename here update there. */
const _PAUSE_CARD_ANCHOR_ID = "niche-pause-card";

export function NichePauseBanner() {
  const { data } = useQuery({
    // Same key + fetcher as `NichePauseCard` — React Query dedupes
    // the request across both consumers automatically.
    queryKey: queryKeys.schedulingPauses.list(),
    queryFn: () => schedulingPauses.list(),
    staleTime: 60_000,
    refetchInterval: 60_000,
    retry: false,
  });

  // Cold-start or empty — render nothing. Banner MUST NOT appear when
  // no niches are paused; the whole point is that its presence itself
  // is the signal.
  if (!data || data.length === 0) {
    return null;
  }

  const pausedIds = data.map((p) => p.niche_id);

  function handleClick() {
    const el = document.getElementById(_PAUSE_CARD_ANCHOR_ID);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="niche-pause-banner"
      className="mc-pause-banner"
      onClick={handleClick}
      style={{
        cursor: "pointer",
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "8px 12px",
        margin: "8px 0",
        background: "rgba(245, 158, 11, 0.10)",
        border: "1px solid rgba(245, 158, 11, 0.40)",
        borderRadius: 6,
        color: "var(--color-warning, #f59e0b)",
        fontSize: 12,
        fontWeight: 500,
      }}
    >
      <PauseCircle size={14} aria-hidden />
      <span>
        {pausedIds.length === 1
          ? "1 niche paused: "
          : `${pausedIds.length} niches paused: `}
        {pausedIds.map((id, i) => {
          const info = getNicheInfo(id);
          return (
            <span key={id}>
              {i > 0 && ", "}
              <span
                style={{
                  color: info.hex,
                  fontWeight: 600,
                }}
              >
                {info.shortLabel}
              </span>
            </span>
          );
        })}
      </span>
      <span
        style={{
          marginLeft: "auto",
          fontSize: 11,
          opacity: 0.7,
        }}
      >
        click to jump →
      </span>
    </div>
  );
}
