import { useQuery } from "@tanstack/react-query";
import { stories } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { Story, PaginatedResponse, SingleResponse } from "@/api/types";

export function useStories(params?: Record<string, string>) {
  return useQuery<PaginatedResponse<Story>>({
    queryKey: queryKeys.stories.list(params),
    queryFn: () => stories.list(params),
  });
}

export function useStory(id: string | undefined) {
  return useQuery<SingleResponse<Story>>({
    queryKey: queryKeys.stories.detail(id!),
    queryFn: () => stories.get(id!),
    enabled: !!id,
  });
}
