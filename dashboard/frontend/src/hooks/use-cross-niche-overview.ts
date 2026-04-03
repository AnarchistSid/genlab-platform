import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { crossNiche } from "@/api/client";
import type { CrossNicheOverviewResponse } from "@/api/types";
import { queryKeys } from "@/api/query-keys";
import { getSocket } from "@/api/socket";

export type { CrossNicheOverviewResponse };

export function useCrossNicheOverview() {
  const queryClient = useQueryClient();

  const query = useQuery<CrossNicheOverviewResponse>({
    queryKey: queryKeys.crossNiche.overview(),
    queryFn: () => crossNiche.overview(),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });

  // Invalidate on any pipeline event for instant status updates
  useEffect(() => {
    const socket = getSocket();

    function onPipelineEvent() {
      void queryClient.invalidateQueries({ queryKey: queryKeys.crossNiche.all() });
    }

    // Use underscore event names to match server-side socketio.emit()
    socket.on("pipeline_progress", onPipelineEvent);
    socket.on("pipeline_complete", onPipelineEvent);
    socket.on("pipeline_state_update", onPipelineEvent);
    socket.on("blueprints_updated", onPipelineEvent);

    return () => {
      socket.off("pipeline_progress", onPipelineEvent);
      socket.off("pipeline_complete", onPipelineEvent);
      socket.off("pipeline_state_update", onPipelineEvent);
      socket.off("blueprints_updated", onPipelineEvent);
    };
  }, [queryClient]);

  return query;
}
