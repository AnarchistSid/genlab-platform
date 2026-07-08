/**
 * Pin tests for EnsembleVotesCard (L5 deferred card, 2026-07-08
 * audit).
 *
 * The load-bearing invariants:
 *
 *   1. Migration-pending (data=null) MUST render distinct copy from
 *      "0 decisions in the last N days". The two are different
 *      operator signals with different fix paths:
 *        - null    → deploy the ensemble_votes migration
 *        - zero    → wait for decisions to land
 *      Conflating them would train the operator to ignore both.
 *   2. Any component with abstained > 0 MUST render with amber
 *      styling — the case that motivates the card is a component
 *      like ``bandit`` abstaining on 90% of decisions. That's
 *      an "input feature missing" health issue that must be
 *      visually distinct.
 *   3. Recommendation counts MUST sum to total_decisions — an
 *      operator-facing sanity assertion.
 *   4. Flag state badge must correctly reflect the payload's
 *      flag_enabled bit (active vs observation-only).
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/api/client", () => ({
  ensembleVotes: {
    summary: vi.fn(),
  },
}));

import { ensembleVotes } from "@/api/client";
import { EnsembleVotesCard } from "../EnsembleVotesCard";
import type { EnsembleVotesSummaryPayload } from "@/api/types";

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

function makePayload(
  overrides: Partial<EnsembleVotesSummaryPayload> = {},
): EnsembleVotesSummaryPayload {
  return {
    flag_enabled: false,
    window_days: 7,
    niche_id: null,
    total_decisions: 100,
    components: [
      {
        component: "bandit",
        voted: 100,
        abstained: 0,
        vote_rate: 1.0,
        mean_score: 0.42,
        mean_disagreement_when_voting: 0.1,
      },
      {
        component: "hook_classifier",
        voted: 100,
        abstained: 0,
        vote_rate: 1.0,
        mean_score: 0.6,
        mean_disagreement_when_voting: 0.15,
      },
      {
        component: "bayesian_gate",
        voted: 100,
        abstained: 0,
        vote_rate: 1.0,
        mean_score: 0.55,
        mean_disagreement_when_voting: 0.12,
      },
      {
        component: "conformal_router",
        voted: 100,
        abstained: 0,
        vote_rate: 1.0,
        mean_score: 0.48,
        mean_disagreement_when_voting: 0.11,
      },
      {
        component: "llm_judge",
        voted: 30,
        abstained: 70,
        vote_rate: 0.3,
        mean_score: 0.7,
        mean_disagreement_when_voting: 0.2,
      },
    ],
    recommendations: {
      auto_approve: 45,
      worth_your_look: 32,
      route_to_operator: 15,
      auto_reject: 8,
    },
    ...overrides,
  };
}

describe("EnsembleVotesCard — L5 deferred card", () => {
  it("shows loading state before data arrives", () => {
    // Never-resolving promise keeps the query in "isLoading" state.
    vi.mocked(ensembleVotes.summary).mockReturnValue(new Promise(() => {}));
    renderWithClient(<EnsembleVotesCard />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("renders 'waiting for migration' copy when data is null", async () => {
    // The endpoint returns null when the ensemble_votes table hasn't
    // been created yet. This is the "not yet installed" signal — must
    // be visually distinct from "installed but idle".
    vi.mocked(ensembleVotes.summary).mockResolvedValue(null);
    renderWithClient(<EnsembleVotesCard />);
    const pending = await screen.findByTestId(
      "ensemble-votes-migration-pending",
    );
    expect(pending).toBeInTheDocument();
    expect(pending.textContent).toMatch(/waiting for migration/i);
    // The zero-decisions copy MUST NOT appear here — conflating the
    // two would train the operator to ignore both signals.
    expect(
      screen.queryByTestId("ensemble-votes-empty-state"),
    ).not.toBeInTheDocument();
  });

  it("renders 'No decisions in the last N days' when total_decisions=0", async () => {
    vi.mocked(ensembleVotes.summary).mockResolvedValue(
      makePayload({
        total_decisions: 0,
        components: [],
        recommendations: {},
      }),
    );
    renderWithClient(<EnsembleVotesCard />);
    const empty = await screen.findByTestId("ensemble-votes-empty-state");
    expect(empty).toBeInTheDocument();
    expect(empty.textContent).toMatch(/No decisions in the last 7 days/i);
    // Migration-pending copy MUST NOT leak into the idle-but-installed
    // state — different fix path (wait vs deploy migration).
    expect(
      screen.queryByTestId("ensemble-votes-migration-pending"),
    ).not.toBeInTheDocument();
  });

  it.each([
    ["bandit", 100, 0],
    ["hook_classifier", 100, 0],
    ["llm_judge", 30, 70],
  ])(
    "renders %s component row with voted=%i/total=voted+abstained=%i+voted",
    async (name, voted, abstained) => {
      vi.mocked(ensembleVotes.summary).mockResolvedValue(makePayload());
      renderWithClient(<EnsembleVotesCard />);
      const row = await screen.findByTestId(
        `ensemble-votes-component-row-${name}`,
      );
      const total = voted + abstained;
      // Textual assertion — the "voted/total" fraction must render
      // exactly. Operators use this to spot participation dropouts.
      expect(row.textContent).toContain(`${voted}/${total}`);
    },
  );

  it("gives amber styling to components with abstained > 0", async () => {
    vi.mocked(ensembleVotes.summary).mockResolvedValue(makePayload());
    renderWithClient(<EnsembleVotesCard />);

    // llm_judge abstains on 70/100 — that's a critical signal.
    // If bandit were the abstainer at 90% the card must make it
    // visually distinct, so we pin the data-attribute here.
    const abstaining = await screen.findByTestId(
      "ensemble-votes-component-row-llm_judge",
    );
    expect(abstaining).toHaveAttribute("data-has-abstentions", "true");
    // The abstained-count badge on the abstainer must carry the
    // amber Tailwind class — the specific class is load-bearing
    // because it's the operator's visual cue.
    const badge = screen.getByTestId(
      "ensemble-votes-abstained-count-llm_judge",
    );
    expect(badge.className).toMatch(/amber/);

    // Non-abstainers must NOT get the amber styling — the signal has
    // to remain rare to be useful.
    const banditRow = screen.getByTestId(
      "ensemble-votes-component-row-bandit",
    );
    expect(banditRow).toHaveAttribute("data-has-abstentions", "false");
    const banditBadge = screen.getByTestId(
      "ensemble-votes-abstained-count-bandit",
    );
    expect(banditBadge.className).not.toMatch(/amber/);
  });

  it("SIMULATES the load-bearing 'bandit abstains 90%' case", async () => {
    // This is the case that motivates the card. If bandit abstains on
    // 90% of decisions, the operator MUST see it as visually distinct.
    vi.mocked(ensembleVotes.summary).mockResolvedValue(
      makePayload({
        components: [
          {
            component: "bandit",
            voted: 10,
            abstained: 90,
            vote_rate: 0.1,
            mean_score: 0.42,
            mean_disagreement_when_voting: 0.1,
          },
        ],
      }),
    );
    renderWithClient(<EnsembleVotesCard />);
    const row = await screen.findByTestId(
      "ensemble-votes-component-row-bandit",
    );
    // Must render as visually distinct (amber tint + attribute).
    expect(row).toHaveAttribute("data-has-abstentions", "true");
    const badge = screen.getByTestId("ensemble-votes-abstained-count-bandit");
    expect(badge.className).toMatch(/amber/);
    // Must show 10/100 so the operator can eyeball the ratio.
    expect(row.textContent).toContain("10/100");
  });

  it("recommendation counts sum to total_decisions", async () => {
    const payload = makePayload();
    vi.mocked(ensembleVotes.summary).mockResolvedValue(payload);
    renderWithClient(<EnsembleVotesCard />);

    // Each recommendation row exposes its count via data-testid; sum
    // the visible text and compare to the header's total_decisions.
    // If the card ever silently drops rows, this test fires.
    await screen.findByTestId("ensemble-votes-card");
    const recEntries = Object.entries(payload.recommendations);
    let sum = 0;
    for (const [name, count] of recEntries) {
      const row = screen.getByTestId(
        `ensemble-votes-recommendation-${name}`,
      );
      expect(row.textContent).toContain(String(count));
      sum += count;
    }
    expect(sum).toBe(payload.total_decisions);
  });

  it("shows 'active' badge when flag_enabled=true", async () => {
    vi.mocked(ensembleVotes.summary).mockResolvedValue(
      makePayload({ flag_enabled: true }),
    );
    renderWithClient(<EnsembleVotesCard />);
    const badge = await screen.findByTestId("ensemble-votes-flag-badge");
    expect(badge).toHaveAttribute("data-flag-state", "active");
    expect(badge.textContent).toContain("active");
  });

  it("shows 'observation only' badge when flag_enabled=false", async () => {
    vi.mocked(ensembleVotes.summary).mockResolvedValue(
      makePayload({ flag_enabled: false }),
    );
    renderWithClient(<EnsembleVotesCard />);
    const badge = await screen.findByTestId("ensemble-votes-flag-badge");
    expect(badge).toHaveAttribute("data-flag-state", "observation-only");
    expect(badge.textContent).toContain("observation only");
  });
});
