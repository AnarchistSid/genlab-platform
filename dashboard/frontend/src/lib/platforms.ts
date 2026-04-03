export interface PlatformInfo {
  id: string;
  label: string;
  shortLabel: string;
  color: string;    // CSS variable reference for Tailwind/CSS contexts
  hex: string;      // Raw hex for Recharts/SVG contexts where CSS vars don't work
}

const PLATFORM_LIST: PlatformInfo[] = [
  { id: "instagram",  label: "Instagram",    shortLabel: "IG", color: "var(--platform-instagram)", hex: "#E1306C" },
  { id: "youtube",    label: "YouTube",      shortLabel: "YT", color: "var(--platform-youtube)",   hex: "#FF0000" },
  { id: "facebook",   label: "Facebook",     shortLabel: "FB", color: "var(--platform-facebook)",  hex: "#1877F2" },
  { id: "x_twitter",  label: "X / Twitter",  shortLabel: "X",  color: "var(--platform-x)",        hex: "#1DA1F2" },
  { id: "twitter",    label: "X / Twitter",  shortLabel: "X",  color: "var(--platform-x)",        hex: "#1DA1F2" },
  { id: "threads",    label: "Threads",      shortLabel: "TH", color: "var(--platform-threads)",   hex: "#A0A0A0" },
  { id: "tiktok",     label: "TikTok",       shortLabel: "TK", color: "var(--platform-tiktok)",    hex: "#00F2EA" },
];

const platformMap = new Map<string, PlatformInfo>(
  PLATFORM_LIST.map((p) => [p.id, p]),
);

const FALLBACK: PlatformInfo = {
  id: "unknown",
  label: "Unknown",
  shortLabel: "?",
  color: "var(--text-muted)",
  hex: "#71717a",
};

/** Get platform info by ID with safe fallback. */
export function getPlatformInfo(id: string): PlatformInfo {
  return platformMap.get(id) ?? { ...FALLBACK, id, label: id, shortLabel: id.slice(0, 2).toUpperCase() };
}

/** Pre-built lookup maps for drop-in replacement of existing constants. */
export const PLATFORM_COLORS: Record<string, string> = Object.fromEntries(
  PLATFORM_LIST.map((p) => [p.id, p.color]),
);

export const PLATFORM_HEX: Record<string, string> = Object.fromEntries(
  PLATFORM_LIST.map((p) => [p.id, p.hex]),
);

export const PLATFORM_LABELS: Record<string, string> = Object.fromEntries(
  PLATFORM_LIST.map((p) => [p.id, p.label]),
);

/** All unique platform IDs (without the twitter alias). */
export const PLATFORM_IDS = PLATFORM_LIST.filter((p) => p.id !== "twitter").map((p) => p.id);
