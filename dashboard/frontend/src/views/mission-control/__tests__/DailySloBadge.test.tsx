import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// Mock the data hook so the component can render without a QueryClient
// or any network. Each test overrides the return value.
let mockReturn: unknown = { isLoading: false, isError: false, data: undefined };
vi.mock("@/hooks/use-cross-niche-overview", () => ({
  useCrossNicheOverview: () => mockReturn,
}));

import { DailySloBadge } from "../DailySloBadge";

function makeNiche(id: string, publishedToday: number) {
  return {
    id,
    display_name: id,
    accent_hex: "#000000",
    pipeline_status: "idle" as const,
    current_stage: null,
    last_run_at: null,
    pending_review: 0,
    published_today: publishedToday,
    target_posts_per_day: 1,
    best_performer_today: null,
  };
}

describe("DailySloBadge", () => {
  it("shows a dash and 'pending' rows when no niches have published yet", () => {
    mockReturn = {
      isLoading: false,
      isError: false,
      data: {
        niches: [
          makeNiche("ai_creators", 0),
          makeNiche("anime", 0),
          makeNiche("gaming", 0),
          makeNiche("movies", 0),
          makeNiche("sports", 0),
        ],
      },
    };
    render(<DailySloBadge />);
    expect(screen.getByText(/0 \/ 5/)).toBeInTheDocument();
    expect(screen.getAllByText(/pending/i).length).toBe(5);
  });

  it("shows green '5 / 5' when every niche has met the SLO", () => {
    mockReturn = {
      isLoading: false,
      isError: false,
      data: {
        niches: [
          makeNiche("ai_creators", 1),
          makeNiche("anime", 1),
          makeNiche("gaming", 1),
          makeNiche("movies", 1),
          makeNiche("sports", 1),
        ],
      },
    };
    render(<DailySloBadge />);
    expect(screen.getByText(/5 \/ 5/)).toBeInTheDocument();
    expect(screen.getAllByText(/met/i).length).toBe(5);
  });

  it("surfaces a cap-violation alert when a niche published > 1", () => {
    mockReturn = {
      isLoading: false,
      isError: false,
      data: {
        niches: [
          makeNiche("gaming", 2),
          makeNiche("sports", 1),
        ],
      },
    };
    render(<DailySloBadge />);
    expect(screen.getByText(/cap violated/i)).toBeInTheDocument();
    // The headline-level alert must mention the count + PR ref so the
    // operator knows what regressed.
    expect(screen.getByText(/over the 1\/day cap/i)).toBeInTheDocument();
    expect(screen.getByText(/PR #220/)).toBeInTheDocument();
  });

  it("renders an error state without crashing when the overview endpoint fails", () => {
    mockReturn = { isLoading: false, isError: true, data: undefined };
    render(<DailySloBadge />);
    expect(
      screen.getByText(/Overview endpoint unreachable/i),
    ).toBeInTheDocument();
  });

  it("renders skeleton rows during initial load", () => {
    mockReturn = { isLoading: true, isError: false, data: undefined };
    const { container } = render(<DailySloBadge />);
    // 5 animate-pulse skeleton divs while data is loading
    expect(container.querySelectorAll(".animate-pulse").length).toBe(5);
  });
});
