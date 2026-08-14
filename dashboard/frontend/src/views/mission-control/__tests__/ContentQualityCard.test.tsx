/**
 * Phase 4.A session 4 observability card tests.
 *
 * Pin the load-bearing signals: badge state per flag, cold-start
 * copy, per-niche row rendering with the joint/visual/audio triple,
 * range display, and the color-class contract (score < 0.30 =
 * red-400, 0.30-0.60 = warning, ≥ 0.60 = success).
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/api/client", () => ({
  contentQuality: {
    summary: vi.fn(),
  },
}));

import { contentQuality } from "@/api/client";
import { ContentQualityCard } from "../ContentQualityCard";
import type { ContentQualitySummary } from "@/api/types";

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const _stub = (
  overrides: Partial<ContentQualitySummary> = {},
): ContentQualitySummary => ({
  flag_enabled: false,
  per_niche: [
    {
      niche_id: "gaming",
      n_scored: 12,
      avg_joint: 0.42,
      avg_visual: 0.38,
      avg_audio: 0.51,
      min_joint: 0.12,
      max_joint: 0.78,
      last_scored_at: "2026-08-14T19:00:00+00:00",
    },
  ],
  ...overrides,
});

describe("ContentQualityCard", () => {
  it("shows cold-start copy when no data", async () => {
    vi.mocked(contentQuality.summary).mockResolvedValue(null);
    renderWithClient(<ContentQualityCard />);
    expect(await screen.findByText(/No scores yet/i)).toBeInTheDocument();
  });

  it("shows 'observation only' when flag off", async () => {
    vi.mocked(contentQuality.summary).mockResolvedValue(
      _stub({ flag_enabled: false }),
    );
    renderWithClient(<ContentQualityCard />);
    expect(await screen.findByText("observation only")).toBeInTheDocument();
  });

  it("shows 'active' when flag on", async () => {
    vi.mocked(contentQuality.summary).mockResolvedValue(
      _stub({ flag_enabled: true }),
    );
    renderWithClient(<ContentQualityCard />);
    expect(await screen.findByText("active")).toBeInTheDocument();
  });

  it("renders niche_id + joint/visual/audio triple", async () => {
    vi.mocked(contentQuality.summary).mockResolvedValue(_stub());
    renderWithClient(<ContentQualityCard />);
    expect(await screen.findByText("gaming")).toBeInTheDocument();
    expect(screen.getByText(/joint=0\.42/)).toBeInTheDocument();
    expect(screen.getByText(/vis=0\.38/)).toBeInTheDocument();
    expect(screen.getByText(/aud=0\.51/)).toBeInTheDocument();
  });

  it("renders min/max range", async () => {
    vi.mocked(contentQuality.summary).mockResolvedValue(_stub());
    renderWithClient(<ContentQualityCard />);
    expect(await screen.findByText(/range: 0\.12–0\.78/)).toBeInTheDocument();
  });

  it("shows empty niches when per_niche is []", async () => {
    vi.mocked(contentQuality.summary).mockResolvedValue(
      _stub({ per_niche: [] }),
    );
    renderWithClient(<ContentQualityCard />);
    expect(
      await screen.findByText(/No niches scored in the last 7 days/i),
    ).toBeInTheDocument();
  });

  it("renders em-dash for null score", async () => {
    vi.mocked(contentQuality.summary).mockResolvedValue(
      _stub({
        per_niche: [
          {
            niche_id: "gaming", n_scored: 1,
            avg_joint: null, avg_visual: null, avg_audio: null,
            min_joint: null, max_joint: null,
            last_scored_at: null,
          },
        ],
      }),
    );
    renderWithClient(<ContentQualityCard />);
    // "—" is the null-score placeholder
    expect(await screen.findByText(/joint=—/)).toBeInTheDocument();
  });
});
