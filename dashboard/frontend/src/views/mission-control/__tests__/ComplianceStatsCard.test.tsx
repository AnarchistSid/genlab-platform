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
import { fireEvent, render, screen, within } from "@testing-library/react";
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

// ── Drill-down popover (PR after #582) ────────────────────────────

describe("ComplianceStatsCard drill-down", () => {
  function statsFixture() {
    return {
      window_days: 7,
      by_niche: {
        gaming: {
          total: 12,
          warns: 5,
          blocks: 1,
          allows: 6,
          top_event_type: "spam_pattern_detected",
          top_event_count: 4,
          by_event_type: {
            spam_pattern_detected: { warn: 4, block: 1, allow: 0 },
            pre_publish_check: { warn: 1, block: 0, allow: 5 },
            copyright_flag: { warn: 0, block: 0, allow: 1 },
          },
        },
      },
    };
  }

  it("drill-down panel is hidden by default", async () => {
    vi.mocked(complianceStats.fetch).mockResolvedValueOnce(statsFixture());
    renderWithClient(<ComplianceStatsCard />);
    // Wait for the card to render
    await screen.findByText(/· spam/);
    // Panel must NOT be in the DOM until clicked
    expect(screen.queryByTestId("drill-down-panel")).not.toBeInTheDocument();
  });

  it("clicking a drillable niche row reveals the by_event_type panel", async () => {
    vi.mocked(complianceStats.fetch).mockResolvedValueOnce(statsFixture());
    renderWithClient(<ComplianceStatsCard />);
    // Find the gaming row by its button role (only drillable rows
    // get role=button)
    const rows = await screen.findAllByRole("button");
    // Only one drillable row in fixture (gaming has by_event_type)
    expect(rows.length).toBe(1);
    fireEvent.click(rows[0]);

    const panel = await screen.findByTestId("drill-down-panel");
    expect(panel).toBeInTheDocument();
    // Panel should show all 3 event_types in the fixture
    expect(within(panel).getByText("spam")).toBeInTheDocument();
    expect(within(panel).getByText("pre-publish")).toBeInTheDocument();
    expect(within(panel).getByText("copyright")).toBeInTheDocument();
  });

  it("panel orders rows by attention (warn+block) desc", async () => {
    vi.mocked(complianceStats.fetch).mockResolvedValueOnce(statsFixture());
    renderWithClient(<ComplianceStatsCard />);
    const rows = await screen.findAllByRole("button");
    fireEvent.click(rows[0]);

    const panel = await screen.findByTestId("drill-down-panel");
    // spam (4w+1b=5 attn) → pre-publish (1w=1 attn) → copyright (0 attn)
    const listItems = within(panel).getAllByRole("listitem");
    expect(listItems[0]).toHaveTextContent("spam");
    expect(listItems[1]).toHaveTextContent("pre-publish");
    expect(listItems[2]).toHaveTextContent("copyright");
  });

  it("panel surfaces warn / block / allow counts per event_type", async () => {
    vi.mocked(complianceStats.fetch).mockResolvedValueOnce(statsFixture());
    renderWithClient(<ComplianceStatsCard />);
    const rows = await screen.findAllByRole("button");
    fireEvent.click(rows[0]);

    const panel = await screen.findByTestId("drill-down-panel");
    // spam: 4w + 1b
    expect(within(panel).getByText("4w")).toBeInTheDocument();
    expect(within(panel).getByText("1b")).toBeInTheDocument();
    // pre-publish: 1w + 5a
    expect(within(panel).getByText("1w")).toBeInTheDocument();
    expect(within(panel).getByText("5a")).toBeInTheDocument();
  });

  it("clicking again collapses the panel", async () => {
    vi.mocked(complianceStats.fetch).mockResolvedValueOnce(statsFixture());
    renderWithClient(<ComplianceStatsCard />);
    const rows = await screen.findAllByRole("button");

    fireEvent.click(rows[0]);
    expect(await screen.findByTestId("drill-down-panel")).toBeInTheDocument();
    fireEvent.click(rows[0]);
    expect(screen.queryByTestId("drill-down-panel")).not.toBeInTheDocument();
  });

  it("zero-state niches are not interactive (no drillable role)", async () => {
    // No niches have data → no drillable rows
    vi.mocked(complianceStats.fetch).mockResolvedValueOnce({
      window_days: 7,
      by_niche: {},
    });
    renderWithClient(<ComplianceStatsCard />);
    await screen.findByText(/All niches clean/i);
    // No role=button rows — clean niches aren't drillable
    expect(screen.queryAllByRole("button").length).toBe(0);
  });

  it("aria-expanded attribute reflects the panel state for accessibility", async () => {
    vi.mocked(complianceStats.fetch).mockResolvedValueOnce(statsFixture());
    renderWithClient(<ComplianceStatsCard />);
    const rows = await screen.findAllByRole("button");
    // Collapsed initially
    expect(rows[0]).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(rows[0]);
    expect(rows[0]).toHaveAttribute("aria-expanded", "true");
  });

  it("keyboard activation (Enter) toggles the panel", async () => {
    vi.mocked(complianceStats.fetch).mockResolvedValueOnce(statsFixture());
    renderWithClient(<ComplianceStatsCard />);
    const rows = await screen.findAllByRole("button");
    fireEvent.keyDown(rows[0], { key: "Enter" });
    expect(await screen.findByTestId("drill-down-panel")).toBeInTheDocument();
  });
});
