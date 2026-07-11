/**
 * Layer 5 attribution health card tests (PR #Layer5, 2026-07-11).
 *
 * Pins the per-niche status colour mapping (misclassifying critical
 * as healthy would tell the operator the pipeline is fine when it's
 * actually leaking attribution) + the overall footer aggregation
 * + the zero-state / loading / error rendering.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/api/client", () => ({
  attributionHealth: {
    stats: vi.fn(),
  },
}));

import { attributionHealth } from "@/api/client";
import { AttributionHealthCard } from "../AttributionHealthCard";
import type { AttributionHealthResponse } from "@/api/types";

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const stub = (
  overrides: Partial<AttributionHealthResponse> = {},
): AttributionHealthResponse => ({
  window_hours: 24,
  generated_at: "2026-07-11T14:00:00+00:00",
  threshold_healthy_pct: 95,
  threshold_caution_pct: 90,
  niches: [
    {
      niche_id: "ai_creators",
      total_published: 8,
      with_attribution: 7,
      attribution_pct: 87.5,
      status: "critical",
    },
    {
      niche_id: "anime",
      total_published: 5,
      with_attribution: 5,
      attribution_pct: 100,
      status: "healthy",
    },
    {
      niche_id: "gaming",
      total_published: 8,
      with_attribution: 8,
      attribution_pct: 100,
      status: "healthy",
    },
    {
      niche_id: "movies",
      total_published: 6,
      with_attribution: 6,
      attribution_pct: 100,
      status: "healthy",
    },
    {
      niche_id: "sports",
      total_published: 5,
      with_attribution: 5,
      attribution_pct: 100,
      status: "healthy",
    },
  ],
  overall: {
    total_published: 32,
    with_attribution: 31,
    attribution_pct: 96.9,
    status: "healthy",
  },
  ...overrides,
});

describe("AttributionHealthCard", () => {
  it("renders per-niche rows with pct + status pill", async () => {
    vi.mocked(attributionHealth.stats).mockResolvedValue(stub());
    renderWithClient(<AttributionHealthCard />);
    // await react-query settle
    await screen.findByText("Overall");
    // gaming should show 100.0% (the post-Twitch-fix state)
    expect(screen.getAllByText("100.0%").length).toBeGreaterThanOrEqual(1);
    // at least one row shows the critical pill (ai_creators)
    expect(screen.getByText("critical")).toBeInTheDocument();
    // at least one healthy pill on niches + overall
    expect(screen.getAllByText("healthy").length).toBeGreaterThanOrEqual(2);
  });

  it("shows overall footer with aggregate stats", async () => {
    vi.mocked(attributionHealth.stats).mockResolvedValue(stub());
    renderWithClient(<AttributionHealthCard />);
    await screen.findByText("Overall");
    expect(screen.getByText("96.9%")).toBeInTheDocument();
  });

  it("renders no_data status pill when a niche has zero publishes", async () => {
    vi.mocked(attributionHealth.stats).mockResolvedValue(
      stub({
        niches: [
          {
            niche_id: "gaming",
            total_published: 0,
            with_attribution: 0,
            attribution_pct: 0,
            status: "no_data",
          },
        ],
      }),
    );
    renderWithClient(<AttributionHealthCard />);
    await screen.findByText("Overall");
    expect(screen.getByText("no data")).toBeInTheDocument();
    // Ensure pct is dashed out, not shown as 0.0%
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(1);
  });

  it("shows unknown status pill on server fail-open path", async () => {
    vi.mocked(attributionHealth.stats).mockResolvedValue(
      stub({
        overall: {
          total_published: 0,
          with_attribution: 0,
          attribution_pct: 0,
          status: "unknown",
        },
      }),
    );
    renderWithClient(<AttributionHealthCard />);
    await screen.findByText("Overall");
    expect(screen.getByText("?")).toBeInTheDocument();
  });

  it("renders error state when the request throws", async () => {
    vi.mocked(attributionHealth.stats).mockRejectedValue(
      new Error("network"),
    );
    renderWithClient(<AttributionHealthCard />);
    // Wait until either the error text or the initial 'Loading…' resolves
    expect(
      await screen.findByText(/Unable to load/),
    ).toBeInTheDocument();
  });
});
