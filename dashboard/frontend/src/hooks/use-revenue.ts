import { useQuery } from "@tanstack/react-query";
import { revenue } from "@/api/client";
import { queryKeys } from "@/api/query-keys";

export function useRevenueSummary() {
  return useQuery({
    queryKey: queryKeys.revenue.summary(),
    queryFn: () => revenue.summary(),
    staleTime: 300_000,
  });
}
