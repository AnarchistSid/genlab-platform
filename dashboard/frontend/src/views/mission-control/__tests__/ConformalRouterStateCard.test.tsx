/**
 * Pin tests for ConformalRouterStateCard (L4 deferred card,
 * 2026-07-08 audit).
 *
 * The load-bearing invariants:
 *   1. The ``X / N samples`` countdown chip must render the raw
 *      values verbatim (not derived percentages, not "N-X to go")
 *      so the operator can eyeball how close each niche is to
 *      READY without doing mental math.
 *   2. ``ready=true`` must render a green ``READY`` badge and NOT
 *      the countdown — the two must never appear on the same row.
 *   3. Flag badge active-vs-observation-only mirrors the read-side
 *      state of GENLAB_CONFORMAL_ROUTER_ENABLED. Getting this wrong
 *      would mislead the operator into thinking the router is
 *      steering when it's not.
 *   4. Niches are alphabetically ordered — server may return in
 *      any order; card must sort so the operator's scan is stable.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ConformalRouterStatePayload } from "@/api/types";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn<() => Promise<ConformalRouterStatePayload>>();

vi.mock("@/api/client", () => ({
  conformalRouterState: {
    get: () => mockGet(),
  },
}));

vi.mock("@/api/query-keys", () => ({
  queryKeys: {
    conformalRouterState: {
      get: () => ["conformal-router-state"],
    },
  },
}));

import { ConformalRouterStateCard } from "../ConformalRouterStateCard";

function renderCard() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <ConformalRouterStateCard />
    </QueryClientProvider>,
  );
}

function makePayload(
  overrides: Partial<ConformalRouterStatePayload> = {},
): ConformalRouterStatePayload {
  return {
    flag_enabled: false,
    state_path: ".tmp/conformal-router/state.json",
    min_niche_samples: 50,
    niches: [
      {
        niche_id: "gaming",
        sample_count: 120,
        n_train: 84,
        n_calib: 36,
        alpha: 0.1,
        q_hat: 0.427,
        feature_names: ["hook_length", "duration"],
        ready: true,
      },
      {
        niche_id: "anime",
        sample_count: 32,
        n_train: 22,
        n_calib: 10,
        alpha: 0.1,
        q_hat: 0.618,
        feature_names: ["hook_length"],
        ready: false,
      },
    ],
    ...overrides,
  };
}

describe("ConformalRouterStateCard — L4 calibration surface", () => {
  beforeEach(() => {
    mockGet.mockReset();
  });

  it("shows loading state before data arrives", () => {
    mockGet.mockReturnValue(new Promise(() => {})); // never resolve
    renderCard();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("renders cold-start empty copy when niches list is empty", async () => {
    mockGet.mockResolvedValue(makePayload({ niches: [] }));
    renderCard();
    await screen.findByTestId("conformal-router-card");
    expect(
      screen.getByTestId("conformal-router-empty-state"),
    ).toHaveTextContent(/No calibration yet/i);
  });

  it("renders green READY badge and no countdown when ready=true", async () => {
    mockGet.mockResolvedValue(
      makePayload({
        niches: [
          {
            niche_id: "gaming",
            sample_count: 120,
            n_train: 84,
            n_calib: 36,
            alpha: 0.1,
            q_hat: 0.427,
            feature_names: ["hook_length"],
            ready: true,
          },
        ],
      }),
    );
    renderCard();
    await screen.findByTestId("conformal-router-card");

    // READY badge present.
    const ready = screen.getByTestId("conformal-router-ready-badge-gaming");
    expect(ready).toBeInTheDocument();
    expect(ready).toHaveTextContent(/READY/);

    // Countdown chip MUST NOT render on ready rows — the two are
    // mutually exclusive by design. If both showed the operator
    // would think there's still data to accumulate.
    expect(
      screen.queryByTestId("conformal-router-countdown-gaming"),
    ).not.toBeInTheDocument();
  });

  it("renders `X / N samples` countdown verbatim when ready=false", async () => {
    mockGet.mockResolvedValue(
      makePayload({
        min_niche_samples: 50,
        niches: [
          {
            niche_id: "anime",
            sample_count: 32,
            n_train: 22,
            n_calib: 10,
            alpha: 0.1,
            q_hat: 0.618,
            feature_names: ["hook_length"],
            ready: false,
          },
        ],
      }),
    );
    renderCard();
    await screen.findByTestId("conformal-router-card");

    const countdown = screen.getByTestId("conformal-router-countdown-anime");
    // Raw values must appear verbatim — no derived percentage, no
    // "N remaining". This is what makes the countdown scannable.
    expect(countdown).toHaveTextContent(/32\s*\/\s*50\s*samples/);

    // READY badge MUST NOT render on non-ready rows.
    expect(
      screen.queryByTestId("conformal-router-ready-badge-anime"),
    ).not.toBeInTheDocument();
  });

  it("renders green active flag badge when flag_enabled=true", async () => {
    mockGet.mockResolvedValue(makePayload({ flag_enabled: true }));
    renderCard();
    await screen.findByTestId("conformal-router-card");

    const badge = screen.getByTestId("conformal-router-flag-badge");
    expect(badge).toHaveAttribute("data-flag-state", "active");
    expect(badge).toHaveTextContent(/active/);
  });

  it("renders amber observation-only flag badge when flag_enabled=false", async () => {
    mockGet.mockResolvedValue(makePayload({ flag_enabled: false }));
    renderCard();
    await screen.findByTestId("conformal-router-card");

    const badge = screen.getByTestId("conformal-router-flag-badge");
    expect(badge).toHaveAttribute("data-flag-state", "observation-only");
    expect(badge).toHaveTextContent(/observation only/);
  });

  it("sorts niches alphabetically regardless of server order", async () => {
    // Deliberately reverse-alphabetical from the server so the sort
    // in the card is the only thing making the DOM ascend.
    mockGet.mockResolvedValue(
      makePayload({
        niches: [
          {
            niche_id: "sports",
            sample_count: 60,
            n_train: 42,
            n_calib: 18,
            alpha: 0.1,
            q_hat: 0.5,
            feature_names: [],
            ready: true,
          },
          {
            niche_id: "movies",
            sample_count: 60,
            n_train: 42,
            n_calib: 18,
            alpha: 0.1,
            q_hat: 0.5,
            feature_names: [],
            ready: true,
          },
          {
            niche_id: "gaming",
            sample_count: 60,
            n_train: 42,
            n_calib: 18,
            alpha: 0.1,
            q_hat: 0.5,
            feature_names: [],
            ready: true,
          },
          {
            niche_id: "anime",
            sample_count: 60,
            n_train: 42,
            n_calib: 18,
            alpha: 0.1,
            q_hat: 0.5,
            feature_names: [],
            ready: true,
          },
        ],
      }),
    );
    renderCard();
    await screen.findByTestId("conformal-router-card");

    const rows = document.querySelectorAll(
      '[data-testid^="conformal-router-niche-row-"]',
    );
    const rendered = Array.from(rows).map((el) =>
      el
        .getAttribute("data-testid")!
        .replace("conformal-router-niche-row-", ""),
    );
    expect(rendered).toEqual(["anime", "gaming", "movies", "sports"]);
  });

  it("renders α, q̂ and n_calib chips per niche row for scannability", async () => {
    mockGet.mockResolvedValue(
      makePayload({
        niches: [
          {
            niche_id: "gaming",
            sample_count: 120,
            n_train: 84,
            n_calib: 36,
            alpha: 0.1,
            q_hat: 0.427,
            feature_names: [],
            ready: true,
          },
        ],
      }),
    );
    renderCard();
    await screen.findByTestId("conformal-router-card");

    const row = screen.getByTestId("conformal-router-niche-row-gaming");
    // Contents should include the three summary chips. Regexes
    // guard against future rewordings that would silently drop a
    // number the operator relies on.
    expect(row.textContent).toMatch(/α=0\.10/);
    expect(row.textContent).toMatch(/q̂=0\.427/);
    expect(row.textContent).toMatch(/n_calib=36/);
  });
});
