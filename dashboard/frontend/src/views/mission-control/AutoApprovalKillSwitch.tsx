import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ShieldAlert, ShieldCheck } from "lucide-react";

/**
 * D3.10 (2026-06-15, AUTO #2 runbook) — Global auto-approval kill switch.
 *
 * Big red button at the top of Mission Control. Click → confirm dialog
 * → POST /api/v1/auto-approval/kill-switch {active: true} → worker
 * skips every niche regardless of per-niche publishing.yaml settings.
 *
 * Design choices
 * --------------
 * - **Two visual states, never ambiguous**: red "DISABLE GLOBALLY" when
 *   live, green "AUTO-APPROVAL ENABLED" when killed. The button text
 *   and colour always reflect what clicking it WILL do.
 * - **Hard confirm dialog**: prevents an errant click during a stressful
 *   incident from flipping the state.
 * - **Shows env-var state**: if `GENLAB_AUTO_APPROVE_DISABLED` is also
 *   set via the shell, the button can't fully clear it — surface that
 *   so the operator knows to SSH and unset the env var to restore.
 * - **60s poll**: matches AutoApprovalCalibrationCard's cadence so
 *   they can share a render slot.
 */

type KillSwitchState = {
  active: boolean;
  source: "env" | "file" | "both" | "none";
  file_path: string;
  env_var_set: boolean;
};

async function fetchKillSwitch(): Promise<KillSwitchState> {
  const r = await fetch("/api/v1/auto-approval/kill-switch");
  if (!r.ok) throw new Error(`kill-switch GET ${r.status}`);
  const json = await r.json();
  return json.data as KillSwitchState;
}

async function setKillSwitch(active: boolean): Promise<KillSwitchState> {
  const r = await fetch("/api/v1/auto-approval/kill-switch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ active }),
  });
  if (!r.ok) throw new Error(`kill-switch POST ${r.status}`);
  const json = await r.json();
  return json.data as KillSwitchState;
}

export function AutoApprovalKillSwitch() {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState<null | boolean>(null);

  const { data: state, isLoading, isError } = useQuery({
    queryKey: ["auto-approval-kill-switch"],
    queryFn: fetchKillSwitch,
    refetchInterval: 60_000,
  });

  const mutation = useMutation({
    mutationFn: setKillSwitch,
    onSuccess: (next) => {
      queryClient.setQueryData(["auto-approval-kill-switch"], next);
      setConfirming(null);
    },
  });

  if (isLoading || isError || !state) {
    // Don't render a spinner — this is a header element and the
    // dashboard already has a global loading state for the page.
    // Returning null keeps the header tidy on cold load.
    return null;
  }

  const handleClick = () => {
    setConfirming(!state.active);
  };

  const handleConfirm = () => {
    if (confirming !== null) {
      mutation.mutate(confirming);
    }
  };

  const isPending = mutation.isPending;

  return (
    <>
      <button
        onClick={handleClick}
        disabled={isPending}
        className={`flex items-center gap-2 px-3 py-2 rounded-md font-semibold text-sm transition-colors ${
          state.active
            ? "bg-green-600 hover:bg-green-700 text-white"
            : "bg-red-600 hover:bg-red-700 text-white"
        } ${isPending ? "opacity-60 cursor-wait" : ""}`}
        title={
          state.active
            ? `Kill switch active (source: ${state.source}). Click to re-enable.`
            : "Auto-approval is LIVE. Click to disable globally."
        }
        data-testid="auto-approval-kill-switch"
      >
        {state.active ? (
          <>
            <ShieldCheck className="h-4 w-4" />
            ENABLE AUTO-APPROVAL
          </>
        ) : (
          <>
            <ShieldAlert className="h-4 w-4" />
            DISABLE AUTO-APPROVE GLOBALLY
          </>
        )}
      </button>

      {/* Env-var warning — surfaces when SSH-set env var blocks UI reset */}
      {state.active && state.env_var_set && (
        <span className="text-xs text-amber-400" title={state.file_path}>
          ⚠ GENLAB_AUTO_APPROVE_DISABLED env var also set — clear via shell
        </span>
      )}

      {/* Hard confirm dialog */}
      {confirming !== null && (
        <div
          className="fixed inset-0 bg-black/60 flex items-center justify-center z-50"
          onClick={() => !isPending && setConfirming(null)}
        >
          <div
            className="bg-zinc-900 border border-zinc-700 rounded-lg p-6 max-w-md mx-4 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-bold text-white mb-3">
              {confirming
                ? "Disable auto-approval globally?"
                : "Re-enable auto-approval globally?"}
            </h3>
            <p className="text-sm text-zinc-300 mb-5">
              {confirming
                ? "This stops auto-approval for ALL 5 niches immediately. The auto-approver worker will skip every blueprint on its next pass. Per-niche publishing.yaml settings are NOT changed — clearing the kill switch restores the previous per-niche state."
                : "This restores auto-approval to whatever per-niche publishing.yaml says (most are still opt-in only). The worker will resume auto-approving on its next pass for any niche where enabled=true."}
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setConfirming(null)}
                disabled={isPending}
                className="px-3 py-2 rounded bg-zinc-700 hover:bg-zinc-600 text-white text-sm"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirm}
                disabled={isPending}
                className={`px-3 py-2 rounded font-semibold text-sm text-white ${
                  confirming
                    ? "bg-red-600 hover:bg-red-700"
                    : "bg-green-600 hover:bg-green-700"
                } ${isPending ? "opacity-60 cursor-wait" : ""}`}
              >
                {isPending
                  ? "Working..."
                  : confirming
                  ? "Yes, disable globally"
                  : "Yes, re-enable"}
              </button>
            </div>
            {mutation.isError && (
              <p className="text-xs text-red-400 mt-3">
                Failed: {(mutation.error as Error).message}
              </p>
            )}
          </div>
        </div>
      )}
    </>
  );
}
