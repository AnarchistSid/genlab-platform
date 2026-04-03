import { useState } from "react";
import { useDraggable } from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";
import { Image, Film, Layers } from "lucide-react";
import { StatusBadge } from "@/components/shared/status-badge";
import { cn } from "@/lib/utils";
import { getThumbnailInfo, type ThumbnailInfo } from "@/lib/format";
import type { Blueprint } from "@/api/types";

interface DragCardProps {
  blueprint: Blueprint;
  compact?: boolean;
}

const FORMAT_ICON = {
  carousel: Layers,
  reel: Film,
  single_image: Image,
} as const;

function FormatIcon({ format, className }: { format?: string; className?: string }) {
  const Icon = FORMAT_ICON[(format ?? "") as keyof typeof FORMAT_ICON] ?? Image;
  return <Icon className={cn("size-3.5 text-text-muted", className)} />;
}

/** Renders a video/image thumbnail with graceful error fallback to an icon. */
function MediaThumb({
  thumb,
  format,
  compact,
}: {
  thumb: ThumbnailInfo | null;
  format?: string;
  compact?: boolean;
}) {
  const [errored, setErrored] = useState(false);
  const iconSize = compact ? "size-3" : "size-4";

  if (!thumb || errored) {
    return (
      <div className="flex size-full items-center justify-center">
        <FormatIcon format={format} className={iconSize} />
      </div>
    );
  }

  if (thumb.isVideo) {
    return (
      <video
        src={`${thumb.url}#t=0.5`}
        muted
        preload="metadata"
        className="size-full object-contain bg-black"
        onError={() => setErrored(true)}
      />
    );
  }

  return (
    <img
      src={thumb.url}
      alt=""
      className="size-full object-cover"
      onError={() => setErrored(true)}
    />
  );
}

export function DragCard({ blueprint, compact = false }: DragCardProps) {
  const { attributes, listeners, setNodeRef, transform, isDragging } =
    useDraggable({
      id: blueprint.id,
      data: { blueprint },
    });

  const style = transform
    ? { transform: CSS.Translate.toString(transform) }
    : undefined;

  const thumb = getThumbnailInfo(blueprint);

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...listeners}
      {...attributes}
      className={cn(
        "rounded-md border border-bg-hover bg-bg-surface p-2 cursor-grab active:cursor-grabbing transition-opacity",
        isDragging && "opacity-40",
      )}
    >
      <div className="flex items-center gap-2">
        {/* Thumbnail — always visible, smaller in compact mode */}
        <div
          className={cn(
            "shrink-0 overflow-hidden rounded bg-bg-elevated",
            compact ? "size-9" : "size-12",
          )}
        >
          <MediaThumb thumb={thumb} format={blueprint.format} compact={compact} />
        </div>

        <div className="min-w-0 flex-1">
          <p
            className={cn(
              "truncate font-medium text-text-primary",
              compact ? "text-xs" : "text-sm",
            )}
          >
            {blueprint.hook_text || "Untitled"}
          </p>
          <div className="mt-0.5 flex items-center gap-1.5">
            <StatusBadge
              status={blueprint.status}
              className={compact ? "text-[10px] px-1 py-0" : undefined}
            />
            {blueprint.format && !compact && (
              <span className="inline-flex items-center gap-0.5 rounded bg-bg-elevated px-1 py-0 text-[10px] text-text-muted">
                <FormatIcon format={blueprint.format} className="size-2.5" />
                {blueprint.format.replace("_", " ")}
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * Overlay shown while dragging (rendered inside DragOverlay).
 * Receives the same props but does not bind drag listeners.
 */
export function DragCardOverlay({ blueprint, compact = false }: DragCardProps) {
  const thumb = getThumbnailInfo(blueprint);

  return (
    <div
      className={cn(
        "rounded-md border border-indigo-500/50 bg-bg-surface p-2 shadow-lg shadow-indigo-500/10",
      )}
    >
      <div className="flex items-center gap-2">
        <div
          className={cn(
            "shrink-0 overflow-hidden rounded bg-bg-elevated",
            compact ? "size-9" : "size-12",
          )}
        >
          <MediaThumb thumb={thumb} format={blueprint.format} compact={compact} />
        </div>

        <div className="min-w-0 flex-1">
          <p
            className={cn(
              "truncate font-medium text-text-primary",
              compact ? "text-xs" : "text-sm",
            )}
          >
            {blueprint.hook_text || "Untitled"}
          </p>
          <div className="mt-0.5">
            <StatusBadge
              status={blueprint.status}
              className={compact ? "text-[10px] px-1 py-0" : undefined}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
