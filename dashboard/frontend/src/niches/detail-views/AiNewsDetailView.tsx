import { useState } from "react";
import { Clock, Zap } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { DetailViewProps } from "@/niches/registry";
import { PlatformAdaptationsPanel } from "@/components/review/PlatformAdaptationsPanel";

export function AiNewsDetailView({ item }: DetailViewProps) {
  const [captionExpanded, setCaptionExpanded] = useState(false);

  const captionText = item.caption ?? "";
  const needsTruncation = captionText.length > 250;
  const displayCaption =
    captionExpanded || !needsTruncation
      ? captionText
      : captionText.slice(0, 250) + "...";

  const hashtagList = item.hashtags
    ? item.hashtags.split(/[\s,]+/).filter((h) => h.length > 0).map((h) => (h.startsWith("#") ? h : `#${h}`))
    : [];

  return (
    <div className="flex flex-col gap-4 overflow-y-auto pr-2">
      {/* Hook — large and bold */}
      {item.hook_text && (
        <div>
          <h3 className="mb-1 text-xs font-medium uppercase tracking-wider text-text-muted">
            Hook
          </h3>
          <p className="text-xl font-bold leading-snug text-text-primary">
            {item.hook_text}
          </p>
        </div>
      )}

      {/* Priority + Schedule row */}
      <div className="flex items-center gap-3">
        {typeof item.priority_score === "number" && (
          <div
            className="flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
            style={{
              background: "color-mix(in srgb, var(--niche-current) 15%, transparent)",
              color: "var(--niche-current)",
            }}
          >
            <Zap className="size-3" />
            {item.priority_score.toFixed(2)}
          </div>
        )}
        {item.scheduled_for && (
          <div
            className="flex items-center gap-1.5 text-xs text-text-muted"
          >
            <Clock className="size-3" />
            {new Date(item.scheduled_for).toLocaleString()}
          </div>
        )}
        {item.template_id && (
          <span className="text-xs font-mono text-text-muted">
            {item.template_id}
          </span>
        )}
      </div>

      {/* Caption */}
      {captionText && (
        <div>
          <h3 className="mb-1 text-xs font-medium uppercase tracking-wider text-text-muted">
            Caption
          </h3>
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-text-secondary">
            {displayCaption}
          </p>
          {needsTruncation && (
            <button
              type="button"
              className="mt-1 text-xs hover:underline"
              style={{ color: "var(--niche-current)" }}
              onClick={() => setCaptionExpanded((v) => !v)}
            >
              {captionExpanded ? "Show less" : "Show more"}
            </button>
          )}
        </div>
      )}

      {/* CTA */}
      {item.cta && (
        <div>
          <h3 className="mb-1 text-xs font-medium uppercase tracking-wider text-text-muted">
            CTA
          </h3>
          <p className="text-sm text-text-secondary">{item.cta}</p>
        </div>
      )}

      {/* Hashtags */}
      {hashtagList.length > 0 && (
        <div>
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wider text-text-muted">
            Hashtags
          </h3>
          <div className="flex flex-wrap gap-1.5">
            {hashtagList.map((tag) => (
              <Badge
                key={tag}
                variant="outline"
                className="text-xs border-border bg-bg-elevated text-text-muted"
              >
                {tag}
              </Badge>
            ))}
          </div>
        </div>
      )}

      {/* Platform adaptations — tabbed panel */}
      <PlatformAdaptationsPanel item={item} />

      {/* Video duration */}
      {item.video_duration && (
        <div className="text-xs text-text-muted">
          Duration: {Math.round(item.video_duration)}s
        </div>
      )}
    </div>
  );
}
