import { useState, useCallback } from "react";
import { motion } from "framer-motion";
import {
  X,
  ChevronDown,
  ChevronUp,
  Image,
  Hash,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StatusBadge } from "@/components/shared/status-badge";
import { CarouselViewer } from "@/components/shared/carousel-viewer";
import { ReviewActions } from "@/components/blueprints/review-actions";
import { PlatformPreview } from "@/components/blueprints/platform-preview";
import { ScoringExplainer } from "@/components/review/scoring-explainer";
import { cn } from "@/lib/utils";
import { isVideoUrl } from "@/lib/media";
import { formatRelativeTime } from "@/lib/format";
import { useBlueprint } from "@/hooks/use-blueprints";

interface BlueprintDetailProps {
  blueprintId: string;
  onClose: () => void;
}

function MetadataRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-text-ghost">{label}</span>
      <span className="text-xs text-text-secondary break-all">{value}</span>
    </div>
  );
}

function formatMetric(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toString();
}

function DetailSkeleton() {
  return (
    <div className="space-y-4 p-4">
      <Skeleton className="h-6 w-48" />
      <Skeleton className="aspect-square w-full rounded-lg" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-3/4" />
      <Skeleton className="h-20 w-full rounded-lg" />
    </div>
  );
}

export function BlueprintDetail({ blueprintId, onClose }: BlueprintDetailProps) {
  const { data: response, isLoading } = useBlueprint(blueprintId);
  const blueprint = response?.data;

  // State resets automatically via key={blueprintId} on the parent
  const [captionExpanded, setCaptionExpanded] = useState(false);
  const [videoError, setVideoError] = useState(false);

  const toggleCaption = useCallback(() => {
    setCaptionExpanded((prev) => !prev);
  }, []);

  const hashtags = blueprint?.hashtags
    ? blueprint.hashtags.split(/\s+/).filter(Boolean)
    : [];

  const slides = Array.isArray(blueprint?.slide_previews) ? blueprint.slide_previews : undefined;
  const videoUrl = blueprint?.visual_paths ?? undefined;
  const hasVideo = Boolean(videoUrl && isVideoUrl(videoUrl));
  // When visual_paths is an HTTP image URL (e.g. Steam/YouTube CDN), show it as an image
  const hasImagePreview = Boolean(videoUrl?.startsWith("http") && !isVideoUrl(videoUrl));
  const hasSlides = slides && slides.length > 0 && !isVideoUrl(slides[0]?.url);

  const captionText = blueprint?.caption ?? "";
  const isCaptionLong = captionText.length > 200;

  return (
    <>
      {/* Mobile backdrop */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm lg:hidden"
        onClick={onClose}
      />

      {/* Panel */}
      <motion.div
        initial={{ x: "100%" }}
        animate={{ x: 0 }}
        exit={{ x: "100%" }}
        transition={{ type: "spring", damping: 25, stiffness: 200 }}
        className={cn(
          "fixed right-0 top-0 z-50 flex h-full flex-col border-l border-bg-hover bg-bg-primary",
          "w-full sm:w-[420px] lg:w-[480px]"
        )}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-bg-hover px-4 py-3">
          <div className="flex items-center gap-2 min-w-0">
            <Button variant="ghost" size="icon-xs" onClick={onClose}>
              <X className="size-4" />
            </Button>
            {blueprint && (
              <>
                <span className="truncate text-xs font-mono text-text-ghost">
                  {blueprint.id.slice(0, 12)}...
                </span>
                <StatusBadge status={blueprint.status} />
              </>
            )}
          </div>
        </div>

        {/* Scrollable content */}
        <ScrollArea className="flex-1 overflow-hidden">
          {isLoading ? (
            <DetailSkeleton />
          ) : blueprint ? (
            <div className="space-y-5 p-4">
              {/* Media section */}
              <div>
                {hasSlides ? (
                  <CarouselViewer
                    images={slides!.map((s) => ({ url: s.url }))}
                    className="rounded-lg"
                  />
                ) : hasImagePreview ? (
                  <img
                    src={videoUrl}
                    alt={blueprint?.hook_text ?? "Preview"}
                    className="w-full rounded-lg bg-bg-surface object-cover"
                    onError={() => setVideoError(true)}
                  />
                ) : hasVideo && !videoError ? (
                  <video
                    src={videoUrl}
                    controls
                    className="w-full rounded-lg bg-bg-surface"
                    preload="auto"
                    onError={() => setVideoError(true)}
                  />
                ) : hasVideo && videoError ? (
                  <div className="flex aspect-video items-center justify-center rounded-lg bg-bg-surface border border-bg-hover">
                    <div className="text-center">
                      <Image className="mx-auto size-8 text-text-disabled mb-2" />
                      <p className="text-xs text-text-ghost">Video unavailable</p>
                    </div>
                  </div>
                ) : (
                  <div className="flex aspect-video items-center justify-center rounded-lg bg-bg-surface border border-bg-hover">
                    <Image className="size-10 text-text-disabled" />
                  </div>
                )}
              </div>

              {/* Content section */}
              <div className="space-y-3">
                {/* Hook text */}
                {blueprint.hook_text && (
                  <p className="text-sm font-semibold text-text-primary leading-snug">
                    {blueprint.hook_text}
                  </p>
                )}

                {/* Caption */}
                {captionText && (
                  <div>
                    <p
                      className={cn(
                        "text-sm text-text-secondary leading-relaxed whitespace-pre-wrap",
                        !captionExpanded && isCaptionLong && "line-clamp-4"
                      )}
                    >
                      {captionText}
                    </p>
                    {isCaptionLong && (
                      <button
                        type="button"
                        onClick={toggleCaption}
                        className="mt-1 flex items-center gap-0.5 text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
                      >
                        {captionExpanded ? (
                          <>
                            <ChevronUp className="size-3" /> Show less
                          </>
                        ) : (
                          <>
                            <ChevronDown className="size-3" /> Show more
                          </>
                        )}
                      </button>
                    )}
                  </div>
                )}

                {/* Hashtags */}
                {hashtags.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {hashtags.map((tag) => (
                      <Badge
                        key={tag}
                        variant="outline"
                        className="border-bg-hover text-text-secondary text-[10px] px-1.5 py-0"
                      >
                        <Hash className="mr-0.5 size-2.5" />
                        {tag.replace(/^#/, "")}
                      </Badge>
                    ))}
                  </div>
                )}
              </div>

              <Separator className="bg-bg-hover" />

              {/* Platform preview tabs */}
              <div>
                <Tabs defaultValue="instagram">
                  <TabsList variant="line" className="w-full">
                    <TabsTrigger value="instagram">Instagram</TabsTrigger>
                    <TabsTrigger value="facebook">Facebook</TabsTrigger>
                    <TabsTrigger value="youtube">YouTube</TabsTrigger>
                    <TabsTrigger value="twitter">Twitter</TabsTrigger>
                    <TabsTrigger value="threads">Threads</TabsTrigger>
                  </TabsList>
                  <TabsContent value="instagram" className="mt-3">
                    <PlatformPreview
                      blueprint={blueprint}
                      platform="instagram"
                    />
                  </TabsContent>
                  <TabsContent value="facebook" className="mt-3">
                    <PlatformPreview
                      blueprint={blueprint}
                      platform="facebook"
                    />
                  </TabsContent>
                  <TabsContent value="youtube" className="mt-3">
                    <PlatformPreview
                      blueprint={blueprint}
                      platform="youtube"
                    />
                  </TabsContent>
                  <TabsContent value="twitter" className="mt-3">
                    <PlatformPreview
                      blueprint={blueprint}
                      platform="twitter"
                    />
                  </TabsContent>
                  <TabsContent value="threads" className="mt-3">
                    <PlatformPreview
                      blueprint={blueprint}
                      platform="threads"
                    />
                  </TabsContent>
                </Tabs>
              </div>

              <Separator className="bg-bg-hover" />

              {/* Review actions */}
              <div>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-text-ghost">
                  Review
                </h3>
                <ReviewActions
                  blueprintId={blueprint.id}
                  onComplete={onClose}
                />
              </div>

              <Separator className="bg-bg-hover" />

              {/* Auto-approval gate breakdown (D2.6, AUTO #2 runbook).
                  Collapsed by default so it doesn't compete with the
                  primary metadata grid; expand to see why the gate
                  would approve / reject this specific blueprint. */}
              <div>
                <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-text-ghost">
                  Gate Verdict
                </h3>
                <ScoringExplainer blueprintId={blueprint.id} />
              </div>

              <Separator className="bg-bg-hover" />

              {/* Metadata grid */}
              <div>
                <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-text-ghost">
                  Metadata
                </h3>
                <div className="grid grid-cols-2 gap-3">
                  {blueprint.template_id && (
                    <MetadataRow
                      label="Template"
                      value={blueprint.template_id}
                    />
                  )}
                  {blueprint.story_id && (
                    <MetadataRow
                      label="Story ID"
                      value={blueprint.story_id}
                    />
                  )}
                  {blueprint.priority_score != null && (
                    <MetadataRow
                      label="Priority"
                      value={blueprint.priority_score.toFixed(2)}
                    />
                  )}
                  {blueprint.generation_cost_usd != null && blueprint.generation_cost_usd > 0 && (
                    <MetadataRow
                      label="Generation Cost"
                      value={`$${blueprint.generation_cost_usd.toFixed(4)}`}
                    />
                  )}
                  {blueprint.format && (
                    <MetadataRow
                      label="Format"
                      value={blueprint.format}
                    />
                  )}
                  {blueprint.scheduled_for && (
                    <MetadataRow
                      label="Scheduled For"
                      value={formatRelativeTime(blueprint.scheduled_for)}
                    />
                  )}
                  {blueprint.created_at && (
                    <MetadataRow
                      label="Created"
                      value={formatRelativeTime(blueprint.created_at)}
                    />
                  )}
                  {blueprint.reviewed_at && (
                    <MetadataRow
                      label="Reviewed"
                      value={formatRelativeTime(blueprint.reviewed_at)}
                    />
                  )}
                  {blueprint.action_taken && (
                    <MetadataRow
                      label="Last Action"
                      value={blueprint.action_taken}
                    />
                  )}
                  {blueprint.feedback_issue && (
                    <MetadataRow
                      label="Issue"
                      value={blueprint.feedback_issue}
                    />
                  )}
                </div>
              </div>

              {/* Engagement metrics — only for published blueprints with data */}
              {blueprint.status === "PUBLISHED" &&
               (blueprint.ig_reach || blueprint.yt_views) && (
                <>
                  <Separator className="bg-bg-hover" />
                  <div>
                    <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-text-ghost">
                      Engagement
                    </h3>
                    <div className="grid grid-cols-2 gap-3">
                      {blueprint.ig_reach != null && blueprint.ig_reach > 0 && (
                        <MetadataRow label="IG Reach" value={formatMetric(blueprint.ig_reach)} />
                      )}
                      {blueprint.ig_likes != null && (
                        <MetadataRow label="IG Likes" value={formatMetric(blueprint.ig_likes)} />
                      )}
                      {blueprint.ig_comments != null && (
                        <MetadataRow label="IG Comments" value={formatMetric(blueprint.ig_comments)} />
                      )}
                      {blueprint.yt_views != null && blueprint.yt_views > 0 && (
                        <MetadataRow label="YT Views" value={formatMetric(blueprint.yt_views)} />
                      )}
                      {blueprint.yt_likes != null && (
                        <MetadataRow label="YT Likes" value={formatMetric(blueprint.yt_likes)} />
                      )}
                      {blueprint.yt_comments != null && (
                        <MetadataRow label="YT Comments" value={formatMetric(blueprint.yt_comments)} />
                      )}
                      {blueprint.engagement_rate != null && blueprint.engagement_rate > 0 && (
                        <MetadataRow
                          label="Engagement Rate"
                          value={`${(blueprint.engagement_rate * 100).toFixed(1)}%`}
                        />
                      )}
                      {blueprint.insights_collected_at && (
                        <MetadataRow
                          label="Insights Collected"
                          value={formatRelativeTime(blueprint.insights_collected_at)}
                        />
                      )}
                    </div>
                  </div>
                </>
              )}
            </div>
          ) : (
            <div className="flex items-center justify-center py-20">
              <p className="text-sm text-text-ghost">Blueprint not found</p>
            </div>
          )}
        </ScrollArea>
      </motion.div>
    </>
  );
}
