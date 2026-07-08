/**
 * Pin tests for FlagStateCard (L11 deferred card, 2026-07-08 audit).
 *
 * The primary invariant to lock in: **mis_set entries must surface
 * the warning message**. That's what makes the card valuable — it
 * catches the case-sensitivity footgun that motivated L11 in the
 * first place.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { FlagStatePayload } from "@/api/types";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn<() => Promise<FlagStatePayload>>();

vi.mock("@/api/client", () => ({
  flagState: {
    get: () => mockGet(),
  },
}));

vi.mock("@/api/query-keys", () => ({
  queryKeys: {
    flagState: {
      get: () => ["flag-state"],
    },
  },
}));

import { FlagStateCard } from "../FlagStateCard";

function renderCard() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <FlagStateCard />
    </QueryClientProvider>,
  );
}

function makePayload(overrides: Partial<FlagStatePayload> = {}): FlagStatePayload {
  return {
    flags: [
      {
        name: "GENLAB_BAYESIAN_GATE_ENABLED",
        group: "learning",
        role: "Enables Bayesian LR gate",
        polarity: "enable",
        raw_value: "1",
        status: "active",
        warning: null,
      },
      {
        name: "GENLAB_CROSS_NICHE_TRANSFER_ENABLED",
        group: "intelligence",
        role: "Enables cross-niche transfer",
        polarity: "enable",
        raw_value: null,
        status: "dormant",
        warning: null,
      },
      {
        name: "GENLAB_INTELLIGENT_TRANSFORM_ENABLED",
        group: "content_quality",
        role: "Enables 11-dim transformation",
        polarity: "enable",
        raw_value: "true",
        status: "mis_set",
        warning:
          'value "true" doesn\'t match a known truthy/falsy convention — may silently no-op depending on reader',
      },
    ],
    summary: {
      total: 3,
      active: 1,
      dormant: 1,
      mis_set: 1,
    },
    ...overrides,
  };
}

describe("FlagStateCard — audit-shipping-arc footgun surfacer", () => {
  beforeEach(() => {
    mockGet.mockReset();
  });

  it("shows loading state before data arrives", () => {
    mockGet.mockReturnValue(new Promise(() => {})); // never resolve
    renderCard();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("renders every flag from the payload with its status attribute", async () => {
    mockGet.mockResolvedValue(makePayload());
    renderCard();

    // Await the card to appear.
    await screen.findByTestId("flag-state-card");

    // Every flag shows up as a row.
    expect(
      screen.getByTestId("flag-row-GENLAB_BAYESIAN_GATE_ENABLED"),
    ).toHaveAttribute("data-status", "active");
    expect(
      screen.getByTestId("flag-row-GENLAB_CROSS_NICHE_TRANSFER_ENABLED"),
    ).toHaveAttribute("data-status", "dormant");
    expect(
      screen.getByTestId("flag-row-GENLAB_INTELLIGENT_TRANSFORM_ENABLED"),
    ).toHaveAttribute("data-status", "mis_set");
  });

  it("surfaces the mis-set count prominently in the summary", async () => {
    mockGet.mockResolvedValue(makePayload());
    renderCard();
    await screen.findByTestId("flag-state-card");
    expect(screen.getByTestId("flag-state-mis-set-count")).toHaveTextContent(
      /1 mis-set/,
    );
  });

  it("HIDES the mis-set summary chip when nothing is mis-set (healthy path)", async () => {
    mockGet.mockResolvedValue(
      makePayload({
        flags: [
          {
            name: "GENLAB_BAYESIAN_GATE_ENABLED",
            group: "learning",
            role: "Enables Bayesian LR gate",
            polarity: "enable",
            raw_value: "1",
            status: "active",
            warning: null,
          },
        ],
        summary: { total: 1, active: 1, dormant: 0, mis_set: 0 },
      }),
    );
    renderCard();
    await screen.findByTestId("flag-state-card");
    // Zero mis-set = healthy = the amber chip MUST NOT render.
    // Its presence trains operators to look for it; presence must
    // remain a signal, absence must remain silent.
    expect(
      screen.queryByTestId("flag-state-mis-set-count"),
    ).not.toBeInTheDocument();
  });

  it("shows the warning message for mis_set entries", async () => {
    mockGet.mockResolvedValue(makePayload());
    renderCard();
    await screen.findByTestId("flag-state-card");
    const warning = screen.getByTestId(
      "flag-warning-GENLAB_INTELLIGENT_TRANSFORM_ENABLED",
    );
    expect(warning).toBeInTheDocument();
    // The warning MUST include the raw value that caused the mis_set
    // — otherwise the operator has no signal about what to fix.
    expect(warning.textContent).toMatch(/"true"/);
  });

  it("shows raw_value only for mis_set entries (not active/dormant)", async () => {
    mockGet.mockResolvedValue(makePayload());
    renderCard();
    await screen.findByTestId("flag-state-card");

    // Active flag has raw="1" but we deliberately don't repeat it
    // — reduces visual noise. Only mis_set needs the raw value
    // surfaced.
    const activeRow = screen.getByTestId(
      "flag-row-GENLAB_BAYESIAN_GATE_ENABLED",
    );
    expect(activeRow.textContent).not.toMatch(/\b1\b(?!.*Enables)/);

    // Mis-set row includes "true" as the raw value chip.
    const misSetRow = screen.getByTestId(
      "flag-row-GENLAB_INTELLIGENT_TRANSFORM_ENABLED",
    );
    expect(misSetRow.textContent).toMatch(/true/);
  });

  it("groups flags by category and preserves group order", async () => {
    mockGet.mockResolvedValue(makePayload());
    renderCard();
    await screen.findByTestId("flag-state-card");

    const learning = screen.getByTestId("flag-group-learning");
    const intelligence = screen.getByTestId("flag-group-intelligence");
    const contentQuality = screen.getByTestId("flag-group-content_quality");

    // DOM order matters — the card renders groups in the
    // _GROUP_ORDER declared at the top of the component. If someone
    // reorders that array without thought, operator scanning order
    // changes silently.
    const positions = [learning, intelligence, contentQuality].map((el) =>
      Array.from(document.querySelectorAll('[data-testid^="flag-group-"]')).indexOf(el),
    );
    expect(positions).toEqual([0, 1, 2]);
  });

  it("surfaces disabled_by_flag with a red chip when a kill switch is active", async () => {
    mockGet.mockResolvedValue({
      flags: [
        {
          name: "GENLAB_AUTO_APPROVE_DISABLED",
          group: "infrastructure",
          role: "Kill switch",
          polarity: "disable",
          raw_value: "1",
          status: "disabled_by_flag",
          warning: null,
        },
      ],
      summary: { total: 1, active: 0, dormant: 0, mis_set: 0, disabled_by_flag: 1 },
    });
    renderCard();
    await screen.findByTestId("flag-state-card");
    expect(
      screen.getByText(/kill switch ON/i),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("flag-row-GENLAB_AUTO_APPROVE_DISABLED"),
    ).toHaveAttribute("data-status", "disabled_by_flag");
  });
});
