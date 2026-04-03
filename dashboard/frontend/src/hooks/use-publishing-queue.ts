import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { queue } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import { useNicheStore } from "@/stores/niche-store";
import type { QueueItem, QueueStats } from "@/api/types";

export function usePublishingQueue(queueStatus?: string) {
  const nicheId = useNicheStore((s) => s.selectedNicheId);

  return useQuery<{ data: QueueItem[]; meta: { total: number; niche_id: string } }>({
    queryKey: queryKeys.queue.list(nicheId, queueStatus),
    queryFn: () => {
      const params: Record<string, string> = { niche_id: nicheId };
      if (queueStatus) params.queue_status = queueStatus;
      return queue.list(params);
    },
    refetchInterval: 15_000,
  });
}

export function useQueueStats() {
  const nicheId = useNicheStore((s) => s.selectedNicheId);

  return useQuery<{ data: QueueStats }>({
    queryKey: queryKeys.queue.stats(nicheId),
    queryFn: () => queue.stats({ niche_id: nicheId }),
    refetchInterval: 30_000,
  });
}

export function useApproveItem() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, notes }: { id: string; notes?: string }) =>
      queue.approve(id, { notes }),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.queue.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.blueprints.all() });
      toast.success(`Blueprint ${variables.id} approved for publishing`);
    },
    onError: (error: Error) => {
      toast.error(`Approve failed: ${error.message}`);
    },
  });
}

export function useHoldItem() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) =>
      queue.hold(id, { reason }),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.queue.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.blueprints.all() });
      toast.success(`Blueprint ${variables.id} held`);
    },
    onError: (error: Error) => {
      toast.error(`Hold failed: ${error.message}`);
    },
  });
}

export function useReleaseItem() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) => queue.release(id),
    onSuccess: (_data, id) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.queue.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.blueprints.all() });
      toast.success(`Blueprint ${id} released back to queue`);
    },
    onError: (error: Error) => {
      toast.error(`Release failed: ${error.message}`);
    },
  });
}

export function useUnscheduleItem() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) => queue.unschedule(id),
    onSuccess: (_data, id) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.queue.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.blueprints.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.schedule.all() });
      toast.success(`Blueprint ${id} removed from schedule`);
    },
    onError: (error: Error) => {
      toast.error(`Unschedule failed: ${error.message}`);
    },
  });
}

export function useArchiveItem() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) => queue.archive(id),
    onSuccess: (_data, id) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.queue.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.blueprints.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.schedule.all() });
      toast.success(`Blueprint ${id} archived`);
    },
    onError: (error: Error) => {
      toast.error(`Archive failed: ${error.message}`);
    },
  });
}
