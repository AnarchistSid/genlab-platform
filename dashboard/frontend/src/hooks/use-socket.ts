import { useEffect, useRef, useState, useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getSocket } from "@/api/socket";
import type { SocketEvents } from "@/api/socket";
import { queryKeys } from "@/api/query-keys";
import type {
  BlueprintUpdatedEvent,
  PipelineProgressEvent,
  PipelineRun,
} from "@/api/types";
import { useNotificationStore } from "@/stores/notification-store";
import { useNicheStore } from "@/stores/niche-store";

// ── Connection status (shared across hooks) ────────────────
export type ConnectionStatus = "connected" | "reconnecting" | "disconnected";

let globalStatus: ConnectionStatus = "connected";
const statusListeners = new Set<() => void>();

function setGlobalStatus(s: ConnectionStatus) {
  if (globalStatus === s) return;
  globalStatus = s;
  statusListeners.forEach((fn) => fn());
}

/**
 * Returns the current Socket.IO connection status.
 * Updates reactively on connect/disconnect/reconnect.
 */
export function useConnectionStatus(): {
  status: ConnectionStatus;
  reconnectAttempt: number;
} {
  const [status, setStatus] = useState<ConnectionStatus>(globalStatus);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const socket = getSocket();

    const onConnect = () => { setGlobalStatus("connected"); setStatus("connected"); setAttempt(0); };
    const onDisconnect = () => { setGlobalStatus("disconnected"); setStatus("disconnected"); };
    const onReconnecting = (a: number) => { setGlobalStatus("reconnecting"); setStatus("reconnecting"); setAttempt(a); };
    const onReconnectFailed = () => { setGlobalStatus("disconnected"); setStatus("disconnected"); };

    socket.on("connect", onConnect);
    socket.on("disconnect", onDisconnect);
    socket.io.on("reconnect_attempt", onReconnecting);
    socket.io.on("reconnect_failed", onReconnectFailed);

    // Sync initial state from the external socket system. This is the
    // canonical "subscribe to an external store" useEffect pattern —
    // setState here is a one-shot sync at subscribe time, not a render-
    // cascading update. ESLint's set-state-in-effect rule is overly
    // conservative for this case.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (socket.connected) setStatus("connected");

    return () => {
      socket.off("connect", onConnect);
      socket.off("disconnect", onDisconnect);
      socket.io.off("reconnect_attempt", onReconnecting);
      socket.io.off("reconnect_failed", onReconnectFailed);
    };
  }, []);

  return { status, reconnectAttempt: attempt };
}

// ── Main socket subscription hook ──────────────────────────
export function useSocketUpdates() {
  const queryClient = useQueryClient();
  const addNotification = useNotificationStore((s) => s.addNotification);
  const notifiedRef = useRef(false);
  const selectedNicheId = useNicheStore((s) => s.selectedNicheId);

  // Subscribe to niche-specific room when niche changes
  useEffect(() => {
    const socket = getSocket();
    const nicheId = selectedNicheId === "all" ? null : selectedNicheId;

    if (nicheId) {
      socket.emit("subscribe_niche", { niche_id: nicheId });
    }

    return () => {
      if (nicheId) {
        socket.emit("unsubscribe_niche", { niche_id: nicheId });
      }
    };
  }, [selectedNicheId]);

  const invalidateAll = useCallback(() => {
    void queryClient.invalidateQueries();
  }, [queryClient]);

  useEffect(() => {
    const socket = getSocket();

    // Event names use underscores to match server-side emit names
    function onBlueprintUpdated(_event: BlueprintUpdatedEvent) {
      void queryClient.invalidateQueries({ queryKey: queryKeys.blueprints.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.schedule.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.queue.all() });
    }

    function onPipelineProgress(event: PipelineProgressEvent) {
      queryClient.setQueryData<{
        data: { last_run: PipelineRun | null; health: string };
      }>(queryKeys.pipeline.globalStatus(), (old) => {
        if (!old) return old;
        return {
          ...old,
          data: {
            ...old.data,
            last_run: old.data.last_run
              ? {
                  ...old.data.last_run,
                  steps_completed: event.step_index,
                  total_steps: event.total_steps,
                }
              : {
                  run_id: event.run_id,
                  date: new Date().toISOString(),
                  duration_seconds: 0,
                  steps_completed: event.step_index,
                  total_steps: event.total_steps,
                  errors: 0,
                  cost_estimate: 0,
                },
          },
        };
      });
    }

    function onPipelineComplete(
      _event: SocketEvents["pipeline_complete"]
    ) {
      void queryClient.invalidateQueries({ queryKey: queryKeys.pipeline.all() });
    }

    function onBlueprintsUpdated(
      _event: SocketEvents["blueprints_updated"]
    ) {
      void queryClient.invalidateQueries({ queryKey: queryKeys.blueprints.all() });
    }

    function onReconnectFailed() {
      if (!notifiedRef.current) {
        notifiedRef.current = true;
        addNotification({
          type: "info",
          title: "Connection lost",
          body: "Real-time updates are unavailable. Refresh the page to reconnect.",
        });
      }
    }

    // On reconnect, invalidate ALL active queries to recover consistency
    function onReconnect() {
      invalidateAll();

      if (notifiedRef.current) {
        notifiedRef.current = false;
        addNotification({
          type: "info",
          title: "Reconnected",
          body: "Real-time updates have been restored.",
        });
      }
    }

    socket.on("blueprint_updated", onBlueprintUpdated);
    socket.on("pipeline_progress", onPipelineProgress);
    socket.on("pipeline_complete", onPipelineComplete);
    socket.on("blueprints_updated", onBlueprintsUpdated);
    socket.io.on("reconnect_failed", onReconnectFailed);
    socket.io.on("reconnect", onReconnect);

    // NOTE: Do NOT invalidate on initial "connect" — the query mounts already
    // fetch fresh data. Invalidating here caused a thundering-herd double-fetch.
    // The "reconnect" handler above covers the stale-data-after-disconnect case.

    return () => {
      socket.off("blueprint_updated", onBlueprintUpdated);
      socket.off("pipeline_progress", onPipelineProgress);
      socket.off("pipeline_complete", onPipelineComplete);
      socket.off("blueprints_updated", onBlueprintsUpdated);
      socket.io.off("reconnect_failed", onReconnectFailed);
      socket.io.off("reconnect", onReconnect);
    };
  }, [queryClient, addNotification, invalidateAll]);
}
