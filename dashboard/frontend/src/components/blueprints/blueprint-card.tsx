import { useCallback, useState } from "react";
import { motion } from "framer-motion";
import {
  Clock,
  FileText,
  MoreVertical,
  Check,
  X,
  Star,
  DollarSign,
  Instagram,
  Youtube,
  Twitter,
  Facebook,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { StatusBadge } from "@/components/shared/status-badge";
import { cn } from "@/lib/utils";
import { formatRelativeTime, formatCompact } from "@/lib/format";
import { useReviewBlueprint } from "@/hooks/use-blueprints";
import { getArchiveReasonLabel } from "@/constants/archive-reasons";
import type { Blueprint } from "@/api/types";

interface BlueprintCardProps {
  blueprint: Blueprint;
  selected: boolean;
  onSelect: (e?: React.MouseEvent) => void;
  onClick: () => void;
}

export function BlueprintCard({
  blueprint,
  selected,
  onSelect,
  onClick,
}: BlueprintCardProps) {
  const reviewMutation = useReviewBlueprint();

  const handleApprove = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      reviewMutation.mutate({
        id: blueprint.id,
        body: { action: "approved" },
      });
    },
    [blueprint.id, reviewMutation]
  );

  const handleReject = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      reviewMutation.mutate({
        id: blueprint.id,
        body: { action: "rejected" },
      });
    },
    [blueprint.id, reviewMutation]
  );

  const handleSelectClick = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      onSelect(e);
    },
    [onSelect]
  );

  const slides = blueprint.slide_previews;
  const thumbnail = Array.isArray(slides) ? slides[0]?.url : undefined;
  const videoUrl = blueprint.visual_paths ?? undefined;
  const isVideo = Boolean(videoUrl?.endsWith(".mp4") || videoUrl?.endsWith(".webm"));
  const priorityScore = blueprint.priority_score;
  const cost = blueprint.generation_cost_usd;
  const pps = blueprint.platform_publish_status;
  const [mediaError, setMediaError] = useState(false);

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
    >
      <Card
        className={cn(
          "group relative cursor-pointer transition-all duration-200",
          "hover:border-text-disabled hover:shadow-lg hover:shadow-black/20",
          selected && "border-indigo-500/60 ring-1 ring-indigo-500/30"
        )}
        onClick={onClick}
      >
        {/* Selection checkbox overlay */}
        <div
          className={cn(
            "absolute top-2 left-2 z-10 transition-opacity",
            selected ? "opacity-100" : "opacity-0 group-hover:opacity-100"
          )}
        >
          <button
            type="button"
            onClick={handleSelectClick}
            className={cn(
              "flex size-5 items-center justify-center rounded border transition-colors",
              selected
                ? "border-indigo-500 bg-indigo-500 text-white"
                : "border-text-disabled bg-bg-surface/80 backdrop-blur-sm hover:border-text-ghost"
            )}
          >
            {selected && <Check className="size-3" />}
          </button>
        </div>

        {/* Thumbnail — 3:4 crop, top-aligned so branding+video are visible, bottom black bar is cropped */}
        <div className="relative aspect-[3/4] overflow-hidden rounded-t-lg bg-black">
          {mediaError ? (
            <div className="flex size-full items-center justify-center bg-bg-elevated">
              <FileText className="size-10 text-text-disabled" />
            </div>
          ) : thumbnail && !isVideo ? (
            <img
              src={thumbnail}
              alt={blueprint.hook_text ?? "Blueprint preview"}
              className="size-full object-cover object-top"
              loading="lazy"
              onError={() => setMediaError(true)}
            />
          ) : isVideo ? (
            <video
              src={videoUrl}
              className="size-full object-cover object-top"
              preload="metadata"
              muted
              playsInline
              onError={() => setMediaError(true)}
            />
          ) : (
            <div className="flex size-full items-center justify-center bg-bg-elevated">
              <FileText className="size-10 text-text-disabled" />
            </div>
          )}
        </div>

        <CardContent className="p-3">
          {/* Hook text */}
          <p className="mb-2 line-clamp-2 text-sm font-medium text-text-primary leading-snug">
            {blueprint.hook_text ?? "Untitled blueprint"}
          </p>

          {/* Status + scheduled date */}
          <div className="mb-3 flex items-center gap-2">
            <StatusBadge status={blueprint.status} />
            {blueprint.action_taken?.startsWith("auto_archived_") && (
              <Badge variant="outline" className="text-xs text-muted-foreground border-gray-600/30" title={blueprint.action_taken}>
                {getArchiveReasonLabel(blueprint.action_taken)}
              </Badge>
            )}
            {blueprint.scheduled_for && (
              <span className="flex items-center gap-1 text-xs text-text-secondary">
                <Clock className="size-3" />
                {formatRelativeTime(blueprint.scheduled_for)}
              </span>
            )}
          </div>

          {/* Engagement metrics — only on published items with data */}
          {blueprint.status === "PUBLISHED" &&
           (blueprint.ig_reach || blueprint.yt_views) && (
            <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
              {blueprint.ig_reach != null && blueprint.ig_reach > 0 && (
                <span>
                  <span className="font-medium text-pink-400">ig</span>{" "}
                  {formatCompact(blueprint.ig_reach)} reach
                  {blueprint.ig_likes ? ` · ${formatCompact(blueprint.ig_likes)} likes` : ""}
                </span>
              )}
              {blueprint.yt_views != null && blueprint.yt_views > 0 && (
                <span>
                  <span className="font-medium text-red-400">yt</span>{" "}
                  {formatCompact(blueprint.yt_views)} views
                  {blueprint.yt_likes ? ` · ${formatCompact(blueprint.yt_likes)} likes` : ""}
                </span>
              )}
              {blueprint.engagement_rate != null && blueprint.engagement_rate > 0 && (
                <span className="font-medium text-emerald-400">
                  ER: {(blueprint.engagement_rate * 100).toFixed(1)}%
                </span>
              )}
            </div>
          )}

          {/* Bottom row: priority + cost + platforms + quick actions */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              {priorityScore != null && (
                <span className="flex items-center gap-1 rounded-md bg-bg-elevated px-1.5 py-0.5 text-xs font-medium text-text-secondary">
                  <Star className="size-3 text-amber-400" />
                  {priorityScore.toFixed(1)}
                </span>
              )}
              {cost != null && cost > 0 && (
                <span className="flex items-center gap-0.5 rounded-md bg-bg-elevated px-1.5 py-0.5 text-xs font-mono text-emerald-400">
                  <DollarSign className="size-2.5" />
                  {cost.toFixed(3)}
                </span>
              )}
              {pps && Object.keys(pps).length > 0 && (
                <span className="flex items-center gap-0.5 ml-0.5">
                  {pps.instagram && <Instagram className="size-3 text-pink-400" />}
                  {pps.facebook && <Facebook className="size-3 text-blue-400" />}
                  {pps.youtube && <Youtube className="size-3 text-red-400" />}
                  {pps.twitter && <Twitter className="size-3 text-sky-400" />}
                </span>
              )}
            </div>

            <DropdownMenu>
              <DropdownMenuTrigger asChild onClick={(e) => e.stopPropagation()}>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  className="opacity-0 group-hover:opacity-100 transition-opacity"
                >
                  <MoreVertical className="size-3.5" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-36">
                <DropdownMenuItem
                  onClick={handleApprove}
                  disabled={reviewMutation.isPending}
                >
                  <Check className="size-3.5 text-green-400" />
                  Approve
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={handleReject}
                  disabled={reviewMutation.isPending}
                >
                  <X className="size-3.5 text-red-400" />
                  Reject
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </CardContent>
      </Card>
    </motion.div>
  );
}
