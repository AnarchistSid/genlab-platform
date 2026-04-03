import { useQuery } from "@tanstack/react-query";
import { channelHealth } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { ChannelHealth } from "@/api/types";

export function useChannelHealth() {
  return useQuery<{ data: ChannelHealth }>({
    queryKey: queryKeys.channelHealth(),
    queryFn: () => channelHealth.get(),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
}
