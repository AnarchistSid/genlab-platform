/**
 * L11 observability card (2026-07-08 audit) — GENLAB_* flag states.
 *
 * Renders every catalogued env flag grouped by capability domain
 * (learning / intelligence / content_quality / monetization /
 * experimentation / infrastructure). Each entry shows a status
 * badge (active / dormant / mis_set / disabled_by_flag).
 *
 * ## The primary operator value: catching mis-set values
 *
 * The audit-shipping arc surfaced multiple case-sensitivity footguns
 * where operators set a flag to `"true"` but the reader required
 * literal `"1"`. Silent no-ops meant features shipped disabled
 * without any signal. This card surfaces those cases explicitly:
 *
 *   * `mis_set` rows show an amber badge + the endpoint's warning
 *     message tooltip explaining exactly what the raw value was
 *     and why it may silently no-op.
 *   * The summary at the top prominently shows the mis_set count.
 *     Zero mis_set = the healthy path; any mis_set = something an
 *     operator should look at.
 *
 * ## Cadence
 *
 * Env values don't change without SSH access. A 5-min poll is
 * plenty — reading the endpoint every 60s would be wasteful. Manual
 * refresh (via React Query invalidation on other card interactions)
 * catches the rare case of an intentional runtime env override.
 *
 * ## Sibling cards
 *
 * Visual language matches `CrossNichePriorsCard` — grouped-list card
 * with per-row badges + a summary strip at the top.
 */
import { useQuery } from "@tanstack/react-query";

import { flagState } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { FlagEntry, FlagStatus } from "@/api/types";

// Group display order — reflects operator priority. Learning +
// intelligence come first because those are the interventions
// under active validation; infrastructure last because it's mostly
// stable kill switches.
const _GROUP_ORDER: string[] = [
  "learning",
  "intelligence",
  "content_quality",
  "monetization",
  "experimentation",
  "infrastructure",
];

const _GROUP_LABEL: Record<string, string> = {
  learning: "Learning",
  intelligence: "Intelligence",
  content_quality: "Content quality",
  monetization: "Monetization",
  experimentation: "Experimentation",
  infrastructure: "Infrastructure",
};

const _STATUS_TITLE: Record<FlagStatus, string> = {
  active: "Feature is enabled (raw value matches a truthy convention)",
  dormant: "Feature is off (env var unset or value is falsy)",
  mis_set:
    "Env value doesn't match any truthy/falsy convention — may silently no-op depending on the reader",
  disabled_by_flag:
    "Kill switch is ACTIVE — feature is deliberately disabled by the operator",
};

/** CSS classes chosen to match the sibling cards' badge language:
 *  green = healthy running, gray = deliberately off, amber = attention
 *  needed, red = disabled/blocked. */
function statusBadgeClasses(status: FlagStatus): string {
  const base =
    "inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-mono font-medium";
  switch (status) {
    case "active":
      return `${base} bg-emerald-500/15 text-emerald-400`;
    case "dormant":
      return `${base} bg-zinc-500/15 text-zinc-400`;
    case "mis_set":
      return `${base} bg-amber-500/20 text-amber-400`;
    case "disabled_by_flag":
      return `${base} bg-red-500/20 text-red-400`;
  }
}

function FlagRow({ flag }: { flag: FlagEntry }) {
  // Show the raw_value only when non-null AND status is mis_set —
  // "active" (raw="1") doesn't need to be repeated; "dormant" (null)
  // has no value. This keeps rows compact.
  const showRawValue = flag.raw_value !== null && flag.status === "mis_set";
  return (
    <div
      className="grid grid-cols-[auto,1fr,auto] items-center gap-2 border-b border-border/40 py-1.5 text-xs"
      data-testid={`flag-row-${flag.name}`}
      data-status={flag.status}
    >
      <span className={statusBadgeClasses(flag.status)} title={_STATUS_TITLE[flag.status]}>
        {flag.status}
      </span>
      <div className="min-w-0">
        <div className="truncate font-mono text-[11px]" title={flag.name}>
          {flag.name}
        </div>
        <div className="truncate text-[10px] text-text-muted" title={flag.role}>
          {flag.role}
        </div>
        {flag.warning && (
          <div
            className="mt-0.5 text-[10px] text-amber-400"
            data-testid={`flag-warning-${flag.name}`}
          >
            ⚠ {flag.warning}
          </div>
        )}
      </div>
      {showRawValue && (
        <span
          className="rounded bg-zinc-800 px-1 py-0.5 font-mono text-[10px] text-zinc-300"
          title="Raw env value (redacted if >32 chars)"
        >
          {flag.raw_value}
        </span>
      )}
    </div>
  );
}

export function FlagStateCard() {
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.flagState.get(),
    queryFn: () => flagState.get(),
    // 5-min poll — env values change rarely, no point hammering.
    refetchInterval: 5 * 60 * 1000,
    staleTime: 60 * 1000,
  });

  if (isLoading || !data) {
    return (
      <div className="rounded-lg border border-border/60 bg-surface-1 p-3">
        <div className="text-sm font-semibold">Feature flags</div>
        <div className="mt-2 text-xs text-text-muted">Loading…</div>
      </div>
    );
  }

  const { flags, summary } = data;
  const misSetCount = summary.mis_set ?? 0;
  const disabledByFlagCount = summary.disabled_by_flag ?? 0;

  // Group flags for rendering.
  const grouped = new Map<string, FlagEntry[]>();
  for (const flag of flags) {
    if (!grouped.has(flag.group)) {
      grouped.set(flag.group, []);
    }
    grouped.get(flag.group)!.push(flag);
  }

  return (
    <div className="rounded-lg border border-border/60 bg-surface-1 p-3" data-testid="flag-state-card">
      <div className="mb-2">
        <div className="text-sm font-semibold">Feature flags</div>
        <div className="text-xs text-text-muted">
          {summary.total} catalogued GENLAB_* env flags
        </div>
      </div>

      {/* Summary strip — the mis_set count is the primary alert
          signal. Zero = healthy; any > 0 = something to look at. */}
      <div
        className="mb-3 flex flex-wrap gap-1.5 text-[11px]"
        data-testid="flag-state-summary"
      >
        <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-emerald-400">
          {summary.active} active
        </span>
        <span className="rounded bg-zinc-500/15 px-1.5 py-0.5 text-zinc-400">
          {summary.dormant} dormant
        </span>
        {misSetCount > 0 && (
          <span
            className="rounded bg-amber-500/20 px-1.5 py-0.5 text-amber-400"
            data-testid="flag-state-mis-set-count"
            title="Values that don't match any truthy/falsy convention — may silently no-op"
          >
            ⚠ {misSetCount} mis-set
          </span>
        )}
        {disabledByFlagCount > 0 && (
          <span className="rounded bg-red-500/20 px-1.5 py-0.5 text-red-400">
            {disabledByFlagCount} kill switch ON
          </span>
        )}
      </div>

      <div className="flex flex-col gap-3">
        {_GROUP_ORDER.map((group) => {
          const entries = grouped.get(group);
          if (!entries || entries.length === 0) return null;
          return (
            <div key={group} data-testid={`flag-group-${group}`}>
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-text-muted">
                {_GROUP_LABEL[group] ?? group}{" "}
                <span className="text-text-muted">({entries.length})</span>
              </div>
              <div>
                {entries.map((flag) => (
                  <FlagRow key={flag.name} flag={flag} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
