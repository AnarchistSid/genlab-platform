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
