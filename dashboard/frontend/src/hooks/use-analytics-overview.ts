import { useQuery } from "@tanstack/react-query";
import { analytics } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { AnalyticsOverviewResponse } from "@/api/types";

export function useAnalyticsOverview(nicheId: string, window: string) {
  const query = useQuery<AnalyticsOverviewResponse>({
    queryKey: queryKeys.analytics.overview(nicheId, window),
    queryFn: () =>
      analytics.overview({ niche_id: nicheId, window }),
    staleTime: 60_000,
    refetchInterval: 60_000,
  });

  return {
    data: query.data,
    isLoading: query.isLoading,
    error: query.error,
    isEstimated: query.data?.is_estimated ?? false,
  };
}
