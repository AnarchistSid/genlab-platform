/**
 * Phase 3.A observability card tests (2026-08-14).
 *
 * Pin the load-bearing "active vs observation only" badge — same
 * class-of-bug we watch for on CrossNichePriorsCard. Also pin the
 * thin-baseline dimming (opacity-70 tint when our_reference is
 * < 10 views) since that's the operator's cue that the delta ratio
 * is inflated by a broken metric-collector, not by real reach gap.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/api/client", () => ({
  competitorDeltas: {
    latest: vi.fn(),
  },
}));

import { competitorDeltas } from "@/api/client";
import { CompetitorDeltasCard } from "../CompetitorDeltasCard";
import type { CompetitorDeltasArtifact } from "@/api/types";

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const _stub = (
  overrides: Partial<CompetitorDeltasArtifact> = {},
): CompetitorDeltasArtifact => ({
  generated_at: "2026-08-14T09:30:00+00:00",
  flag_enabled: false,
  rows: [
    {
      niche_id: "ai_creators",
      competitor_channel_id: "UCBJycsmduvYEL83R_U4JriQ",
      competitor_channel_label: "MKBHD",
      competitor_video_id: "vid1",
      competitor_title: "Samsung Z Fold 8 Review",
      competitor_published_at: "2026-08-01T00:00:00+00:00",
      competitor_view_count: 4_635_853,
      competitor_like_count: 117_000,
      competitor_comment_count: 6_000,
      our_reference_view_count: 15_000,
      delta_views: 4_620_853,
      delta_ratio: 309.0,
      computed_at: "2026-08-14T09:30:00+00:00",
    },
  ],
  ...overrides,
});

describe("CompetitorDeltasCard", () => {
  it("shows 'No competitor deltas yet' cold-start message", async () => {
    vi.mocked(competitorDeltas.latest).mockResolvedValue(null);
    renderWithClient(<CompetitorDeltasCard />);
    expect(
      await screen.findByText(/No competitor deltas yet/i),
    ).toBeInTheDocument();
  });

  it("shows 'observation only' badge when flag is off", async () => {
    vi.mocked(competitorDeltas.latest).mockResolvedValue(
      _stub({ flag_enabled: false }),
    );
    renderWithClient(<CompetitorDeltasCard />);
    expect(await screen.findByText("observation only")).toBeInTheDocument();
  });

  it("shows 'active' badge when flag is on", async () => {
    vi.mocked(competitorDeltas.latest).mockResolvedValue(
      _stub({ flag_enabled: true }),
    );
    renderWithClient(<CompetitorDeltasCard />);
    expect(await screen.findByText("active")).toBeInTheDocument();
  });

  it("renders competitor label, title, and delta ratio", async () => {
    vi.mocked(competitorDeltas.latest).mockResolvedValue(_stub());
    renderWithClient(<CompetitorDeltasCard />);
    expect(await screen.findByText("MKBHD")).toBeInTheDocument();
    expect(screen.getByText(/Samsung Z Fold 8 Review/)).toBeInTheDocument();
    expect(screen.getByText(/309x/)).toBeInTheDocument();
  });

  it("groups rows by niche with a niche header", async () => {
    vi.mocked(competitorDeltas.latest).mockResolvedValue(_stub());
    renderWithClient(<CompetitorDeltasCard />);
    expect(await screen.findByText(/ai_creators/i)).toBeInTheDocument();
  });

  it("renders empty state when no rows meet the floor", async () => {
    vi.mocked(competitorDeltas.latest).mockResolvedValue(
      _stub({ rows: [] }),
    );
    renderWithClient(<CompetitorDeltasCard />);
    expect(
      await screen.findByText(/No competitor uploads met the 5x floor/i),
    ).toBeInTheDocument();
  });

  it("renders 'n/a' for null delta_ratio (division-guarded)", async () => {
    vi.mocked(competitorDeltas.latest).mockResolvedValue(
      _stub({
        rows: [
          {
            ...(_stub().rows[0]),
            delta_ratio: null,
            our_reference_view_count: 0,
          },
        ],
      }),
    );
    renderWithClient(<CompetitorDeltasCard />);
    expect(await screen.findByText(/n\/a/i)).toBeInTheDocument();
  });

  it("links competitor video_id to YouTube", async () => {
    vi.mocked(competitorDeltas.latest).mockResolvedValue(_stub());
    renderWithClient(<CompetitorDeltasCard />);
    const link = await screen.findByText(/Samsung Z Fold 8/i);
    expect(link.closest("a")).toHaveAttribute(
      "href",
      "https://www.youtube.com/watch?v=vid1",
    );
  });
});
