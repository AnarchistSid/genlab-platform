import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { pipeline } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { PipelineRun, PaginatedResponse } from "@/api/types";

export function usePipelineStatus() {
  return useQuery<{ data: { last_run: PipelineRun | null; health: string } }>({
    queryKey: queryKeys.pipeline.globalStatus(),
    queryFn: () => pipeline.status(),
    refetchInterval: 15_000,
  });
}

export function usePipelineRuns(params?: Record<string, string>) {
  return useQuery<PaginatedResponse<PipelineRun>>({
    queryKey: queryKeys.pipeline.list(params),
    queryFn: () => pipeline.runs(params),
  });
}

export function usePipelineRun(id: string | undefined) {
  return useQuery<{ data: Record<string, unknown> }>({
    queryKey: queryKeys.pipeline.runDetail(id!),
    queryFn: () => pipeline.run(id!),
    enabled: !!id,
  });
}

export function useTriggerPipeline() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (params?: { niche_id?: string; mode?: string }) => pipeline.trigger(params),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.pipeline.all() });
      toast.success(`Pipeline triggered (run: ${data.run_id})`);
    },
    onError: (error: Error) => {
      toast.error(`Pipeline trigger failed: ${error.message}`);
    },
  });
}
