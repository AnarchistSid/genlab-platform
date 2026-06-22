import { useState, useCallback, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { blueprints } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { Blueprint } from "@/api/types";
import { useNicheStore } from "@/stores/niche-store";

type ReviewAction = "approved" | "rejected" | "revised" | "skipped";

interface ReviewStats {
  approved: number;
  rejected: number;
  revised: number;
  skipped: number;
}

export interface FocusReviewState {
  items: Blueprint[];
  currentIndex: number;
  reviewed: number;
  approved: number;
  rejected: number;
  revised: number;
  skipped: number;
  isComplete: boolean;
  total: number;
}

export function useFocusReviewQueue() {
  const { selectedNicheId } = useNicheStore();
  const nicheParam = selectedNicheId ?? "all";
  const queryClient = useQueryClient();

  const { data, isLoading, refetch } = useQuery({
    queryKey: queryKeys.review.queue(nicheParam),
    queryFn: () => blueprints.reviewQueue({ niche_id: nicheParam }),
    staleTime: 30_000,
  });

  const items = useMemo(() => data?.data ?? [], [data]);
  const isFallback = data?.meta?.fallback ?? false;

  const [currentIndex, setCurrentIndex] = useState(0);
  const [stats, setStats] = useState<ReviewStats>({
    approved: 0,
    rejected: 0,
    revised: 0,
    skipped: 0,
  });

  // Clamp currentIndex when items array shrinks (React 19 idiom:
  // derive during render rather than useEffect → setState which
  // cascades — see stories.tsx for the same pattern).
  if (items.length > 0 && currentIndex >= items.length) {
    setCurrentIndex(Math.max(0, items.length - 1));
  }

  const reviewed = stats.approved + stats.rejected + stats.revised + stats.skipped;
  const isComplete = items.length > 0 && currentIndex >= items.length;
  const currentItem: Blueprint | null = isComplete ? null : (items[currentIndex] ?? null);

  const state: FocusReviewState = {
    items,
    currentIndex,
    reviewed,
    approved: stats.approved,
    rejected: stats.rejected,
    revised: stats.revised,
    skipped: stats.skipped,
    isComplete,
    total: items.length,
  };

  const reviewMutation = useMutation({
    mutationFn: ({
      id,
      body,
    }: {
      id: string;
      // 2026-06-22 Loop 7 close: review_duration_ms is the operator's
      // dwell time (submitTime - loadTime), required so the backend can
      // record it on auto_approval_calibration. PR #423 shipped the
      // backend column + reader on 2026-06-21 but the frontend never
      // wired the field — prod probe found 0/203 calibration rows had
      // dwell time populated. Required (not optional) so the wire is
      // unmissable in PRs that touch this call site.
      body: { action: string; issue?: string; notes?: string; review_duration_ms: number };
    }) => blueprints.reviewAction(id, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.blueprints.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.crossNiche.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.schedule.all() });
    },
    onError: (error: Error) => {
      toast.error(`Review failed: ${error.message}`);
    },
  });

  const advance = useCallback(
    (action: ReviewAction) => {
      setStats((prev) => ({ ...prev, [action]: prev[action] + 1 }));
      setCurrentIndex((prev) => prev + 1);
    },
    []
  );

  const goTo = useCallback(
    (index: number) => {
      if (index >= 0 && index < items.length) {
        setCurrentIndex(index);
      }
    },
    [items.length]
  );

  const restart = useCallback(() => {
    setCurrentIndex(0);
    setStats({ approved: 0, rejected: 0, revised: 0, skipped: 0 });
    void refetch();
  }, [refetch]);

  return {
    state,
    currentItem,
    advance,
    goTo,
    restart,
    isLoading,
    isFallback,
    reviewMutation,
    nicheId: nicheParam,
  };
}
