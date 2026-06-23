/**
 * PR U (2026-06-23) — operator-deliverable per-niche media kit.
 *
 * Standalone print-friendly route (no DashboardLayout). The operator
 * navigates to ``/media-kit/<niche_id>``, hits Cmd+P → "Save as PDF",
 * and emails the result to a brand.
 *
 * Composes on PR #481 (sponsorship-readiness tier) + the existing
 * monetisationprogress data + the niche registry's branding.
 * Deliberately ZERO new data infra — pure rendering layer.
 *
 * Visual language: clean A4-ish layout, brand accent stripe at top,
 * platform-by-platform audience table (sorted descending), prominent
 * tier badge. Designed to look intentional on screen AND in print.
 */
import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { mediaKit } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type {
  MediaKitAudienceEntry,
  MediaKitData,
  MediaKitTopPost,
  SponsorshipTier,
} from "@/api/types";
import { getNiche, type NicheId } from "@/niches/registry";

const TIER_LABEL: Record<SponsorshipTier, string> = {
  eligible_now: "Ready for sponsorship",
  within_2_months: "Approaching threshold (≤ 2 months)",
  within_6_months: "Building toward threshold (≤ 6 months)",
  tracking: "Growing audience",
};

const TIER_TONE: Record<SponsorshipTier, string> = {
  eligible_now: "text-emerald-600 bg-emerald-50 border-emerald-200",
  within_2_months: "text-amber-700 bg-amber-50 border-amber-200",
  within_6_months: "text-blue-700 bg-blue-50 border-blue-200",
  tracking: "text-slate-700 bg-slate-50 border-slate-200",
};

const KNOWN_NICHE_IDS: ReadonlySet<NicheId> = new Set<NicheId>([
  "ai_creators",
  "gaming",
  "sports",
  "movies",
  "anime",
]);

export default function MediaKitView() {
  const { niche } = useParams<{ niche: string }>();
  const nicheId = niche ?? "";
  const isKnown = (KNOWN_NICHE_IDS as Set<string>).has(nicheId);

  const { data, isLoading, error } = useQuery({
    queryKey: queryKeys.mediaKit.get(nicheId),
    queryFn: () => mediaKit.get(nicheId),
    enabled: isKnown,
    retry: false,
  });

  if (!isKnown) {
    return (
      <PrintShell>
        <ErrorState message={`Unknown niche: "${niche}". Valid niches: ai_creators, gaming, sports, movies, anime.`} />
      </PrintShell>
    );
  }

  if (isLoading) {
    return (
      <PrintShell>
        <div className="p-12 text-center text-slate-500">Loading kit…</div>
      </PrintShell>
    );
  }

  if (error || !data) {
    return (
      <PrintShell>
        <ErrorState message="Could not load the kit. Refresh and try again." />
      </PrintShell>
    );
  }

  return (
    <PrintShell>
      <KitDocument niche={nicheId as NicheId} data={data} />
    </PrintShell>
  );
}

function PrintShell({ children }: { children: React.ReactNode }) {
  // Standalone shell — no app chrome. Print CSS shrinks to A4 page.
  // Light theme always (kits print on white paper).
  return (
    <div className="min-h-screen bg-white text-slate-900 print:bg-white">
      <style>{`
        @media print {
          @page { size: A4 portrait; margin: 12mm; }
          .no-print { display: none !important; }
          html, body { background: white !important; }
        }
      `}</style>
      <div className="max-w-3xl mx-auto p-8 print:p-0">{children}</div>
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="p-12 text-center text-red-700 bg-red-50 border border-red-200 rounded-lg">
      {message}
    </div>
  );
}

function KitDocument({ niche, data }: { niche: NicheId; data: MediaKitData }) {
  const def = getNiche(niche);
  const tierLabel = TIER_LABEL[data.tier];
  const tierTone = TIER_TONE[data.tier];
  const generatedDate = formatGeneratedDate(data.generated_at);

  return (
    <article className="space-y-8">
      {/* Brand header — accent stripe + display name */}
      <header className="relative pt-6 pb-4 border-b border-slate-200">
        <div
          className="absolute top-0 left-0 right-0 h-1 rounded-t"
          style={{ background: def.accentHex }}
          aria-hidden
        />
        <div className="flex items-end justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">{def.displayName}</h1>
            <p className="text-slate-600 mt-1">{def.tagline}</p>
          </div>
          <div
            className={`text-xs font-medium px-3 py-1.5 rounded-full border ${tierTone}`}
            title={tierTone}
          >
            {tierLabel}
          </div>
        </div>
      </header>

      {/* Audience table — strongest platform first */}
      <section>
        <h2 className="text-lg font-semibold mb-3">Audience</h2>
        {data.audience.length === 0 ? (
          <p className="text-slate-500 italic">
            Audience data is being collected. Check back after the next daily
            collector run.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-xs uppercase tracking-wide text-slate-500 border-b border-slate-200">
              <tr>
                <th className="text-left py-2 font-medium">Platform</th>
                <th className="text-right py-2 font-medium">Audience</th>
                <th className="text-right py-2 font-medium">7-day delta</th>
                <th className="text-right py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {data.audience.map((entry) => (
                <AudienceRow key={entry.platform} entry={entry} accentHex={def.accentHex} />
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* PR CC: top-performing posts. Brands judge content, not
          numbers — embedding clickable top-post links closes the
          kit's prequalification gap. Only render section when there
          are URL-derivable posts (YT/FB/X at v1). */}
      {data.top_posts.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3">Recent top posts</h2>
          <ul className="space-y-2 text-sm">
            {data.top_posts.map((post) => (
              <TopPostRow key={`${post.platform}-${post.post_id}`} post={post} />
            ))}
          </ul>
        </section>
      )}

      {/* Monetised platforms — only visible when there's something to say */}
      {data.monetised_platforms.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3">Monetised platforms</h2>
          <div className="flex gap-2 flex-wrap">
            {data.monetised_platforms.map((p) => (
              <span
                key={p}
                className="text-xs font-medium px-2 py-1 rounded border border-emerald-200 bg-emerald-50 text-emerald-700 capitalize"
              >
                {p}
              </span>
            ))}
          </div>
        </section>
      )}

      {/* Nearest-threshold call-out for non-eligible niches */}
      {data.tier !== "eligible_now" && data.nearest_threshold_days !== null && (
        <section>
          <p className="text-sm text-slate-600">
            Closest unmet monetisation threshold:{" "}
            <span className="font-medium text-slate-900">
              {data.nearest_threshold_days} days at current velocity
            </span>
          </p>
        </section>
      )}

      {/* Footer — handle + generated stamp */}
      <footer className="pt-6 border-t border-slate-200 flex items-center justify-between text-xs text-slate-500">
        <div>
          Find us at <span className="font-medium text-slate-700">{nicheHandle(niche)}</span>
        </div>
        <div>Generated {generatedDate}</div>
      </footer>

      {/* Print/screen-only action bar */}
      <div className="no-print pt-4 border-t border-slate-200">
        <button
          type="button"
          onClick={() => window.print()}
          className="text-xs px-3 py-1.5 rounded border border-slate-300 hover:bg-slate-50"
        >
          Print / Save as PDF
        </button>
      </div>
    </article>
  );
}

function AudienceRow({
  entry,
  accentHex,
}: {
  entry: MediaKitAudienceEntry;
  accentHex: string;
}) {
  const audience = formatNumber(entry.current_value);
  const delta = entry.delta_7d;
  const deltaText =
    delta === null ? "—" : delta >= 0 ? `+${formatNumber(delta)}` : formatNumber(delta);
  const deltaClass =
    delta === null
      ? "text-slate-400"
      : delta > 0
        ? "text-emerald-600"
        : delta < 0
          ? "text-red-600"
          : "text-slate-500";

  return (
    <tr className="border-b border-slate-100">
      <td className="py-3 capitalize flex items-center gap-2">
        <span
          className="size-2 rounded-full"
          style={{ background: accentHex }}
          aria-hidden
        />
        {entry.platform}
      </td>
      <td className="py-3 text-right font-mono">{audience}</td>
      <td className={`py-3 text-right font-mono text-xs ${deltaClass}`}>{deltaText}</td>
      <td className="py-3 text-right text-xs">
        {entry.is_threshold_met ? (
          <span className="text-emerald-700 font-medium">monetised</span>
        ) : (
          <span className="text-slate-500">{entry.metric_name}</span>
        )}
      </td>
    </tr>
  );
}

function TopPostRow({ post }: { post: MediaKitTopPost }) {
  const date = post.published_at
    ? new Date(post.published_at).toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
      })
    : "";
  return (
    <li className="flex items-center justify-between gap-3 border-b border-slate-100 pb-2">
      <div className="flex-1 min-w-0">
        <a
          href={post.post_url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-slate-900 hover:underline capitalize font-medium"
          title={`Open this ${post.platform} post in a new tab`}
        >
          {post.platform}
        </a>
        <span className="text-slate-500 text-xs ml-2">{date}</span>
      </div>
      <div className="flex gap-3 text-xs text-slate-600 font-mono">
        <span title="Views">{formatNumber(post.views)} views</span>
        <span title="Likes">{formatNumber(post.likes)} likes</span>
        <span title="Comments">{formatNumber(post.comments)} comments</span>
      </div>
    </li>
  );
}

function formatNumber(n: number | null): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  // Compact format for big numbers — "1.2K", "5.3M" — saves horizontal
  // space in the print column.
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

function formatGeneratedDate(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}

const NICHE_HANDLES: Record<NicheId, string> = {
  ai_creators: "@blackbox.brief",
  gaming: "@criticalrush",
  sports: "@clutchwire",
  movies: "@splicereel",
  anime: "@framedrift",
};

function nicheHandle(niche: NicheId): string {
  return NICHE_HANDLES[niche] ?? "";
}
