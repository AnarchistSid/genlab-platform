/**
 * Pin tests for DriftSignalCard (L7 audit deferred card, 2026-07-08).
 *
 * Load-bearing invariants:
 *
 *   1. **Regressions render above improvements.** The whole point of
 *      the card is that arms whose posterior rotted are the operator's
 *      high-value signal. If someone reordered the sections silently,
 *      it would demote the load-bearing surface without any signal.
 *
 *   2. **Regression rows preserve endpoint's z_score ASC ordering**
 *      and **improvement rows preserve z_score DESC ordering**. The
 *      endpoint enforces this server-side; the card must not
 *      re-sort or shuffle.
 *
 *   3. **Migration-pending vs empty-state must NOT collapse.**
 *      `data === null` (migration pending) and empty arrays
 *      (posteriors stable) are semantically different — the card
 *      renders distinct copy for each.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DriftSignalsSummaryPayload, DriftSignal } from "@/api/types";

vi.mock("@/api/client", () => ({
  driftSignals: {
    summary: vi.fn(),
  },
}));

import { driftSignals } from "@/api/client";
import { DriftSignalCard } from "../DriftSignalCard";

function renderCard() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <DriftSignalCard />
    </QueryClientProvider>,
  );
}

function makeSignal(overrides: Partial<DriftSignal> = {}): DriftSignal {
  return {
    arm_id: "hook:revelation",
    niche_id: "gaming",
    recent_rate: 0.15,
    baseline_rate: 0.32,
    z_score: -3.24,
    recent_obs: 22,
    baseline_obs: 45,
    is_regression: true,
    computed_at: "2026-07-08T04:00:00+00:00",
    ...overrides,
  };
}

function makePayload(
  overrides: Partial<DriftSignalsSummaryPayload> = {},
): DriftSignalsSummaryPayload {
  return {
    flag_enabled: true,
    window_days: 7,
    niche_id: null,
    min_abs_z: 2.0,
    regressions: [],
    improvements: [],
    counts_by_niche: [],
    ...overrides,
  };
}

describe("DriftSignalCard — L7 observability", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows loading state before data arrives", () => {
    vi.mocked(driftSignals.summary).mockReturnValue(new Promise(() => {}));
    renderCard();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("shows migration-pending copy when data is null", async () => {
    vi.mocked(driftSignals.summary).mockResolvedValue(
      null as unknown as DriftSignalsSummaryPayload,
    );
    renderCard();
    await screen.findByTestId("drift-signal-card");
    expect(
      screen.getByTestId("drift-signal-migration-pending"),
    ).toHaveTextContent(/table not yet available/i);
    // Empty-state must NOT render when we're in migration-pending —
    // the two are semantically distinct.
    expect(
      screen.queryByTestId("drift-signal-empty-state"),
    ).not.toBeInTheDocument();
  });

  it("shows stable-posterior copy when both arrays are empty", async () => {
    vi.mocked(driftSignals.summary).mockResolvedValue(makePayload());
    renderCard();
    await screen.findByTestId("drift-signal-card");
    expect(screen.getByTestId("drift-signal-empty-state")).toHaveTextContent(
      /posteriors stable/i,
    );
    // Migration-pending must NOT render when we have a real payload —
    // the two are semantically distinct.
    expect(
      screen.queryByTestId("drift-signal-migration-pending"),
    ).not.toBeInTheDocument();
  });

  it("renders regressions in endpoint's z_score ASC order (most-regressed first)", async () => {
    // Endpoint returns regressions pre-sorted ASC (most-negative first).
    // Card must preserve that ordering — no shuffling in the render.
    vi.mocked(driftSignals.summary).mockResolvedValue(
      makePayload({
        regressions: [
          makeSignal({ arm_id: "arm-worst", z_score: -4.5 }),
          makeSignal({ arm_id: "arm-middle", z_score: -3.0 }),
          makeSignal({ arm_id: "arm-mild", z_score: -2.1 }),
        ],
      }),
    );
    renderCard();
    await screen.findByTestId("drift-signal-card");

    const rows = document.querySelectorAll(
      '[data-testid^="drift-signal-regression-"]',
    );
    const order = Array.from(rows).map((el) =>
      el.getAttribute("data-testid"),
    );
    expect(order).toEqual([
      "drift-signal-regression-arm-worst",
      "drift-signal-regression-arm-middle",
      "drift-signal-regression-arm-mild",
    ]);
    // Regression rows carry the red tone attribute for downstream
    // visual pins.
    expect(
      screen.getByTestId("drift-signal-regression-arm-worst"),
    ).toHaveAttribute("data-tone", "regression");
  });

  it("renders improvements in endpoint's z_score DESC order (best-first)", async () => {
    vi.mocked(driftSignals.summary).mockResolvedValue(
      makePayload({
        improvements: [
          makeSignal({
            arm_id: "arm-best",
            z_score: 4.1,
            is_regression: false,
          }),
          makeSignal({
            arm_id: "arm-second",
            z_score: 3.0,
            is_regression: false,
          }),
        ],
      }),
    );
    renderCard();
    await screen.findByTestId("drift-signal-card");
    const rows = document.querySelectorAll(
      '[data-testid^="drift-signal-improvement-"]',
    );
    const order = Array.from(rows).map((el) =>
      el.getAttribute("data-testid"),
    );
    expect(order).toEqual([
      "drift-signal-improvement-arm-best",
      "drift-signal-improvement-arm-second",
    ]);
    expect(
      screen.getByTestId("drift-signal-improvement-arm-best"),
    ).toHaveAttribute("data-tone", "improvement");
  });

  it("renders regressions section ABOVE improvements section (load-bearing ordering)", async () => {
    // The single most load-bearing invariant of the card: regressions
    // — arms whose win-rate rotted — must be visually more prominent
    // than improvements, even when there are more improvements than
    // regressions.
    vi.mocked(driftSignals.summary).mockResolvedValue(
      makePayload({
        regressions: [makeSignal({ arm_id: "arm-r1", z_score: -2.5 })],
        improvements: [
          makeSignal({
            arm_id: "arm-i1",
            z_score: 3.0,
            is_regression: false,
          }),
          makeSignal({
            arm_id: "arm-i2",
            z_score: 2.5,
            is_regression: false,
          }),
          makeSignal({
            arm_id: "arm-i3",
            z_score: 2.1,
            is_regression: false,
          }),
        ],
      }),
    );
    renderCard();
    await screen.findByTestId("drift-signal-card");

    const regressionRow = screen.getByTestId("drift-signal-regression-arm-r1");
    const firstImprovement = screen.getByTestId(
      "drift-signal-improvement-arm-i1",
    );
    // Assert DOM order: regression node's compareDocumentPosition
    // reports FOLLOWING for a node that comes after it.
    expect(
      regressionRow.compareDocumentPosition(firstImprovement) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("renders flag_enabled=true as active green badge", async () => {
    vi.mocked(driftSignals.summary).mockResolvedValue(
      makePayload({ flag_enabled: true }),
    );
    renderCard();
    const badge = await screen.findByTestId("drift-signal-flag-badge");
    expect(badge).toHaveAttribute("data-state", "active");
    expect(badge).toHaveTextContent(/active/i);
  });

  it("renders flag_enabled=false as observation-only amber badge", async () => {
    vi.mocked(driftSignals.summary).mockResolvedValue(
      makePayload({ flag_enabled: false }),
    );
    renderCard();
    const badge = await screen.findByTestId("drift-signal-flag-badge");
    expect(badge).toHaveAttribute("data-state", "observation-only");
    expect(badge).toHaveTextContent(/observation only/i);
  });

  it("renders per-niche summary strip with ↓ and ↑ symbols", async () => {
    vi.mocked(driftSignals.summary).mockResolvedValue(
      makePayload({
        regressions: [makeSignal({ arm_id: "r1", z_score: -2.5 })],
        counts_by_niche: [
          { niche_id: "gaming", regressions: 3, improvements: 1 },
          { niche_id: "movies", regressions: 1, improvements: 0 },
          // sports is inert this window — MUST NOT render (would burn
          // vertical space with no signal).
          { niche_id: "sports", regressions: 0, improvements: 0 },
        ],
      }),
    );
    renderCard();
    await screen.findByTestId("drift-signal-card");

    const gaming = screen.getByTestId("drift-signal-niche-summary-gaming");
    expect(gaming.textContent).toMatch(/3↓/);
    expect(gaming.textContent).toMatch(/1↑/);

    const movies = screen.getByTestId("drift-signal-niche-summary-movies");
    expect(movies.textContent).toMatch(/1↓/);
    // movies has zero improvements — no ↑ chip.
    expect(movies.textContent).not.toMatch(/↑/);

    // sports is silent — no row.
    expect(
      screen.queryByTestId("drift-signal-niche-summary-sports"),
    ).not.toBeInTheDocument();
  });

  it("shows 'showing 10 of N' when regressions exceed display cap", async () => {
    // Endpoint caps regressions at 20; card displays 10. When more
    // than 10 are returned, the operator needs a signal that there
    // are hidden rows.
    const many: DriftSignal[] = Array.from({ length: 15 }, (_, i) =>
      makeSignal({ arm_id: `arm-${i}`, z_score: -(3.0 + i * 0.05) }),
    );
    vi.mocked(driftSignals.summary).mockResolvedValue(
      makePayload({ regressions: many }),
    );
    renderCard();
    await screen.findByTestId("drift-signal-card");
    const affordance = screen.getByTestId("drift-signal-more-hidden");
    expect(affordance).toHaveTextContent(/showing 10 of 15/i);

    // Sanity: exactly 10 regression rows rendered (not 15).
    const rendered = document.querySelectorAll(
      '[data-testid^="drift-signal-regression-"]',
    );
    expect(rendered.length).toBe(10);
  });

  it("hides 'showing N of M' affordance when regressions are under the cap", async () => {
    // Absence must remain silent — presence of the affordance is a
    // signal ("some are hidden"). If it rendered when everything is
    // visible, it would train the operator to ignore it.
    vi.mocked(driftSignals.summary).mockResolvedValue(
      makePayload({
        regressions: [
          makeSignal({ arm_id: "r1", z_score: -2.5 }),
          makeSignal({ arm_id: "r2", z_score: -2.3 }),
        ],
      }),
    );
    renderCard();
    await screen.findByTestId("drift-signal-card");
    expect(
      screen.queryByTestId("drift-signal-more-hidden"),
    ).not.toBeInTheDocument();
  });

  it("formats z-scores with sign and 2 decimals + rate transition + obs counts", async () => {
    vi.mocked(driftSignals.summary).mockResolvedValue(
      makePayload({
        regressions: [
          makeSignal({
            arm_id: "arm-x",
            z_score: -3.24,
            recent_rate: 0.15,
            baseline_rate: 0.32,
            recent_obs: 22,
            baseline_obs: 45,
          }),
        ],
      }),
    );
    renderCard();
    const row = await screen.findByTestId("drift-signal-regression-arm-x");
    // z-score formatted with sign, 2dp.
    expect(row.textContent).toMatch(/z=-3\.24/);
    // Rate transition renders left→right with the ↓ arrow (regression).
    expect(row.textContent).toMatch(/15\.0% ↓ 32\.0%/);
    // obs counts render as recent/baseline.
    expect(row.textContent).toMatch(/22\/45/);
  });
});
