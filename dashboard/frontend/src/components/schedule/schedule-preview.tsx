import { useState } from "react";
import { X, Film, Calendar, ExternalLink, CalendarX, Archive } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StatusBadge } from "@/components/shared/status-badge";
import { PlatformPreview } from "@/components/blueprints/platform-preview";
import { useUnscheduleItem, useArchiveItem } from "@/hooks/use-publishing-queue";
import { getThumbnailInfo } from "@/lib/format";
import { getNicheInfo } from "@/niches/registry";
import type { Blueprint } from "@/api/types";

interface SchedulePreviewProps {
  blueprint: Blueprint | null;
  onClose: () => void;
}

export function SchedulePreview({ blueprint, onClose }: SchedulePreviewProps) {
  const [videoError, setVideoError] = useState(false);

  if (!blueprint) return null;

  const thumb = getThumbnailInfo(blueprint);
  const nicheInfo = getNicheInfo(blueprint.niche_id ?? "");
  const niche = { label: nicheInfo.label, color: nicheInfo.hex };
  const hook = blueprint.hook_text || blueprint.title || "Untitled";
  const scheduled = blueprint.scheduled_for
    ? new Date(blueprint.scheduled_for).toLocaleDateString("en-US", {
        weekday: "short",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })
    : "Not scheduled";

  return (
    <div className="fixed right-0 top-0 z-40 h-full flex justify-end" style={{ pointerEvents: "auto" }}>
      {/* Panel — side drawer that doesn't block the schedule grid */}
      <div
        className="w-full max-w-md overflow-y-auto border-l border-border bg-bg-primary shadow-2xl animate-in slide-in-from-right duration-200"
      >
        {/* Header */}
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-bg-primary/95 backdrop-blur px-4 py-3">
          <div className="flex items-center gap-2">
            <div
              className="rounded px-2 py-0.5 text-[11px] font-bold text-white"
              style={{ backgroundColor: niche.color }}
            >
              {niche.label}
            </div>
            <StatusBadge status={blueprint.status} />
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} className="size-7" aria-label="Close preview">
            <X className="size-4" />
          </Button>
        </div>

        {/* Video preview */}
        <div className="aspect-[9/16] w-full bg-black">
          {thumb && !videoError ? (
            thumb.isVideo ? (
              <video
                src={thumb.url}
                controls
                preload="auto"
                className="size-full object-contain"
                onError={() => setVideoError(true)}
              />
            ) : (
              <img
                src={thumb.url}
                alt=""
                className="size-full object-contain"
                onError={() => setVideoError(true)}
              />
            )
          ) : (
            <div className="flex size-full items-center justify-center">
              <Film className="size-12 text-text-disabled" />
            </div>
          )}
        </div>

        {/* Content details */}
        <div className="space-y-4 p-4">
          {/* Hook */}
          <div>
            <h3 className="text-sm font-bold text-text-primary leading-snug">
              {hook}
            </h3>
          </div>

          {/* Meta row */}
          <div className="flex flex-wrap items-center gap-3 text-[11px] text-text-muted">
            <span className="inline-flex items-center gap-1">
              <Calendar className="size-3" />
              {scheduled}
            </span>
            {blueprint.format && (
              <span className="inline-flex items-center gap-1">
                <Film className="size-3" />
                {blueprint.format}
                {blueprint.video_duration ? ` · ${Math.round(blueprint.video_duration)}s` : ""}
              </span>
            )}
            {blueprint.video_url && (
              <a
                href={blueprint.video_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-indigo-400 hover:text-indigo-300"
                onClick={(e) => e.stopPropagation()}
              >
                <ExternalLink className="size-3" />
                Source
              </a>
            )}
          </div>

          {/* Platform content tabs */}
          <Tabs defaultValue="instagram">
            <TabsList variant="line" className="w-full">
              <TabsTrigger value="instagram" className="text-[11px]">IG</TabsTrigger>
              <TabsTrigger value="youtube" className="text-[11px]">YT</TabsTrigger>
              <TabsTrigger value="facebook" className="text-[11px]">FB</TabsTrigger>
              <TabsTrigger value="twitter" className="text-[11px]">X</TabsTrigger>
              <TabsTrigger value="threads" className="text-[11px]">TH</TabsTrigger>
            </TabsList>
            <TabsContent value="instagram" className="mt-2">
              <PlatformPreview blueprint={blueprint} platform="instagram" />
            </TabsContent>
            <TabsContent value="youtube" className="mt-2">
              <PlatformPreview blueprint={blueprint} platform="youtube" />
            </TabsContent>
            <TabsContent value="facebook" className="mt-2">
              <PlatformPreview blueprint={blueprint} platform="facebook" />
            </TabsContent>
            <TabsContent value="twitter" className="mt-2">
              <PlatformPreview blueprint={blueprint} platform="twitter" />
            </TabsContent>
            <TabsContent value="threads" className="mt-2">
              <PlatformPreview blueprint={blueprint} platform="threads" />
            </TabsContent>
          </Tabs>

          {/* Hashtags */}
          {blueprint.hashtags && (
            <div className="flex flex-wrap gap-1">
              {String(blueprint.hashtags).split(/\s+/).filter(Boolean).map((tag, i) => (
                <span
                  key={i}
                  className="rounded-full bg-bg-elevated px-2 py-0.5 text-[10px] text-text-muted"
                >
                  {tag.startsWith("#") ? tag : `#${tag}`}
                </span>
              ))}
            </div>
          )}

          {/* Schedule actions */}
          <ScheduleActions blueprint={blueprint} onClose={onClose} />
        </div>
      </div>
    </div>
  );
}

function ScheduleActions({ blueprint, onClose }: { blueprint: Blueprint; onClose: () => void }) {
  const unschedule = useUnscheduleItem();
  const archive = useArchiveItem();

  if (blueprint.status === "PUBLISHED") return null;

  return (
    <div className="flex gap-2 pt-2 border-t border-border">
      <Button
        variant="outline"
        size="sm"
        className="flex-1 gap-1.5 text-amber-400 border-amber-500/30 hover:bg-amber-500/10"
        disabled={unschedule.isPending}
        onClick={() => {
          unschedule.mutate(blueprint.id);
          onClose();
        }}
      >
        <CalendarX className="size-3.5" />
        Unschedule
      </Button>
      <Button
        variant="outline"
        size="sm"
        className="flex-1 gap-1.5 text-red-400 border-red-500/30 hover:bg-red-500/10"
        disabled={archive.isPending}
        onClick={() => {
          archive.mutate(blueprint.id);
          onClose();
        }}
      >
        <Archive className="size-3.5" />
        Archive
      </Button>
    </div>
  );
}
