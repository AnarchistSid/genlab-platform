/**
 * Mission Control NichePauseCard (PR after #580, 2026-06-25).
 *
 * Operator's emergency-stop surface for the per-niche pause primitive
 * (PR #575). Backend endpoints (PR #577) work via curl; this card is
 * the operator-leverage layer that means a copyright strike → pause-
 * publishing is one click instead of a DB row.
 *
 * Mirrors SponsorshipReadinessCard's 5-niche row layout for visual
 * consistency. State badge per niche:
 *   active           — no pause row; green dot
 *   paused (4h 12m)  — auto-pause row with future paused_until
 *   paused (indef.)  — paused_until=null; operator must unpause
 *
 * Polls every 60s — same cadence as the other Mission Control cards.
 *
 * ## Design decisions worth flagging
 *
 *   * Pause uses native window.prompt() for the reason input. A modal
 *     would be prettier but the operator's emergency-stop should be
 *     fast and uninterrupted. Confirm + reason in one keystroke.
 *   * Unpause confirms via window.confirm() (single click could
 *     accidentally re-enable publishing on a halted niche).
 *   * `paused_until` is auto-set to 24h from now by default. Operators
 *     can override by editing the reason prompt — see future PR for a
 *     proper duration picker. 24h covers a typical operator-response
 *     window without making indefinite the default (which would be too
 *     sticky).
 *
 * ## SR-F awareness
 *
 *   Backend filters list responses by allowlist + rejects out-of-
 *   allowlist mutations with 403. Both surface as toast errors via
 *   the standard mutate() error path.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { schedulingPauses } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { NichePauseRecord } from "@/api/types";
import { getNicheInfo, NICHE_IDS, type NicheId } from "@/niches/registry";

/** Default pause window when the operator clicks "Pause" without
 * specifying. 24h ~ overnight + working day, matches typical operator
 * response windows on copyright strikes. */
const DEFAULT_PAUSE_HOURS = 24;

export function NichePauseCard() {
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.schedulingPauses.list(),
    queryFn: () => schedulingPauses.list(),
    staleTime: 60_000,
    refetchInterval: 60_000,
    retry: false,
  });

  const pausedByNiche = new Map<string, NichePauseRecord>();
  for (const p of data ?? []) {
    pausedByNiche.set(p.niche_id, p);
  }

  const pausedCount = pausedByNiche.size;

  return (
    <div className="bento-card">
      <h3 className="card-title">
        Publishing Pauses
        <span className="ml-2 text-[10px] text-text-muted font-normal">
          operator emergency-stop
        </span>
      </h3>

      {/* Headline — at-a-glance count of paused niches. Mirrors the
          sponsorship-readiness card's dot-+-summary pattern so
          operator's eye finds it in the same spot. */}
      <div className="flex items-center gap-2 mb-3">
        <span
          className={
            pausedCount === 0
              ? "size-2 rounded-full bg-success"
              : pausedCount === NICHE_IDS.length
                ? "size-2 rounded-full bg-error"
                : "size-2 rounded-full bg-warning"
          }
        />
        <span className="text-sm text-text-secondary">
          {pausedCount === 0
            ? "All 5 niches publishing"
            : pausedCount === NICHE_IDS.length
              ? `All ${NICHE_IDS.length} niches PAUSED`
              : `${pausedCount} of ${NICHE_IDS.length} niches paused`}
        </span>
      </div>

      <div className="flex flex-col gap-2">
        {isLoading && !data ? (
          <>
            <div className="shimmer" style={{ height: 14, width: "100%", borderRadius: 4 }} />
            <div className="shimmer" style={{ height: 14, width: "100%", borderRadius: 4 }} />
            <div className="shimmer" style={{ height: 14, width: "100%", borderRadius: 4 }} />
          </>
        ) : (
          NICHE_IDS.map((nicheId) => {
            const info = getNicheInfo(nicheId);
            return (
              <NicheRow
                key={nicheId}
                nicheId={nicheId}
                label={info.shortLabel}
                hex={info.hex}
                pause={pausedByNiche.get(nicheId)}
              />
            );
          })
        )}
      </div>

      {/* Footer — link to the auto-pause docs / config so operators
          can find the env-flag info that unlocks auto-pause writes
          (PR #579 + auto-pause-on-critical). Until /docs route lands
          this is a placeholder text. */}
      <div className="mt-3 pt-2 border-t border-text-muted/10">
        <span className="text-[10px] text-text-muted">
          Auto-pause on shadowban: opt-in via{" "}
          <code className="text-text-secondary">
            GENLAB_AUTO_PAUSE_ON_HEALTH_CRITICAL=1
          </code>
        </span>
      </div>
    </div>
  );
}

interface NicheRowProps {
  nicheId: NicheId;
  label: string;
  hex: string;
  /** Active pause record, or undefined when the niche is publishing. */
  pause: NichePauseRecord | undefined;
}

function NicheRow({ nicheId, label, hex, pause }: NicheRowProps) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="size-2 rounded-full shrink-0" style={{ background: hex }} />
      <span className="font-medium text-text-secondary shrink-0" style={{ width: 48 }}>
        {label}
      </span>

      {/* Middle slot: status badge OR pause reason */}
      <div className="flex-1 min-w-0">
        {pause ? (
          <PauseBadge pause={pause} />
        ) : (
          <span className="text-[11px] text-success">● publishing</span>
        )}
      </div>

      {/* Right slot: pause/resume action */}
      {pause ? (
        <ResumeButton nicheId={nicheId} />
      ) : (
        <PauseButton nicheId={nicheId} label={label} />
      )}
    </div>
  );
}

function PauseBadge({ pause }: { pause: NichePauseRecord }) {
  const remaining = formatPauseRemaining(pause.paused_until);
  const title =
    `Reason: ${pause.reason}\n` +
    `Paused by: ${pause.paused_by ?? "unknown"}\n` +
    (pause.paused_until
      ? `Auto-resume at: ${pause.paused_until}`
      : "Indefinite — operator must resume");
  return (
    <span
      className="font-mono text-[10px] text-warning px-1.5 py-0.5 rounded border border-warning/30 bg-warning/10"
      title={title}
    >
      paused {remaining}
    </span>
  );
}

function PauseButton({ nicheId, label }: { nicheId: NicheId; label: string }) {
  const qc = useQueryClient();
  const mutation = useMutation({
    mutationFn: ({ reason, pausedUntil }: { reason: string; pausedUntil: string | null }) =>
      schedulingPauses.pause(nicheId, reason, pausedUntil),
    onSuccess: () => {
      toast.success(`${label} paused`);
      qc.invalidateQueries({ queryKey: queryKeys.schedulingPauses.list() });
    },
    onError: (err) => {
      toast.error(err instanceof Error ? `Pause failed: ${err.message}` : "Pause failed");
    },
  });

  function handleClick() {
    // window.prompt() keeps the emergency-stop flow fast — operator
    // types reason + Enter and the row updates. A modal would add a
    // click that an emergency-stop shouldn't pay for.
    const reason = window.prompt(
      `Reason for pausing ${label}? (required, for audit log)`,
      "",
    );
    if (reason === null) return; // operator cancelled
    if (!reason.trim()) {
      toast.error("Reason is required (backend enforces)");
      return;
    }
    const pausedUntil = new Date(
      Date.now() + DEFAULT_PAUSE_HOURS * 60 * 60 * 1000,
    ).toISOString();
    mutation.mutate({ reason: reason.trim(), pausedUntil });
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={mutation.isPending}
      className="text-[10px] px-1.5 py-0.5 rounded border border-text-muted/30 text-text-secondary hover:bg-text-muted/10 disabled:opacity-50 disabled:cursor-not-allowed shrink-0"
      title={`Pause publishing on ${label} for ${DEFAULT_PAUSE_HOURS}h`}
      style={{ minWidth: 48 }}
    >
      {mutation.isPending ? "…" : "Pause"}
    </button>
  );
}

function ResumeButton({ nicheId }: { nicheId: NicheId }) {
  const qc = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => schedulingPauses.unpause(nicheId),
    onSuccess: () => {
      toast.success(`${nicheId} resumed — publishing re-enabled`);
      qc.invalidateQueries({ queryKey: queryKeys.schedulingPauses.list() });
    },
    onError: (err) => {
      toast.error(err instanceof Error ? `Resume failed: ${err.message}` : "Resume failed");
    },
  });

  function handleClick() {
    // Confirm to prevent accidental click — single click on the
    // operator's emergency-stop "Resume" should not silently re-enable
    // publishing on a niche that was halted for a copyright issue.
    if (!window.confirm(`Resume publishing on ${nicheId}? (this clears the pause)`)) {
      return;
    }
    mutation.mutate();
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={mutation.isPending}
      className="text-[10px] px-1.5 py-0.5 rounded border border-success/30 text-success hover:bg-success/10 disabled:opacity-50 disabled:cursor-not-allowed shrink-0"
      title="Clear the pause and re-enable publishing"
      style={{ minWidth: 48 }}
    >
      {mutation.isPending ? "…" : "Resume"}
    </button>
  );
}

/**
 * Format the pause-remaining display: "4h 12m", "12m", "32s",
 * "indef.", or "expired" for stale rows the sweeper hasn't GC'd yet.
 *
 * Exported visible for snapshot testing — formats are intentionally
 * compact so the badge fits the row width.
 */
export function formatPauseRemaining(isoUntil: string | null): string {
  if (!isoUntil) return "indef.";
  const until = new Date(isoUntil).getTime();
  if (Number.isNaN(until)) return "indef.";
  const remainingMs = until - Date.now();
  if (remainingMs <= 0) return "expired";
  const totalMin = Math.floor(remainingMs / 60_000);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (totalMin > 0) return `${totalMin}m`;
  // Sub-minute pause — rare but we show seconds so the badge moves
  const s = Math.max(1, Math.floor(remainingMs / 1000));
  return `${s}s`;
}
