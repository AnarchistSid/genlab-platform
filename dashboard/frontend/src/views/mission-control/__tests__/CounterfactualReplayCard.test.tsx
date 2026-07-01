/**
 * Intervention 7 observability card tests (2026-07-01).
 *
 * Pins the DR vs IPS badge state (load-bearing — misrendering
 * ``DR`` when the flag is off would tell the operator the
 * ``dr_reward`` fields they see are Ridge-model output when
 * they're actually null stubs) + the top-arm selection logic
 * (which arm surfaces + the n≥3 filter).
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/api/client", () => ({
  counterfactualReplay: {
    latest: vi.fn(),
  },
}));

import { counterfactualReplay } from "@/api/client";
import { CounterfactualReplayCard } from "../CounterfactualReplayCard";
import type { CounterfactualReplayArtifact } from "@/api/types";

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const _stub = (
  overrides: Partial<CounterfactualReplayArtifact> = {},
): CounterfactualReplayArtifact => ({
  generated_at: "2026-07-01T04:30:00+00:00",
  niche: "gaming",
  window_days: 30,
  n_decisions_total: 42,
  dr_enabled: false,
  per_arm: [
    {
      arm_id: "style:gaming:revelation",
      n_decisions: 20,
      n_with_reward: 18,
      naive_reward: 0.3,
      ips_reward: 0.32,
      relative_lift: 0.07,
      ess: 15.2,
      dr_reward: null,
      dr_note: "flag off (default stub)",
    },
  ],
  ...overrides,
});

describe("CounterfactualReplayCard", () => {
  it("renders header + subtitle", async () => {
    vi.mocked(counterfactualReplay.latest).mockResolvedValue(null);
    renderWithClient(<CounterfactualReplayCard />);
    expect(
      await screen.findByText(/Counterfactual replay/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/1st @ 04:30 UTC/)).toBeInTheDocument();
  });

  it("shows cold-start message for niches with no artifact", async () => {
    vi.mocked(counterfactualReplay.latest).mockResolvedValue(null);
    renderWithClient(<CounterfactualReplayCard />);
    expect(
      await screen.findAllByText(/No replay yet/i),
    ).toBeTruthy();
  });

  it("renders IPS badge when dr_enabled=false", async () => {
    vi.mocked(counterfactualReplay.latest).mockImplementation(
      (nicheId: string) => {
        if (nicheId === "gaming") return Promise.resolve(_stub());
        return Promise.resolve(null);
      },
    );
    renderWithClient(<CounterfactualReplayCard />);
    expect(await screen.findByText("IPS")).toBeInTheDocument();
  });

  it("renders DR badge when dr_enabled=true", async () => {
    vi.mocked(counterfactualReplay.latest).mockImplementation(
      (nicheId: string) => {
        if (nicheId === "gaming") {
          return Promise.resolve(
            _stub({
              dr_enabled: true,
              per_arm: [
                {
                  ..._stub().per_arm[0],
                  dr_reward: 0.35,
                  dr_note: "",
                },
              ],
            }),
          );
        }
        return Promise.resolve(null);
      },
    );
    renderWithClient(<CounterfactualReplayCard />);
    expect(await screen.findByText("DR")).toBeInTheDocument();
  });

  it("features top arm by DR reward when populated", async () => {
    vi.mocked(counterfactualReplay.latest).mockImplementation(
      (nicheId: string) => {
        if (nicheId === "gaming") {
          return Promise.resolve(
            _stub({
              dr_enabled: true,
              per_arm: [
                {
                  arm_id: "style:gaming:low",
                  n_decisions: 20,
                  n_with_reward: 15,
                  naive_reward: 0.1,
                  ips_reward: 0.1,
                  relative_lift: 0,
                  ess: 10,
                  dr_reward: 0.12,
                  dr_note: "",
                },
                {
                  arm_id: "style:gaming:high",
                  n_decisions: 20,
                  n_with_reward: 15,
                  naive_reward: 0.4,
                  ips_reward: 0.42,
                  relative_lift: 0.1,
                  ess: 12,
                  dr_reward: 0.55,
                  dr_note: "",
                },
              ],
            }),
          );
        }
        return Promise.resolve(null);
      },
    );
    renderWithClient(<CounterfactualReplayCard />);
    // The "high" arm has dr=0.55 vs low's 0.12; high should surface
    expect(await screen.findByText(/style:gaming:high/)).toBeInTheDocument();
    // low shouldn't appear
    expect(screen.queryByText(/style:gaming:low/)).not.toBeInTheDocument();
  });

  it("filters arms below n_with_reward=3", async () => {
    // Arms with too few closed 48h windows are dropped from the
    // top-arm selection — their reward estimates are too noisy to
    // feature (would show a "pristine" 0.9 with n=2 as the top arm).
    vi.mocked(counterfactualReplay.latest).mockImplementation(
      (nicheId: string) => {
        if (nicheId === "gaming") {
          return Promise.resolve(
            _stub({
              per_arm: [
                {
                  arm_id: "style:gaming:pristine",
                  n_decisions: 5,
                  n_with_reward: 2, // below threshold
                  naive_reward: 0.9,
                  ips_reward: 0.9,
                  relative_lift: 0,
                  ess: 2,
                  dr_reward: null,
                },
              ],
            }),
          );
        }
        return Promise.resolve(null);
      },
    );
    renderWithClient(<CounterfactualReplayCard />);
    expect(
      await screen.findByText(/No arm cleared the n≥3 threshold/i),
    ).toBeInTheDocument();
  });
});
