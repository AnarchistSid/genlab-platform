import { useState, useMemo, useCallback, useEffect } from "react";
import { FileText, AlertTriangle, ArrowUpDown } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { EmptyState } from "@/components/shared/empty-state";
import { useBlueprints, useReviewBlueprint, useBatchReview, useBatchApproveSchedule } from "@/hooks/use-blueprints";
import { useNicheStore } from "@/stores/niche-store";
import { getAllNiches } from "@/niches/registry";
import { ContentCard } from "./ContentCard";
import { FocusOverlay } from "./FocusOverlay";
import { PageHeader } from "@/components/shared/page-header";
import { cn } from "@/lib/utils";
import type { Blueprint } from "@/api/types";

// ── Constants ────────────────────────────────────────────────────────────────

const STATUS_OPTIONS = [
  { value: "ALL",          label: "All" },
  { value: "VISUAL_READY", label: "Visual Ready" },
  { value: "SCHEDULED",    label: "Scheduled" },
  { value: "DRAFTED",      label: "Drafted" },
  { value: "PUBLISHED",    label: "Published" },
  { value: "ARCHIVED",     label: "Archived" },
] as const;

const NICHE_OPTIONS = [
  { value: "all", label: "All Niches" },
  ...getAllNiches().map(n => ({ value: n.id, label: n.displayName })),
];

const SORT_OPTIONS = [
  { value: "newest",   label: "Newest" },
  { value: "priority", label: "Priority Score" },
  { value: "status",   label: "Status" },
] as const;

type SortValue = (typeof SORT_OPTIONS)[number]["value"];

// ── Skeleton grid ────────────────────────────────────────────────────────────

function SkeletonGrid() {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 9 }).map((_, i) => (
        <div
          key={i}
          className="rounded-lg border border-border-subtle bg-bg-elevated p-3 space-y-3"
        >
          <div className="flex items-start gap-3">
            <Skeleton className="size-16 rounded-md shrink-0" />
            <div className="flex-1 space-y-2">
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-3 w-3/4" />
              <div className="flex gap-2">
                <Skeleton className="h-5 w-16 rounded-full" />
                <Skeleton className="h-5 w-10 rounded-full" />
              </div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Sort helper ──────────────────────────────────────────────────────────────

function sortBlueprints(items: Blueprint[], sort: SortValue): Blueprint[] {
  const copy = [...items];
  switch (sort) {
    case "priority":
      return copy.sort((a, b) => (b.priority_score ?? 0) - (a.priority_score ?? 0));
    case "status":
      return copy.sort((a, b) => a.status.localeCompare(b.status));
    case "newest":
    default:
      return copy.sort((a, b) => {
        const ta = a.created_at ? new Date(a.created_at).getTime() : 0;
        const tb = b.created_at ? new Date(b.created_at).getTime() : 0;
        return tb - ta;
      });
  }
}

// ── Main view ────────────────────────────────────────────────────────────────

export default function ContentReviewView() {
  const { selectedNicheId } = useNicheStore();

  const [activeStatus, setActiveStatus] = useState<string>("VISUAL_READY");
  const [activeNiche,  setActiveNiche]  = useState<string>(selectedNicheId ?? "all");
  const [sort,         setSort]         = useState<SortValue>("newest");
  const [searchQuery,  setSearchQuery]  = useState("");

  // Focus overlay state
  const [overlayIndex, setOverlayIndex] = useState<number | null>(null);

  // Batch selection state
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const toggleSelect = useCallback((id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }, []);
  const clearSelection = useCallback(() => setSelectedIds(new Set()), []);

  const reviewMutation = useReviewBlueprint();
  const batchReview = useBatchReview();
  const batchApproveSchedule = useBatchApproveSchedule();

  // Build API params — pass status filter to API when possible
  const apiParams = useMemo(() => {
    const params: Record<string, string> = {
      per_page: "100",
      sort: sort === "priority" ? "priority" : sort === "status" ? "status" : "newest",
    };
    if (activeStatus === "ALL") {
      params.include_published = "true";  // ALL shows everything including published
    } else {
      params.status = activeStatus;
    }
    // niche_id: use the sidebar selection as default, override with local filter
    const niche = activeNiche !== "all" ? activeNiche : selectedNicheId ?? "all";
    params.niche_id = niche;
    return params;
  }, [activeStatus, activeNiche, sort, selectedNicheId]);

  const { data, isLoading, error, refetch } = useBlueprints(apiParams);

  // Client-side filter for niche (in case API doesn't filter by niche)
  const allItems: Blueprint[] = data?.data ?? [];

  const filtered = useMemo(() => {
    let items = allItems;

    // Status filter (API may or may not honour it — filter defensively)
    if (activeStatus !== "ALL" && activeStatus !== "SCHEDULED") {
      items = items.filter((bp) => bp.status === activeStatus);
    }
    // SCHEDULED is a virtual status — backend returns VISUAL_READY posts with
    // action_taken=approved + scheduled_for set. No client-side re-filter needed
    // since the API already handles this.

    // Niche filter
    const niche = activeNiche !== "all" ? activeNiche : selectedNicheId;
    if (niche && niche !== "all") {
      items = items.filter((bp) => bp.niche_id === niche);
    }

    // Text search filter
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      items = items.filter((bp) =>
        (bp.hook_text ?? "").toLowerCase().includes(q) ||
        (bp.title ?? "").toLowerCase().includes(q) ||
        (bp.caption ?? "").toLowerCase().includes(q)
      );
    }

    return sortBlueprints(items, sort);
  }, [allItems, activeStatus, activeNiche, selectedNicheId, sort, searchQuery]);

  // ── Handlers ────────────────────────────────────────────────────────────

  const handleApprove = useCallback((id: string) => {
    reviewMutation.mutate({ id, body: { action: "approved" } });
  }, [reviewMutation]);

  const handleReject = useCallback((id: string) => {
    reviewMutation.mutate({ id, body: { action: "rejected" } });
  }, [reviewMutation]);

  const handleCardClick = useCallback((index: number) => {
    setOverlayIndex(index);
  }, []);

  const handleOverlayNavigate = useCallback((index: number) => {
    setOverlayIndex(index);
  }, []);

  const handleOverlayClose = useCallback(() => {
    setOverlayIndex(null);
  }, []);

  // Clear selection when filters change
  useEffect(() => { clearSelection(); }, [activeStatus, activeNiche, sort, clearSelection]);

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col gap-5">
      {/* ── Page header ── */}
      <PageHeader
        title="Content Review"
        className="mb-0"
        actions={
          <Select
            value={sort}
            onValueChange={(v) => setSort(v as SortValue)}
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
        }
      />

      {/* ── Filter bar ── */}
      <div className="flex flex-col gap-3">
        {/* Status pills */}
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-medium text-text-muted">Status:</span>
          {STATUS_OPTIONS.map((opt) => {
            const isActive = activeStatus === opt.value;
            return (
              <Badge
                key={opt.value}
                variant={isActive ? "default" : "outline"}
                className={cn(
                  "cursor-pointer select-none transition-colors",
                  !isActive && "hover:bg-bg-hover"
                )}
                onClick={() => setActiveStatus(opt.value)}
              >
                {opt.label}
              </Badge>
            );
          })}
        </div>

        {/* Niche dropdown + Search */}
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-text-muted">Niche:</span>
            <Select value={activeNiche} onValueChange={setActiveNiche}>
              <SelectTrigger size="sm" className="w-[180px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {NICHE_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <input
            type="text"
            placeholder="Search hooks, titles, captions..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="flex-1 min-w-[200px] max-w-[360px] bg-bg-elevated border border-border rounded-md text-sm text-text-primary px-3 py-1.5 outline-none focus:border-[var(--niche-current)] transition-colors placeholder:text-text-ghost"
          />
        </div>
      </div>

      {/* ── Content area ── */}
      {isLoading && <SkeletonGrid />}

      {error && !isLoading && (
        <EmptyState
          icon={AlertTriangle}
          title="Failed to load content"
          description={error.message}
          action={{ label: "Retry", onClick: () => void refetch() }}
        />
      )}

      {!isLoading && !error && filtered.length === 0 && (
        <EmptyState
          icon={FileText}
          title="No content matching your filters"
          description="Try selecting a different status or niche."
        />
      )}

      {!isLoading && !error && filtered.length > 0 && (
        <>
          <p className="text-xs text-text-muted -mt-2">
            {filtered.length} item{filtered.length !== 1 ? "s" : ""}
          </p>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {filtered.map((bp, i) => (
              <ContentCard
                key={bp.id}
                blueprint={bp}
                onApprove={handleApprove}
                onReject={handleReject}
                onClick={() => handleCardClick(i)}
                selected={selectedIds.has(bp.id)}
                onToggleSelect={() => toggleSelect(bp.id)}
              />
            ))}
          </div>
        </>
      )}

      {/* ── Floating batch action bar ── */}
      {selectedIds.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-30 flex items-center gap-3 rounded-xl border border-border bg-bg-raised px-5 py-3 shadow-lg animate-in slide-in-from-bottom duration-200">
          <span className="text-sm font-semibold text-text-primary">{selectedIds.size} selected</span>
          <Button
            size="sm"
            onClick={() => { batchReview.mutate({ ids: [...selectedIds], action: "approved" }); clearSelection(); }}
          >
            Approve All
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="border-indigo-600/30 text-indigo-400"
            onClick={() => { batchApproveSchedule.mutate([...selectedIds]); clearSelection(); }}
          >
            Approve &amp; Schedule
          </Button>
          <Button size="sm" variant="ghost" onClick={clearSelection}>
            Cancel
          </Button>
        </div>
      )}

      {/* ── Focus overlay ── */}
      <FocusOverlay
        blueprints={filtered}
        currentIndex={overlayIndex ?? 0}
        isOpen={overlayIndex !== null}
        onNavigate={handleOverlayNavigate}
        onApprove={handleApprove}
        onReject={handleReject}
        onClose={handleOverlayClose}
      />
    </div>
  );
}
