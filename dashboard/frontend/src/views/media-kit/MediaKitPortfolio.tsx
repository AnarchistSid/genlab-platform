/**
 * PR V (2026-06-23) — multi-niche aggregate sponsor portfolio.
 *
 * Renders all 5 niches as one printable document at /media-kit/all.
 * Used for cross-channel sponsor pitches — brands that want a single
 * deal covering multiple niches (e.g., gaming + sports + movies in
 * one campaign) get one PDF instead of five.
 *
 * Composes on PR #482's per-niche kit data (same shape, just iterated)
 * and on PR #481's tier-computation primitives (imported server-side
 * so the two surfaces NEVER disagree on tier).
 *
 * Each niche section uses ``page-break-inside: avoid`` so brand
 * sections don't split awkwardly across PDF pages — the operator
 * gets one clean A4 page per niche when long enough, otherwise
 * multiple niches per page.
 */
import { useQuery } from "@tanstack/react-query";

import { mediaKit } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type {
  MediaKitAudienceEntry,
  MediaKitPortfolioData,
  MediaKitPortfolioNiche,
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

const NICHE_HANDLES: Record<NicheId, string> = {
  ai_creators: "@blackbox.brief",
  gaming: "@criticalrush",
  sports: "@clutchwire",
  movies: "@splicereel",
  anime: "@framedrift",
};

export default function MediaKitPortfolioView() {
  const { data, isLoading, error } = useQuery({
    queryKey: queryKeys.mediaKit.all(),
    queryFn: () => mediaKit.all(),
    retry: false,
  });

  return (
    <PrintShell>
      {isLoading && !data ? (
        <div className="p-12 text-center text-slate-500">Loading portfolio…</div>
      ) : error || !data ? (
        <ErrorState message="Could not load the portfolio kit. Refresh and try again." />
      ) : (
        <PortfolioDocument data={data} />
      )}
    </PrintShell>
  );
}

function PrintShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-white text-slate-900 print:bg-white">
      <style>{`
        @media print {
          @page { size: A4 portrait; margin: 12mm; }
          .no-print { display: none !important; }
          html, body { background: white !important; }
          .niche-section { page-break-inside: avoid; break-inside: avoid; }
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

function PortfolioDocument({ data }: { data: MediaKitPortfolioData }) {
  const { niches, summary } = data;
  const generatedDate = formatGeneratedDate(data.generated_at);

  return (
    <article className="space-y-8">
      <header className="pt-6 pb-4 border-b-2 border-slate-200">
        <h1 className="text-3xl font-bold tracking-tight">Gen Lab Portfolio</h1>
        <p className="text-slate-600 mt-1">
          5 video-first content channels across AI, gaming, sports, movies, and anime
        </p>
        <div className="mt-4 grid grid-cols-2 gap-4 text-sm">
          <StatBox
            label="Pitch-ready niches"
            value={`${summary.eligible_now_count} / ${niches.length}`}
          />
          <StatBox
            label="Monetised platforms"
            value={String(summary.monetised_platforms_total)}
          />
        </div>
      </header>

      {niches.map((niche) => (
        <NicheSection key={niche.niche_id} niche={niche} />
      ))}

      <footer className="pt-6 border-t border-slate-200 text-xs text-slate-500 flex items-center justify-between">
        <div>Gen Lab — multi-niche video portfolio</div>
        <div>Generated {generatedDate}</div>
      </footer>

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

function StatBox({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-slate-200 rounded p-3">
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-bold tracking-tight">{value}</div>
    </div>
  );
}

function NicheSection({ niche }: { niche: MediaKitPortfolioNiche }) {
  // The backend's _STABLE_NICHE_ORDER guarantees only valid IDs land
  // here, but TypeScript needs the explicit narrowing for getNiche().
  const nid = niche.niche_id as NicheId;
  const def = getNiche(nid);
  const tier = niche.tier;
  const handle = NICHE_HANDLES[nid] ?? "";

  return (
    <section className="niche-section">
      <header className="pt-4 pb-3 border-b border-slate-200">
        <div className="flex items-end justify-between gap-4">
          <div className="flex items-center gap-3">
            <span
              className="size-3 rounded-full"
              style={{ background: def.accentHex }}
              aria-hidden
            />
            <div>
              <h2 className="text-xl font-bold">{def.displayName}</h2>
              <p className="text-xs text-slate-500">
                {def.tagline} · {handle}
              </p>
            </div>
          </div>
          <div
            className={`text-xs font-medium px-3 py-1 rounded-full border ${TIER_TONE[tier]}`}
          >
            {TIER_LABEL[tier]}
          </div>
        </div>
      </header>

      <div className="pt-3">
        {niche.audience.length === 0 ? (
          <p className="text-xs text-slate-500 italic py-2">
            Audience data is being collected.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-[10px] uppercase tracking-wide text-slate-500 border-b border-slate-200">
              <tr>
                <th className="text-left py-1.5 font-medium">Platform</th>
                <th className="text-right py-1.5 font-medium">Audience</th>
                <th className="text-right py-1.5 font-medium">7-day delta</th>
                <th className="text-right py-1.5 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {niche.audience.map((entry) => (
                <AudienceRow
                  key={entry.platform}
                  entry={entry}
                  accentHex={def.accentHex}
                />
              ))}
            </tbody>
          </table>
        )}

        {/* PR CC: top posts inside each portfolio section. Brands
            scrolling the portfolio can click into actual content per
            niche without navigating to platforms manually. Compact
            layout — 3 lines max per niche to keep the printable
            document section-per-niche scannable. */}
        {niche.top_posts.length > 0 && (
          <ul className="mt-3 space-y-1 text-xs">
            {niche.top_posts.map((post) => (
              <PortfolioTopPostRow
                key={`${post.platform}-${post.post_id}`}
                post={post}
              />
            ))}
          </ul>
        )}

        {niche.tier !== "eligible_now" && niche.nearest_threshold_days !== null && (
          <p className="mt-3 text-xs text-slate-600">
            Closest unmet threshold:{" "}
            <span className="font-medium text-slate-900">
              {niche.nearest_threshold_days} days at current velocity
            </span>
          </p>
        )}
      </div>
    </section>
  );
}

function PortfolioTopPostRow({ post }: { post: MediaKitTopPost }) {
  const date = post.published_at
    ? new Date(post.published_at).toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
      })
    : "";
  return (
    <li className="flex items-center gap-2 text-slate-600">
      {/* PR HH (2026-06-23): symmetry fix for PR #494 — MediaKit.tsx
          got YT thumbnails but MediaKitPortfolio missed them. Portfolio
          row is more compact than the single-niche kit, so thumbnail
          shrinks to w-12 h-8 (vs MediaKit's w-20 h-12). Same fallback
          to platform-name placeholder when thumbnail_url is null. */}
      {post.thumbnail_url ? (
        <a
          href={post.post_url}
          target="_blank"
          rel="noopener noreferrer"
          className="shrink-0"
          title={`Open this ${post.platform} post in a new tab`}
        >
          <img
            src={post.thumbnail_url}
            alt={`${post.platform} post thumbnail`}
            className="w-12 h-8 object-cover rounded border border-slate-200"
            loading="lazy"
          />
        </a>
      ) : (
        <div
          className="shrink-0 w-12 h-8 rounded border border-slate-200 bg-slate-100 flex items-center justify-center text-[8px] uppercase text-slate-500 font-medium"
          title={`No thumbnail derivable for ${post.platform}`}
        >
          {post.platform.slice(0, 4)}
        </div>
      )}
      <a
        href={post.post_url}
        target="_blank"
        rel="noopener noreferrer"
        className="hover:underline capitalize text-slate-700 flex-1"
      >
        {post.platform}
        {date && <span className="text-slate-400 ml-1">· {date}</span>}
      </a>
      <span className="font-mono text-slate-500">
        {formatNumber(post.views)} views
      </span>
    </li>
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
      <td className="py-2 capitalize flex items-center gap-2">
        <span
          className="size-2 rounded-full"
          style={{ background: accentHex }}
          aria-hidden
        />
        {entry.platform}
      </td>
      <td className="py-2 text-right font-mono">{audience}</td>
      <td className={`py-2 text-right font-mono text-xs ${deltaClass}`}>{deltaText}</td>
      <td className="py-2 text-right text-xs">
        {entry.is_threshold_met ? (
          <span className="text-emerald-700 font-medium">monetised</span>
        ) : (
          <span className="text-slate-500">{entry.metric_name}</span>
        )}
      </td>
    </tr>
  );
}

function formatNumber(n: number | null): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
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
