import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { getSocket } from "@/api/socket";
import type { SocketEvents } from "@/api/socket";
import { useNotificationStore } from "@/stores/notification-store";

/**
 * Hook that subscribes to Socket.IO events and creates notifications.
 * Call once near the top of the component tree (e.g. Shell).
 */
export function useNotifications() {
  const { notifications, unreadCount, markRead, markAllRead, clearAll } =
    useNotificationStore();
  const addNotification = useNotificationStore((s) => s.addNotification);

  useEffect(() => {
    const socket = getSocket();

    function onPipelineComplete(
      event: SocketEvents["pipeline_complete"],
    ) {
      addNotification({
        type: "pipeline_complete",
        title: "Pipeline Complete",
        body: `Run ${event.run_id} finished in ${Math.round(event.duration)}s`,
        entity_id: event.run_id,
        entity_type: "pipeline_run",
      });
    }

    function onBlueprintUpdated(
      event: SocketEvents["blueprint_updated"],
    ) {
      addNotification({
        type: "info",
        title: "Blueprint Updated",
        body: `Blueprint ${event.id} status changed to ${event.status}`,
        entity_id: event.id,
        entity_type: "blueprint",
      });
    }

    function onExpressProgress(
      event: SocketEvents["express_progress"],
    ) {
      const step = event.step ?? "";
      if (step.toLowerCase().includes("error")) {
        addNotification({
          type: "pipeline_error",
          title: "Pipeline Error",
          body: `Error in step: ${step}${event.run_id ? ` (run ${event.run_id})` : ""}`,
          entity_id: event.run_id,
          entity_type: "pipeline_run",
        });
      }
    }

    // Use underscore event names to match server-side socketio.emit()
    socket.on("pipeline_complete", onPipelineComplete);
    socket.on("blueprint_updated", onBlueprintUpdated);
    socket.on("express_progress", onExpressProgress);

    return () => {
      socket.off("pipeline_complete", onPipelineComplete);
      socket.off("blueprint_updated", onBlueprintUpdated);
      socket.off("express_progress", onExpressProgress);
    };
  }, [addNotification]);

  // Also poll /api/v1/events/recent for pipeline and publisher events
  const seenIds = useRef(new Set<string>());
  const eventsQuery = useQuery<Array<{ id: string; type: string; title: string; body: string; entity_id: string; entity_type: string; created_at: string }>>({
    queryKey: ["dashboard-events"],
    queryFn: async () => {
      const resp = await fetch("/api/v1/events/recent?limit=20");
      if (!resp.ok) return [];
      const data = await resp.json();
      return data?.data ?? (Array.isArray(data) ? data : []);
    },
    staleTime: 30_000,
    refetchInterval: 30_000,
  });

  useEffect(() => {
    const events = eventsQuery.data;
    if (!events?.length) return;
    for (const ev of events) {
      if (seenIds.current.has(ev.id)) continue;
      seenIds.current.add(ev.id);
      // Only add events from the last hour
      const age = Date.now() - new Date(ev.created_at).getTime();
      if (age > 3600_000) continue;
      addNotification({
        type: ev.type as any,
        title: ev.title,
        body: ev.body,
        entity_id: ev.entity_id,
        entity_type: ev.entity_type,
      });
    }
  }, [eventsQuery.data, addNotification]);

  return { notifications, unreadCount, markRead, markAllRead, clearAll };
}
