import { useQuery } from "@tanstack/react-query";
import { Heart, MessageCircle, Eye } from "lucide-react";
import { analytics } from "@/api/client";
import { PlatformIcon } from "@/components/shared/PlatformIcon";
import { formatCompact } from "@/lib/format";
import { getNicheInfo } from "@/niches/registry";
import type { TopPost } from "@/api/types";

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function TopPostSpotlight() {
  const { data: postsResponse } = useQuery({
    queryKey: ["analytics", "top-posts"],
    queryFn: () => analytics.topPosts(),
    staleTime: 5 * 60_000,
  });

  // The API may return { posts: TopPost[] } or TopPost[] directly
  const posts: TopPost[] | undefined = Array.isArray(postsResponse)
    ? postsResponse
    : (postsResponse as unknown as { posts: TopPost[] })?.posts;

  if (!posts || posts.length === 0) {
    return (
      <div className="bento-card">
        <h3 className="card-title">Top Post</h3>
        <p className="text-sm text-text-muted py-4">No engagement data yet — metrics appear after 6h</p>
      </div>
    );
  }

  // Find the top post by reach
  const topPost = posts.reduce((best, p) => (p.reach > best.reach ? p : best), posts[0]);

  const hookText = topPost.hook_text || topPost.title || "Untitled";
  const nicheInfo = getNicheInfo(topPost.niche_id);
  const channel = nicheInfo.handle || topPost.niche_id;
  const accentColor = nicheInfo.hex;

  return (
    <div className="bento-card overflow-hidden">
      <h3 className="card-title">Top Post</h3>

      <div className="flex gap-4 items-start">
        {/* Thumbnail placeholder */}
        <div
          className="size-16 rounded-lg border flex items-center justify-center shrink-0"
          style={{
            background: `linear-gradient(135deg, ${accentColor}22, ${accentColor}08)`,
            borderColor: `${accentColor}33`,
          }}
        >
          <Eye size={20} style={{ color: accentColor, opacity: 0.5 }} />
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <p className="text-base font-semibold text-text-primary mb-1 leading-snug">{hookText}</p>
          <p className="text-xs text-text-muted flex items-center gap-1.5 mb-2">
            <span style={{ color: accentColor }}>{channel}</span>
            <span className="text-text-ghost">&middot;</span>
            <PlatformIcon platform={topPost.platform} size={12} />
            <span className="text-text-ghost">&middot;</span>
            <span>{formatDate(topPost.collected_at)}</span>
          </p>

          <div className="flex gap-4">
            <span className="inline-flex items-center gap-1 text-sm font-medium text-text-secondary tabular-nums">
              <Heart size={12} style={{ color: accentColor }} />
              {formatCompact(topPost.likes)}
            </span>
            <span className="inline-flex items-center gap-1 text-sm font-medium text-text-secondary tabular-nums">
              <MessageCircle size={12} />
              {formatCompact(topPost.comments)}
            </span>
            <span className="inline-flex items-center gap-1 text-sm font-medium text-text-secondary tabular-nums">
              <Eye size={12} />
              {formatCompact(topPost.reach)}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
