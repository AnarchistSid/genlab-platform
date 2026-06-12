import { useMemo, useState } from "react";
import { AlertOctagon, ChevronDown, ChevronUp } from "lucide-react";
import { useCriticalAlerts } from "@/hooks/use-critical-alerts";

/**
 * Mission Control top-of-page CRITICAL alerts banner.
 *
 * Reads ``GET /api/v1/alerts/critical`` (only unresolved rows of
 * severity=critical from the ``pipeline_alerts`` table). Closes the
 * visibility gap that let the 2026-05-20 WARP outage go
 * undetected for 20+ days — the system had been firing
 * ``warp_down`` CRITICAL alerts with the exact remediation
 * (``Run: systemctl restart warp-svc``) in the alert message, and
 * nothing in the operator's primary view was relaying them.
 *
 * Design choices
 * --------------
 *
 * 1. **Top of the page, ABOVE the publishing banner.**
 *    Publishing-alert rows are derived from blueprint state
 *    ("3 blueprints permanently failed"); CRITICAL pipeline alerts
 *    are infrastructure / operator-action signals (``warp_down``,
 *    ``service_down``, ``download_failure``). The operator should
 *    see the infrastructure issue FIRST.
 *
 * 2. **Dedup by ``(niche_id, check_name)``.**
 *    The downstream daily-cron health checks can fire the same
 *    ``download_failure`` row every hour for days; without dedup
 *    the banner becomes wall-of-noise and the operator stops
 *    reading it. The first row (most recent created_at) is the
 *    representative; ``occurrences`` shows the group size.
 *
 * 3. **Show the remediation verbatim.**
 *    Several check_names embed the fix in the message body
 *    (``"Run: systemctl restart warp-svc"``,
 *    ``"warp-cli mode proxy && warp-cli proxy port 40000"``).
 *    The component renders the message untransformed so the
 *    operator can copy-paste. This is the single most important
 *    UX choice: anything that interprets / summarises the message
 *    risks hiding the literal command.
 *
 * 4. **Auto-fix outcome rendered separately.**
 *    When the system tried to auto-fix and it failed
 *    (``auto_fix_applied = "yt-dlp update failed (rc=2)"``), that
 *    signal is critical — it tells the operator "the routine
 *    remediation didn't work, this is the case that needs manual
 *    intervention." Shown as a sub-row.
 *
 * 5. **No "dismiss" button.**
 *    The ``pipeline_alerts.resolved_at`` column is set by the
 *    backend when the underlying condition clears (``health_monitor``
 *    re-runs its check and finds it healthy). Adding a
 *    frontend-only dismiss would lie to the operator about the
 *    underlying state. Resolution must come from the data.
 *
 * 6. **Hidden when count=0.**
 *    The banner takes screen real estate; when there are no
 *    unresolved critical alerts the operator doesn't need to see
 *    "all clear" noise. Matches the existing PublishingAlertBanner
 *    pattern.
 */

interface CriticalAlertRow {
  id: string;
  niche_id: string | null;
  check_name: string;
  message: string;
  details: Record<string, unknown> | null;
  auto_fix_applied: string | null;
  created_at: string; // ISO
}

interface CriticalAlertsPayload {
  unresolved_critical: CriticalAlertRow[];
  count: number;
}

interface DedupedGroup {
  representative: CriticalAlertRow;
  occurrences: number;
  earliest_created_at: string;
}

function groupKey(row: CriticalAlertRow): string {
  return `${row.niche_id ?? "global"}::${row.check_name}`;
}

function dedupGroups(rows: CriticalAlertRow[]): DedupedGroup[] {
  // Rows arrive ordered by created_at DESC (server contract); the
  // first row per group is therefore the most-recent representative.
  const map = new Map<string, DedupedGroup>();
  for (const row of rows) {
    const k = groupKey(row);
    const existing = map.get(k);
    if (existing) {
      existing.occurrences += 1;
      if (row.created_at < existing.earliest_created_at) {
        existing.earliest_created_at = row.created_at;
      }
    } else {
      map.set(k, {
        representative: row,
        occurrences: 1,
        earliest_created_at: row.created_at,
      });
    }
  }
  return Array.from(map.values()).sort(
    (a, b) =>
      b.representative.created_at.localeCompare(a.representative.created_at),
  );
}

function formatAge(iso: string): string {
  const then = new Date(iso).getTime();
  const now = Date.now();
  const minutes = Math.max(0, Math.floor((now - then) / 60_000));
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function CriticalAlertsBanner() {
  const { data: raw } = useCriticalAlerts();
  const [expanded, setExpanded] = useState(true);

  const payload = useMemo(() => {
    // The shared ``get`` wrapper unwraps ``{success, data}`` so the
    // hook returns the inner object. Be defensive about shape: in dev
    // (no DATABASE_URL) the server returns count=0 with an empty
    // array, and we want to hide rather than crash.
    if (!raw || typeof raw !== "object") return null;
    const candidate = raw as Partial<CriticalAlertsPayload>;
    if (!Array.isArray(candidate.unresolved_critical)) return null;
    return {
      unresolved_critical: candidate.unresolved_critical,
      count: candidate.count ?? candidate.unresolved_critical.length,
    } as CriticalAlertsPayload;
  }, [raw]);

  const groups = useMemo(
    () => (payload ? dedupGroups(payload.unresolved_critical) : []),
    [payload],
  );

  if (!payload || groups.length === 0) return null;

  return (
    <div
      role="alert"
      className="rounded-lg border bg-red-500/10 border-red-500/40 p-3 mb-4"
    >
      <button
        type="button"
        className="flex items-center gap-2 w-full text-left"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        aria-controls="critical-alerts-list"
      >
        <AlertOctagon className="size-4 text-red-400 shrink-0" aria-hidden />
        <span className="text-sm font-semibold text-red-400">
          {groups.length} unresolved CRITICAL system alert
          {groups.length !== 1 ? "s" : ""}
        </span>
        <span className="text-xs text-text-muted ml-auto flex items-center gap-1">
          {expanded ? (
            <>
              collapse
              <ChevronUp className="size-3" aria-hidden />
            </>
          ) : (
            <>
              expand
              <ChevronDown className="size-3" aria-hidden />
            </>
          )}
        </span>
      </button>

      {expanded && (
        <ul
          id="critical-alerts-list"
          className="mt-3 space-y-3 text-xs"
        >
          {groups.map((g) => (
            <li
              key={g.representative.id}
              className="rounded border border-red-500/20 bg-red-500/5 p-2"
            >
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-mono uppercase text-red-300 font-semibold">
                  {g.representative.check_name}
                </span>
                {g.representative.niche_id && (
                  <span className="font-mono text-text-muted">
                    [{g.representative.niche_id}]
                  </span>
                )}
                <span className="text-text-muted">
                  {formatAge(g.representative.created_at)}
                </span>
                {g.occurrences > 1 && (
                  <span className="px-1.5 py-0.5 rounded bg-red-500/20 text-red-300 text-[10px]">
                    ×{g.occurrences} occurrences
                  </span>
                )}
              </div>
              <div className="mt-1 text-text whitespace-pre-wrap leading-relaxed">
                {g.representative.message}
              </div>
              {g.representative.auto_fix_applied && (
                <div className="mt-1 text-text-muted">
                  <span className="text-amber-400">auto-fix:</span>{" "}
                  <span className="font-mono">
                    {g.representative.auto_fix_applied}
                  </span>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
