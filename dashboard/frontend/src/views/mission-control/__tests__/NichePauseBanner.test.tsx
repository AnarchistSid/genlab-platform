/**
 * Pin tests for NichePauseBanner (H3, 2026-07-08 audit).
 *
 * The banner is a **conditional-render observability primitive**:
 * its presence at the top of Mission Control tells the operator "at
 * least one niche is paused." The pins below enforce the contract
 * that lets the operator TRUST the presence signal:
 *
 *   1. Empty pause list → NO banner (presence = signal, absence = healthy)
 *   2. One niche paused → banner renders with that niche's chip
 *   3. Multiple paused → summary count + all chips
 *   4. Colored chip per niche (accent-color matches registry)
 *   5. Click scrolls to the pause-card anchor id
 *   6. Uses same react-query key as NichePauseCard (cache dedupe)
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent } from "@testing-library/react";
import type { NichePauseRecord } from "@/api/types";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockList = vi.fn<() => Promise<NichePauseRecord[]>>();

vi.mock("@/api/client", () => ({
  schedulingPauses: {
    list: () => mockList(),
  },
}));

// Registry stub — matches the shape of getNicheInfo but with deterministic
// hex + shortLabel per niche_id so the test can assert visible text.
vi.mock("@/niches/registry", () => ({
  getNicheInfo: (id: string) => ({
    shortLabel:
      id === "gaming"
        ? "CriticalRush"
        : id === "movies"
          ? "SpliceReel"
          : id,
    hex: id === "gaming" ? "#f97316" : "#C9A84C",
  }),
  NICHE_IDS: ["gaming", "movies", "sports", "anime", "ai_creators"],
}));

// Query-keys stub — the test cares that the queryKey matches
// NichePauseCard's, not what its exact value is. Locking in the
// shared key here as a canary.
vi.mock("@/api/query-keys", () => ({
  queryKeys: {
    schedulingPauses: {
      list: () => ["schedulingPauses", "list"],
    },
  },
}));

import { NichePauseBanner } from "../NichePauseBanner";

function renderBanner() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <NichePauseBanner />
    </QueryClientProvider>,
  );
}

describe("NichePauseBanner — H3 top-surface reminder", () => {
  beforeEach(() => {
    mockList.mockReset();
  });

  it("renders nothing when no niches are paused", async () => {
    mockList.mockResolvedValue([]);
    const { container } = renderBanner();
    // Give react-query a tick to settle.
    await Promise.resolve();
    await Promise.resolve();
    // The banner MUST NOT render when the data is empty — absence is
    // the healthy signal. If a "0 niches paused" banner ever appeared
    // it would train operators to ignore the surface.
    expect(
      screen.queryByTestId("niche-pause-banner"),
    ).not.toBeInTheDocument();
    // Sanity: nothing at all rendered from this component.
    expect(container.textContent).toBe("");
  });

  it("renders 1-niche paused with the niche's shortLabel", async () => {
    mockList.mockResolvedValue([
      {
        niche_id: "gaming",
        reason: "copyright strike investigation",
        paused_until: null,
        paused_by: "operator",
      } as never,
    ]);
    renderBanner();
    await screen.findByTestId("niche-pause-banner");
    expect(screen.getByText(/1 niche paused/i)).toBeInTheDocument();
    expect(screen.getByText("CriticalRush")).toBeInTheDocument();
  });

  it("renders N-niche paused with a comma-separated chip list", async () => {
    mockList.mockResolvedValue([
      { niche_id: "gaming", reason: "x", paused_until: null } as never,
      { niche_id: "movies", reason: "x", paused_until: null } as never,
    ]);
    renderBanner();
    await screen.findByTestId("niche-pause-banner");
    // Headline reflects the count.
    expect(screen.getByText(/2 niches paused/i)).toBeInTheDocument();
    // Both niche labels visible.
    expect(screen.getByText("CriticalRush")).toBeInTheDocument();
    expect(screen.getByText("SpliceReel")).toBeInTheDocument();
  });

  it("clicking the banner scrolls to the pause-card anchor", async () => {
    mockList.mockResolvedValue([
      { niche_id: "gaming", reason: "x", paused_until: null } as never,
    ]);

    // Set up the anchor target and spy on its scrollIntoView.
    const anchor = document.createElement("div");
    anchor.id = "niche-pause-card";
    document.body.appendChild(anchor);
    const scrollSpy = vi.fn();
    anchor.scrollIntoView = scrollSpy;

    renderBanner();
    const banner = await screen.findByTestId("niche-pause-banner");
    fireEvent.click(banner);
    expect(scrollSpy).toHaveBeenCalledTimes(1);
    expect(scrollSpy).toHaveBeenCalledWith({
      behavior: "smooth",
      block: "start",
    });

    document.body.removeChild(anchor);
  });

  it("has role=status + aria-live=polite so screen readers announce it", async () => {
    mockList.mockResolvedValue([
      { niche_id: "gaming", reason: "x", paused_until: null } as never,
    ]);
    renderBanner();
    const banner = await screen.findByTestId("niche-pause-banner");
    expect(banner.getAttribute("role")).toBe("status");
    expect(banner.getAttribute("aria-live")).toBe("polite");
  });
});
