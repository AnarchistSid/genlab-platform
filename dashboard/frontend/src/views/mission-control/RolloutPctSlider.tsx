/**
 * RolloutPctSlider — AUTO #2 per-niche graduated-rollout slider.
 *
 * Operator workflow per AUTO_2_ROLLOUT_ADDENDUM_2026-06-15-PM.md:
 *   Week 1: rollout_pct: 0.1   → ~10% of qualifiers auto-approve
 *   Week 2: rollout_pct: 0.25  → if Week-1 confusion matrix holds
 *   Week 3: rollout_pct: 0.50
 *   Week 4: rollout_pct: 1.0   → full traffic
 *
 * Pre-W4.4, those changes meant editing publishing.yaml + git
 * commit + deploy + worker reload (~5 min friction). This slider
 * makes the ramp a 1-click action while preserving the safety
 * boundaries:
 *
 *   - rollout_pct IS writeable (it's a volume knob, not a policy knob)
 *   - enabled / min_confidence / max_approvals_per_pass STAY on the
 *     git-commit path — they're calibrated decisions, not knobs
 *
 * What the slider shows:
 *   - Per-niche row × 5: current rollout_pct + slider + status pill
 *     (enabled / disabled / kill-switched)
 *   - Status pill reads ``enabled`` so operator knows whether the
 *     slide will have any effect (slide on a disabled niche is a
 *     no-op until enabled flips via git commit)
 *
 * Backend contract:
 *   - GET /api/v1/config/auto-publish?niche_id=X
 *   - PATCH /api/v1/config/auto-publish?niche_id=X {rollout_pct}
 *
 * Accessibility:
 *   - Each slider has a label + aria-valuetext (percentage form)
 *   - Slide commits on ``input`` debounced via state — actual PATCH
 *     only fires on slider release (onPointerUp) to avoid flooding
 *     the backend / yaml writer with intermediate values
 */
import { useState } from "react";
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { autoPublish } from "@/api/client";
import { NICHE_IDS, getNicheInfo, type NicheId } from "@/niches/registry";

const QK = (nicheId: NicheId) => ["config", "auto-publish", nicheId];

// Snap points let the operator hit the runbook's recommended ramp
// stops without needing pixel precision. Slider still slides freely;
// these are just visual references in the label.
const SNAP_POINTS = [0, 10, 25, 50, 100];

export function RolloutPctSlider() {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="mb-3">
        <h3 className="text-sm font-semibold">Auto-publish rollout</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Per-niche graduated rollout (W4.3). Recommended ramp:{" "}
          {SNAP_POINTS.map((p, i) => (
            <span key={p}>
              {p}%{i < SNAP_POINTS.length - 1 ? " → " : ""}
            </span>
          ))}{" "}
          over 4 weeks. Slider only affects niches where{" "}
          <code>enabled</code> is true; flipping <code>enabled</code> stays
          on the git-commit path.
        </p>
      </div>

      <div className="space-y-3">
        {NICHE_IDS.map((nicheId) => (
          <NicheRow key={nicheId} nicheId={nicheId} />
        ))}
      </div>
    </div>
  );
}

function NicheRow({ nicheId }: { nicheId: NicheId }) {
  const qc = useQueryClient();
  const nicheInfo = getNicheInfo(nicheId);

  const results = useQueries({
    queries: [
      {
        queryKey: QK(nicheId),
        queryFn: () => autoPublish.get(nicheId),
        staleTime: 60_000,
      },
    ],
  });
  const policyQuery = results[0];

  // Slider is controlled. ``localPct`` mirrors the slider during
  // active drag; when the server value changes (initial load or
  // post-mutation invalidate), we sync via the React 19 "set state
  // during render" pattern (guarded by lastSeenServer check). This
  // avoids the ``react-hooks/set-state-in-effect`` cascade the
  // useEffect-based version triggered.
  const serverPct = policyQuery.data
    ? Math.round(policyQuery.data.auto_publish.rollout_pct * 100)
    : null;
  const [localPct, setLocalPct] = useState<number | null>(null);
  const [lastSeenServer, setLastSeenServer] = useState<number | null>(null);
  if (serverPct !== null && serverPct !== lastSeenServer) {
    setLastSeenServer(serverPct);
    setLocalPct(serverPct);
  }

  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">(
    "idle",
  );
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: (pct: number) => autoPublish.setRolloutPct(nicheId, pct / 100),
    onMutate: () => {
      setStatus("saving");
      setErrorMsg(null);
    },
    onSuccess: () => {
      setStatus("saved");
      void qc.invalidateQueries({ queryKey: QK(nicheId) });
      // Clear the "saved" pill after a couple seconds so the row
      // doesn't permanently show stale confirmation.
      setTimeout(() => setStatus("idle"), 2000);
    },
    onError: (err: Error) => {
      setStatus("error");
      setErrorMsg(err.message);
    },
  });

  const handleCommit = () => {
    if (localPct === null) return;
    if (!policyQuery.data) return;
    const currentServer = Math.round(
      policyQuery.data.auto_publish.rollout_pct * 100,
    );
    if (localPct === currentServer) return;  // no-op
    mutation.mutate(localPct);
  };

  const enabled = policyQuery.data?.auto_publish.enabled ?? false;
  const displayPct = localPct ?? 0;

  return (
    <div className="rounded-md border border-border bg-background p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span
            className="rounded-full px-2 py-0.5 text-xs"
            style={{
              backgroundColor: `${nicheInfo.hex}20`,
              color: nicheInfo.hex,
            }}
          >
            {nicheInfo.label}
          </span>
          <StatusPill enabled={enabled} status={status} />
        </div>
        <span
          className="text-xs font-mono tabular-nums"
          aria-live="polite"
        >
          {displayPct}%
        </span>
      </div>

      <input
        type="range"
        min={0}
        max={100}
        step={1}
        value={displayPct}
        onChange={(e) => setLocalPct(parseInt(e.target.value, 10))}
        onPointerUp={handleCommit}
        onKeyUp={(e) => {
          // Slider supports keyboard arrows — commit on release as well
          if (e.key === "ArrowRight" || e.key === "ArrowLeft" || e.key === "Home" || e.key === "End") {
            handleCommit();
          }
        }}
        disabled={!policyQuery.data || mutation.isPending}
        aria-valuetext={`${displayPct} percent rollout`}
        aria-label={`${nicheInfo.label} auto-publish rollout percentage`}
        className="w-full accent-primary"
      />

      {errorMsg && (
        <p role="alert" className="mt-1 text-xs text-destructive">
          {errorMsg}
        </p>
      )}
    </div>
  );
}

function StatusPill({
  enabled,
  status,
}: {
  enabled: boolean;
  status: "idle" | "saving" | "saved" | "error";
}) {
  if (status === "saving") {
    return <span className="text-xs text-muted-foreground">saving…</span>;
  }
  if (status === "saved") {
    return <span className="text-xs text-emerald-500">saved</span>;
  }
  if (status === "error") {
    return <span className="text-xs text-destructive">error</span>;
  }
  if (!enabled) {
    return (
      <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] uppercase text-muted-foreground">
        disabled
      </span>
    );
  }
  return (
    <span className="rounded-full bg-emerald-500/20 px-1.5 py-0.5 text-[10px] uppercase text-emerald-500">
      enabled
    </span>
  );
}
