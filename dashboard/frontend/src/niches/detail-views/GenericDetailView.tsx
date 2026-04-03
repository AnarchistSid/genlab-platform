import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import type { DetailViewProps } from "@/niches/registry";
import { PlatformAdaptationsPanel } from "@/components/review/PlatformAdaptationsPanel";

export function GenericDetailView({ item }: DetailViewProps) {
  const [captionExpanded, setCaptionExpanded] = useState(false);

  const captionText = item.caption ?? "";
  const needsTruncation = captionText.length > 200;
  const displayCaption =
    captionExpanded || !needsTruncation
      ? captionText
      : captionText.slice(0, 200) + "...";

  const hashtagList = item.hashtags
    ? item.hashtags
        .split(/[\s,]+/)
        .filter((h) => h.length > 0)
        .map((h) => (h.startsWith("#") ? h : `#${h}`))
    : [];

  return (
    <div className="flex flex-col gap-5 overflow-y-auto pr-2">
      {item.hook_text && (
        <div>
          <h3 className="mb-1 text-xs font-medium uppercase tracking-wider text-text-muted">
            Hook
          </h3>
          <p className="text-lg font-bold leading-snug text-text-primary">
            {item.hook_text}
          </p>
        </div>
      )}

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

      <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
        {item.template_id && (
          <div>
            <span className="text-text-muted">Template: </span>
            <span className="text-text-primary">{item.template_id}</span>
          </div>
        )}
        {typeof item.priority_score === "number" && (
          <div>
            <span className="text-text-muted">Priority: </span>
            <span className="text-text-primary">{item.priority_score.toFixed(2)}</span>
          </div>
        )}
        {item.scheduled_for && (
          <div>
            <span className="text-text-muted">Scheduled: </span>
            <span className="text-text-primary">{new Date(item.scheduled_for).toLocaleString()}</span>
          </div>
        )}
      </div>

      {/* Platform adaptations — tabbed panel */}
      <PlatformAdaptationsPanel item={item} />
    </div>
  );
}
