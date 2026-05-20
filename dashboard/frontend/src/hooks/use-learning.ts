import { useQuery } from "@tanstack/react-query";
import { learning } from "@/api/client";
import { queryKeys } from "@/api/query-keys";

export function useLearningStatus() {
  return useQuery({
    queryKey: queryKeys.learning.status(),
    queryFn: learning.status,
    staleTime: 60_000,
  });
}

export function useHookClassifierStatus() {
  return useQuery({
    queryKey: queryKeys.learning.hookClassifierStatus(),
    queryFn: learning.hookClassifierStatus,
    // Trained-status metadata is written by the weekly trainer cron;
    // there's no signal that changes faster than that, so 5 min cache.
    staleTime: 5 * 60_000,
  });
}

export function useConfigUpdates() {
  return useQuery({
    queryKey: queryKeys.learning.configUpdates(),
    queryFn: learning.configUpdates,
    staleTime: 60_000,
  });
}
