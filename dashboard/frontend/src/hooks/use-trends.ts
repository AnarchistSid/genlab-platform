import { useQuery } from "@tanstack/react-query";
import { trendsApi } from "@/api/client";
import { queryKeys } from "@/api/query-keys";

export function useTrends() {
  return useQuery({
    queryKey: queryKeys.trends.current(),
    queryFn: trendsApi.current,
    staleTime: 6 * 60 * 60_000,
  });
}
