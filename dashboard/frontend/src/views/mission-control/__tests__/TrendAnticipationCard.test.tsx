/**
 * Intervention 5 Session 3 — TrendAnticipationCard tests.
 *
 * Coverage:
 * - Header + subtitle render
 * - Cold-start "No ranking today" message when API returns null
 * - Top topic renders with composite score + confidence label
 * - Signal strip: null signals render as hollow slots (data-testid
 *   ``signal-<key>-null``), populated as filled (``signal-<key>-value``)
 * - formatPeakEta path — "at peak" vs positive/negative hours
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/api/client", () => ({
  trendAnticipation: {
    latest: vi.fn(),
  },
}));

import { trendAnticipation } from "@/api/client";
import { TrendAnticipationCard } from "../TrendAnticipationCard";
import type { TrendAnticipationArtifact } from "@/api/types";

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const _stubArtifact = (
  overrides: Partial<TrendAnticipationArtifact> = {},
): TrendAnticipationArtifact => ({
  generated_at: "2026-07-01T03:30:00+00:00",
  niche_id: "gaming",
  flag_enabled: false,
  ranking: [
    {
      topic: "Zelda TOTK 2",
      niche_id: "gaming",
      composite_score: 0.82,
      confidence: 0.75,
      anticipated_peak_hours_ahead: 12,
      signals: {
        search_velocity: 0.85,
        creator_pickup: 0.72,
        social_velocity: 0.6,
        news_lead: null,
      },
      reasons: ["search_velocity=0.850"],
    },
  ],
  ...overrides,
});

describe("TrendAnticipationCard", () => {
  it("renders card header + subtitle", async () => {
    vi.mocked(trendAnticipation.latest).mockResolvedValue(null);
    renderWithClient(<TrendAnticipationCard />);
    expect(
      await screen.findByText(/Trend anticipation/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/03:30 UTC/)).toBeInTheDocument();
  });

  it("shows 'No ranking today' for cold-start niches", async () => {
    vi.mocked(trendAnticipation.latest).mockResolvedValue(null);
    renderWithClient(<TrendAnticipationCard />);
    // 5 niche rows all cold-start → 5 identical fallback messages.
    // Asserting ≥ 1 (as with StrategistReportCard) keeps the pin
    // forward-compat with a future 6th niche.
    expect(
      await screen.findAllByText(/No ranking today/i),
    ).toBeTruthy();
  });

  it("renders top topic with composite score and confidence", async () => {
    vi.mocked(trendAnticipation.latest).mockImplementation(
      (nicheId: string) => {
        if (nicheId === "gaming") {
          return Promise.resolve(_stubArtifact());
        }
        return Promise.resolve(null);
      },
    );
    renderWithClient(<TrendAnticipationCard />);

    // Topic string appears
    expect(await screen.findByText("Zelda TOTK 2")).toBeInTheDocument();
    // Composite as percentage
    expect(screen.getByText("82%")).toBeInTheDocument();
    // Confidence as "3/4 signals" (0.75 * 4 = 3)
    expect(screen.getByText("3/4")).toBeInTheDocument();
    // Peak ETA as "12h ahead"
    expect(screen.getByText("12h ahead")).toBeInTheDocument();
  });

  it("signal strip renders null slot for missing signal", async () => {
    vi.mocked(trendAnticipation.latest).mockImplementation((nicheId) => {
      if (nicheId === "gaming") return Promise.resolve(_stubArtifact());
      return Promise.resolve(null);
    });
    renderWithClient(<TrendAnticipationCard />);
    // news_lead is null in the stub → renders as null-slot
    expect(
      await screen.findByTestId("signal-news_lead-null"),
    ).toBeInTheDocument();
    // The other three are populated
    expect(
      screen.getByTestId("signal-search_velocity-value"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("signal-creator_pickup-value"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("signal-social_velocity-value"),
    ).toBeInTheDocument();
  });

  it("formatPeakEta path renders 'at peak' for hours=0", async () => {
    vi.mocked(trendAnticipation.latest).mockImplementation((nicheId) => {
      if (nicheId === "anime") {
        return Promise.resolve(
          _stubArtifact({
            niche_id: "anime",
            ranking: [
              {
                ..._stubArtifact().ranking[0],
                anticipated_peak_hours_ahead: 0,
              },
            ],
          }),
        );
      }
      return Promise.resolve(null);
    });
    renderWithClient(<TrendAnticipationCard />);
    expect(await screen.findByText("at peak")).toBeInTheDocument();
  });

  it("formatPeakEta path renders 'past' for negative hours", async () => {
    vi.mocked(trendAnticipation.latest).mockImplementation((nicheId) => {
      if (nicheId === "movies") {
        return Promise.resolve(
          _stubArtifact({
            niche_id: "movies",
            ranking: [
              {
                ..._stubArtifact().ranking[0],
                anticipated_peak_hours_ahead: -12,
              },
            ],
          }),
        );
      }
      return Promise.resolve(null);
    });
    renderWithClient(<TrendAnticipationCard />);
    expect(await screen.findByText("12h past")).toBeInTheDocument();
  });
});
