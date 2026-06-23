import { useQuery } from "@tanstack/react-query";
import { sponsorship } from "@/api/client";
import { queryKeys } from "@/api/query-keys";

/**
 * 60-second poll on the /api/v1/sponsorship/readiness endpoint.
 *
 * Same cadence as ``useQuery``-based ``AutoApprovalCalibrationCard``.
 * The underlying ``monetisationprogress`` rows only update daily (via
 * the audience-collector launchd job), so 60s polling is purely for
 * "operator opens dashboard, sees fresh data without a manual refresh".
 *
 * The server endpoint also caches for 60s — same TTL — so concurrent
 * dashboard tabs collapse into a single DB round-trip per minute.
 */
export function useSponsorshipReadiness() {
  return useQuery({
    queryKey: queryKeys.sponsorship.readiness(),
    queryFn: () => sponsorship.readiness(),
    staleTime: 60_000,
    refetchInterval: 60_000,
    retry: false,
  });
}
