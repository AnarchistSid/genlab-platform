/**
 * Intervention 2 observability card tests (2026-07-01).
 *
 * Pin the load-bearing "active vs observation only" badge — that
 * signal tells the operator whether flipping the flag would change
 * behaviour. Misrendering it (say showing "active" when the flag is
 * off) would mislead the operator into thinking priors are already
 * being consumed and they don't need to flip anything.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/api/client", () => ({
  crossNicheTransfer: {
    priors: vi.fn(),
  },
}));

import { crossNicheTransfer } from "@/api/client";
import { CrossNichePriorsCard } from "../CrossNichePriorsCard";
import type { CrossNichePriorsArtifact } from "@/api/types";

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const _stub = (
  overrides: Partial<CrossNichePriorsArtifact> = {},
): CrossNichePriorsArtifact => ({
  generated_at: "2026-07-07T05:30:00+00:00",
  flag_enabled: false,
  priors: {
    revelation: {
      style: "revelation",
      alpha: 2.4,
      beta: 13.6,
      observed_rate_mean: 0.15,
      observed_rate_variance: 0.02,
      n_niches: 3,
      reasons: [],
    },
  },
  ...overrides,
});

describe("CrossNichePriorsCard", () => {
  it("renders header + refit-cadence subtitle", async () => {
    vi.mocked(crossNicheTransfer.priors).mockResolvedValue(null);
    renderWithClient(<CrossNichePriorsCard />);
    expect(
      await screen.findByText(/Cross-niche priors/i),
    ).toBeInTheDocument();
  });

  it("shows 'No priors yet' cold-start message", async () => {
    vi.mocked(crossNicheTransfer.priors).mockResolvedValue(null);
    renderWithClient(<CrossNichePriorsCard />);
    expect(await screen.findByText(/No priors yet/i)).toBeInTheDocument();
  });

  it("shows 'observation only' badge when flag is off", async () => {
    vi.mocked(crossNicheTransfer.priors).mockResolvedValue(
      _stub({ flag_enabled: false }),
    );
    renderWithClient(<CrossNichePriorsCard />);
    expect(await screen.findByText("observation only")).toBeInTheDocument();
  });

  it("shows 'active' badge when flag is on", async () => {
    vi.mocked(crossNicheTransfer.priors).mockResolvedValue(
      _stub({ flag_enabled: true }),
    );
    renderWithClient(<CrossNichePriorsCard />);
    expect(await screen.findByText("active")).toBeInTheDocument();
  });

  it("renders each prior row with α, β, μ, σ², N", async () => {
    vi.mocked(crossNicheTransfer.priors).mockResolvedValue(_stub());
    renderWithClient(<CrossNichePriorsCard />);
    expect(await screen.findByText(/α=/)).toBeInTheDocument();
    expect(screen.getByText(/β=/)).toBeInTheDocument();
    expect(screen.getByText(/μ=15\.0%/)).toBeInTheDocument();
    expect(screen.getByText(/N=3/)).toBeInTheDocument();
    expect(screen.getByText("revelation")).toBeInTheDocument();
  });

  it("shows 'no transferable priors' when the priors dict is empty", async () => {
    vi.mocked(crossNicheTransfer.priors).mockResolvedValue(
      _stub({ priors: {} }),
    );
    renderWithClient(<CrossNichePriorsCard />);
    expect(
      await screen.findByText(/no style met the min-observation threshold/i),
    ).toBeInTheDocument();
  });
});
