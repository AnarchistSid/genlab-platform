import { useState } from "react";
import { Image, Youtube, Twitter, Facebook, MessageCircle, CheckCircle, XCircle, AlertCircle } from "lucide-react";
import { CarouselViewer } from "@/components/shared/carousel-viewer";
import { cn } from "@/lib/utils";
import { isVideoUrl } from "@/lib/media";
import { getNicheInfo } from "@/niches/registry";
import type { Blueprint } from "@/api/types";

function VideoWithFallback({ src, className, ...props }: React.ComponentProps<"video">) {
  const [error, setError] = useState(false);

  if (error || !src) {
    return (
      <div className={cn("flex items-center justify-center bg-bg-surface", className)}>
        <div className="text-center">
          <Image className="mx-auto size-8 text-text-disabled mb-2" />
          <p className="text-xs text-text-ghost">Video unavailable</p>
        </div>
      </div>
    );
  }

  return (
    <video
      src={src}
      onError={() => setError(true)}
      className={className}
      {...props}
    />
  );
}

function PlatformStatusBadge({ blueprint, platform }: { blueprint: Blueprint; platform: string }) {
  const pps = blueprint.platform_publish_status;
  if (!pps || !pps[platform]) return null;

  const status = pps[platform];
  const isPublished = status === "PUBLISHED" || status === "SUCCESS";
  const isFailed = status === "FAILED";

  return (
    <div className={cn(
      "flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium w-fit",
      isPublished && "bg-green-500/10 text-green-400",
      isFailed && "bg-red-500/10 text-red-400",
      !isPublished && !isFailed && "bg-yellow-500/10 text-yellow-400"
    )}>
      {isPublished ? <CheckCircle className="size-3" /> : isFailed ? <XCircle className="size-3" /> : <AlertCircle className="size-3" />}
      {status}
    </div>
  );
}

interface PlatformPreviewProps {
  blueprint: Blueprint;
  platform: "instagram" | "youtube" | "twitter" | "facebook" | "threads";
}

function InstagramPreview({ blueprint }: { blueprint: Blueprint }) {
  const nicheInfo = getNicheInfo(blueprint.niche_id ?? "");
  const slides = blueprint.slide_previews;
  const videoUrl = blueprint.visual_paths ?? undefined;
  const hashtags = blueprint.hashtags
    ? blueprint.hashtags.split(/\s+/).filter(Boolean)
    : [];

  // Detect if slides contain video URLs (not images)
  const firstSlideUrl = slides?.[0]?.url;
  const isVideo = isVideoUrl(firstSlideUrl);
  const hasImageSlides = slides && slides.length > 0 && !isVideo;
  const hasVideo = isVideo || Boolean(videoUrl);
  const effectiveVideoUrl = isVideo ? firstSlideUrl : videoUrl;

  return (
    <div className="space-y-3">
      <PlatformStatusBadge blueprint={blueprint} platform="instagram" />
      {/* Mock phone aspect ratio container */}
      <div className="mx-auto w-full max-w-[320px] overflow-hidden rounded-xl border border-bg-hover bg-bg-primary">
        {/* Phone header bar */}
        <div className="flex items-center gap-2 border-b border-bg-hover px-3 py-2">
          <div className="size-6 rounded-full bg-bg-hover" />
          <span className="text-xs font-semibold text-text-primary">
            {nicheInfo.handle.replace("@", "")}
          </span>
        </div>

        {/* Media: carousel slides or video reel */}
        <div className={cn(hasVideo ? "aspect-[9/16]" : "aspect-square", "bg-bg-surface")}>
          {hasImageSlides ? (
            <CarouselViewer
              images={slides!.map((s) => ({ url: s.url }))}
              className="h-full"
            />
          ) : hasVideo ? (
            <VideoWithFallback
              src={effectiveVideoUrl}
              controls
              className="size-full object-contain"
              preload="auto"
            />
          ) : (
            <div className="flex size-full items-center justify-center">
              <Image className="size-10 text-text-disabled" />
            </div>
          )}
        </div>

        {/* Caption area */}
        <div className="space-y-1.5 px-3 py-2.5">
          {blueprint.caption && (
            <p className="text-xs text-text-primary leading-relaxed line-clamp-4">
              <span className="font-semibold">{nicheInfo.handle.replace("@", "")}</span>{" "}
              {blueprint.caption}
            </p>
          )}
          {hashtags.length > 0 && (
            <p className="text-xs text-indigo-400">
              {hashtags.slice(0, 8).join(" ")}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function YouTubePreview({ blueprint }: { blueprint: Blueprint }) {
  const ytContent = blueprint.youtube_content;
  const videoUrl = blueprint.visual_paths ?? undefined;

  // Determine format: use publish_as from adapted content, then fall back to video_duration
  const publishAs = (ytContent?.publish_as as string) ?? "";
  const isShort = publishAs === "shorts" || publishAs === "short";
  // Community posts and regular videos use landscape; Shorts use portrait
  const duration = blueprint.video_duration ?? 0;
  const isLandscape = publishAs
    ? !isShort  // Trust the adaptation decision
    : duration > 180;  // Fallback: >3min = landscape
  const effectiveVideoUrl = isLandscape ? (blueprint.landscape_video_url ?? videoUrl) : videoUrl;

  if (!ytContent && !videoUrl && !effectiveVideoUrl) {
    return <EmptyPlatform platform="YouTube" icon={Youtube} />;
  }

  const communityPost = (ytContent?.community_post_text as string) ?? "";
  const shortsTitle = (ytContent?.shorts_title as string) ?? "";
  const title = (ytContent?.title as string) ?? "";
  const description = (ytContent?.description as string) ?? "";
  const displayText = communityPost || description;
  const videoLabel = isShort
    ? "YouTube Short"
    : publishAs === "community_post"
      ? "YouTube Community Post"
      : "YouTube Video";

  return (
    <div className="space-y-3">
      <PlatformStatusBadge blueprint={blueprint} platform="youtube" />

      {/* Video preview — landscape for community posts/videos, portrait for Shorts */}
      {effectiveVideoUrl && (
        <div className="mx-auto w-full max-w-[320px] overflow-hidden rounded-xl border border-bg-hover bg-bg-primary">
          <div className="flex items-center gap-2 border-b border-bg-hover px-3 py-2">
            <div className="flex size-6 items-center justify-center rounded-full bg-red-600/20">
              <Youtube className="size-3.5 text-red-400" />
            </div>
            <span className="text-xs font-semibold text-text-primary">
              {videoLabel}
            </span>
          </div>
          {shortsTitle && isShort && (
            <div className="px-3 py-1.5 border-b border-bg-hover">
              <p className="text-xs font-medium text-text-primary line-clamp-2">{shortsTitle}</p>
            </div>
          )}
          <div className={isLandscape ? "aspect-[16/9] bg-bg-surface" : "aspect-[9/16] bg-bg-surface"}>
            <VideoWithFallback
              src={effectiveVideoUrl}
              controls
              className="size-full object-contain"
              preload="auto"
            />
          </div>
        </div>
      )}

      {/* Community post text */}
      {displayText && (
        <div className="rounded-lg border border-bg-hover bg-bg-surface p-4">
          <div className="mb-3 flex items-center gap-2">
            <div className="flex size-8 items-center justify-center rounded-full bg-red-600/20">
              <Youtube className="size-4 text-red-400" />
            </div>
            <span className="text-xs font-medium text-text-secondary">
              Community Post Text
            </span>
          </div>

          {title && (
            <h4 className="mb-2 text-sm font-semibold text-text-primary">
              {title}
            </h4>
          )}
          <p className="text-xs text-text-secondary leading-relaxed whitespace-pre-wrap line-clamp-8">
            {displayText}
          </p>
        </div>
      )}

      {!effectiveVideoUrl && !title && !displayText && (
        <p className="text-xs text-text-ghost italic text-center py-4">
          No YouTube content available
        </p>
      )}
    </div>
  );
}

function TwitterPreview({ blueprint }: { blueprint: Blueprint }) {
  const nicheInfo = getNicheInfo(blueprint.niche_id ?? "");
  const twContent = blueprint.twitter_content;

  if (!twContent) {
    return <EmptyPlatform platform="X / Twitter" icon={Twitter} />;
  }

  const tweetText = (twContent.tweet_text as string) ?? (twContent.text as string) ?? (twContent.tweet as string) ?? "";
  const threadItems = Array.isArray(twContent.thread)
    ? (twContent.thread as Array<Record<string, unknown>>)
    : null;
  // Twitter uses landscape (16:9) video — prefer landscape_video_url over portrait visual_paths
  const videoUrl = blueprint.landscape_video_url ?? blueprint.visual_paths ?? undefined;

  return (
    <div className="space-y-3">
      <PlatformStatusBadge blueprint={blueprint} platform="twitter" />
      {/* Main tweet */}
      {tweetText && (
        <div className="rounded-lg border border-bg-hover bg-bg-surface p-4">
          <div className="mb-3 flex items-center gap-2">
            <div className="flex size-8 items-center justify-center rounded-full bg-bg-elevated">
              <Twitter className="size-4 text-text-primary" />
            </div>
            <div>
              <span className="text-xs font-semibold text-text-primary">
                {nicheInfo.label}
              </span>
              <span className="ml-1 text-xs text-text-ghost">{nicheInfo.handle}</span>
            </div>
          </div>
          <p className="text-sm text-text-primary leading-relaxed whitespace-pre-wrap">
            {tweetText}
          </p>
          {/* Landscape video preview for X/Twitter */}
          {videoUrl && (
            <div className="mt-3 overflow-hidden rounded-xl border border-bg-hover">
              <VideoWithFallback
                src={videoUrl}
                controls
                className="w-full bg-bg-primary"
                preload="auto"
              />
            </div>
          )}
        </div>
      )}

      {/* Thread items */}
      {threadItems && threadItems.length > 0 && (
        <div className="space-y-0">
          {threadItems.map((item, idx) => {
            const itemText = (item.text as string) ?? "";
            return (
              <div
                key={idx}
                className={cn(
                  "border border-bg-hover bg-bg-surface p-4",
                  idx === 0 && "rounded-t-lg",
                  idx === threadItems.length - 1 && "rounded-b-lg",
                  idx > 0 && "border-t-0"
                )}
              >
                <div className="mb-2 flex items-center gap-2">
                  <div className="flex h-4 justify-center">
                    <div className="w-0.5 bg-bg-hover" />
                  </div>
                  <span className="text-xs text-text-ghost">
                    {idx + 1}/{threadItems.length}
                  </span>
                </div>
                <p className="text-sm text-text-primary leading-relaxed whitespace-pre-wrap pl-5">
                  {itemText}
                </p>
              </div>
            );
          })}
        </div>
      )}

      {!tweetText && !threadItems && (
        <EmptyPlatform platform="X / Twitter" icon={Twitter} />
      )}
    </div>
  );
}

function EmptyPlatform({
  platform,
  icon: Icon,
}: {
  platform: string;
  icon: typeof Youtube;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-bg-hover py-10">
      <Icon className="mb-2 size-8 text-text-disabled" />
      <p className="text-sm text-text-ghost">
        No {platform} content yet
      </p>
      <p className="text-xs text-text-disabled mt-1">
        Content is generated when the pipeline runs adapt_for_platforms
      </p>
    </div>
  );
}

function FacebookPreview({ blueprint }: { blueprint: Blueprint }) {
  const nicheInfo = getNicheInfo(blueprint.niche_id ?? "");
  const fbContent = blueprint.facebook_content;
  // facebook_content is stored as a string, not an object with sub-fields
  const caption = (typeof fbContent === "string" ? fbContent : (fbContent?.facebook_caption as string)) ?? blueprint.caption ?? "";
  const fbHashtags = (fbContent?.facebook_hashtags as string) ?? "";
  const displayText = caption || blueprint.hook_text || "";
  // Facebook feeds display landscape 16:9 video best — prefer landscape_video_url
  const videoUrl = blueprint.landscape_video_url ?? blueprint.visual_paths ?? undefined;
  const slides = blueprint.slide_previews;
  const isVideo = isVideoUrl(slides?.[0]?.url);
  const hasImageSlides = slides && slides.length > 0 && !isVideo;
  const hasVideo = isVideo || Boolean(videoUrl);
  const effectiveVideoUrl = videoUrl ?? (isVideo ? slides![0].url : undefined);

  if (!displayText && !hasVideo && !hasImageSlides) {
    return <EmptyPlatform platform="Facebook" icon={Facebook} />;
  }

  return (
    <div className="space-y-3">
      <PlatformStatusBadge blueprint={blueprint} platform="facebook" />
      <div className="rounded-lg border border-bg-hover bg-bg-surface overflow-hidden">
        <div className="flex items-center gap-2 p-3 pb-2">
          <div className="flex size-8 items-center justify-center rounded-full bg-blue-600/20">
            <Facebook className="size-4 text-blue-400" />
          </div>
          <div>
            <span className="text-xs font-semibold text-text-primary">
              {nicheInfo.label}
            </span>
            <p className="text-[10px] text-text-ghost">Facebook Page Post</p>
          </div>
        </div>

        {displayText && (
          <p className="text-sm text-text-primary leading-relaxed whitespace-pre-wrap line-clamp-6 px-3 pb-2">
            {displayText}
          </p>
        )}

        {hasVideo ? (
          <div className="aspect-[16/9] bg-bg-primary">
            <VideoWithFallback
              src={effectiveVideoUrl}
              controls
              className="size-full object-contain"
              preload="auto"
            />
          </div>
        ) : hasImageSlides ? (
          <CarouselViewer
            images={slides!.map((s) => ({ url: s.url }))}
            className="w-full"
          />
        ) : null}

        {fbHashtags && (
          <p className="text-xs text-blue-400 px-3 py-1.5">
            {fbHashtags}
          </p>
        )}
        {blueprint.cta && (
          <p className="text-xs text-blue-400 px-3 py-2">
            {blueprint.cta}
          </p>
        )}
      </div>
    </div>
  );
}

function ThreadsPreview({ blueprint }: { blueprint: Blueprint }) {
  const nicheInfo = getNicheInfo(blueprint.niche_id ?? "");
  const caption = blueprint.caption ?? blueprint.hook_text ?? "";

  if (!caption) {
    return <EmptyPlatform platform="Threads" icon={MessageCircle} />;
  }

  return (
    <div className="space-y-3">
      <PlatformStatusBadge blueprint={blueprint} platform="threads" />
      <div className="rounded-lg border border-bg-hover bg-bg-surface p-4">
        <div className="mb-3 flex items-center gap-2">
          <div className="flex size-8 items-center justify-center rounded-full bg-bg-elevated">
            <MessageCircle className="size-4 text-text-primary" />
          </div>
          <div>
            <span className="text-xs font-semibold text-text-primary">
              {nicheInfo.label}
            </span>
            <span className="ml-1 text-xs text-text-ghost">{nicheInfo.handle}</span>
          </div>
        </div>
        <p className="text-sm text-text-primary leading-relaxed whitespace-pre-wrap line-clamp-6">
          {caption}
        </p>
      </div>
    </div>
  );
}

export function PlatformPreview({ blueprint, platform }: PlatformPreviewProps) {
  switch (platform) {
    case "instagram":
      return <InstagramPreview blueprint={blueprint} />;
    case "youtube":
      return <YouTubePreview blueprint={blueprint} />;
    case "twitter":
      return <TwitterPreview blueprint={blueprint} />;
    case "facebook":
      return <FacebookPreview blueprint={blueprint} />;
    case "threads":
      return <ThreadsPreview blueprint={blueprint} />;
  }
}
