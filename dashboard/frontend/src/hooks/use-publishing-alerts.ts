import { useQuery } from "@tanstack/react-query";
import { alerts } from "@/api/client";
import { queryKeys } from "@/api/query-keys";

export function usePublishingAlerts() {
  return useQuery({
    queryKey: queryKeys.publishingAlerts(),
    queryFn: () => alerts.publishing(),
    staleTime: 60_000,
    refetchInterval: 60_000,
  });
}
