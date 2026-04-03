import { useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { pipeline } from "@/api/client";
import { getSocket } from "@/api/socket";
import { useNicheStore } from "@/stores/niche-store";
import { queryKeys } from "@/api/query-keys";

export interface PipelineNicheStatus {
  id: string;
  display_name: string;
  accent_hex: string;
  pipeline_status: "running" | "idle" | "error";
  current_stage: string | null;
  stage_progress: {
    stage: string;
    stage_index: number;
    total_stages: number;
    items_processed: number;
    items_total: number;
    elapsed_seconds: number;
  } | null;
  last_run: PipelineRunDetail | null;
  run_history: PipelineRunDetail[];
}

export interface PipelineRunStage {
  name: string;
  label: string;
  status: "completed" | "error" | "skipped" | string;
  duration_seconds: number;
}

export interface PipelineRunDetail {
  run_id: string;
  run_type: string;
  date: string;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number;
  steps_completed: number;
  total_steps: number;
  errors: number;
  cost_estimate: number;
  status: string;
  stages: PipelineRunStage[];
}

interface PipelineStatusResponse {
  niches: PipelineNicheStatus[];
  prefect_connected: boolean;
  data: {
    last_run: PipelineRunDetail | null;
    health: string;
  };
}

export function usePipelineMonitor() {
  const queryClient = useQueryClient();
  const selectedNicheId = useNicheStore((s) => s.selectedNicheId);
  const { data, isLoading, error } = useQuery<PipelineStatusResponse>({
    queryKey: queryKeys.pipeline.status(selectedNicheId),
    queryFn: () => pipeline.status() as Promise<PipelineStatusResponse>,
    // Socket events handle live updates; 30s polling is a safety fallback
    staleTime: 30_000,
    refetchInterval: 30_000,
  });

  // WebSocket-driven invalidation for instant updates
  useEffect(() => {
    const socket = getSocket();
    const invalidate = () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.pipeline.all() });
    };
    socket.on("pipeline_progress", invalidate);
    socket.on("pipeline_complete", invalidate);
    socket.on("pipeline_started", invalidate);
    socket.on("pipeline_state_update", invalidate);
    return () => {
      socket.off("pipeline_progress", invalidate);
      socket.off("pipeline_complete", invalidate);
      socket.off("pipeline_started", invalidate);
      socket.off("pipeline_state_update", invalidate);
    };
  }, [queryClient, selectedNicheId]);

  return {
    niches: data?.niches ?? [],
    prefectConnected: data?.prefect_connected ?? false,
    health: data?.data?.health ?? "unknown",
    isLoading,
    error,
  };
}

export function usePipelineTrigger() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (params?: { niche_id?: string; mode?: string }) =>
      pipeline.trigger(params),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.pipeline.all() });
      toast.success(`Pipeline triggered (${data.run_id})`);
    },
    onError: (error: Error) => {
      toast.error(`Pipeline trigger failed: ${error.message}`);
    },
  });
}

export function usePipelineRuns(limit = 20) {
  const selectedNicheId = useNicheStore((s) => s.selectedNicheId);
  return useQuery({
    queryKey: queryKeys.pipeline.runs(selectedNicheId, limit),
    queryFn: () => pipeline.runs({ per_page: String(limit) }),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });
}
