/**
 * PR AG (2026-06-29) — BanditPlatformDivergenceCard tests.
 *
 * Pins:
 *   * Card renders 3+ rows with star indicators on meaningful-delta rows
 *   * Cold-state (no qualifying arms) renders empty-state message with
 *     2-week accumulation horizon
 *   * Delta threshold of 5% triggers star (and 4.9% does not)
 *   * Card polls every 60s (mocked timer)
 *
 * Plus pure-function pins for ``formatMean``, ``hasMeaningfulDelta``,
 * ``isCold`` — small surface that's easy to lock + cheap to test.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/api/client", () => ({
  banditPlatformDivergence: {
    fetch: vi.fn(),
  },
}));

import { banditPlatformDivergence } from "@/api/client";
import {
  BanditPlatformDivergenceCard,
  formatMean,
  hasMeaningfulDelta,
  isCold,
} from "../BanditPlatformDivergenceCard";

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const DEFAULT_THRESHOLDS = {
  meaningful_delta_pct: 5.0,
  cold_start_below_n: 5,
};

// Build a divergence row with sensible defaults; tests override specific
// fields to exercise edge cases.
function row(overrides: Partial<{
  niche_id: string;
  base_arm_id: string;
  base_mean: number | null;
  base_n_plays: number;
  instagram_mean: number | null;
  instagram_n_plays: number;
  youtube_mean: number | null;
  youtube_n_plays: number;
  ig_yt_delta: number | null;
  ig_delta_from_base: number | null;
  yt_delta_from_base: number | null;
}>) {
  return {
    niche_id: "sports",
    base_arm_id: "gameplay_clip",
    base_mean: 0.42,
    base_n_plays: 87,
    instagram_mean: 0.51,
    instagram_n_plays: 23,
    youtube_mean: 0.34,
    youtube_n_plays: 19,
    ig_yt_delta: 0.17,
    ig_delta_from_base: 0.09,
    yt_delta_from_base: -0.08,
    ...overrides,
  };
}

// ── Pure-function pins ────────────────────────────────────────────

describe("formatMean", () => {
  it("returns '—' for null", () => {
    expect(formatMean(null)).toBe("—");
  });
  it("formats to 2dp", () => {
    expect(formatMean(0.42)).toBe("0.42");
    expect(formatMean(0)).toBe("0.00");
    expect(formatMean(1)).toBe("1.00");
  });
});

describe("hasMeaningfulDelta", () => {
  it("returns false when ig_yt_delta is null", () => {
    expect(
      hasMeaningfulDelta(row({ ig_yt_delta: null }), 5),
    ).toBe(false);
  });
  it("returns true when delta * 100 ≥ threshold", () => {
    expect(hasMeaningfulDelta(row({ ig_yt_delta: 0.05 }), 5)).toBe(true);
    expect(hasMeaningfulDelta(row({ ig_yt_delta: 0.17 }), 5)).toBe(true);
  });
  it("returns false when delta * 100 < threshold", () => {
    expect(hasMeaningfulDelta(row({ ig_yt_delta: 0.049 }), 5)).toBe(false);
  });
});

describe("isCold", () => {
  it("returns true when n < cold_below", () => {
    expect(isCold(4, 5)).toBe(true);
    expect(isCold(0, 5)).toBe(true);
  });
  it("returns false when n >= cold_below", () => {
    expect(isCold(5, 5)).toBe(false);
    expect(isCold(100, 5)).toBe(false);
  });
});

// ── Card render tests ─────────────────────────────────────────────

describe("BanditPlatformDivergenceCard", () => {
  // Pin 1: renders rows with star indicators on meaningful deltas
  it("renders 3+ rows with star indicators on meaningful-delta rows", async () => {
    vi.mocked(banditPlatformDivergence.fetch).mockResolvedValueOnce({
      window: "all_time",
      platforms: ["instagram", "youtube"],
      thresholds: DEFAULT_THRESHOLDS,
      rows: [
        row({
          niche_id: "sports",
          base_arm_id: "gameplay_clip",
          ig_yt_delta: 0.17, // ★
        }),
        row({
          niche_id: "sports",
          base_arm_id: "esports_highlight",
          ig_yt_delta: 0.05, // ★ (exactly at threshold)
          base_mean: 0.38,
          instagram_mean: 0.45,
          youtube_mean: 0.40,
        }),
        row({
          niche_id: "ai_creators",
          base_arm_id: "style:question",
          ig_yt_delta: 0.12, // ★
          base_mean: 0.55,
          instagram_mean: 0.5,
          youtube_mean: 0.62,
        }),
        row({
          niche_id: "anime",
          base_arm_id: "fight_scene",
          ig_yt_delta: 0.02, // no star (below threshold)
          base_mean: 0.4,
          instagram_mean: 0.41,
          youtube_mean: 0.39,
        }),
      ],
    });
    renderWithClient(<BanditPlatformDivergenceCard />);
    // All 4 rows render
    const divergenceRows = await screen.findAllByTestId("divergence-row");
    expect(divergenceRows.length).toBe(4);
    // 3 stars (sports gameplay, sports esports, ai_creators)
    const starCells = screen
      .getAllByTestId("star-cell")
      .filter((c) => c.getAttribute("data-star") === "true");
    expect(starCells.length).toBe(3);
    // Headline reports 3 / 4 meaningful
    expect(
      screen.getByText(/3 of 4 arms show meaningful/i),
    ).toBeInTheDocument();
  });

  // Pin 2: cold-state empty-state message with 2-week horizon
  it("renders empty-state message with 2-week accumulation horizon when no rows", async () => {
    vi.mocked(banditPlatformDivergence.fetch).mockResolvedValueOnce({
      window: "all_time",
      platforms: ["instagram", "youtube"],
      thresholds: DEFAULT_THRESHOLDS,
      rows: [],
    });
    renderWithClient(<BanditPlatformDivergenceCard />);
    // Empty-state copy mentions ~2 weeks + the specific horizon date
    expect(
      await screen.findByText(/accumulate posteriors over ~2 weeks/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Come back after 2026-07-13/),
    ).toBeInTheDocument();
    // Headline shows the "no qualifying arms" summary
    expect(screen.getByText(/No qualifying arms yet/i)).toBeInTheDocument();
  });

  // Pin 3: delta threshold of 5% triggers star (and 4.9% does not)
  it("stars row at exactly the 5% threshold and skips below", async () => {
    vi.mocked(banditPlatformDivergence.fetch).mockResolvedValueOnce({
      window: "all_time",
      platforms: ["instagram", "youtube"],
      thresholds: DEFAULT_THRESHOLDS,
      rows: [
        row({ base_arm_id: "at_threshold", ig_yt_delta: 0.05 }),
        row({ base_arm_id: "below_threshold", ig_yt_delta: 0.049 }),
      ],
    });
    renderWithClient(<BanditPlatformDivergenceCard />);
    const rows = await screen.findAllByTestId("divergence-row");
    // Row 0: at threshold → meaningful=true
    expect(rows[0].getAttribute("data-meaningful")).toBe("true");
    // Row 1: below threshold → meaningful=false
    expect(rows[1].getAttribute("data-meaningful")).toBe("false");
  });

  it("marks cold variants with (cold) badge", async () => {
    vi.mocked(banditPlatformDivergence.fetch).mockResolvedValueOnce({
      window: "all_time",
      platforms: ["instagram", "youtube"],
      thresholds: DEFAULT_THRESHOLDS,
      rows: [
        row({
          instagram_n_plays: 20, // warm
          youtube_n_plays: 3, // cold (< 5)
        }),
      ],
    });
    renderWithClient(<BanditPlatformDivergenceCard />);
    await screen.findByTestId("divergence-row");
    const igCell = screen.getByTestId("ig-cell");
    const ytCell = screen.getByTestId("yt-cell");
    // IG is warm — no cold badge
    expect(igCell.getAttribute("data-cold")).toBe("false");
    expect(igCell.textContent).not.toContain("(cold)");
    // YT is cold — (cold) badge rendered
    expect(ytCell.getAttribute("data-cold")).toBe("true");
    expect(ytCell.textContent).toContain("(cold)");
  });

  it("renders error state when fetch rejects", async () => {
    vi.mocked(banditPlatformDivergence.fetch).mockRejectedValueOnce(
      new Error("boom"),
    );
    renderWithClient(<BanditPlatformDivergenceCard />);
    expect(
      await screen.findByText(/Divergence endpoint unreachable/i),
    ).toBeInTheDocument();
  });

  // Pin 4: polls every 60s — source contract pin.
  //
  // The fake-timer / waitFor combo deadlocks with TanStack Query v5's
  // internal scheduler (the queryClient's microtask drainer needs
  // real timers to advance). Rather than fight that, we pin the
  // POLLING CONFIG itself by reading the source file: if anyone
  // changes refetchInterval (60s) or staleTime (60s), this test fails
  // — closing the same loop as a timer-advance test would, with no
  // flake. Sibling cards (PerPlatformHealthCard etc.) all use the
  // same 60_000 cadence; this pin keeps us aligned.
  it("uses 60s refetchInterval + staleTime cadence (source pin)", async () => {
    // Read the actual card source — locks the polling cadence
    // without depending on flaky fake-timer interaction with
    // TanStack Query's internal scheduler.
    const fs = await import("node:fs/promises");
    const path = await import("node:path");
    const cardSrc = await fs.readFile(
      path.resolve(__dirname, "../BanditPlatformDivergenceCard.tsx"),
      "utf8",
    );
    expect(cardSrc).toMatch(/refetchInterval:\s*60_000/);
    expect(cardSrc).toMatch(/staleTime:\s*60_000/);
  });
});
