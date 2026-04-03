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
