import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { blueprints } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { Blueprint, PaginatedResponse, SingleResponse } from "@/api/types";

export function useBlueprints(params?: Record<string, string>) {
  return useQuery<PaginatedResponse<Blueprint>>({
    queryKey: queryKeys.blueprints.list(params),
    queryFn: () => blueprints.list(params),
    // Socket.IO `blueprint_updated` events drive real-time freshness via
    // invalidation in use-socket.ts. The 60s poll is the reconciliation
    // safety net for the "server died silently / event dropped" case —
    // not the primary update path. Previously 15s, which was 4× the
    // necessary load with sockets wired.
    refetchInterval: 60_000,
  });
}

export function useBlueprint(id: string | undefined) {
  return useQuery<SingleResponse<Blueprint>>({
    queryKey: queryKeys.blueprints.detail(id!),
    queryFn: () => blueprints.get(id!),
    enabled: !!id,
  });
}

export function useReviewBlueprint() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      id,
      body,
    }: {
      id: string;
      body: { action: string; issue?: string; notes?: string };
    }) => blueprints.review(id, body),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.blueprints.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.schedule.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.queue.all() });
      toast.success(`Blueprint ${variables.body.action}`, {
        action: {
          label: "Undo",
          onClick: () => {
            toast.info("Undo is not yet supported");
          },
        },
      });
    },
    onError: (error: Error) => {
      toast.error(`Review failed: ${error.message}`);
    },
  });
}

export function useBatchReview() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (body: { ids: string[]; action: string }) =>
      blueprints.batchReview(body),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.blueprints.all() });
      toast.success(
        `Batch review complete: ${data.data.length} blueprint(s) updated`
      );
    },
    onError: (error: Error) => {
      toast.error(`Batch review failed: ${error.message}`);
    },
  });
}

export function useRescheduleBlueprint() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      id,
      scheduledFor,
    }: {
      id: string;
      scheduledFor: string;
    }) => blueprints.reschedule(id, scheduledFor),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.blueprints.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.schedule.all() });
      toast.success("Blueprint rescheduled");
    },
    onError: (error: Error) => {
      toast.error(`Reschedule failed: ${error.message}`);
    },
  });
}

export function useUpdateContent() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      id,
      body,
    }: {
      id: string;
      body: Partial<Pick<Blueprint, "hook_text" | "caption" | "hashtags">>;
    }) => blueprints.updateContent(id, body),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({
        queryKey: queryKeys.blueprints.detail(variables.id),
      });
      toast.success("Content updated");
    },
    onError: (error: Error) => {
      toast.error(`Content update failed: ${error.message}`);
    },
  });
}

export function useApproveAndSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => blueprints.approveAndSchedule(id),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.blueprints.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.schedule.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.queue.all() });
      const dateStr = data?.scheduled_for
        ? new Date(data.scheduled_for).toLocaleDateString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
        : "";
      toast.success(`Approved & scheduled${dateStr ? ` for ${dateStr}` : ""}`);
    },
    onError: (error: Error) => toast.error(`Approve & schedule failed: ${error.message}`),
  });
}

export function useBatchApproveSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (ids: string[]) => blueprints.batchApproveSchedule({ ids }),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.blueprints.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.schedule.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.queue.all() });
      const count = Array.isArray(data?.data) ? data.data.length : (data as unknown as Array<unknown>)?.length ?? 0;
      toast.success(`${count} post(s) approved & scheduled`);
    },
    onError: (error: Error) => toast.error(`Batch approve & schedule failed: ${error.message}`),
  });
}
