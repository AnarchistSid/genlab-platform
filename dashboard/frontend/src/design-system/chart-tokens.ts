/**
 * Chart & platform color tokens.
 *
 * CSS variables are the source of truth (tokens.css).
 * These constants are for Recharts props that require JS strings.
 * Keep in sync with tokens.css if you change values there.
 */

export const PLATFORM_COLORS: Record<string, string> = {
  instagram: "var(--platform-instagram)",
  youtube: "var(--platform-youtube)",
  x_twitter: "var(--platform-x)",
  twitter: "var(--platform-x)",
  facebook: "var(--platform-facebook)",
  tiktok: "var(--platform-tiktok)",
  threads: "var(--platform-threads)",
};

export const CHART_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-6)",
] as const;

export const CHART_GRID_COLOR = "var(--chart-grid)";
export const CHART_FILL_OPACITY = 0.15;
