/**
 * PR B (2026-06-27) — PerPlatformHealthCard tests.
 *
 * Pins:
 *   * Renders the 5-niche × N-platform matrix
 *   * Color tones: red < red_below_pct, yellow < yellow_below_pct, else green
 *   * Insufficient-data rows render "—" with grey tone
 *   * Tooltip surfaces last_failure_at + first 80 chars of error + counts
 *   * Headline counts red cells across the matrix
 *   * Error state renders without crashing
 *   * classifyRate pure function math
 *
 * Mutation behaviors don't exist on this card — it's read-only.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/api/client", () => ({
  publishingHealthPerNiche: {
    fetch: vi.fn(),
  },
}));

import { publishingHealthPerNiche } from "@/api/client";
import {
  PerPlatformHealthCard,
  classifyRate,
} from "../PerPlatformHealthCard";

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const DEFAULT_THRESHOLDS = { red_below_pct: 50, yellow_below_pct: 80 };

// ── classifyRate pure function ────────────────────────────────────

describe("classifyRate", () => {
  it("returns grey for null rate (insufficient data)", () => {
    expect(classifyRate(null, DEFAULT_THRESHOLDS)).toBe("grey");
  });

  it("returns red below red_below_pct", () => {
    expect(classifyRate(0, DEFAULT_THRESHOLDS)).toBe("red");
    expect(classifyRate(49.9, DEFAULT_THRESHOLDS)).toBe("red");
  });

  it("returns yellow between red_below_pct (incl) and yellow_below_pct", () => {
    expect(classifyRate(50, DEFAULT_THRESHOLDS)).toBe("yellow");
    expect(classifyRate(79.9, DEFAULT_THRESHOLDS)).toBe("yellow");
  });

  it("returns green at or above yellow_below_pct", () => {
    expect(classifyRate(80, DEFAULT_THRESHOLDS)).toBe("green");
    expect(classifyRate(100, DEFAULT_THRESHOLDS)).toBe("green");
  });

  it("respects custom thresholds (e.g. relaxed yellow at 70)", () => {
    const relaxed = { red_below_pct: 30, yellow_below_pct: 70 };
    expect(classifyRate(29.9, relaxed)).toBe("red");
    expect(classifyRate(30, relaxed)).toBe("yellow");
    expect(classifyRate(69.9, relaxed)).toBe("yellow");
    expect(classifyRate(70, relaxed)).toBe("green");
  });
});

// ── Card render tests ─────────────────────────────────────────────

describe("PerPlatformHealthCard", () => {
  it("renders 5 niche rows × N platform columns", async () => {
    vi.mocked(publishingHealthPerNiche.fetch).mockResolvedValueOnce({
      window_days: 14,
      thresholds: DEFAULT_THRESHOLDS,
      rows: [
        {
          niche_id: "gaming",
          platform: "youtube",
          success_count: 10,
          failed_count: 0,
          success_rate_pct: 100,
          last_success_at: "2026-06-27T00:00:00Z",
          last_failure_at: null,
          last_failure_error: null,
        },
        {
          niche_id: "gaming",
          platform: "instagram",
          success_count: 5,
          failed_count: 5,
          success_rate_pct: 50,
          last_success_at: "2026-06-26T00:00:00Z",
          last_failure_at: "2026-06-27T00:00:00Z",
          last_failure_error: "boom",
        },
      ],
    });
    renderWithClient(<PerPlatformHealthCard />);
    // Headers: youtube + instagram
    expect(await screen.findByText("youtube")).toBeInTheDocument();
    expect(await screen.findByText("instagram")).toBeInTheDocument();
    // All 5 niches rendered as row headers (short labels from registry)
    expect(screen.getByText("AI")).toBeInTheDocument();
    expect(screen.getByText("Gaming")).toBeInTheDocument();
    expect(screen.getByText("Sports")).toBeInTheDocument();
    expect(screen.getByText("Movies")).toBeInTheDocument();
    expect(screen.getByText("Anime")).toBeInTheDocument();
  });

  it("applies red tone when rate is below threshold", async () => {
    vi.mocked(publishingHealthPerNiche.fetch).mockResolvedValueOnce({
      window_days: 14,
      thresholds: DEFAULT_THRESHOLDS,
      rows: [
        {
          niche_id: "ai_creators",
          platform: "instagram",
          success_count: 0,
          failed_count: 3,
          success_rate_pct: 0,
          last_success_at: null,
          last_failure_at: "2026-06-27T06:42:47Z",
          last_failure_error: "Container processing error: 2207077",
        },
      ],
    });
    renderWithClient(<PerPlatformHealthCard />);
    const cells = await screen.findAllByTestId("health-cell");
    const redCell = cells.find((c) => c.getAttribute("data-tone") === "red");
    expect(redCell).toBeDefined();
    expect(redCell).toHaveTextContent("0%");
  });

  it("applies yellow tone when rate is 50-79%", async () => {
    vi.mocked(publishingHealthPerNiche.fetch).mockResolvedValueOnce({
      window_days: 14,
      thresholds: DEFAULT_THRESHOLDS,
      rows: [
        {
          niche_id: "gaming",
          platform: "youtube",
          success_count: 6,
          failed_count: 4,
          success_rate_pct: 60,
          last_success_at: "2026-06-27T00:00:00Z",
          last_failure_at: "2026-06-26T00:00:00Z",
          last_failure_error: "transient",
        },
      ],
    });
    renderWithClient(<PerPlatformHealthCard />);
    const cells = await screen.findAllByTestId("health-cell");
    const yellowCell = cells.find(
      (c) => c.getAttribute("data-tone") === "yellow",
    );
    expect(yellowCell).toBeDefined();
    expect(yellowCell).toHaveTextContent("60%");
  });

  it("applies green tone when rate is >= 80%", async () => {
    vi.mocked(publishingHealthPerNiche.fetch).mockResolvedValueOnce({
      window_days: 14,
      thresholds: DEFAULT_THRESHOLDS,
      rows: [
        {
          niche_id: "sports",
          platform: "facebook",
          success_count: 10,
          failed_count: 0,
          success_rate_pct: 100,
          last_success_at: "2026-06-27T00:00:00Z",
          last_failure_at: null,
          last_failure_error: null,
        },
      ],
    });
    renderWithClient(<PerPlatformHealthCard />);
    const cells = await screen.findAllByTestId("health-cell");
    const greenCell = cells.find(
      (c) => c.getAttribute("data-tone") === "green",
    );
    expect(greenCell).toBeDefined();
    expect(greenCell).toHaveTextContent("100%");
  });

  it("renders '—' for insufficient-data cells", async () => {
    vi.mocked(publishingHealthPerNiche.fetch).mockResolvedValueOnce({
      window_days: 14,
      thresholds: DEFAULT_THRESHOLDS,
      rows: [
        {
          niche_id: "anime",
          platform: "youtube",
          success_count: 1,
          failed_count: 0,
          success_rate_pct: null,
          last_success_at: "2026-06-27T00:00:00Z",
          last_failure_at: null,
          last_failure_error: null,
        },
      ],
    });
    renderWithClient(<PerPlatformHealthCard />);
    const cells = await screen.findAllByTestId("health-cell");
    const greyCell = cells.find((c) => c.getAttribute("data-tone") === "grey");
    expect(greyCell).toBeDefined();
    expect(greyCell).toHaveTextContent("—");
  });

  it("tooltip surfaces last failure error + counts + last_failure_at", async () => {
    const longError =
      "Container processing error: Error: Media upload has failed with error code 2207077";
    vi.mocked(publishingHealthPerNiche.fetch).mockResolvedValueOnce({
      window_days: 14,
      thresholds: DEFAULT_THRESHOLDS,
      rows: [
        {
          niche_id: "ai_creators",
          platform: "instagram",
          success_count: 0,
          failed_count: 3,
          success_rate_pct: 0,
          last_success_at: "2026-06-20T00:00:00Z",
          last_failure_at: "2026-06-27T06:42:47Z",
          last_failure_error: longError,
        },
      ],
    });
    renderWithClient(<PerPlatformHealthCard />);
    const cells = await screen.findAllByTestId("health-cell");
    const failedCell = cells.find(
      (c) => c.getAttribute("data-tone") === "red",
    )!;
    const title = failedCell.getAttribute("title") ?? "";
    // Contains the failure timestamp
    expect(title).toContain("2026-06-27T06:42:47Z");
    // Contains the error message (or its truncation up to 80 chars)
    expect(title).toContain("Container processing error");
    // Contains the raw sample counts
    expect(title).toContain("0 success / 3 failed");
  });

  it("headline counts red cells across the matrix", async () => {
    vi.mocked(publishingHealthPerNiche.fetch).mockResolvedValueOnce({
      window_days: 14,
      thresholds: DEFAULT_THRESHOLDS,
      rows: [
        {
          niche_id: "ai_creators",
          platform: "instagram",
          success_count: 0,
          failed_count: 3,
          success_rate_pct: 0,
          last_success_at: null,
          last_failure_at: "2026-06-27T00:00:00Z",
          last_failure_error: "fail",
        },
        {
          niche_id: "gaming",
          platform: "instagram",
          success_count: 1,
          failed_count: 9,
          success_rate_pct: 10,
          last_success_at: "2026-06-20T00:00:00Z",
          last_failure_at: "2026-06-27T00:00:00Z",
          last_failure_error: "fail",
        },
        {
          niche_id: "sports",
          platform: "youtube",
          success_count: 10,
          failed_count: 0,
          success_rate_pct: 100,
          last_success_at: "2026-06-27T00:00:00Z",
          last_failure_at: null,
          last_failure_error: null,
        },
      ],
    });
    renderWithClient(<PerPlatformHealthCard />);
    // Headline: 2 reds out of 3 total cells with data
    expect(
      await screen.findByText(
        /2 of 3 niche-platform combinations red/i,
      ),
    ).toBeInTheDocument();
  });

  it("headline says 'All N healthy' when no reds", async () => {
    vi.mocked(publishingHealthPerNiche.fetch).mockResolvedValueOnce({
      window_days: 14,
      thresholds: DEFAULT_THRESHOLDS,
      rows: [
        {
          niche_id: "sports",
          platform: "youtube",
          success_count: 10,
          failed_count: 0,
          success_rate_pct: 100,
          last_success_at: "2026-06-27T00:00:00Z",
          last_failure_at: null,
          last_failure_error: null,
        },
      ],
    });
    renderWithClient(<PerPlatformHealthCard />);
    expect(
      await screen.findByText(/All 1 combinations healthy/i),
    ).toBeInTheDocument();
  });

  it("headline says 'Insufficient data across all combinations' when nothing graded", async () => {
    vi.mocked(publishingHealthPerNiche.fetch).mockResolvedValueOnce({
      window_days: 14,
      thresholds: DEFAULT_THRESHOLDS,
      rows: [
        {
          niche_id: "sports",
          platform: "youtube",
          success_count: 1,
          failed_count: 1,
          success_rate_pct: null,
          last_success_at: null,
          last_failure_at: null,
          last_failure_error: null,
        },
      ],
    });
    renderWithClient(<PerPlatformHealthCard />);
    expect(
      await screen.findByText(/Insufficient data across all combinations/i),
    ).toBeInTheDocument();
  });

  it("renders empty-platforms zero-state when no rows at all", async () => {
    vi.mocked(publishingHealthPerNiche.fetch).mockResolvedValueOnce({
      window_days: 14,
      thresholds: DEFAULT_THRESHOLDS,
      rows: [],
    });
    renderWithClient(<PerPlatformHealthCard />);
    expect(
      await screen.findByText(/No publish attempts in the window/i),
    ).toBeInTheDocument();
  });

  it("falls back to default thresholds when API omits them (defensive)", async () => {
    // Server-supplied thresholds are the canonical source, but if a
    // pre-PR-B deploy returns the old shape, the card falls back to
    // sensible defaults rather than crash. Synthesised here by omitting
    // thresholds in the mock — runtime cast forces TS to accept it.
    vi.mocked(publishingHealthPerNiche.fetch).mockResolvedValueOnce({
      window_days: 14,
      rows: [
        {
          niche_id: "gaming",
          platform: "youtube",
          success_count: 5,
          failed_count: 5,
          success_rate_pct: 50,
          last_success_at: null,
          last_failure_at: null,
          last_failure_error: null,
        },
      ],
      // @ts-expect-error — pretending the server forgot thresholds
      thresholds: undefined,
    });
    renderWithClient(<PerPlatformHealthCard />);
    const cells = await screen.findAllByTestId("health-cell");
    // 50% → yellow under defaults (red_below=50, yellow_below=80)
    const yellowCell = cells.find(
      (c) => c.getAttribute("data-tone") === "yellow",
    );
    expect(yellowCell).toBeDefined();
  });

  it("renders error state when fetch rejects", async () => {
    vi.mocked(publishingHealthPerNiche.fetch).mockRejectedValueOnce(
      new Error("boom"),
    );
    renderWithClient(<PerPlatformHealthCard />);
    expect(
      await screen.findByText(/Health endpoint unreachable/i),
    ).toBeInTheDocument();
  });

  it("matrix has accessible table role + aria-label", async () => {
    vi.mocked(publishingHealthPerNiche.fetch).mockResolvedValueOnce({
      window_days: 14,
      thresholds: DEFAULT_THRESHOLDS,
      rows: [
        {
          niche_id: "gaming",
          platform: "youtube",
          success_count: 10,
          failed_count: 0,
          success_rate_pct: 100,
          last_success_at: null,
          last_failure_at: null,
          last_failure_error: null,
        },
      ],
    });
    renderWithClient(<PerPlatformHealthCard />);
    const table = await screen.findByRole("table");
    expect(table).toHaveAttribute(
      "aria-label",
      expect.stringMatching(/per-niche per-platform/i),
    );
    // Each platform column header
    const colHeaders = within(table).getAllByRole("columnheader");
    expect(colHeaders.length).toBeGreaterThanOrEqual(2); // Niche + N platforms
    // Each niche row header
    const rowHeaders = within(table).getAllByRole("rowheader");
    expect(rowHeaders.length).toBe(5);
  });
});
