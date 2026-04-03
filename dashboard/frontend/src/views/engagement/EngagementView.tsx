import { useState } from "react";
import { useRecentComments } from "@/hooks/use-engagement";
import { getActiveNiches } from "@/niches/registry";
import { PLATFORM_IDS, getPlatformInfo } from "@/lib/platforms";
import { PageHeader } from "@/components/shared/page-header";
import { ErrorState } from "@/components/shared/error-state";
import { KpiCard } from "@/components/shared/kpi-card";
import type { EngagementComment } from "@/api/types";
import { CommentFeed } from "./CommentFeed";
import { ReplyQueue } from "./ReplyQueue";

// ── Build filter options from registries ─────────────────────

const NICHES = [
  { value: "", label: "All Niches" },
  ...getActiveNiches().map((n) => ({ value: n.id, label: n.displayName })),
];

const PLATFORMS = [
  { value: "", label: "All Platforms" },
  ...PLATFORM_IDS.map((id) => ({ value: id, label: getPlatformInfo(id).label })),
];

// ── Derive stats from comments ───────────────────────────────

function deriveStats(comments: EngagementComment[]) {
  const platforms = new Set(comments.map((c) => c.platform.toLowerCase()));
  return {
    total: comments.length,
    autoReplied: 0,          // no data yet — engine hasn't generated replies
    pendingReview: 0,        // ditto
    platformsActive: platforms.size,
  };
}

// ── Main view ────────────────────────────────────────────────

export default function EngagementView() {
  const [nicheFilter, setNicheFilter] = useState("");
  const [platformFilter, setPlatformFilter] = useState("");

  const resp = useRecentComments();
  const comments: EngagementComment[] = resp.data ?? [];

  const stats = deriveStats(comments);

  // Error state — show banner when query failed and no cached data
  if (resp.isError && comments.length === 0) {
    return (
      <>
        <PageHeader
          title="Engagement"
          subtitle="Comments, replies, and community interaction"
        />
        <ErrorState
          title="Unable to load engagement data"
          onRetry={() => void resp.refetch()}
        />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Engagement"
        subtitle="Comments, replies, and community interaction"
      />

      {/* Stats row */}
      <div className="flex gap-3 mb-6 flex-wrap">
        <KpiCard label="Total Comments"   value={stats.total} className="flex-1 min-w-0" />
        <KpiCard label="Auto-Replied"     value={stats.autoReplied} className="flex-1 min-w-0" />
        <KpiCard label="Pending Review"   value={stats.pendingReview} className="flex-1 min-w-0" />
        <KpiCard label="Platforms Active" value={stats.platformsActive} className="flex-1 min-w-0" />
      </div>

      {/* Filter bar */}
      <div className="flex gap-2.5 mb-4 flex-wrap items-center">
        <span className="label-caps">Filter</span>
        <select
          value={nicheFilter}
          onChange={(e) => setNicheFilter(e.target.value)}
          className="bg-bg-elevated border border-border rounded-sm text-text-secondary text-sm px-2.5 py-1.5 cursor-pointer outline-none"
        >
          {NICHES.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        <select
          value={platformFilter}
          onChange={(e) => setPlatformFilter(e.target.value)}
          className="bg-bg-elevated border border-border rounded-sm text-text-secondary text-sm px-2.5 py-1.5 cursor-pointer outline-none"
        >
          {PLATFORMS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </div>

      {/* Two-panel layout */}
      <div className="grid grid-cols-[2fr_1fr] gap-4 engagement-grid">
        {/* Left: comment feed */}
        <div>
          <h2 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-2.5">
            Recent Comments
          </h2>
          <CommentFeed
            comments={comments}
            nicheFilter={nicheFilter}
            platformFilter={platformFilter}
          />
        </div>

        {/* Right: reply queue */}
        <div>
          <h2 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-2.5">
            Reply Queue
          </h2>
          <ReplyQueue />
        </div>
      </div>

      {/* Mobile responsive: stack columns */}
      <style>{`
        @media (max-width: 680px) {
          .engagement-grid {
            grid-template-columns: 1fr !important;
          }
        }
      `}</style>
    </>
  );
}
