import { useState } from "react";
import { useDraggable, useDroppable } from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";
import { Film, Plus, CheckCircle2, Loader2, CalendarX, Archive, MoreHorizontal, GripVertical } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useApproveItem, useUnscheduleItem, useArchiveItem } from "@/hooks/use-publishing-queue";
import { cn } from "@/lib/utils";
import { getThumbnailInfo } from "@/lib/format";
import type { Blueprint } from "@/api/types";

interface TimeSlotProps {
  time: string;
  date: string;
  blueprint: Blueprint | null;
  status: "published" | "scheduled" | "empty" | "failed";
  nicheColor?: string;
  onPreview?: (blueprint: Blueprint) => void;
}

function SlotThumb({ blueprint }: { blueprint: Blueprint }) {
  const [errored, setErrored] = useState(false);
  const thumb = getThumbnailInfo(blueprint);

  if (!thumb || errored) {
    return (
      <div className="size-10 shrink-0 overflow-hidden rounded-md bg-bg-elevated flex items-center justify-center">
        <Film className="size-4 text-text-disabled" />
      </div>
    );
  }

  return (
    <div className="size-10 shrink-0 overflow-hidden rounded-md bg-black">
      {thumb.isVideo ? (
        <video
          src={`${thumb.url}#t=0.5`}
          muted
          preload="metadata"
          className="size-full object-cover"
          onError={() => setErrored(true)}
        />
      ) : (
        <img
          src={thumb.url}
          alt=""
          className="size-full object-cover"
          onError={() => setErrored(true)}
        />
      )}
    </div>
  );
}

export function TimeSlot({ time, date, blueprint, status, nicheColor: _nicheColor, onPreview }: TimeSlotProps) {
  const droppableId = `${date}_${time}`;
  const { setNodeRef: setDropRef, isOver } = useDroppable({
    id: droppableId,
    data: { time, date },
  });

  const isPublished = status === "published";
  const isScheduled = status === "scheduled";
  const isDraggable = isScheduled && blueprint != null;

  // Make scheduled (non-published) slots draggable so users can rearrange
  const {
    attributes,
    listeners,
    setNodeRef: setDragRef,
    transform,
    isDragging,
  } = useDraggable({
    id: blueprint?.id ?? `empty-${droppableId}`,
    data: { blueprint },
    disabled: !isDraggable,
  });

  const dragStyle = transform
    ? { transform: CSS.Translate.toString(transform) }
    : undefined;

  const approve = useApproveItem();
  const unschedule = useUnscheduleItem();
  const archive = useArchiveItem();

  const handleClick = () => {
    if (blueprint && onPreview) {
      onPreview(blueprint);
    }
  };

  const needsApproval =
    isScheduled &&
    blueprint != null &&
    blueprint.action_taken !== "approved";

  // Combine refs: droppable always, draggable only for scheduled non-published
  const combinedRef = (node: HTMLElement | null) => {
    setDropRef(node);
    if (isDraggable) setDragRef(node);
  };

  return (
    <div
      ref={combinedRef}
      style={dragStyle}
      onClick={blueprint ? handleClick : undefined}
      className={cn(
        "group rounded-md transition-all min-h-[52px]",
        blueprint && "cursor-pointer",
        isPublished && "bg-green-500/8 border border-green-500/20 hover:bg-green-500/12",
        isScheduled && "bg-bg-surface border border-border hover:border-text-disabled hover:bg-bg-elevated",
        status === "empty" && "border border-dashed border-bg-hover/40",
        isOver && !isDragging && "border-indigo-500 bg-indigo-500/8 shadow-sm",
        isDragging && "opacity-40 border-indigo-500/50",
      )}
    >
      {blueprint ? (
        <div className="flex items-center gap-2.5 p-1.5">
          {/* Drag handle — only for scheduled (non-published) posts */}
          {isDraggable && (
            <div
              {...listeners}
              {...attributes}
              className="shrink-0 cursor-grab active:cursor-grabbing touch-none opacity-0 group-hover:opacity-60 hover:!opacity-100 transition-opacity"
              onClick={(e) => e.stopPropagation()}
            >
              <GripVertical className="size-3.5 text-text-muted" />
            </div>
          )}

          <SlotThumb blueprint={blueprint} />
          <div className="min-w-0 flex-1">
            <p
              className="text-[11px] font-medium text-text-primary leading-snug line-clamp-2"
              title={blueprint.hook_text || blueprint.title || "Untitled"}
            >
              {blueprint.hook_text || blueprint.title || "Untitled"}
            </p>
            <div className="mt-0.5 flex items-center gap-2">
              {isPublished && (
                <span className="inline-flex items-center gap-0.5 text-[9px] font-medium text-green-400">
                  <CheckCircle2 className="size-2.5" />
                  Live
                </span>
              )}
              {needsApproval && (
                <button
                  type="button"
                  disabled={approve.isPending}
                  onClick={(e) => {
                    e.stopPropagation();
                    approve.mutate({ id: blueprint.id });
                  }}
                  className="inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-[9px] font-medium text-amber-400 bg-amber-500/10 hover:bg-amber-500/20 transition-colors disabled:opacity-50"
                >
                  {approve.isPending ? (
                    <Loader2 className="size-2.5 animate-spin" />
                  ) : (
                    <CheckCircle2 className="size-2.5" />
                  )}
                  Approve
                </button>
              )}
            </div>
          </div>

          {/* Actions menu — visible on hover */}
          {!isPublished && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  type="button"
                  onClick={(e) => e.stopPropagation()}
                  className="shrink-0 rounded p-0.5 opacity-0 group-hover:opacity-100 transition-opacity hover:bg-bg-elevated"
                >
                  <MoreHorizontal className="size-3.5 text-text-muted" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-40">
                <DropdownMenuItem
                  onClick={(e) => {
                    e.stopPropagation();
                    unschedule.mutate(blueprint.id);
                  }}
                  className="text-amber-400 focus:text-amber-300 gap-2"
                >
                  <CalendarX className="size-3.5" />
                  Unschedule
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={(e) => {
                    e.stopPropagation();
                    archive.mutate(blueprint.id);
                  }}
                  className="text-red-400 focus:text-red-300 gap-2"
                >
                  <Archive className="size-3.5" />
                  Archive
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      ) : (
        <div
          className={cn(
            "flex items-center justify-center h-[52px] transition-colors",
            isOver ? "text-indigo-400" : "text-text-disabled/40",
          )}
        >
          <Plus className="size-3.5" />
        </div>
      )}
    </div>
  );
}
