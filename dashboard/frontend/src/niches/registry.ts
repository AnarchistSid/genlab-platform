import type { ComponentType } from 'react';
import type { Blueprint } from '@/api/types';
import { AiNewsDetailView } from '@/niches/detail-views/AiNewsDetailView';
import { GamingDetailView } from '@/niches/detail-views/GamingDetailView';
import { GenericDetailView } from '@/niches/detail-views/GenericDetailView';

export type NicheId = 'ai_creators' | 'gaming' | 'sports' | 'anime' | 'movies';

export interface DetailViewProps {
  item: Blueprint;
}

export interface PipelineStage {
  name: string;
  label: string;
  description: string;
}

export interface NicheDefinition {
  id: NicheId;
  displayName: string;
  tagline: string;
  accentColor: string;
  accentHex: string;
  icon: string;
  isActive: boolean;
  detailView?: ComponentType<DetailViewProps>;
  pipelineStages: PipelineStage[];
  sharePointListIds: {
    blueprints: string;
    stories: string;
  } | null;
}

/**
 * Shared pipeline stages — all niches run the same daily_intel.sh pipeline.
 * Only the Fetch description varies per niche; all other stages are identical.
 */
export const SHARED_PIPELINE_STAGES: PipelineStage[] = [
  { name: '1', label: 'Fetch', description: 'Pull from configured sources' },
  { name: '2', label: 'Parse', description: 'Extract content + media URLs' },
  { name: '4', label: 'Dedupe', description: 'TF-IDF clustering + similarity scoring' },
  { name: '6', label: 'Download', description: 'Download videos for top stories' },
  { name: '7', label: 'Compose', description: 'Stories × templates → blueprint candidates' },
  { name: '10', label: 'Write', description: 'Content outlines + hooks + post copy' },
  { name: '11', label: 'Backlog', description: 'Push to Microsoft Lists' },
  { name: '13', label: 'Adapt', description: 'Platform-native rewrites (YT, X, FB)' },
  { name: '16', label: 'Render', description: 'Video assembly + text overlays + audio' },
  { name: '19', label: 'Validate', description: 'Video spec validation + auto-fix' },
  { name: '20', label: 'Publish', description: 'Multi-platform concurrent publishing' },
  { name: '21', label: 'Insights', description: 'Engagement metrics + virality scoring' },
];

const definitions: NicheDefinition[] = [
  {
    id: 'ai_creators',
    displayName: 'Blackbox Brief',
    tagline: 'AI Creator Clips',
    accentColor: '--niche-ai',
    accentHex: '#00D4FF',
    icon: 'brain',
    isActive: true,
    detailView: AiNewsDetailView,
    pipelineStages: SHARED_PIPELINE_STAGES,
    sharePointListIds: {
      blueprints: 'Blueprints',
      stories: 'Stories',
    },
  },
  {
    id: 'gaming',
    displayName: 'CriticalRush',
    tagline: 'Gaming',
    accentColor: '--niche-gaming',
    accentHex: '#F97316',
    icon: 'gamepad-2',
    isActive: true,
    detailView: GamingDetailView,
    pipelineStages: SHARED_PIPELINE_STAGES,
    sharePointListIds: {
      blueprints: 'Gaming_Blueprints',
      stories: 'Gaming_Stories',
    },
  },
  {
    id: 'sports',
    displayName: 'ClutchWire',
    tagline: 'Sports',
    accentColor: '--niche-sports',
    accentHex: '#FF2040',
    icon: 'zap',
    isActive: true,
    detailView: GenericDetailView,
    pipelineStages: SHARED_PIPELINE_STAGES,
    sharePointListIds: {
      blueprints: 'Blueprints',
      stories: 'Stories',
    },
  },
  {
    id: 'anime',
    displayName: 'FrameDrift',
    tagline: 'Anime',
    accentColor: '--niche-anime',
    accentHex: '#7B3FE4',
    icon: 'sparkles',
    isActive: true,
    detailView: GenericDetailView,
    pipelineStages: SHARED_PIPELINE_STAGES,
    sharePointListIds: {
      blueprints: 'Blueprints',
      stories: 'Stories',
    },
  },
  {
    id: 'movies',
    displayName: 'SpliceReel',
    tagline: 'Movies',
    accentColor: '--niche-movies',
    accentHex: '#C9A84C',
    icon: 'clapperboard',
    isActive: true,
    detailView: GenericDetailView,
    pipelineStages: SHARED_PIPELINE_STAGES,
    sharePointListIds: {
      blueprints: 'Blueprints',
      stories: 'Stories',
    },
  },
];

export const nicheRegistry = new Map<NicheId, NicheDefinition>(
  definitions.map((d) => [d.id, d]),
);

export function getNiche(id: NicheId): NicheDefinition {
  const niche = nicheRegistry.get(id);
  if (!niche) throw new Error(`Unknown niche: ${id}`);
  return niche;
}

export function getActiveNiches(): NicheDefinition[] {
  return definitions.filter((d) => d.isActive);
}

export function getAllNiches(): NicheDefinition[] {
  return [...definitions];
}

export function getDetailView(id: NicheId): ComponentType<DetailViewProps> | undefined {
  return nicheRegistry.get(id)?.detailView;
}

// ---------------------------------------------------------------------------
// Convenience lookups — single source of truth for colors / labels / handles.
//
// Every component that previously maintained its own NICHE_COLORS / NICHE_LABELS /
// NICHE_CONFIG / NICHE_CHANNELS map should import from here instead.
// ---------------------------------------------------------------------------

export interface NicheQuickInfo {
  id: NicheId;
  label: string;       // Full display name, e.g. "CriticalRush"
  shortLabel: string;  // Compact label for tight UIs, e.g. "Gaming"
  color: string;       // CSS variable reference, e.g. "var(--niche-gaming)"
  hex: string;         // Raw hex for contexts where CSS vars don't work (SVG, recharts)
  handle: string;      // Social handle, e.g. "@criticalrush"
}

const QUICK_INFO: NicheQuickInfo[] = [
  { id: "ai_creators", label: "Blackbox Brief", shortLabel: "AI",      color: "var(--niche-ai)",      hex: "#00D4FF", handle: "@blackbox.brief" },
  { id: "gaming",      label: "CriticalRush",   shortLabel: "Gaming",  color: "var(--niche-gaming)",  hex: "#F97316", handle: "@criticalrush" },
  { id: "sports",      label: "ClutchWire",     shortLabel: "Sports",  color: "var(--niche-sports)",  hex: "#FF2040", handle: "@clutchwire" },
  { id: "movies",      label: "SpliceReel",     shortLabel: "Movies",  color: "var(--niche-movies)",  hex: "#C9A84C", handle: "@splicereel" },
  { id: "anime",       label: "FrameDrift",     shortLabel: "Anime",   color: "var(--niche-anime)",   hex: "#7B3FE4", handle: "@framedrift" },
];

const quickMap = new Map<string, NicheQuickInfo>(
  QUICK_INFO.map((n) => [n.id, n]),
);

const FALLBACK_INFO: NicheQuickInfo = {
  id: "ai_creators",
  label: "Unknown",
  shortLabel: "?",
  color: "var(--text-muted)",
  hex: "#71717a",
  handle: "",
};

/** Get the quick-info record for a niche, with a safe fallback. */
export function getNicheInfo(nicheId: string): NicheQuickInfo {
  return quickMap.get(nicheId) ?? { ...FALLBACK_INFO, id: nicheId as NicheId, label: nicheId, shortLabel: nicheId };
}

/** Pre-built lookup maps for drop-in replacement of legacy constants. */
export const NICHE_COLORS: Record<string, string> = Object.fromEntries(
  QUICK_INFO.map((n) => [n.id, n.color]),
);

export const NICHE_HEX: Record<string, string> = Object.fromEntries(
  QUICK_INFO.map((n) => [n.id, n.hex]),
);

export const NICHE_LABELS: Record<string, string> = Object.fromEntries(
  QUICK_INFO.map((n) => [n.id, n.label]),
);

export const NICHE_SHORT_LABELS: Record<string, string> = Object.fromEntries(
  QUICK_INFO.map((n) => [n.id, n.shortLabel]),
);

export const NICHE_CHANNELS: Record<string, string> = Object.fromEntries(
  QUICK_INFO.map((n) => [n.id, n.handle]),
);

/** All niche IDs in canonical order. */
export const NICHE_IDS: NicheId[] = QUICK_INFO.map((n) => n.id);
