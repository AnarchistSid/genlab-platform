/**
 * PR after #586 — SourceDiscoveryCard tests.
 *
 * Coverage philosophy: pin the per-niche selector + zero-state
 * messaging + proposal-table rendering. Mutation surfaces don't
 * exist on this card (read-only ranking).
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/api/client", () => ({
  sourceDiscovery: {
    proposals: vi.fn(),
  },
}));

import { sourceDiscovery } from "@/api/client";
import { SourceDiscoveryCard } from "../SourceDiscoveryCard";

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("SourceDiscoveryCard", () => {
  it("renders zero-state when no proposals returned", async () => {
    vi.mocked(sourceDiscovery.proposals).mockResolvedValueOnce({
      niche_id: "ai_creators",
      window_days: 30,
      min_clips: 2,
      proposals: [],
    });
    renderWithClient(<SourceDiscoveryCard />);
    expect(
      await screen.findByText(/No source channels yet/i),
    ).toBeInTheDocument();
    // The zero-state should mention "try again in a few days" so
    // operators know the proposer isn't broken — it's data-starved
    expect(
      await screen.findByText(/try again in a few days/i),
    ).toBeInTheDocument();
  });

  it("renders a graceful fallback when the warning flag is present", async () => {
    vi.mocked(sourceDiscovery.proposals).mockResolvedValueOnce({
      niche_id: "ai_creators",
      window_days: 30,
      min_clips: 2,
      proposals: [],
      warning: "source_discovery module unavailable on this core build",
    });
    renderWithClient(<SourceDiscoveryCard />);
    expect(
      await screen.findByText(/Proposer unavailable on this build/i),
    ).toBeInTheDocument();
  });

  it("renders proposal rows with channel ID + clips + avg engagement", async () => {
    vi.mocked(sourceDiscovery.proposals).mockResolvedValueOnce({
      niche_id: "ai_creators",
      window_days: 30,
      min_clips: 2,
      proposals: [
        {
          source_channel_id: "UCabcdef",
          clips_used: 5,
          total_engagement: 25000,
          avg_engagement: 5000,
          last_used_at: "2026-06-20T12:00:00Z",
          channel_name: null,
          sample_blueprints: [],
        },
      ],
    });
    renderWithClient(<SourceDiscoveryCard />);
    expect(await screen.findByText("UCabcdef")).toBeInTheDocument();
    // 5 clips
    expect(await screen.findByText("5")).toBeInTheDocument();
    // avg engagement formatted as "5.0k" (compact formatter)
    expect(await screen.findByText("5.0k")).toBeInTheDocument();
  });

  it("proposal rows link to the YouTube channel page externally", async () => {
    vi.mocked(sourceDiscovery.proposals).mockResolvedValueOnce({
      niche_id: "ai_creators",
      window_days: 30,
      min_clips: 2,
      proposals: [
        {
          source_channel_id: "UCabcdef",
          clips_used: 3,
          total_engagement: 1500,
          avg_engagement: 500,
          last_used_at: null,
          channel_name: null,
          sample_blueprints: [],
        },
      ],
    });
    renderWithClient(<SourceDiscoveryCard />);
    const link = await screen.findByRole("link");
    expect(link).toHaveAttribute(
      "href",
      "https://www.youtube.com/channel/UCabcdef",
    );
    expect(link).toHaveAttribute("target", "_blank");
    // rel must include noopener for the external link
    expect(link.getAttribute("rel") || "").toContain("noopener");
  });

  it("clicking a niche pill triggers a new fetch for that niche", async () => {
    // First render: ai_creators (default niche)
    vi.mocked(sourceDiscovery.proposals)
      .mockResolvedValueOnce({
        niche_id: "ai_creators",
        window_days: 30,
        min_clips: 2,
        proposals: [],
      })
      // Second render after pill click: gaming
      .mockResolvedValueOnce({
        niche_id: "gaming",
        window_days: 30,
        min_clips: 2,
        proposals: [
          {
            source_channel_id: "UCgaming",
            clips_used: 2,
            total_engagement: 1000,
            avg_engagement: 500,
            last_used_at: null,
            channel_name: null,
            sample_blueprints: [],
          },
        ],
      });
    renderWithClient(<SourceDiscoveryCard />);

    // Wait for the initial zero-state to settle
    await screen.findByText(/No source channels yet/i);

    // Click the gaming pill (one of 5 — find by visible label)
    const gamingPill = await screen.findByRole("button", { name: /gaming/i });
    fireEvent.click(gamingPill);

    // The gaming-specific channel ID should now appear
    expect(await screen.findByText("UCgaming")).toBeInTheDocument();
  });

  it("shows an error message when the endpoint fetch rejects", async () => {
    vi.mocked(sourceDiscovery.proposals).mockRejectedValueOnce(
      new Error("net down"),
    );
    renderWithClient(<SourceDiscoveryCard />);
    expect(
      await screen.findByText(/Source-discovery endpoint unreachable/i),
    ).toBeInTheDocument();
  });

  it("compact engagement formatter handles k / M / raw correctly", async () => {
    vi.mocked(sourceDiscovery.proposals).mockResolvedValueOnce({
      niche_id: "ai_creators",
      window_days: 30,
      min_clips: 2,
      proposals: [
        {
          source_channel_id: "UCsmall",
          clips_used: 1,
          total_engagement: 500,
          avg_engagement: 500,
          last_used_at: null,
          channel_name: null,
          sample_blueprints: [],
        },
        {
          source_channel_id: "UCmid",
          clips_used: 1,
          total_engagement: 12345,
          avg_engagement: 12345,
          last_used_at: null,
          channel_name: null,
          sample_blueprints: [],
        },
        {
          source_channel_id: "UCbig",
          clips_used: 1,
          total_engagement: 1500000,
          avg_engagement: 1500000,
          last_used_at: null,
          channel_name: null,
          sample_blueprints: [],
        },
      ],
    });
    renderWithClient(<SourceDiscoveryCard />);

    // 500 stays as "500" (no k suffix below 1000)
    expect(await screen.findByText("500")).toBeInTheDocument();
    // 12345 → "12.3k"
    expect(await screen.findByText("12.3k")).toBeInTheDocument();
    // 1500000 → "1.5M"
    expect(await screen.findByText("1.5M")).toBeInTheDocument();
  });

  it("footer surfaces the proposer ranking explanation", async () => {
    vi.mocked(sourceDiscovery.proposals).mockResolvedValueOnce({
      niche_id: "ai_creators",
      window_days: 30,
      min_clips: 2,
      proposals: [],
    });
    renderWithClient(<SourceDiscoveryCard />);
    expect(
      await screen.findByText(/per-clip avg engagement.*min 2 clips/i),
    ).toBeInTheDocument();
  });
});
