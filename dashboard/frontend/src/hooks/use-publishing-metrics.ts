import { useQuery } from "@tanstack/react-query";
import { metrics } from "@/api/client";
import { queryKeys } from "@/api/query-keys";

export function usePublishingMetrics() {
  return useQuery({
    queryKey: queryKeys.publishingMetrics(),
    queryFn: () => metrics.publishing(),
    staleTime: 60_000,
    refetchInterval: 60_000,
  });
}
