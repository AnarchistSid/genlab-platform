import { useQuery } from "@tanstack/react-query";
import { engagementApi } from "@/api/client";
import { queryKeys } from "@/api/query-keys";

export function useRecentComments() {
  return useQuery({
    queryKey: queryKeys.engagement.recent(),
    queryFn: engagementApi.recent,
    staleTime: 5 * 60_000,
  });
}

export function useEngagementStatus() {
  return useQuery({
    queryKey: queryKeys.engagement.status(),
    queryFn: engagementApi.status,
    staleTime: 60_000,
  });
}
