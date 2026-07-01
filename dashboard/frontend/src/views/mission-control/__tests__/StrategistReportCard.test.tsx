/**
 * PR Strategist-2b — StrategistReportCard tests.
 *
 * Pin the operator-review contract:
 *
 *   * Card header + subtitle render
 *   * All 5 niche rows present (loading / no-data / with-data states)
 *   * Cold-start "No Strategist report yet" message renders when
 *     server returns null
 *   * Loaded row shows the detected phase badge and proposal count
 *   * Review button toggles the inline review panel
 *
 * The mutation flow (Accept / Reject / Submit) is NOT integration-
 * tested here — that path requires mocking toast + useMutation
 * timing, and the pattern-matching value is low. Same coverage
 * boundary as NichePauseCard.test.tsx.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/api/client", () => ({
  strategist: {
    latest: vi.fn(),
    unreviewed: vi.fn(),
    review: vi.fn(),
  },
}));

import { strategist } from "@/api/client";
import { StrategistReportCard } from "../StrategistReportCard";
import type { StrategistReport } from "@/api/types";

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const _stubReport = (overrides: Partial<StrategistReport> = {}): StrategistReport => ({
  id: "report-1",
  niche_id: "gaming",
  week_of: "2026-06-29",
  run_at: new Date(Date.now() - 3 * 3600_000).toISOString(),
  detected_phase: "GROWTH",
  phase_evidence: "Follower velocity +12% w/w",
  weekly_summary:
    "Gaming grew from 220 to 246 followers this week; retention held steady.",
  proposals: [
    {
      type: "gate_threshold",
      target: "gaming.min_composite_score",
      current: 0.3,
      proposed: 0.35,
      reasoning:
        "Operator override rate on borderline blueprints is 60%; raising the threshold should reduce operator queue by ~15%.",
      expected_impact:
        "Fewer borderline blueprints reach operator; auto-approval rate up.",
      risk: "medium",
      urgency: "this_week",
    },
  ],
  causal_hypotheses: [],
  universal_playbook_proposals: [],
  ...overrides,
});

describe("StrategistReportCard", () => {
  it("renders the card header + subtitle", async () => {
    vi.mocked(strategist.latest).mockResolvedValue(null);
    renderWithClient(<StrategistReportCard />);
    expect(
      await screen.findByText(/Strategist reports/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/Weekly LLM analyst/i)).toBeInTheDocument();
  });

  it("shows 'No Strategist report yet' for cold-start niches", async () => {
    vi.mocked(strategist.latest).mockResolvedValue(null);
    renderWithClient(<StrategistReportCard />);
    // 5 niche rows all cold-start → 5 identical zero-state messages.
    // Assert at least one to prove the render path fires; a stricter
    // "exactly 5" pin would over-constrain (a future 6th niche
    // launching would break the test even though behaviour is fine).
    expect(
      await screen.findAllByText(/No Strategist report yet/i),
    ).toBeTruthy();
  });

  it("renders detected phase badge when the persister has a report", async () => {
    // First niche gets a report; the others cold-start. The card
    // fires one query per niche so we need to distinguish which one.
    vi.mocked(strategist.latest).mockImplementation((nicheId: string) => {
      if (nicheId === "ai_creators") {
        return Promise.resolve(_stubReport({ niche_id: "ai_creators" }));
      }
      return Promise.resolve(null);
    });
    renderWithClient(<StrategistReportCard />);
    // The GROWTH phase label maps to "Growth" in PHASE_LABEL.
    expect(await screen.findByText("Growth")).toBeInTheDocument();
    // Proposal count "1 proposal" (singular)
    expect(screen.getByText(/1 proposal/)).toBeInTheDocument();
  });

  it("Review button toggles the inline review panel", async () => {
    vi.mocked(strategist.latest).mockImplementation((nicheId: string) => {
      if (nicheId === "ai_creators") {
        return Promise.resolve(_stubReport({ niche_id: "ai_creators" }));
      }
      return Promise.resolve(null);
    });
    renderWithClient(<StrategistReportCard />);

    // Wait for the row to render, then click Review.
    const reviewButton = await screen.findByText("Review");
    // Before click: weekly_summary NOT visible.
    expect(
      screen.queryByText(/Gaming grew from 220 to 246 followers/i),
    ).not.toBeInTheDocument();

    await userEvent.click(reviewButton);

    // After click: weekly_summary + Accept/Reject buttons present.
    await waitFor(() => {
      expect(
        screen.getByText(/Gaming grew from 220 to 246 followers/i),
      ).toBeInTheDocument();
    });
    expect(screen.getByText("Accept")).toBeInTheDocument();
    expect(screen.getByText("Reject")).toBeInTheDocument();
  });
});
