import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  FileText,
  AlertTriangle,
  Check,
  X,
  Loader2,
  ArrowUpDown,
  Columns2,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { useQueryStates, parseAsString } from "nuqs";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { FilterBar } from "@/components/shared/filter-bar";
import { EmptyState } from "@/components/shared/empty-state";
import { BlueprintCard } from "@/components/blueprints/blueprint-card";
import { BlueprintDetail } from "@/components/blueprints/blueprint-detail";
import { ComparisonView } from "@/components/blueprints/comparison-view";
import { useBlueprints, useBatchReview } from "@/hooks/use-blueprints";
import { useSelectionStore } from "@/stores/selection-store";
import { useNicheStore } from "@/stores/niche-store";
import { cn } from "@/lib/utils";
import type { Blueprint } from "@/api/types";

const STATUS_FILTERS = [
  { value: "INTEL_READY", label: "Intel Ready" },
  { value: "DRAFTED", label: "Drafted" },
  { value: "VISUAL_READY", label: "Visual Ready" },
  { value: "SCHEDULED", label: "Scheduled" },
  { value: "NEEDS_REVIEW", label: "Needs Review" },
  { value: "PUBLISHED", label: "Published" },
  { value: "ERROR", label: "Error" },
  { value: "ARCHIVED", label: "Archived" },
];

const FILTER_DEFINITIONS = [
  {
    key: "status",
    label: "Status",
    options: STATUS_FILTERS,
  },
];

const SORT_OPTIONS = [
  { value: "newest", label: "Newest first" },
  { value: "priority", label: "Priority" },
  { value: "cost", label: "Highest cost" },
  { value: "scheduled", label: "Scheduled date" },
];

function CardSkeletonGrid() {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="rounded-lg border border-bg-hover bg-bg-surface">
          <Skeleton className="aspect-[4/3] w-full rounded-t-lg" />
          <div className="space-y-2 p-3">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-2/3" />
            <div className="flex gap-2">
              <Skeleton className="h-5 w-16 rounded-full" />
              <Skeleton className="h-5 w-20" />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

export default function BlueprintsView() {
  const [sortParams, setSortParams] = useQueryStates({
    sort: parseAsString.withDefault("newest"),
  });

  const [page, setPage] = useState(1);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [compareOpen, setCompareOpen] = useState(false);

  const [focusedIndex, setFocusedIndex] = useState(-1);
  const gridRef = useRef<HTMLDivElement>(null);

  const selectedIds = useSelectionStore((s) => s.selectedIds);
  const toggle = useSelectionStore((s) => s.toggle);
  const selectRange = useSelectionStore((s) => s.selectRange);
  const lastSelectedIndex = useSelectionStore((s) => s.lastSelectedIndex);
  const clearAll = useSelectionStore((s) => s.clearAll);

  const batchReview = useBatchReview();
  const { selectedNicheId } = useNicheStore();

  // Build API params from filter bar + sort
  // FilterBar manages its own nuqs state via useFilterParams internally,
  // but we also need to read those params for the API call.
  const [filterParams] = useQueryStates({
    status: parseAsString.withDefault(""),
    q: parseAsString.withDefault(""),
  });

  const apiParams = useMemo(() => {
    const params: Record<string, string> = {};
    if (filterParams.status) params.status = filterParams.status;
    if (filterParams.q) params.q = filterParams.q;
    if (sortParams.sort) params.sort = sortParams.sort;
    params.page = String(page);
    params.per_page = "25";
    params.niche_id = selectedNicheId ?? "all";
    return params;
  }, [filterParams.status, filterParams.q, sortParams.sort, page, selectedNicheId]);

  // Reset to page 1 when filters or sort change (React 19 idiom —
  // see stories.tsx for the same pattern + rationale).
  const filtersKey = `${filterParams.status ?? ""}|${filterParams.q ?? ""}|${sortParams.sort ?? ""}`;
  const [prevFiltersKey, setPrevFiltersKey] = useState(filtersKey);
  if (prevFiltersKey !== filtersKey) {
    setPrevFiltersKey(filtersKey);
    setPage(1);
  }

  const { data, isLoading, error, refetch } = useBlueprints(apiParams);

  // Memoize the blueprints array so callers depending on it via
  // useCallback/useEffect don't re-fire every render
  // (react-hooks/exhaustive-deps would otherwise flag this site).
  const blueprints: Blueprint[] = useMemo(
    () => (data?.data ?? []) as Blueprint[],
    [data?.data],
  );
  const meta = data?.meta ?? { page: 1, per_page: 25, total: 0, total_pages: 1 };
  const selectionCount = selectedIds.size;

  const handleBatchApprove = useCallback(() => {
    batchReview.mutate(
      { ids: Array.from(selectedIds), action: "approved" },
      { onSuccess: () => clearAll() }
    );
  }, [batchReview, selectedIds, clearAll]);

  const handleBatchReject = useCallback(() => {
    batchReview.mutate(
      { ids: Array.from(selectedIds), action: "rejected" },
      { onSuccess: () => clearAll() }
    );
  }, [batchReview, selectedIds, clearAll]);

  const handleCompare = useCallback(() => {
    setCompareOpen(true);
  }, []);

  const handleCloseCompare = useCallback(() => {
    setCompareOpen(false);
  }, []);

  const compareIds = useMemo((): [string, string] | null => {
    if (selectedIds.size !== 2) return null;
    const ids = Array.from(selectedIds);
    return [ids[0], ids[1]];
  }, [selectedIds]);

  const handleCardClick = useCallback((id: string, index: number) => {
    setDetailId(id);
    setFocusedIndex(index);
  }, []);

  const handleCardSelect = useCallback(
    (id: string, index: number, shiftKey: boolean) => {
      if (shiftKey && lastSelectedIndex !== null) {
        const from = Math.min(lastSelectedIndex, index);
        const to = Math.max(lastSelectedIndex, index);
        const rangeIds = blueprints.slice(from, to + 1).map((b) => b.id);
        selectRange(rangeIds);
      } else {
        toggle(id, index);
      }
    },
    [lastSelectedIndex, blueprints, selectRange, toggle]
  );

  const handleCloseDetail = useCallback(() => {
    setDetailId(null);
  }, []);

  const handleSortChange = useCallback(
    (value: string) => {
      void setSortParams({ sort: value });
    },
    [setSortParams]
  );

  // Clear detail panel when the selected blueprint disappears from the
  // list. Derived during render (React 19 idiom) — checking each frame
  // is cheap because ``blueprints.some`` short-circuits and the page
  // size is bounded at 25.
  if (
    detailId &&
    blueprints.length > 0 &&
    !blueprints.some((b) => b.id === detailId)
  ) {
    setDetailId(null);
  }

  // Keyboard: Escape closes detail, J/K navigates list, Enter opens detail
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      const el = document.activeElement;
      const tag = el?.tagName.toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      if ((el as HTMLElement)?.isContentEditable) return;

      if (e.key === "Escape" && detailId) {
        setDetailId(null);
        return;
      }

      if (blueprints.length === 0) return;

      if (e.key === "j" && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        setFocusedIndex((prev) => {
          const next = Math.min(prev + 1, blueprints.length - 1);
          scrollCardIntoView(next);
          return next;
        });
        return;
      }

      if (e.key === "k" && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        setFocusedIndex((prev) => {
          const next = Math.max(prev - 1, 0);
          scrollCardIntoView(next);
          return next;
        });
        return;
      }

      if (e.key === "Enter" && focusedIndex >= 0 && focusedIndex < blueprints.length) {
        e.preventDefault();
        setDetailId(blueprints[focusedIndex].id);
        return;
      }

      if (e.key === "x" && focusedIndex >= 0 && focusedIndex < blueprints.length) {
        e.preventDefault();
        toggle(blueprints[focusedIndex].id, focusedIndex);
        return;
      }
    }

    function scrollCardIntoView(index: number) {
      const grid = gridRef.current;
      if (!grid) return;
      const cards = grid.querySelectorAll("[data-blueprint-card]");
      cards[index]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [detailId, blueprints, focusedIndex, toggle]);

  const isDetailOpen = detailId !== null;

  return (
    <div className="space-y-4">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Content Board</h1>
        <div className="flex items-center gap-2">
          <Select
            value={sortParams.sort}
            onValueChange={handleSortChange}
          >
            <SelectTrigger size="sm" className="w-[160px]">
              <ArrowUpDown className="size-3.5 text-text-secondary" />
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SORT_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {/* Filter bar */}
      <FilterBar
        filters={FILTER_DEFINITIONS}
        search
        searchPlaceholder="Search blueprints..."
      />

      {/* Floating bulk action bar — fixed to bottom of viewport */}
      <AnimatePresence>
        {selectionCount > 0 && (
          <motion.div
            initial={{ y: 80, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: 80, opacity: 0 }}
            transition={{ type: "spring", damping: 25, stiffness: 300 }}
            className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2"
          >
            <div className="flex items-center gap-3 rounded-xl border border-indigo-500/30 bg-bg-surface/95 backdrop-blur-lg px-5 py-3 shadow-2xl shadow-black/40">
              <span className="text-sm font-medium text-indigo-300">
                {selectionCount} selected
              </span>
              <div className="w-px h-5 bg-bg-hover" />
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="xs"
                  className="border-green-600/30 text-green-400 hover:bg-green-600/20"
                  onClick={handleBatchApprove}
                  disabled={batchReview.isPending}
                >
                  {batchReview.isPending ? (
                    <Loader2 className="size-3 animate-spin" />
                  ) : (
                    <Check className="size-3" />
                  )}
                  Approve
                </Button>
                <Button
                  variant="outline"
                  size="xs"
                  className="border-red-600/30 text-red-400 hover:bg-red-600/20"
                  onClick={handleBatchReject}
                  disabled={batchReview.isPending}
                >
                  {batchReview.isPending ? (
                    <Loader2 className="size-3 animate-spin" />
                  ) : (
                    <X className="size-3" />
                  )}
                  Reject
                </Button>
                {selectionCount === 2 && (
                  <Button
                    variant="outline"
                    size="xs"
                    className="border-indigo-500/30 text-indigo-300 hover:bg-indigo-500/20"
                    onClick={handleCompare}
                  >
                    <Columns2 className="size-3" />
                    Compare
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="xs"
                  className="text-text-secondary"
                  onClick={clearAll}
                >
                  Clear
                </Button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Main content area: grid + detail panel */}
      <div className="relative">
        {/* Loading state */}
        {isLoading && <CardSkeletonGrid />}

        {/* Error state */}
        {error && !isLoading && (
          <EmptyState
            icon={AlertTriangle}
            title="Failed to load blueprints"
            description={error.message}
            action={{
              label: "Retry",
              onClick: () => void refetch(),
            }}
          />
        )}

        {/* Empty state */}
        {!isLoading && !error && blueprints.length === 0 && (
          <EmptyState
            icon={FileText}
            title="No blueprints match your filters"
            description="Try adjusting your status filter or search query."
          />
        )}

        {/* Card grid */}
        {!isLoading && !error && blueprints.length > 0 && (
          <div className="flex items-start gap-0">
            <div
              ref={gridRef}
              className={cn(
                "grid gap-4 transition-all duration-300 flex-1",
                isDetailOpen
                  ? "grid-cols-1 md:grid-cols-2"
                  : "grid-cols-1 md:grid-cols-2 xl:grid-cols-3"
              )}
            >
              {blueprints.map((bp, i) => (
                <div
                  key={bp.id}
                  data-blueprint-card
                  className={cn(
                    "rounded-lg transition-shadow",
                    focusedIndex === i && "ring-2 ring-indigo-500/50 rounded-lg"
                  )}
                >
                  <BlueprintCard
                    blueprint={bp}
                    selected={selectedIds.has(bp.id)}
                    onSelect={(e) => handleCardSelect(bp.id, i, e?.shiftKey ?? false)}
                    onClick={() => handleCardClick(bp.id, i)}
                  />
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Detail panel */}
        <AnimatePresence>
          {detailId && (
            <BlueprintDetail
              key={detailId}
              blueprintId={detailId}
              onClose={handleCloseDetail}
            />
          )}
        </AnimatePresence>
      </div>

      {/* Pagination */}
      {!isLoading && !error && meta.total_pages > 1 && (
        <div className="flex items-center justify-between pt-2">
          <p className="text-sm text-text-muted">
            Page {meta.page} of {meta.total_pages} ({meta.total} total)
          </p>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={meta.page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              className={cn("h-8 gap-1", meta.page <= 1 && "opacity-50")}
            >
              <ChevronLeft className="size-4" />
              Prev
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={meta.page >= meta.total_pages}
              onClick={() => setPage((p) => p + 1)}
              className={cn("h-8 gap-1", meta.page >= meta.total_pages && "opacity-50")}
            >
              Next
              <ChevronRight className="size-4" />
            </Button>
          </div>
        </div>
      )}

      {/* Comparison dialog */}
      {compareOpen && compareIds && (
        <ComparisonView
          blueprintIds={compareIds}
          onClose={handleCloseCompare}
        />
      )}
    </div>
  );
}
