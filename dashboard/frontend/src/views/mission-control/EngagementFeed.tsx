import { MessageCircle } from "lucide-react";
import { useRecentComments } from "@/hooks/use-engagement";
import { PlatformIcon } from "@/components/shared/PlatformIcon";
import { relativeTime } from "@/lib/format";
import type { EngagementComment } from "@/api/types";

export function EngagementFeed() {
  const resp = useRecentComments();

  // Handle both shapes: direct array or { comments: [...] }
  const rawData = resp.data;
  const wrapped = rawData as { comments?: EngagementComment[] } | undefined;
  const comments: EngagementComment[] = Array.isArray(rawData)
    ? rawData
    : (wrapped?.comments ?? []);

  const displayComments = comments.slice(0, 5);
  const totalCount = comments.length;

  return (
    <div className="bento-card">
      <h3 className="card-title">
        <MessageCircle size={12} style={{ marginRight: 4, verticalAlign: "middle" }} />
        Recent Comments
      </h3>

      {displayComments.length === 0 ? (
        <div className="py-6 text-center text-text-muted text-sm leading-normal">
          No comments yet — engagement typically appears 2-6 hours after publishing
        </div>
      ) : (
        <>
          <div className="flex flex-col gap-2">
            {displayComments.map((comment) => (
              <div key={comment.comment_id} className="p-2 rounded-md bg-bg-elevated">
                <div className="flex items-center gap-1.5 mb-1">
                  <span className="text-[10px] font-mono text-text-muted min-w-6">{relativeTime(comment.timestamp)}</span>
                  <PlatformIcon platform={comment.platform} size={11} />
                  <span className="text-xs font-medium text-text-secondary flex-1 truncate">@{comment.username}</span>
                </div>
                <p className="text-xs text-text-muted leading-snug">
                  {comment.text.length > 80
                    ? comment.text.slice(0, 80) + "..."
                    : comment.text}
                </p>
              </div>
            ))}
          </div>

          {totalCount > 0 && (
            <div className="text-xs text-text-ghost text-center mt-3 pt-2 border-t border-border-subtle">
              {totalCount} comments total
            </div>
          )}
        </>
      )}
    </div>
  );
}
