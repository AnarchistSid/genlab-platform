import { PlatformIcon } from "@/components/shared/PlatformIcon";
import { ToxicityBadge } from "@/components/shared/ToxicityBadge";
import { relativeTime } from "@/lib/format";
import { getNicheInfo } from "@/niches/registry";
import type { EngagementComment } from "@/api/types";

// ── Niche config ────────────────────────────────────────────

function getNicheMeta(nicheId: string) {
  const info = getNicheInfo(nicheId);
  return { color: info.color, label: info.shortLabel };
}

// ── Toxicity heuristic (mirrors EngagementFeed) ─────────────

function estimateToxicity(text: string): "safe" | "review" | "toxic" {
  const lower = text.toLowerCase();
  const toxicPatterns = /\b(hate|kill|die|stupid|idiot|trash|garbage|worst)\b/;
  const reviewPatterns = /\b(wtf|omg|lmao|bruh|cap|sus|mid)\b/;
  if (toxicPatterns.test(lower)) return "toxic";
  if (reviewPatterns.test(lower)) return "review";
  return "safe";
}

// ── Props ────────────────────────────────────────────────────

interface Props {
  comments: EngagementComment[];
  nicheFilter: string;
  platformFilter: string;
}

// ── Component ────────────────────────────────────────────────

export function CommentFeed({ comments, nicheFilter, platformFilter }: Props) {
  const filtered = comments.filter((c) => {
    if (nicheFilter && c.niche_id !== nicheFilter) return false;
    if (platformFilter && c.platform.toLowerCase() !== platformFilter.toLowerCase()) return false;
    return true;
  });

  if (filtered.length === 0) {
    return (
      <div className="bg-bg-surface border border-border rounded-lg p-4 flex flex-col gap-2 min-h-[200px] justify-center items-center text-center">
        <p className="text-base text-text-secondary m-0">
          No comments found
        </p>
        <p className="text-sm text-text-muted m-0">
          Engagement typically appears after publishing
        </p>
      </div>
    );
  }

  return (
    <div className="bg-bg-surface border border-border rounded-lg p-4 overflow-y-auto max-h-[560px]">
      {filtered.map((comment, idx) => {
        const niche = getNicheMeta(comment.niche_id);
        return (
          <div
            key={comment.comment_id}
            className="py-3 flex flex-col gap-1"
            style={{
              borderBottom:
                idx < filtered.length - 1 ? "1px solid var(--border-subtle)" : "none",
            }}
          >
            {/* Row 1: platform icon + username + time | niche dot + name + badge */}
            <div className="flex items-center justify-between gap-2">
              {/* Left: icon + username + time */}
              <div className="flex items-center gap-1.5 min-w-0">
                <PlatformIcon platform={comment.platform} size={13} />
                <span className="text-sm font-semibold text-text-primary whitespace-nowrap">
                  @{comment.username}
                </span>
                <span className="text-xs text-text-muted whitespace-nowrap">
                  {relativeTime(comment.timestamp)}
                </span>
              </div>

              {/* Right: niche dot + name + toxicity */}
              <div className="flex items-center gap-1.5 shrink-0">
                <span
                  className="w-[7px] h-[7px] rounded-full inline-block shrink-0"
                  style={{ background: niche.color }}
                />
                <span className="text-xs text-text-muted whitespace-nowrap">
                  {niche.label}
                </span>
                <ToxicityBadge level={estimateToxicity(comment.text)} />
              </div>
            </div>

            {/* Row 2: comment text */}
            <p className="m-0 text-sm text-text-secondary leading-relaxed line-clamp-2">
              {comment.text}
            </p>
          </div>
        );
      })}
    </div>
  );
}
