/**
 * PR after #580 — NichePauseCard tests.
 *
 * Coverage philosophy: pin the operator-emergency-stop UX contract.
 * The card has three states per row (publishing / paused / loading)
 * and two mutation buttons (Pause / Resume). The test surface should
 * catch:
 *
 *   * Headline summary counts (0 / partial / all-5)
 *   * Per-row state badge (publishing vs paused)
 *   * formatPauseRemaining edge cases (indef / expired / sub-min /
 *     hours-+-minutes)
 *
 * Mutation buttons (Pause / Resume) are NOT integration-tested
 * here — those would require mocking toast + clipboard + useMutation
 * + window.prompt and the value-vs-effort tradeoff is poor. They're
 * tested e2e via the dashboard playwright suite in a follow-up.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Mock the API client BEFORE importing the component. The card uses
// schedulingPauses.list() and may construct mutations that touch the
// other methods, so we stub the full surface.
vi.mock("@/api/client", () => ({
  schedulingPauses: {
    list: vi.fn(),
    pause: vi.fn(),
    unpause: vi.fn(),
  },
}));

import { schedulingPauses } from "@/api/client";
import { NichePauseCard, formatPauseRemaining } from "../NichePauseCard";

function renderWithClient(ui: React.ReactElement) {
  // Disable retries so failed-fetch tests don't take 30s. Each test
  // gets its own client so cache state doesn't leak.
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("NichePauseCard", () => {
  it("shows 'All 5 niches publishing' headline when no pauses exist", async () => {
    vi.mocked(schedulingPauses.list).mockResolvedValueOnce([]);
    renderWithClient(<NichePauseCard />);
    expect(
      await screen.findByText(/All 5 niches publishing/i),
    ).toBeInTheDocument();
  });

  it("shows 'N of 5 niches paused' headline on partial pause", async () => {
    vi.mocked(schedulingPauses.list).mockResolvedValueOnce([
      {
        niche_id: "gaming",
        paused_until: new Date(Date.now() + 4 * 3600_000).toISOString(),
        reason: "copyright_strike",
        paused_by: "admin",
        created_at: new Date().toISOString(),
      },
      {
        niche_id: "anime",
        paused_until: null,
        reason: "shadowban_investigation",
        paused_by: "admin",
        created_at: new Date().toISOString(),
      },
    ]);
    renderWithClient(<NichePauseCard />);
    expect(await screen.findByText(/2 of 5 niches paused/i)).toBeInTheDocument();
  });

  it("renders 'paused' badge with formatted time-remaining for paused rows", async () => {
    // 4h 30m + 35s buffer so the floor-minutes math stays at "4h 30m"
    // even after ~1s of test render delay (without the buffer, a 100ms
    // delay rounds down to 269m → "4h 29m" and the regex misses).
    const fourHours = new Date(
      Date.now() + (4 * 60 + 30) * 60_000 + 35_000,
    ).toISOString();
    vi.mocked(schedulingPauses.list).mockResolvedValueOnce([
      {
        niche_id: "gaming",
        paused_until: fourHours,
        reason: "copyright_strike",
        paused_by: "admin",
        created_at: new Date().toISOString(),
      },
    ]);
    renderWithClient(<NichePauseCard />);
    // Badge text is "paused 4h 30m" — match the broader pattern so a
    // future formatter tweak (e.g. "4h 30min") still passes.
    expect(await screen.findByText(/paused 4h 30/)).toBeInTheDocument();
  });

  it("renders 'indef.' for indefinite pauses (paused_until=null)", async () => {
    vi.mocked(schedulingPauses.list).mockResolvedValueOnce([
      {
        niche_id: "movies",
        paused_until: null,
        reason: "manual_review",
        paused_by: "admin",
        created_at: new Date().toISOString(),
      },
    ]);
    renderWithClient(<NichePauseCard />);
    expect(await screen.findByText(/paused indef\./)).toBeInTheDocument();
  });

  it("shows '● publishing' for niches without an active pause row", async () => {
    vi.mocked(schedulingPauses.list).mockResolvedValueOnce([]);
    renderWithClient(<NichePauseCard />);
    // All 5 niches should have the publishing indicator
    const publishingIndicators = await screen.findAllByText(/● publishing/);
    expect(publishingIndicators.length).toBe(5);
  });

  it("surfaces the auto-pause env-flag pointer in the footer", async () => {
    vi.mocked(schedulingPauses.list).mockResolvedValueOnce([]);
    renderWithClient(<NichePauseCard />);
    // Footer mentions the opt-in flag — operators discoverability
    // for the PR #579 auto-pause wire
    expect(
      await screen.findByText(/GENLAB_AUTO_PAUSE_ON_HEALTH_CRITICAL=1/),
    ).toBeInTheDocument();
  });
});

// ── formatPauseRemaining unit ────────────────────────────────────

describe("formatPauseRemaining", () => {
  it("returns 'indef.' for null", () => {
    expect(formatPauseRemaining(null)).toBe("indef.");
  });

  it("returns 'indef.' for an invalid date string", () => {
    expect(formatPauseRemaining("not-a-date")).toBe("indef.");
  });

  it("returns 'expired' for past timestamps", () => {
    const past = new Date(Date.now() - 60_000).toISOString();
    expect(formatPauseRemaining(past)).toBe("expired");
  });

  it("formats hours + minutes for multi-hour pauses", () => {
    const future = new Date(Date.now() + (2 * 60 + 15) * 60_000 + 5_000).toISOString();
    // 2h 15m (the +5s slop avoids flakes from clock drift between
    // the helper-call and the new-Date call)
    expect(formatPauseRemaining(future)).toBe("2h 15m");
  });

  it("formats minutes-only when under an hour", () => {
    const future = new Date(Date.now() + 45 * 60_000 + 5_000).toISOString();
    expect(formatPauseRemaining(future)).toBe("45m");
  });

  it("formats seconds for sub-minute remaining", () => {
    const future = new Date(Date.now() + 30_000).toISOString();
    const result = formatPauseRemaining(future);
    // Allow 25-30s window for clock drift
    expect(result).toMatch(/^(2[5-9]|30)s$/);
  });
});
