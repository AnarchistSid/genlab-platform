/**
 * Phase 5.C session 2 card tests.
 *
 * Load-bearing signals: cold-start copy, eligibility badge, per-row
 * countdown, reject button + prompt integration.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/api/client", () => ({
  flagFlipProposals: { pending: vi.fn(), reject: vi.fn() },
}));

import { flagFlipProposals } from "@/api/client";
import { FlagFlipProposalsCard } from "../FlagFlipProposalsCard";
import type { FlagFlipProposalsSummary } from "@/api/types";

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const _stub = (
  overrides: Partial<FlagFlipProposalsSummary> = {},
): FlagFlipProposalsSummary => ({
  override_window_hours: 24,
  confidence_threshold: 0.9,
  rows: [
    {
      id: "abc",
      flag_name: "GENLAB_STYLE_GUIDANCE_ROLLOUT_PCT",
      from_state: "25",
      to_state: "50",
      rationale: "lift 30% (n_control=50, n_treatment=30)",
      confidence: 0.95,
      age_hours: 6.2,
      hours_until_auto_apply: 17.8,
      auto_apply_eligible: false,
      proposed_at: "2026-08-14T00:00:00+00:00",
      evidence: {},
    },
  ],
  ...overrides,
});

describe("FlagFlipProposalsCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows cold-start copy when data null", async () => {
    vi.mocked(flagFlipProposals.pending).mockResolvedValue(null);
    renderWithClient(<FlagFlipProposalsCard />);
    expect(
      await screen.findByText(/No pending proposals/i),
    ).toBeInTheDocument();
  });

  it("shows empty-queue copy when rows empty", async () => {
    vi.mocked(flagFlipProposals.pending).mockResolvedValue(
      _stub({ rows: [] }),
    );
    renderWithClient(<FlagFlipProposalsCard />);
    expect(
      await screen.findByText(/No pending proposals/i),
    ).toBeInTheDocument();
  });

  it("renders flag_name + transition + confidence for pending row", async () => {
    vi.mocked(flagFlipProposals.pending).mockResolvedValue(_stub());
    renderWithClient(<FlagFlipProposalsCard />);
    expect(
      await screen.findByText("GENLAB_STYLE_GUIDANCE_ROLLOUT_PCT"),
    ).toBeInTheDocument();
    expect(screen.getByText(/25 → 50/)).toBeInTheDocument();
    expect(screen.getByText(/confidence 95%/)).toBeInTheDocument();
  });

  it("shows countdown badge for not-yet-eligible rows", async () => {
    vi.mocked(flagFlipProposals.pending).mockResolvedValue(_stub());
    renderWithClient(<FlagFlipProposalsCard />);
    expect(await screen.findByText(/in 17.8h/)).toBeInTheDocument();
  });

  it("shows apply-eligible badge for aged + confident rows", async () => {
    vi.mocked(flagFlipProposals.pending).mockResolvedValue(
      _stub({
        rows: [
          {
            ..._stub().rows[0],
            age_hours: 30,
            hours_until_auto_apply: 0,
            auto_apply_eligible: true,
          },
        ],
      }),
    );
    renderWithClient(<FlagFlipProposalsCard />);
    expect(
      await screen.findByText(/apply eligible/),
    ).toBeInTheDocument();
  });

  it("reject button calls flagFlipProposals.reject with prompt reason", async () => {
    vi.mocked(flagFlipProposals.pending).mockResolvedValue(_stub());
    vi.mocked(flagFlipProposals.reject).mockResolvedValue({ ok: true });
    // Prompt returns "not now" — should fire reject
    const promptSpy = vi
      .spyOn(window, "prompt")
      .mockReturnValue("wrong direction");
    renderWithClient(<FlagFlipProposalsCard />);
    const btn = await screen.findByRole("button", { name: /Reject/i });
    fireEvent.click(btn);
    expect(promptSpy).toHaveBeenCalled();
    await waitFor(() => {
      expect(vi.mocked(flagFlipProposals.reject)).toHaveBeenCalledWith(
        "abc", "wrong direction",
      );
    });
    promptSpy.mockRestore();
  });

  it("reject skipped when prompt returns empty string", async () => {
    vi.mocked(flagFlipProposals.pending).mockResolvedValue(_stub());
    const promptSpy = vi.spyOn(window, "prompt").mockReturnValue("");
    renderWithClient(<FlagFlipProposalsCard />);
    const btn = await screen.findByRole("button", { name: /Reject/i });
    fireEvent.click(btn);
    expect(promptSpy).toHaveBeenCalled();
    expect(vi.mocked(flagFlipProposals.reject)).not.toHaveBeenCalled();
    promptSpy.mockRestore();
  });
});
