/**
 * PR W — KpiHero window-label pin (audit H1, 2026-03-31).
 *
 * The audit flagged that Mission Control showed "TOTAL REACH = 641.6K"
 * (all-time) while Analytics showed "Total Reach = 20.8K" (7-day window)
 * with no label disambiguation. Operators thought one of them was
 * wrong. Both were correct — they measure different windows.
 *
 * Fix: append "(all time)" to the MC reach + likes labels and "(Nd)"
 * to Analytics labels. This test pins the MC suffix.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/hooks/use-cross-niche-overview", () => ({
  useCrossNicheOverview: vi.fn(),
}));

import { useCrossNicheOverview } from "@/hooks/use-cross-niche-overview";
import { KpiHero } from "../KpiHero";

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const MOCK_OVERVIEW = {
  niches: [
    { niche_id: "gaming", published_today: 1, platform_publish_status: {} },
    { niche_id: "sports", published_today: 2, platform_publish_status: {} },
  ],
  global: {
    total_reach: 641_600,
    total_likes: 12_345,
    platform_health: {
      youtube: "ok",
      instagram: "ok",
      facebook: "ok",
    },
  },
  niche_daily_reach: {},
};

describe("KpiHero (H1 window-label pin)", () => {
  it("renders 'TOTAL REACH (all time)' label — disambiguates from Analytics window", () => {
    vi.mocked(useCrossNicheOverview).mockReturnValue({
      data: MOCK_OVERVIEW,
      isLoading: false,
    } as never);

    renderWithClient(<KpiHero />);

    // The label must include the "(all time)" qualifier so operators
    // can't confuse this all-time aggregate with the windowed values
    // on the Analytics page. Without the suffix, "641K reach (MC) vs
    // 20K reach (Analytics)" reads like a data inconsistency.
    expect(screen.getByText(/TOTAL REACH \(all time\)/i)).toBeInTheDocument();
  });

  it("renders 'TOTAL LIKES (all time)' label", () => {
    vi.mocked(useCrossNicheOverview).mockReturnValue({
      data: MOCK_OVERVIEW,
      isLoading: false,
    } as never);

    renderWithClient(<KpiHero />);

    expect(screen.getByText(/TOTAL LIKES \(all time\)/i)).toBeInTheDocument();
  });
});
