/**
 * A + B intelligence stack observability card tests (2026-07-08).
 *
 * Pin the two load-bearing signals per niche row:
 *
 *   * ``B.2`` / ``A.2`` flag badges — the operator's "would flipping
 *     the flag change anything?" indicator. Two INDEPENDENT badges
 *     because the two runners have independent flag env vars; the
 *     card must not merge them into one bit.
 *
 *   * ``fresh=N/M`` counter — the operator's proxy for "is A.3's
 *     composite going to see anything?" Freshness threshold must
 *     stay in sync with ``_signal_creator_upload_lead``'s recency
 *     weight (>24h zeroes out).
 *
 * Any silent break here mis-signals the operator about whether the
 * A+B stack is live vs observation-only. The tests below assert
 * the badge + counter behaviour on the specific stubbed shapes B.2
 * and A.2 actually write.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/api/client", () => ({
  topCreatorPriors: {
    latest: vi.fn(),
    uploads: vi.fn(),
  },
}));

import { topCreatorPriors } from "@/api/client";
import { TopCreatorPriorsCard } from "../TopCreatorPriorsCard";
import type {
  TopCreatorPriorsArtifact,
  TopCreatorUploadsArtifact,
} from "@/api/types";

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const _priorsStub = (
  overrides: Partial<TopCreatorPriorsArtifact> = {},
): TopCreatorPriorsArtifact => ({
  generated_at: "2026-07-08T04:00:00+00:00",
  niche_id: "gaming",
  video_count: 24,
  correlations: {
    title_ends_with_question: 0.42,
    description_has_timestamps: 0.28,
    title_length_chars: -0.15,
  },
  flag_enabled: false,
  artifact_stamp: "20260708",
  ...overrides,
});

const _uploadsStub = (
  overrides: Partial<TopCreatorUploadsArtifact> = {},
): TopCreatorUploadsArtifact => ({
  generated_at: "2026-07-08T14:00:00+00:00",
  niche_id: "gaming",
  creators: [
    {
      channel_id: "UC1111111111111111111111",
      label: "creator-a",
      uploads: [
        {
          video_id: "abc",
          title: "fresh upload",
          published_at: "2026-07-08T12:00:00Z",
          hours_since_publish: 2.0,
        },
      ],
    },
    {
      channel_id: "UC2222222222222222222222",
      label: "creator-b",
      uploads: [
        {
          video_id: "def",
          title: "day-old upload",
          published_at: "2026-07-07T14:00:00Z",
          hours_since_publish: 22.0,
        },
      ],
    },
    {
      channel_id: "UC3333333333333333333333",
      label: "creator-c",
      uploads: [
        {
          video_id: "ghi",
          title: "old upload",
          published_at: "2026-07-05T14:00:00Z",
          hours_since_publish: 72.0,
        },
      ],
    },
  ],
  flag_enabled: false,
  artifact_stamp: "20260708",
  ...overrides,
});

describe("TopCreatorPriorsCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders header + subtitle", async () => {
    vi.mocked(topCreatorPriors.latest).mockResolvedValue(null);
    vi.mocked(topCreatorPriors.uploads).mockResolvedValue(null);
    renderWithClient(<TopCreatorPriorsCard />);
    expect(
      await screen.findByText(/Top-creator intelligence/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/A\+B stack observability/i),
    ).toBeInTheDocument();
  });

  it("shows 'no corr yet' cold-start when B.2 returns null", async () => {
    vi.mocked(topCreatorPriors.latest).mockResolvedValue(null);
    vi.mocked(topCreatorPriors.uploads).mockResolvedValue(null);
    renderWithClient(<TopCreatorPriorsCard />);
    // 5 niches × 1 "no corr yet" per row = 5 instances
    const cold = await screen.findAllByText(/no corr yet/i);
    expect(cold.length).toBeGreaterThan(0);
  });

  it("renders B.2 flag badge in 'observation only' amber state", async () => {
    vi.mocked(topCreatorPriors.latest).mockResolvedValue(
      _priorsStub({ flag_enabled: false }),
    );
    vi.mocked(topCreatorPriors.uploads).mockResolvedValue(null);
    renderWithClient(<TopCreatorPriorsCard />);
    const b2Badges = await screen.findAllByText("B.2");
    // Each of 5 niches renders its own row → 5 B.2 badges. Sanity:
    // at least one exists AND has the amber "observation only"
    // styling (the title attr encodes the state).
    expect(b2Badges.length).toBeGreaterThan(0);
    expect(b2Badges[0].getAttribute("title")).toMatch(/off/);
  });

  it("renders B.2 flag badge in 'active' green state when flag on", async () => {
    vi.mocked(topCreatorPriors.latest).mockResolvedValue(
      _priorsStub({ flag_enabled: true }),
    );
    vi.mocked(topCreatorPriors.uploads).mockResolvedValue(null);
    renderWithClient(<TopCreatorPriorsCard />);
    const b2Badges = await screen.findAllByText("B.2");
    expect(b2Badges[0].getAttribute("title")).toMatch(/=true/);
  });

  it("shows the top-|r| feature with sign preserved", async () => {
    vi.mocked(topCreatorPriors.latest).mockResolvedValue(_priorsStub());
    vi.mocked(topCreatorPriors.uploads).mockResolvedValue(null);
    renderWithClient(<TopCreatorPriorsCard />);
    // 0.42 is the largest |r|; each row shows '+0.42'. Feature name
    // is displayed truncated to 22 chars (see
    // ``topCorr.feature.slice(0, 22)``), so 'title_ends_with_question'
    // renders as 'title_ends_with_questi'.
    const matches = await screen.findAllByText(/\+0\.42/);
    expect(matches.length).toBeGreaterThan(0);
    expect(
      screen.getAllByText(/title_ends_with_questi/).length,
    ).toBeGreaterThan(0);
  });

  it("counts uploads fresh within 24h as A.3 would see them", async () => {
    vi.mocked(topCreatorPriors.latest).mockResolvedValue(null);
    vi.mocked(topCreatorPriors.uploads).mockResolvedValue(_uploadsStub());
    renderWithClient(<TopCreatorPriorsCard />);
    // The stub has 3 creators at 2h / 22h / 72h. A.3 would zero the
    // 72h upload (>24h), so fresh=2/3 total. Wait for the actual
    // counter to hydrate — findByText fires as soon as ONE row's
    // fresh= renders, but the count itself depends on the second
    // query resolving.
    await waitFor(() => {
      const spans = document.querySelectorAll("span.font-mono");
      const found = Array.from(spans).some(
        (el) => el.textContent === "2/3",
      );
      expect(found).toBe(true);
    });
  });

  it("has independent A.2 and B.2 badges (not merged)", async () => {
    vi.mocked(topCreatorPriors.latest).mockResolvedValue(
      _priorsStub({ flag_enabled: true }),
    );
    vi.mocked(topCreatorPriors.uploads).mockResolvedValue(
      _uploadsStub({ flag_enabled: false }),
    );
    renderWithClient(<TopCreatorPriorsCard />);
    // Both badges present per row; B.2 tooltip = active, A.2 = off
    const b2 = await screen.findAllByText("B.2");
    const a2 = screen.getAllByText("A.2");
    expect(b2.length).toBeGreaterThan(0);
    expect(a2.length).toBeGreaterThan(0);
    expect(b2[0].getAttribute("title")).toMatch(/=true/);
    expect(a2[0].getAttribute("title")).toMatch(/off/);
  });
});
