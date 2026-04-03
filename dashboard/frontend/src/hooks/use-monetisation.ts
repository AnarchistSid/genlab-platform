import { useQuery } from "@tanstack/react-query";
import { monetisation } from "@/api/client";
import { queryKeys } from "@/api/query-keys";

export function useMonetisationProgress() {
  return useQuery({
    queryKey: queryKeys.monetisation.progress(),
    queryFn: () => monetisation.progress(),
    staleTime: 300_000, // 5 min — data only updates daily
  });
}
