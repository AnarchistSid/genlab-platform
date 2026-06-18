import { useState, useRef, useCallback } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  CheckCircle2,
  XCircle,
  Pause,
  Play,
  Filter,
} from "lucide-react";
import {
  usePublishingQueue,
  useQueueStats,
  useApproveItem,
  useHoldItem,
  useReleaseItem,
} from "@/hooks/use-publishing-queue";
import { isVideoUrl } from "@/lib/media";
import { PageHeader } from "@/components/shared/page-header";
import { LoadingSkeleton } from "@/components/shared/loading-skeleton";
import { EmptyState } from "@/components/shared/empty-state";
import type { QueueItem, QueueStatus } from "@/api/types";

// ── Status Filter Tabs ──────────────────────────────────────

const TABS: { key: string; label: string }[] = [
  { key: "", label: "All" },
  { key: "PENDING_APPROVAL", label: "Pending" },
  { key: "APPROVED", label: "Approved" },
  { key: "HELD", label: "Held" },
  { key: "PUBLISH_FAILED", label: "Failed" },
];

// ── PostThumb ────────────────────────────────────────────────

function PostThumb({ item }: { item: QueueItem }) {
  const [errored, setErrored] = useState(false);
  const onError = useCallback(() => setErrored(true), []);

  // Resolve best thumbnail: visual_paths → all_media_urls → thumbnail_url
  const thumbUrl =
    item.visual_paths ||
    (item.all_media_urls && item.all_media_urls.length > 0 ? item.all_media_urls[0] : null) ||
    (item as unknown as Record<string, unknown>).thumbnail_url as string | null;

  if (!thumbUrl || errored) {
    return (
      <div className="size-14 shrink-0 rounded-md overflow-hidden bg-bg-elevated">
        <div className="size-full bg-gradient-to-br from-bg-elevated to-bg-raised flex items-center justify-center">
          <Play size={18} className="text-text-disabled" />
        </div>
      </div>
    );
  }

  return (
    <div className="size-14 shrink-0 rounded-md overflow-hidden bg-bg-elevated">
      {isVideoUrl(thumbUrl) ? (
        <video
          src={`${thumbUrl}#t=1`}
          muted
          preload="metadata"
          className="size-full object-cover"
          onError={onError}
        />
      ) : (
        <img
          src={thumbUrl}
          alt=""
          loading="lazy"
          className="size-full object-cover"
          onError={onError}
        />
      )}
    </div>
  );
}

// ── PostCard ─────────────────────────────────────────────────

function PostCard({
  item,
  selected,
  onToggle,
  onApprove,
  onHold,
  onRelease,
  mutating,
}: {
  item: QueueItem;
  selected: boolean;
  onToggle: () => void;
  onApprove: () => void;
  onHold: () => void;
  onRelease: () => void;
  mutating?: boolean;
}) {
  const qs = item.queue_status;

  return (
    <div
      className={`flex items-center gap-3 p-3 bg-bg-surface border border-border rounded-lg transition-colors hover:border-border-strong hover:bg-bg-elevated ${
        selected ? "border-[var(--niche-current)] bg-[color-mix(in_srgb,var(--niche-current)_5%,var(--bg-surface))]" : ""
      }`}
    >
      <div>
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          aria-label={`Select blueprint ${item.id}`}
          className="size-4 cursor-pointer accent-[var(--niche-current)]"
        />
      </div>

      {/* Thumbnail */}
      <PostThumb item={item} />

      {/* Content */}
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-text-primary truncate flex items-center gap-1.5">
          {item.niche_id && (
            <span
              className="size-2 rounded-full shrink-0"
              style={{ background: ({ ai_creators: "#00D4FF", gaming: "#f97316", sports: "#FF2040", movies: "#C9A84C", anime: "#7B3FE4" } as Record<string,string>)[item.niche_id] ?? "#71717a" }}
              title={item.niche_id}
            />
          )}
          {item.hook_text || "Untitled"}
        </p>
        <p className="text-xs text-text-muted mt-0.5 truncate">
          {(item.caption || "").slice(0, 80)}
        </p>
        <div className="flex items-center gap-2 mt-1">
          <QueueStatusBadge status={qs} />
          {item.scheduled_for && (
            <span className="text-[11px] text-text-muted tabular-nums">
              {new Date(item.scheduled_for).toLocaleString("en-IN", {
                month: "short",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
              })}
            </span>
          )}
          {item.priority_score != null && (
            <span className="text-[11px] text-text-muted tabular-nums">
              {Number(item.priority_score).toFixed(1)}
            </span>
          )}
        </div>
      </div>

      {/* Actions */}
      <div className="flex gap-1 shrink-0">
        {qs === "PENDING_APPROVAL" && (
          <>
            <button
              className="flex items-center justify-center size-8 border border-border rounded-md bg-bg-surface text-success cursor-pointer transition-colors hover:bg-bg-raised hover:border-success"
              onClick={onApprove}
              disabled={mutating}
              title="Approve"
              aria-label="Approve"
            >
              <CheckCircle2 size={16} />
            </button>
            <button
              className="flex items-center justify-center size-8 border border-border rounded-md bg-bg-surface text-warning cursor-pointer transition-colors hover:bg-bg-raised hover:border-warning"
              onClick={onHold}
              disabled={mutating}
              title="Hold"
              aria-label="Hold"
            >
              <Pause size={16} />
            </button>
          </>
        )}
        {qs === "HELD" && (
          <button
            className="flex items-center justify-center size-8 border border-border rounded-md bg-bg-surface text-info cursor-pointer transition-colors hover:bg-bg-raised hover:border-info"
            onClick={onRelease}
            disabled={mutating}
            title="Release"
            aria-label="Release"
          >
            <Play size={16} />
          </button>
        )}
        {qs === "APPROVED" && (
          <button
            className="flex items-center justify-center size-8 border border-border rounded-md bg-bg-surface text-warning cursor-pointer transition-colors hover:bg-bg-raised hover:border-warning"
            onClick={onHold}
            disabled={mutating}
            title="Hold back"
            aria-label="Hold back"
          >
            <Pause size={16} />
          </button>
        )}
        {qs === "PUBLISH_FAILED" && (
          <button
            className="flex items-center justify-center size-8 border border-border rounded-md bg-bg-surface text-success cursor-pointer transition-colors hover:bg-bg-raised hover:border-success"
            onClick={onApprove}
            disabled={mutating}
            title="Retry"
            aria-label="Retry"
          >
            <Play size={16} />
          </button>
        )}
      </div>
    </div>
  );
}

const BADGE_STYLES: Record<QueueStatus, string> = {
  PENDING_APPROVAL: "bg-[#78350f33] text-warning",
  APPROVED: "bg-[#14532d33] text-success",
  HELD: "bg-[#7f1d1d33] text-error",
  PUBLISHED: "bg-[#1e3a5f33] text-info",
  PUBLISH_FAILED: "bg-[#7f1d1d33] text-error",
};

const BADGE_LABELS: Record<QueueStatus, string> = {
  PENDING_APPROVAL: "Pending",
  APPROVED: "Approved",
  HELD: "Held",
  PUBLISHED: "Published",
  PUBLISH_FAILED: "Failed",
};

function QueueStatusBadge({ status }: { status: QueueStatus }) {
  return (
    <span className={`text-[11px] font-medium px-1.5 py-px rounded-full ${BADGE_STYLES[status] ?? ""}`}>
      {BADGE_LABELS[status] ?? status}
    </span>
  );
}

// ── BulkActionBar ──────────────────────────────────────────

function BulkActionBar({
  count,
  onApproveAll,
  onHoldAll,
  onClear,
}: {
  count: number;
  onApproveAll: () => void;
  onHoldAll: () => void;
  onClear: () => void;
}) {
  if (count === 0) return null;

  return (
    <div className="flex items-center gap-2 px-3 py-2 mb-3 bg-bg-elevated border border-[var(--niche-current)] rounded-lg animate-[slideIn_0.2s_var(--ease-out)]">
      <span className="text-sm font-semibold text-text-primary mr-auto">
        {count} selected
      </span>
      <button
        className="flex items-center gap-1 text-xs font-medium px-2 py-1 border border-border rounded-md bg-bg-surface text-success cursor-pointer transition-colors hover:bg-bg-raised"
        onClick={onApproveAll}
      >
        <CheckCircle2 size={14} /> Approve All
      </button>
      <button
        className="flex items-center gap-1 text-xs font-medium px-2 py-1 border border-border rounded-md bg-bg-surface text-warning cursor-pointer transition-colors hover:bg-bg-raised"
        onClick={onHoldAll}
      >
        <Pause size={14} /> Hold All
      </button>
      <button
        className="flex items-center gap-1 text-xs font-medium px-2 py-1 border border-border rounded-md bg-bg-surface text-text-muted cursor-pointer transition-colors hover:bg-bg-raised"
        onClick={onClear}
      >
        <XCircle size={14} /> Clear
      </button>
    </div>
  );
}

// ── Main View ───────────────────────────────────────────────

export default function PublishingQueue() {
  const [activeTab, setActiveTab] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const { data: queueResp, isLoading } = usePublishingQueue(activeTab || undefined);
  const { data: statsResp } = useQueueStats();
  const approve = useApproveItem();
  const hold = useHoldItem();
  const release = useReleaseItem();

  const items = queueResp?.data ?? [];
  const stats = statsResp?.data;

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleBulkApprove = () => {
    for (const id of selectedIds) {
      approve.mutate({ id });
    }
    setSelectedIds(new Set());
  };

  const handleBulkHold = () => {
    for (const id of selectedIds) {
      hold.mutate({ id });
    }
    setSelectedIds(new Set());
  };

  return (
    <div className="max-w-[900px] mx-auto">
      <PageHeader
        title="Publishing Queue"
        subtitle="Approve or hold posts before they go live. Nothing publishes without your sign-off."
      />

      {/* Stats Bar */}
      {stats && (
        <div className="flex gap-2 mb-6 flex-wrap">
          <StatPill label="Pending" count={stats.pending} variant="pending" />
          <StatPill label="Approved" count={stats.approved} variant="approved" />
          <StatPill label="Held" count={stats.held} variant="held" />
          <StatPill label="Failed" count={stats.failed} variant="failed" />
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-0.5 mb-3 border-b border-border">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            className={`bg-transparent border-none text-text-muted text-sm font-medium px-3 py-2 cursor-pointer border-b-2 border-b-transparent transition-colors hover:text-text-secondary ${
              activeTab === tab.key ? "text-[var(--niche-current)] border-b-[var(--niche-current)]" : ""
            }`}
            onClick={() => {
              setActiveTab(tab.key);
              setSelectedIds(new Set());
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Bulk Action Bar */}
      {items.length > 0 && (
        <div className="flex items-center gap-2 mb-2">
          <label className="flex items-center gap-1.5 text-xs text-text-muted cursor-pointer select-none">
            <input
              type="checkbox"
              checked={selectedIds.size === items.length && items.length > 0}
              onChange={() => {
                if (selectedIds.size === items.length) {
                  setSelectedIds(new Set());
                } else {
                  setSelectedIds(new Set(items.map((i) => i.id)));
                }
              }}
              className="cursor-pointer accent-[var(--niche-current)]"
            />
            Select all ({items.length})
          </label>
        </div>
      )}
      <BulkActionBar
        count={selectedIds.size}
        onApproveAll={handleBulkApprove}
        onHoldAll={handleBulkHold}
        onClear={() => setSelectedIds(new Set())}
      />

      {/* Post List */}
      {isLoading ? (
        <LoadingSkeleton variant="card-list" rows={3} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={Filter}
          title="No items in this queue"
          description="There are no posts matching the current filter."
        />
      ) : (
        <VirtualizedPostList
          items={items}
          selectedIds={selectedIds}
          onToggle={toggleSelect}
          onApprove={(id) => approve.mutate({ id })}
          onHold={(id) => hold.mutate({ id })}
          onRelease={(id) => release.mutate(id)}
          mutating={approve.isPending || hold.isPending || release.isPending}
        />
      )}
    </div>
  );
}

function VirtualizedPostList({
  items,
  selectedIds,
  onToggle,
  onApprove,
  onHold,
  onRelease,
  mutating,
}: {
  items: QueueItem[];
  selectedIds: Set<string>;
  onToggle: (id: string) => void;
  onApprove: (id: string) => void;
  onHold: (id: string) => void;
  onRelease: (id: string) => void;
  mutating: boolean;
}) {
  const parentRef = useRef<HTMLDivElement>(null);
  // TanStack Virtual returns un-memoizable functions by design (it owns
  // the scroll-positioning state internally). React Compiler can't safely
  // memoize this component; that's a structural limitation of the
  // library, not a bug in this usage. Suppressing is the recommended
  // workaround until @tanstack/react-virtual ships a compiler-safe API.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 80,
    overscan: 5,
  });

  return (
    <div ref={parentRef} className="flex flex-col gap-2 overflow-auto max-h-[calc(100vh-280px)]">
      <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
        {virtualizer.getVirtualItems().map((virtualRow) => {
          const item = items[virtualRow.index];
          return (
            <div
              key={item.id}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                transform: `translateY(${virtualRow.start}px)`,
              }}
              ref={virtualizer.measureElement}
              data-index={virtualRow.index}
            >
              <PostCard
                item={item}
                selected={selectedIds.has(item.id)}
                onToggle={() => onToggle(item.id)}
                onApprove={() => onApprove(item.id)}
                onHold={() => onHold(item.id)}
                onRelease={() => onRelease(item.id)}
                mutating={mutating}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

const STAT_PILL_COLORS: Record<string, string> = {
  pending: "text-warning",
  approved: "text-success",
  held: "text-error",
  failed: "text-error",
};

function StatPill({
  label,
  count,
  variant,
}: {
  label: string;
  count: number;
  variant: string;
}) {
  return (
    <div className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-bg-surface border border-border">
      <span className={`text-xl font-bold tabular-nums ${STAT_PILL_COLORS[variant] ?? ""}`}>
        {count}
      </span>
      <span className="text-xs text-text-muted uppercase tracking-wide">{label}</span>
    </div>
  );
}
