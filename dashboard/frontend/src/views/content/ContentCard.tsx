import { useState, useCallback } from "react";
import { Check, X, Calendar, Heart, MessageCircle, Play } from "lucide-react";
import { StatusBadge } from "@/components/shared/status-badge";
import { PlatformIcon } from "@/components/shared/PlatformIcon";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { getThumbnailInfo, formatCompact } from "@/lib/format";
import { NICHE_HEX, NICHE_LABELS } from "@/niches/registry";
import { useApproveAndSchedule } from "@/hooks/use-blueprints";
import type { Blueprint } from "@/api/types";

// ── Sub-components ───────────────────────────────────────────────────────────

interface ThumbnailProps {
  src: string | null;
  alt: string;
  isVideo?: boolean;
}

function Thumbnail({ src, alt, isVideo }: ThumbnailProps) {
  const [mediaErr, setMediaErr] = useState(false);
  const [duration, setDuration] = useState<number | null>(null);

  if (src && !mediaErr) {
    return (
      <div className="relative shrink-0 w-28 h-[200px] rounded-lg overflow-hidden bg-black">
        {isVideo ? (
          <>
            <video
              src={src}
              className="w-full h-full object-cover"
              preload="metadata"
              muted
              loop
              playsInline
              onMouseEnter={(e) => e.currentTarget.play().catch(() => {})}
              onMouseLeave={(e) => { e.currentTarget.pause(); e.currentTarget.currentTime = 0; }}
              onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
              onError={() => setMediaErr(true)}
            />
            {duration != null && (
              <span className="absolute bottom-1 right-1 bg-black/70 text-white text-[10px] font-mono px-1 py-0.5 rounded">
                {Math.floor(duration / 60)}:{String(Math.floor(duration % 60)).padStart(2, "0")}
              </span>
            )}
            <div className="absolute inset-0 flex items-center justify-center bg-black/20 opacity-0 hover:opacity-100 transition-opacity">
              <Play className="size-6 text-white fill-white drop-shadow" />
            </div>
          </>
        ) : (
          <img
            src={src}
            alt={alt}
            className="w-full h-full object-cover"
            onError={() => setMediaErr(true)}
          />
        )}
      </div>
    );
  }

  // Placeholder
  return (
    <div className="shrink-0 w-28 h-[200px] rounded-lg bg-bg-hover flex items-center justify-center border border-border-subtle">
      <Play className="size-8 text-text-ghost" />
    </div>
  );
}

interface PlatformStatusRowProps {
  publishStatus: Record<string, string>;
}

function PlatformStatusRow({ publishStatus }: PlatformStatusRowProps) {
  const platforms = Object.entries(publishStatus);
  if (platforms.length === 0) return null;

  return (
    <div className="flex items-center gap-2 flex-wrap">
      {platforms.map(([platform, status]) => {
        const ok = status === "published" || status === "success" || status === "ok";
        return (
          <span key={platform} className="flex items-center gap-0.5">
            <PlatformIcon platform={platform} size={12} />
            {ok ? (
              <Check className="size-2.5 text-green-400" />
            ) : (
              <X className="size-2.5 text-red-400" />
            )}
          </span>
        );
      })}
    </div>
  );
}

// ── Main Card ────────────────────────────────────────────────────────────────

export interface ContentCardProps {
  blueprint: Blueprint;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
  onClick?: () => void;
  selected?: boolean;
  onToggleSelect?: () => void;
}

export function ContentCard({
  blueprint: bp,
  onApprove,
  onReject,
  onClick,
  selected,
  onToggleSelect,
}: ContentCardProps) {
  const thumbInfo  = getThumbnailInfo(bp);
  const thumb      = thumbInfo?.url ?? null;
  const nicheColor = bp.niche_id ? NICHE_HEX[bp.niche_id] ?? "#71717a" : "#71717a";
  const nicheLabel = bp.niche_id ? NICHE_LABELS[bp.niche_id] ?? bp.niche_id : null;
  const isVisualReady = bp.status === "VISUAL_READY";
  const isPublished   = bp.status === "PUBLISHED";
  const isScheduled   = isVisualReady && bp.action_taken === "approved" && !!bp.scheduled_for;

  const approveAndSchedule = useApproveAndSchedule();

  const handleApprove = useCallback(
    (e: React.MouseEvent) => { e.stopPropagation(); onApprove(bp.id); },
    [bp.id, onApprove],
  );
  const handleReject = useCallback(
    (e: React.MouseEvent) => { e.stopPropagation(); onReject(bp.id); },
    [bp.id, onReject],
  );
  const handleApproveSchedule = useCallback(
    (e: React.MouseEvent) => { e.stopPropagation(); approveAndSchedule.mutate(bp.id); },
    [bp.id, approveAndSchedule],
  );

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onClick?.(); }}
      className={cn(
        "group relative flex flex-col gap-3 rounded-lg border bg-bg-elevated p-3 cursor-pointer",
        "border-border-subtle transition-all duration-150",
        "hover:border-border hover:shadow-md hover:bg-bg-raised",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-color-info/50",
        selected && "ring-2 ring-[var(--niche-current)] border-[var(--niche-current)]",
      )}
    >
      {onToggleSelect && (
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggleSelect}
          onClick={(e) => e.stopPropagation()}
          className="absolute top-2 right-2 size-4 accent-[var(--niche-current)] cursor-pointer z-10"
        />
      )}

      {/* ── Top row: thumbnail + content ── */}
      <div className="flex items-start gap-3">
        <Thumbnail
          src={thumb}
          alt={bp.hook_text ?? bp.candidate_id ?? "blueprint"}
          isVideo={thumbInfo?.isVideo}
        />

        <div className="flex flex-col gap-1.5 min-w-0 flex-1">
          {/* Hook text — prominent */}
          {bp.hook_text && (
            <p className="text-sm font-bold text-text-primary leading-snug line-clamp-2">
              {bp.hook_text}
            </p>
          )}

          {/* Title (if different from hook) */}
          {bp.title && bp.title !== bp.hook_text && (
            <p className="text-xs text-text-muted line-clamp-1">
              {bp.title}
            </p>
          )}

          {/* Caption preview — show more */}
          {bp.caption && (
            <p className="text-xs text-text-secondary line-clamp-3 leading-relaxed cursor-help" title={bp.caption}>
              {bp.caption}
            </p>
          )}

          {/* Hashtags */}
          {bp.hashtags && (
            <p className="text-[10px] text-text-ghost truncate">
              {bp.hashtags}
            </p>
          )}

          {/* Meta row: status + priority + niche */}
          <div className="flex items-center gap-2 flex-wrap mt-1">
            <StatusBadge status={bp.status} />

            {typeof bp.priority_score === "number" && (
              <span className="inline-flex items-center rounded-full border border-border-subtle px-1.5 py-0.5 text-[10px] font-medium text-text-muted tabular-nums">
                P {bp.priority_score.toFixed(1)}
              </span>
            )}

            {bp.video_duration != null && bp.video_duration > 0 && (
              <span className="text-[10px] text-text-ghost font-mono">
                {Math.floor(bp.video_duration / 60)}:{String(Math.floor(bp.video_duration % 60)).padStart(2, "0")}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* ── Niche + platform publish status ── */}
      <div className="flex items-center justify-between gap-2">
        {nicheLabel && (
          <span className="flex items-center gap-1.5 text-xs text-text-muted">
            <span
              className="size-2 rounded-full shrink-0"
              style={{ background: nicheColor }}
            />
            {nicheLabel}
          </span>
        )}

        {isPublished && bp.platform_publish_status && (
          <PlatformStatusRow publishStatus={bp.platform_publish_status} />
        )}
      </div>

      {/* ── Engagement mini-stats (PUBLISHED only) ── */}
      {isPublished && (bp.ig_likes || bp.ig_comments || bp.yt_likes || bp.yt_comments) && (
        <div className="flex items-center gap-3 text-xs text-text-muted border-t border-border-subtle pt-2">
          {(bp.ig_likes ?? 0) + (bp.yt_likes ?? 0) > 0 && (
            <span className="flex items-center gap-1">
              <Heart className="size-3" />
              {formatCompact((bp.ig_likes ?? 0) + (bp.yt_likes ?? 0))}
            </span>
          )}
          {(bp.ig_comments ?? 0) + (bp.yt_comments ?? 0) > 0 && (
            <span className="flex items-center gap-1">
              <MessageCircle className="size-3" />
              {formatCompact((bp.ig_comments ?? 0) + (bp.yt_comments ?? 0))}
            </span>
          )}
          {bp.ig_reach && bp.ig_reach > 0 && (
            <span className="flex items-center gap-1 ml-auto">
              {formatCompact(bp.ig_reach)} reach
            </span>
          )}
        </div>
      )}

      {/* ── Action buttons (VISUAL_READY only) ── */}
      {isVisualReady && !isScheduled && (
        <div
          className="flex items-center gap-2 border-t border-border-subtle pt-2"
          onClick={(e) => e.stopPropagation()}
          role="presentation"
        >
          <Button
            size="xs"
            variant="outline"
            className="border-green-600/30 text-green-400 hover:bg-green-600/15 flex-1"
            onClick={handleApprove}
          >
            <Check className="size-3" />
            Approve
          </Button>
          <Button
            size="xs"
            variant="outline"
            className="border-red-600/30 text-red-400 hover:bg-red-600/15 flex-1"
            onClick={handleReject}
          >
            <X className="size-3" />
            Reject
          </Button>
          <Button
            size="xs"
            variant="outline"
            className="border-indigo-600/30 text-indigo-400 hover:bg-indigo-600/15 flex-1"
            onClick={handleApproveSchedule}
          >
            <Calendar className="size-3" />
            Schedule
          </Button>
        </div>
      )}

      {/* ── Scheduled indicator ── */}
      {isScheduled && bp.scheduled_for && (
        <div className="flex items-center gap-2 border-t border-border-subtle pt-2 text-xs text-text-muted">
          <Calendar className="size-3 text-indigo-400" />
          <span>
            Scheduled for{" "}
            <span className="font-medium text-text-primary">
              {new Date(bp.scheduled_for).toLocaleDateString("en-US", {
                month: "short",
                day: "numeric",
                hour: "numeric",
                minute: "2-digit",
              })}
            </span>
          </span>
          <Check className="size-3 text-green-400 ml-auto" />
          <span className="text-green-400">Approved</span>
        </div>
      )}
    </div>
  );
}
