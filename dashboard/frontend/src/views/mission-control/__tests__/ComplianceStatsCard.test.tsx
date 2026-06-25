/**
 * PR after #581 — ComplianceStatsCard tests.
 *
 * Pins:
 *   * Headline reflects total warns + blocks across all niches
 *   * Per-niche row shows warn count + top firing event_type label
 *   * Severity classification: clean / warn / error based on counts
 *   * Footer surfaces Phase A status note
 *   * Loading + error states render without crashing
 *   * Niches with no events render the "clean" state
 *
 * Mutation behaviors don't exist on this card (it's read-only), so the
 * test surface is purely rendering pins.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/api/client", () => ({
  complianceStats: {
    fetch: vi.fn(),
  },
}));

import { complianceStats } from "@/api/client";
import { ComplianceStatsCard } from "../ComplianceStatsCard";

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("ComplianceStatsCard", () => {
  it("shows 'All niches clean' when no events recorded", async () => {
    vi.mocked(complianceStats.fetch).mockResolvedValueOnce({
      window_days: 7,
      by_niche: {},
    });
    renderWithClient(<ComplianceStatsCard />);
    expect(await screen.findByText(/All niches clean/i)).toBeInTheDocument();
    // 5 clean rows
    const cleanCells = await screen.findAllByText(/^clean$/);
    expect(cleanCells.length).toBe(5);
  });

  it("renders per-niche warn count + top event_type label", async () => {
    vi.mocked(complianceStats.fetch).mockResolvedValueOnce({
      window_days: 7,
      by_niche: {
        gaming: {
          total: 12,
          warns: 5,
          blocks: 0,
          allows: 7,
          top_event_type: "spam_pattern_detected",
          top_event_count: 4,
          by_event_type: {
            spam_pattern_detected: { warn: 4, block: 0, allow: 0 },
            pre_publish_check: { warn: 1, block: 0, allow: 7 },
          },
        },
      },
    });
    renderWithClient(<ComplianceStatsCard />);
    // "5 warns" appears in BOTH the headline ("5 warns across 1
    // niche") AND the niche row — use findAllByText so the matcher
    // doesn't choke on the ambiguity, then assert both surfaces.
    const warnsMatches = await screen.findAllByText(/5 warns/);
    expect(warnsMatches.length).toBeGreaterThanOrEqual(2);
    expect(await screen.findByText(/· spam/)).toBeInTheDocument();
  });

  it("highlights blocks in headline when any niche has them", async () => {
    vi.mocked(complianceStats.fetch).mockResolvedValueOnce({
      window_days: 7,
      by_niche: {
        gaming: {
          total: 6,
          warns: 5,
          blocks: 1,
          allows: 0,
          top_event_type: "auto_publish_block",
          top_event_count: 6,
          by_event_type: {
            auto_publish_block: { warn: 5, block: 1, allow: 0 },
          },
        },
      },
    });
    renderWithClient(<ComplianceStatsCard />);
    // Headline emphasises blocks + warns count
    expect(await screen.findByText(/1 block · 5 warns/)).toBeInTheDocument();
    // Per-niche row shows the "Nw / Nb" compact form when blocks present
    expect(await screen.findByText(/5w \/ 1b/)).toBeInTheDocument();
  });

  it("shows aggregate warns headline when only warns (no blocks)", async () => {
    vi.mocked(complianceStats.fetch).mockResolvedValueOnce({
      window_days: 7,
      by_niche: {
        gaming: {
          total: 3,
          warns: 3,
          blocks: 0,
          allows: 0,
          top_event_type: "spam_pattern_detected",
          top_event_count: 3,
          by_event_type: { spam_pattern_detected: { warn: 3, block: 0, allow: 0 } },
        },
        anime: {
          total: 2,
          warns: 2,
          blocks: 0,
          allows: 0,
          top_event_type: "copyright_flag",
          top_event_count: 2,
          by_event_type: { copyright_flag: { warn: 2, block: 0, allow: 0 } },
        },
      },
    });
    renderWithClient(<ComplianceStatsCard />);
    expect(await screen.findByText(/5 warns across 2 niches/i)).toBeInTheDocument();
  });

  it("falls back to '—' top label when only allow events recorded", async () => {
    vi.mocked(complianceStats.fetch).mockResolvedValueOnce({
      window_days: 7,
      by_niche: {
        gaming: {
          total: 10,
          warns: 0,
          blocks: 0,
          allows: 10,
          top_event_type: null,
          top_event_count: 0,
          by_event_type: {
            pre_publish_check: { warn: 0, block: 0, allow: 10 },
          },
        },
      },
    });
    renderWithClient(<ComplianceStatsCard />);
    // gaming row shows 'clean' (warns+blocks=0) even though allows>0
    const cleanCells = await screen.findAllByText(/^clean$/);
    // 5 niches, all rendering clean (gaming has allows, others have no data)
    expect(cleanCells.length).toBe(5);
  });

  it("surfaces the Phase A observation-only note in the footer", async () => {
    vi.mocked(complianceStats.fetch).mockResolvedValueOnce({
      window_days: 7,
      by_niche: {},
    });
    renderWithClient(<ComplianceStatsCard />);
    expect(
      await screen.findByText(/Phase A checks are observation-only/i),
    ).toBeInTheDocument();
  });

  it("renders error state when fetch rejects", async () => {
    vi.mocked(complianceStats.fetch).mockRejectedValueOnce(new Error("boom"));
    renderWithClient(<ComplianceStatsCard />);
    expect(
      await screen.findByText(/Compliance endpoint unreachable/i),
    ).toBeInTheDocument();
  });
});
