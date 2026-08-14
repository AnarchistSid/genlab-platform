/**
 * Phase 4.E session 3 card tests.
 *
 * Same load-bearing signals as sibling cards: badge state per
 * flag, cold-start copy, per-niche row rendering with pending/
 * consumed/expired counts.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/api/client", () => ({
  ideationPool: { summary: vi.fn() },
}));

import { ideationPool } from "@/api/client";
import { IdeationPoolCard } from "../IdeationPoolCard";
import type { IdeationPoolSummary } from "@/api/types";

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const _stub = (
  overrides: Partial<IdeationPoolSummary> = {},
): IdeationPoolSummary => ({
  flag_enabled: false,
  rollout_pct: 0,
  per_niche: [
    {
      niche_id: "gaming",
      pending: 18,
      consumed: 3,
      expired: 0,
      total: 21,
      latest_batch_at: "2026-08-14T05:30:00+00:00",
    },
  ],
  ...overrides,
});

describe("IdeationPoolCard", () => {
  it("shows cold-start copy when data null", async () => {
    vi.mocked(ideationPool.summary).mockResolvedValue(null);
    renderWithClient(<IdeationPoolCard />);
    expect(
      await screen.findByText(/No ideas yet/i),
    ).toBeInTheDocument();
  });

  it("shows observation-only when flag off", async () => {
    vi.mocked(ideationPool.summary).mockResolvedValue(
      _stub({ flag_enabled: false }),
    );
    renderWithClient(<IdeationPoolCard />);
    expect(await screen.findByText(/observation only/)).toBeInTheDocument();
  });

  it("shows active + rollout pct when flag on", async () => {
    vi.mocked(ideationPool.summary).mockResolvedValue(
      _stub({ flag_enabled: true, rollout_pct: 25 }),
    );
    renderWithClient(<IdeationPoolCard />);
    expect(await screen.findByText(/active 25%/)).toBeInTheDocument();
  });

  it("renders pending/consumed/expired counts", async () => {
    vi.mocked(ideationPool.summary).mockResolvedValue(_stub());
    renderWithClient(<IdeationPoolCard />);
    expect(await screen.findByText(/pending=18/)).toBeInTheDocument();
    expect(screen.getByText(/consumed=3/)).toBeInTheDocument();
    expect(screen.getByText(/expired=0/)).toBeInTheDocument();
  });

  it("renders consumption percentage", async () => {
    vi.mocked(ideationPool.summary).mockResolvedValue(_stub());
    renderWithClient(<IdeationPoolCard />);
    // 3 consumed / 21 total = 14%
    expect(await screen.findByText(/14% used/)).toBeInTheDocument();
  });

  it("handles empty per_niche gracefully", async () => {
    vi.mocked(ideationPool.summary).mockResolvedValue(
      _stub({ per_niche: [] }),
    );
    renderWithClient(<IdeationPoolCard />);
    expect(
      await screen.findByText(/No niches have pool rows yet/i),
    ).toBeInTheDocument();
  });

  it("handles null latest_batch_at", async () => {
    vi.mocked(ideationPool.summary).mockResolvedValue(
      _stub({
        per_niche: [
          {
            niche_id: "gaming", pending: 1, consumed: 0,
            expired: 0, total: 1, latest_batch_at: null,
          },
        ],
      }),
    );
    renderWithClient(<IdeationPoolCard />);
    expect(await screen.findByText("—")).toBeInTheDocument();
  });
});
