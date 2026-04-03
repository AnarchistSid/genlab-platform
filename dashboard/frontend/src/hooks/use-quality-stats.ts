import { useQuery } from "@tanstack/react-query";
import { pipeline } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { QualityStats } from "@/api/types";

export function useQualityStats() {
  return useQuery<QualityStats>({
    queryKey: queryKeys.pipeline.qualityStats(),
    queryFn: () => pipeline.qualityStats(),
    staleTime: 300_000,
  });
}
