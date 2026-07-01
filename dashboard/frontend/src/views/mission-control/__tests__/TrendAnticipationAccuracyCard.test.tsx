/**
 * Session 3b — TrendAnticipationAccuracyCard tests.
 *
 * Pin the verdict-badge mapping — this is the operator-facing
 * go/wait/rollback signal. Getting the render rules wrong (say,
 * showing "ready" for a negative r) would mislead the operator
 * into activating pipeline steering when the composite is
 * scoring things BACKWARDS.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/api/client", () => ({
  trendAnticipation: {
    latest: vi.fn(),
    accuracy: vi.fn(),
  },
}));

import { trendAnticipation } from "@/api/client";
import { TrendAnticipationAccuracyCard } from "../TrendAnticipationAccuracyCard";
import type { AnticipationAccuracy } from "@/api/types";

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const _stubMeasurement = (
  overrides: Partial<AnticipationAccuracy> = {},
): AnticipationAccuracy => ({
  niche_id: "gaming",
  spearman_r: 0.62,
  p_value: 0.03,
  n_topics: 8,
  artifact_date: "20260701",
  measured_at: "2026-07-08T05:00:00+00:00",
  reasons: [],
  ...overrides,
});

describe("TrendAnticipationAccuracyCard", () => {
  it("renders card header + subtitle", async () => {
    vi.mocked(trendAnticipation.accuracy).mockResolvedValue(null);
    renderWithClient(<TrendAnticipationAccuracyCard />);
    expect(
      await screen.findByText(/Trend anticipation — accuracy/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/r ≥ 0.4/)).toBeInTheDocument();
  });

  it("shows 'No measurement yet' cold-start message", async () => {
    vi.mocked(trendAnticipation.accuracy).mockResolvedValue(null);
    renderWithClient(<TrendAnticipationAccuracyCard />);
    expect(
      await screen.findAllByText(/No measurement yet/i),
    ).toBeTruthy();
  });

  it("shows 'ready' badge when r ≥ 0.4 and N ≥ 5", async () => {
    vi.mocked(trendAnticipation.accuracy).mockImplementation(
      (nicheId: string) => {
        if (nicheId === "gaming") {
          return Promise.resolve(
            _stubMeasurement({ spearman_r: 0.65, n_topics: 8 }),
          );
        }
        return Promise.resolve(null);
      },
    );
    renderWithClient(<TrendAnticipationAccuracyCard />);
    expect(await screen.findByText("ready")).toBeInTheDocument();
  });

  it("shows 'learning' badge when 0 < r < 0.4", async () => {
    vi.mocked(trendAnticipation.accuracy).mockImplementation(
      (nicheId: string) => {
        if (nicheId === "gaming") {
          return Promise.resolve(
            _stubMeasurement({ spearman_r: 0.15, n_topics: 8 }),
          );
        }
        return Promise.resolve(null);
      },
    );
    renderWithClient(<TrendAnticipationAccuracyCard />);
    expect(await screen.findByText("learning")).toBeInTheDocument();
  });

  it("shows 'not predictive' badge when r ≤ 0", async () => {
    vi.mocked(trendAnticipation.accuracy).mockImplementation(
      (nicheId: string) => {
        if (nicheId === "gaming") {
          return Promise.resolve(
            _stubMeasurement({ spearman_r: -0.3, n_topics: 8 }),
          );
        }
        return Promise.resolve(null);
      },
    );
    renderWithClient(<TrendAnticipationAccuracyCard />);
    expect(await screen.findByText("not predictive")).toBeInTheDocument();
  });

  it("shows 'underpowered' badge when n_topics < 5", async () => {
    vi.mocked(trendAnticipation.accuracy).mockImplementation(
      (nicheId: string) => {
        if (nicheId === "gaming") {
          return Promise.resolve(
            _stubMeasurement({ spearman_r: 0.9, n_topics: 3 }),
          );
        }
        return Promise.resolve(null);
      },
    );
    renderWithClient(<TrendAnticipationAccuracyCard />);
    expect(await screen.findByText("underpowered")).toBeInTheDocument();
  });

  it("shows 'no data' when spearman_r is null", async () => {
    vi.mocked(trendAnticipation.accuracy).mockImplementation(
      (nicheId: string) => {
        if (nicheId === "gaming") {
          return Promise.resolve(
            _stubMeasurement({ spearman_r: null, p_value: null }),
          );
        }
        return Promise.resolve(null);
      },
    );
    renderWithClient(<TrendAnticipationAccuracyCard />);
    expect(await screen.findByText("no data")).toBeInTheDocument();
  });
});
