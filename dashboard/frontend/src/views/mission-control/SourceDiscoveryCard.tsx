/**
 * Mission Control SourceDiscoveryCard (PR after #586, 2026-06-25).
 *
 * Ranks the SOURCE YouTube channels we pulled clips from, ordered
 * by per-clip avg engagement. Helps the operator decide which
 * under-tracked channels deserve a slot in config/sources.yaml.
 *
 * ## Why per-niche selector (not 5-row layout)
 *
 * Unlike NichePauseCard / ComplianceStatsCard / SponsorshipReadinessCard
 * which surface ONE summary signal per niche (1 row × 5 niches), the
 * proposer's output is itself a ranked list per niche (10 rows × 1
 * niche selected at a time). A 5-niche × 10-channel grid would be
 * 50 rows of dense detail — too noisy for at-a-glance Mission Control.
 *
 * Single niche selector + top-10 table is the operator's actual
 * triage workflow: "today let me look at gaming's underdiscovered
 * sources" → tomorrow "what about anime?"
 *
 * ## Zero-state messaging
 *
 * The proposer needs source_channel_id data accumulated on prod
 * blueprints (PR #586 producer wire). Until trending fetches run
 * enough times, every niche returns []. The card renders an
 * explicit "waiting for data" message rather than a confusing
 * "no suggestions" empty state.
 *
 * ## Foundation deps
 *
 *   * PR #586 — blueprints.source_channel_id schema + producer wire
 *   * propose_source_channels library (this session)
 *   * /api/v1/source-discovery/proposals endpoint (this session)
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { sourceDiscovery } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import type { SourceChannelProposal } from "@/api/types";
import { getNicheInfo, NICHE_IDS, type NicheId } from "@/niches/registry";

const WINDOW_DAYS = 30;

export function SourceDiscoveryCard() {
  // Default to the first niche in registry — operator switches via
  // niche-color pills below. Per-niche state is lighter than a 5-niche
  // multi-fetch (10 proposals per niche × 5 niches = 50 rows, plus 5
  // network calls per refresh).
  const [selectedNiche, setSelectedNiche] = useState<NicheId>(NICHE_IDS[0]);

  const { data, isLoading, isError } = useQuery({
    queryKey: queryKeys.sourceDiscovery.proposals(selectedNiche, WINDOW_DAYS),
    queryFn: () => sourceDiscovery.proposals(selectedNiche, WINDOW_DAYS),
    staleTime: 60_000,
    refetchInterval: 60_000,
    retry: false,
  });

  const proposals = data?.proposals ?? [];
  const warning = data?.warning;

  return (
    <div className="bento-card">
      <h3 className="card-title">
        Source Discovery
        <span className="ml-2 text-[10px] text-text-muted font-normal">
          channels by avg engagement · last {WINDOW_DAYS}d
        </span>
      </h3>

      {/* Niche selector: color-coded pills, one per registered niche */}
      <div className="flex gap-1 mb-3 flex-wrap">
        {NICHE_IDS.map((nicheId) => {
          const info = getNicheInfo(nicheId);
          const active = nicheId === selectedNiche;
          return (
            <button
              key={nicheId}
              type="button"
              onClick={() => setSelectedNiche(nicheId)}
              className={`text-[10px] px-2 py-0.5 rounded border transition-colors ${
                active
                  ? "text-text-primary"
                  : "text-text-secondary hover:bg-text-muted/10"
              }`}
              style={{
                borderColor: active ? info.hex : "rgba(128,128,128,0.3)",
                background: active ? `${info.hex}20` : undefined,
              }}
            >
              {info.shortLabel}
            </button>
          );
        })}
      </div>

      {/* Proposal table */}
      {isLoading && !data ? (
        <div className="flex flex-col gap-1">
          <div className="shimmer" style={{ height: 16, width: "100%", borderRadius: 4 }} />
          <div className="shimmer" style={{ height: 16, width: "100%", borderRadius: 4 }} />
          <div className="shimmer" style={{ height: 16, width: "100%", borderRadius: 4 }} />
        </div>
      ) : isError ? (
        <span className="text-xs text-text-muted">
          Source-discovery endpoint unreachable — try again shortly
        </span>
      ) : warning ? (
        <ZeroStateMessage
          message={`Proposer unavailable on this build: ${warning}`}
        />
      ) : proposals.length === 0 ? (
        <ZeroStateMessage
          message={`No source channels yet for ${
            getNicheInfo(selectedNiche).shortLabel
          }. Data accumulates from new trending fetches — try again in a few days.`}
        />
      ) : (
        <ProposalTable proposals={proposals} />
      )}

      <div className="mt-3 pt-2 border-t border-text-muted/10">
        <span className="text-[10px] text-text-muted">
          Proposer ranks SOURCE channels (clips we used) by per-clip avg
          engagement · min 2 clips per channel
        </span>
      </div>
    </div>
  );
}

function ZeroStateMessage({ message }: { message: string }) {
  return (
    <div className="text-xs text-text-muted py-4 text-center italic">
      {message}
    </div>
  );
}

function ProposalTable({ proposals }: { proposals: SourceChannelProposal[] }) {
  return (
    <div className="flex flex-col gap-1">
      {/* Header row */}
      <div className="flex items-center gap-2 text-[9px] uppercase tracking-wide text-text-muted px-1 pb-0.5 border-b border-text-muted/10">
        <span className="flex-1">Channel</span>
        <span className="font-mono text-right" style={{ minWidth: 36 }}>
          Clips
        </span>
        <span className="font-mono text-right" style={{ minWidth: 56 }}>
          Avg eng
        </span>
      </div>
      {proposals.map((p) => (
        <ProposalRow key={p.source_channel_id} proposal={p} />
      ))}
    </div>
  );
}

function ProposalRow({ proposal }: { proposal: SourceChannelProposal }) {
  const { source_channel_id, clips_used, avg_engagement, total_engagement } = proposal;

  // YouTube channel URL — opens externally so operator can vet
  // the channel content before adding to sources.yaml. Channel
  // names aren't fetched yet (deferred to a follow-up that adds
  // a YouTube Data API resolver) — channel_id is the link payload.
  const youtubeUrl = `https://www.youtube.com/channel/${source_channel_id}`;

  const title =
    `Source channel: ${source_channel_id}\n` +
    `Clips used: ${clips_used}\n` +
    `Total engagement: ${total_engagement.toLocaleString()}\n` +
    `Avg per clip: ${avg_engagement.toLocaleString()}\n` +
    `Click to open the channel on YouTube`;

  return (
    <a
      href={youtubeUrl}
      target="_blank"
      rel="noopener noreferrer"
      className="flex items-center gap-2 text-xs hover:bg-text-muted/5 rounded px-1 py-0.5 transition-colors"
      title={title}
    >
      <span className="flex-1 truncate font-mono text-[10px] text-text-secondary">
        {source_channel_id}
      </span>
      <span
        className="font-mono text-[10px] text-text-primary text-right"
        style={{ minWidth: 36 }}
      >
        {clips_used}
      </span>
      <span
        className="font-mono text-[10px] text-text-primary text-right"
        style={{ minWidth: 56 }}
      >
        {formatEngagement(avg_engagement)}
      </span>
    </a>
  );
}

/** Compact engagement formatter: 12345 → "12.3k", 1500000 → "1.5M". */
function formatEngagement(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return Math.round(n).toString();
}
