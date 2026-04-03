import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { schedule } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { ScheduleDay } from "@/api/types";

export function useSchedule(from: string | undefined, to: string | undefined) {
  return useQuery<{ data: ScheduleDay[] }>({
    queryKey: queryKeys.schedule.range(from!, to!),
    queryFn: () => schedule.get(from!, to!),
    enabled: !!from && !!to,
  });
}

export function useScheduleCoverage(
  from: string | undefined,
  to: string | undefined
) {
  return useQuery<{ data: Array<{ date: string; coverage: number }> }>({
    queryKey: queryKeys.schedule.coverage(from!, to!),
    queryFn: () => schedule.coverage(from!, to!),
    enabled: !!from && !!to,
  });
}

export function useReorderSchedule() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (body: {
      blueprint_id: string;
      to_slot: string;
      to_date?: string;
    }) => schedule.reorder(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.schedule.all() });
      toast.success("Schedule reordered");
    },
    onError: (error: Error) => {
      toast.error(`Reorder failed: ${error.message}`);
    },
  });
}
