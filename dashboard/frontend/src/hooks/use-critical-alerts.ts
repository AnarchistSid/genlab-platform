import { useQuery } from "@tanstack/react-query";
import { alerts } from "@/api/client";
import { queryKeys } from "@/api/query-keys";

/**
 * Hook for the Mission Control CriticalAlertsBanner.
 *
 * Polls ``GET /api/v1/alerts/critical`` every 60s — the same cadence
 * as ``use-publishing-alerts`` so a single operator visit refreshes
 * both surfaces together.
 *
 * Why 60s and not faster: the underlying ``pipeline_alerts`` table is
 * written by ``health_monitor`` on its own cron cadence (~hourly),
 * so sub-minute polling has zero signal benefit and would just churn
 * the prod DB. 60s lets a fresh alert surface within a minute of
 * the cron firing, which is what the dashboard cares about.
 *
 * Why poll at all (instead of websocket-push): the dashboard
 * already polls everything else on a similar cadence. Adding a
 * websocket channel just for this one surface is more
 * infrastructure than it earns. If the dashboard later moves to
 * a websocket-first model, this hook is a one-line swap.
 */
export function useCriticalAlerts() {
  return useQuery({
    queryKey: queryKeys.criticalAlerts(),
    queryFn: () => alerts.critical(),
    staleTime: 60_000,
    refetchInterval: 60_000,
  });
}
