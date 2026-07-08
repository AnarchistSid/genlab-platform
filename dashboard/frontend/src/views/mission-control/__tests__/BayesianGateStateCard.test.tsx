/**
 * Pin tests for BayesianGateStateCard (L4 deferred card, 2026-07-08
 * audit).
 *
 * Primary invariants:
 *   1. Cold-start (empty niches) shows the "no posterior yet" copy —
 *      never an empty row list. Operators need to know the runner
 *      hasn't fired, not that the fit is "empty".
 *   2. Flag-badge state must faithfully mirror `flag_enabled` — the
 *      whole point of the card is to signal "is this observation
 *      only or actively steering behaviour?".
 *   3. Top-3 features are selected by |weight| magnitude, not
 *      insertion order. If a small-weight feature outranks a
 *      large-weight one visually, the "does the fit look
 *      reasonable?" mental model breaks.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { BayesianGateStatePayload } from "@/api/types";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn<() => Promise<BayesianGateStatePayload>>();

vi.mock("@/api/client", () => ({
  bayesianGateState: {
    get: () => mockGet(),
  },
}));

vi.mock("@/api/query-keys", () => ({
  queryKeys: {
    bayesianGateState: {
      get: () => ["bayesian-gate-state"],
    },
  },
}));

import { BayesianGateStateCard } from "../BayesianGateStateCard";

function renderCard() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <BayesianGateStateCard />
    </QueryClientProvider>,
  );
}

function makePayload(
  overrides: Partial<BayesianGateStatePayload> = {},
): BayesianGateStatePayload {
  return {
    flag_enabled: false,
    state_path: "/tmp/bayesian_gate/state.json",
    niches: [
      {
        niche_id: "gaming",
        n_rows: 47,
        feature_names: [
          "composite_score",
          "virality_score",
          "hook_length",
          "hashtag_count",
        ],
        mean: [0.412, -0.183, 0.055, -0.021],
      },
      {
        niche_id: "anime",
        n_rows: 12,
        feature_names: [
          "composite_score",
          "virality_score",
          "hook_length",
          "hashtag_count",
        ],
        mean: [0.31, 0.12, -0.09, 0.04],
      },
    ],
    ...overrides,
  };
}

describe("BayesianGateStateCard — L4 observability card", () => {
  beforeEach(() => {
    mockGet.mockReset();
  });

  it("shows loading state before data arrives", () => {
    mockGet.mockReturnValue(new Promise(() => {})); // never resolve
    renderCard();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("shows cold-start copy when niches list is empty — NOT an empty row list", async () => {
    mockGet.mockResolvedValue(
      makePayload({ niches: [], flag_enabled: false }),
    );
    renderCard();
    await screen.findByTestId("bayesian-gate-card");

    // The dedicated empty-state slot MUST render.
    expect(
      screen.getByTestId("bayesian-gate-empty-state"),
    ).toHaveTextContent(/No posterior yet/);

    // And no niche rows may exist — an empty row list would train
    // operators to think the fit produced zero-weight vectors when
    // in reality the runner never fired.
    expect(
      screen.queryAllByTestId(/^bayesian-gate-niche-row-/),
    ).toHaveLength(0);
  });

  it("renders each niche's row sorted alphabetically by niche_id", async () => {
    mockGet.mockResolvedValue(makePayload());
    renderCard();
    await screen.findByTestId("bayesian-gate-card");

    // Both niche rows present.
    expect(
      screen.getByTestId("bayesian-gate-niche-row-anime"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("bayesian-gate-niche-row-gaming"),
    ).toBeInTheDocument();

    // Alphabetical order: anime before gaming.
    const rows = screen.getAllByTestId(/^bayesian-gate-niche-row-/);
    expect(rows[0]).toHaveAttribute(
      "data-testid",
      "bayesian-gate-niche-row-anime",
    );
    expect(rows[1]).toHaveAttribute(
      "data-testid",
      "bayesian-gate-niche-row-gaming",
    );
  });

  it("renders each niche row with N=<n_rows> chip and feature=weight pairs", async () => {
    mockGet.mockResolvedValue(makePayload());
    renderCard();
    await screen.findByTestId("bayesian-gate-card");

    const gamingRow = screen.getByTestId("bayesian-gate-niche-row-gaming");
    // Training size chip.
    expect(gamingRow.textContent).toMatch(/N=47/);
    // At least one feature=weight pair rendered with .toFixed(3).
    expect(gamingRow.textContent).toMatch(/composite_score=0\.412/);
  });

  it("renders green 'active' badge when flag_enabled=true", async () => {
    mockGet.mockResolvedValue(makePayload({ flag_enabled: true }));
    renderCard();
    await screen.findByTestId("bayesian-gate-card");
    const badge = screen.getByTestId("bayesian-gate-flag-badge");
    expect(badge).toHaveTextContent(/active/);
    expect(badge).toHaveAttribute("data-flag-state", "active");
  });

  it("renders amber 'observation only' badge when flag_enabled=false", async () => {
    mockGet.mockResolvedValue(makePayload({ flag_enabled: false }));
    renderCard();
    await screen.findByTestId("bayesian-gate-card");
    const badge = screen.getByTestId("bayesian-gate-flag-badge");
    expect(badge).toHaveTextContent(/observation only/);
    expect(badge).toHaveAttribute("data-flag-state", "observation-only");
  });

  it("selects top-3 features by |weight| magnitude, not insertion order", async () => {
    // Deliberately place the highest-magnitude feature LAST in the
    // feature_names array. If the card were selecting by insertion
    // order instead of magnitude, "composite_score" would be
    // rendered and "big_negative_feature" would be dropped — which
    // is the opposite of what the operator needs to see.
    mockGet.mockResolvedValue(
      makePayload({
        niches: [
          {
            niche_id: "gaming",
            n_rows: 100,
            feature_names: [
              "composite_score", // |0.01|
              "virality_score", // |0.5|
              "hook_length", // |0.3|
              "hashtag_count", // |0.02|
              "big_negative_feature", // |0.9| — should rank #1
            ],
            mean: [0.01, 0.5, 0.3, -0.02, -0.9],
          },
        ],
      }),
    );
    renderCard();
    await screen.findByTestId("bayesian-gate-card");

    const row = screen.getByTestId("bayesian-gate-niche-row-gaming");
    // Top-3 by |weight|: big_negative_feature (0.9), virality_score
    // (0.5), hook_length (0.3). composite_score and hashtag_count
    // must NOT appear inline.
    expect(row.textContent).toMatch(/big_negative_feature=-0\.900/);
    expect(row.textContent).toMatch(/virality_score=0\.500/);
    expect(row.textContent).toMatch(/hook_length=0\.300/);
    expect(row.textContent).not.toMatch(/composite_score=/);
    expect(row.textContent).not.toMatch(/hashtag_count=/);
  });

  it("puts the full feature vector in the row tooltip so nothing is lost", async () => {
    mockGet.mockResolvedValue(
      makePayload({
        niches: [
          {
            niche_id: "gaming",
            n_rows: 47,
            feature_names: ["a", "b", "c", "d", "e"],
            mean: [0.1, 0.2, 0.3, 0.4, 0.5],
          },
        ],
      }),
    );
    renderCard();
    await screen.findByTestId("bayesian-gate-card");

    const row = screen.getByTestId("bayesian-gate-niche-row-gaming");
    // Row title (native tooltip) should carry every feature — even
    // ones that don't make the top-3 render.
    const title = row.getAttribute("title") ?? "";
    expect(title).toMatch(/a=0\.100/);
    expect(title).toMatch(/e=0\.500/);
  });
});
