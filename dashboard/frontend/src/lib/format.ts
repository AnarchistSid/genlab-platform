/**
 * Format a date string as a human-readable relative time.
 *
 * Returns strings like "just now", "5 min ago", "2h ago",
 * "yesterday", "3 days ago", etc.
 */
export function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return "--";

  const now = new Date();
  const diffMs = now.getTime() - date.getTime();

  // Future dates — show "in X time" or the actual date
  if (diffMs < 0) {
    const futureDiffMs = -diffMs;
    const futureMinutes = Math.floor(futureDiffMs / (1000 * 60));
    const futureHours = Math.floor(futureDiffMs / (1000 * 60 * 60));
    const futureDays = Math.floor(futureDiffMs / (1000 * 60 * 60 * 24));

    if (futureMinutes < 60) return `in ${futureMinutes}m`;
    if (futureHours < 24) return `in ${futureHours}h`;
    if (futureDays === 1) return "tomorrow";
    if (futureDays < 7) return `in ${futureDays} days`;
    return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  }

  const diffSeconds = Math.floor(diffMs / 1000);
  const diffMinutes = Math.floor(diffSeconds / 60);
  const diffHours = Math.floor(diffMinutes / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffSeconds < 60) {
    return "just now";
  }

  if (diffMinutes < 60) {
    return `${diffMinutes} min ago`;
  }

  if (diffHours < 24) {
    return `${diffHours}h ago`;
  }

  if (diffDays === 1) {
    return "yesterday";
  }

  if (diffDays < 30) {
    return `${diffDays} days ago`;
  }

  const diffMonths = Math.floor(diffDays / 30);
  if (diffMonths < 12) {
    return `${diffMonths}mo ago`;
  }

  const diffYears = Math.floor(diffDays / 365);
  return `${diffYears}y ago`;
}

/**
 * Format seconds as a human-readable duration (e.g. "2m 15s", "45s").
 */
export function formatDuration(seconds: number): string {
  if (seconds < 60) {
    return `${Math.round(seconds)}s`;
  }
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  if (secs === 0) {
    return `${mins}m`;
  }
  return `${mins}m ${secs}s`;
}

/**
 * Format a cost value as USD.
 */
export function formatCost(cost: number): string {
  return `$${cost.toFixed(2)}`;
}

/** Thumbnail result with media type for proper rendering. */
export interface ThumbnailInfo {
  url: string;
  isVideo: boolean;
}

function isVideoUrl(url: string): boolean {
  return url.toLowerCase().endsWith(".mp4") || url.toLowerCase().endsWith(".mov") || url.toLowerCase().endsWith(".webm");
}

function isLocalUrl(url: string): boolean {
  return url.startsWith("/api/") || url.startsWith("/");
}

/**
 * Resolve the best available thumbnail URL from a blueprint.
 * Prefers local /api/media/ paths (always available) over CDN URLs (may expire).
 * Returns both URL and media type so components render correctly (img vs video).
 */
export function getThumbnail(bp: { slide_previews?: Array<{ url: string }> | null; visual_paths?: string | null }): string | null {
  const info = getThumbnailInfo(bp);
  return info?.url ?? null;
}

export function getThumbnailInfo(bp: { slide_previews?: Array<{ url: string }> | null; visual_paths?: string | null }): ThumbnailInfo | null {
  // 1. Prefer local visual_paths (always available, never expires)
  if (typeof bp.visual_paths === "string" && isLocalUrl(bp.visual_paths)) {
    // visual_paths from the pipeline often point to a visuals/ directory
    // (no file extension) — treat these as video since they contain .mp4 files
    const isVid = isVideoUrl(bp.visual_paths) || bp.visual_paths.includes("/visuals/");
    return { url: bp.visual_paths, isVideo: isVid };
  }
  // 2. Fallback to slide_previews — prefer local URLs, then any URL
  if (bp.slide_previews?.length) {
    const local = bp.slide_previews.find((s) => isLocalUrl(s.url));
    if (local) return { url: local.url, isVideo: isVideoUrl(local.url) };
    const first = bp.slide_previews[0]?.url;
    if (first) return { url: first, isVideo: isVideoUrl(first) };
  }
  // 3. Remote visual_paths URL (Steam CDN, YouTube CDN thumbnail fallback)
  if (typeof bp.visual_paths === "string" && bp.visual_paths.startsWith("http")) {
    return { url: bp.visual_paths, isVideo: isVideoUrl(bp.visual_paths) };
  }
  return null;
}

/**
 * Format a number compactly with locale-aware fallback for small numbers.
 * (e.g. 1200 -> "1.2K", 1500000 -> "1.5M", 42 -> "42")
 *
 * Accepts null for convenience in dashboard components — returns "-" for null.
 */
export function formatCompact(n: number | null): string {
  if (n == null) return "-";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

/**
 * Format an ISO timestamp as a short relative time string.
 * Returns compact labels like "now", "5m ago", "3h ago", "2d ago".
 */
export function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

/**
 * Get today's date as YYYY-MM-DD.
 */
export function getTodayISO(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}
