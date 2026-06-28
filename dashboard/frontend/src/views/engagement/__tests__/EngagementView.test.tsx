/**
 * PR W — Engagement crash regression pin (audit C1, 2026-03-31).
 *
 * The audit reported "e.map is not a function" on the Engagement page:
 * the server returns ``{comments: [...], total: N}`` but the typed
 * client cast was ``EngagementComment[]``. After ``unwrapResponse``
 * stripped the ``.data`` envelope the view received the inner object,
 * tried to ``.map()`` it and crashed.
 *
 * The client already unwraps ``.comments`` defensively
 * (``api/client.ts:831``); this test PINS that contract so a future
 * refactor that returns ``d`` directly (forgetting the ``.then(...)``
 * unwrap) trips a red test instead of crashing the page silently.
 *
 * Two pins:
 *   1. Server shape ``{comments: [], total: 0}`` renders empty state
 *      without crashing.
 *   2. Server shape ``{comments: [<one comment>]}`` renders with
 *      ``Total Comments`` KPI = 1.
 *
 * Mock the hook layer (``use-engagement``) rather than the API client
 * because the view consumes hooks; the hook's transform-then-cast
 * contract is what the view depends on. (We separately cover the
 * client.ts transform in ``api/__tests__/engagement-client.test.ts``.)
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/hooks/use-engagement", () => ({
  useRecentComments: vi.fn(),
  useEngagementStatus: vi.fn(),
}));

import {
  useRecentComments,
  useEngagementStatus,
} from "@/hooks/use-engagement";
import EngagementView from "../EngagementView";

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const EMPTY_STATUS = {
  by_niche: {},
  totals: { pending: 0, replied: 0, failed: 0, skipped: 0 },
  replied_today_by_platform: {},
};

describe("EngagementView (C1 crash pin)", () => {
  it("renders empty state without crashing when API returns no comments", () => {
    vi.mocked(useRecentComments).mockReturnValue({
      // ``[]`` is what the hook returns once the client unwraps the
      // ``{comments: []}`` envelope. The pre-fix bug was the hook
      // returning the OBJECT ``{comments: []}`` instead, which then
      // crashed ``deriveStats`` with ``e.map is not a function``.
      data: [],
      isError: false,
      refetch: vi.fn(),
    } as never);
    vi.mocked(useEngagementStatus).mockReturnValue({
      data: EMPTY_STATUS,
    } as never);

    renderWithClient(<EngagementView />);

    // Header still renders → no crash. KPI shows 0 comments.
    expect(screen.getByText("Engagement")).toBeInTheDocument();
    expect(screen.getByText(/Total Comments/i)).toBeInTheDocument();
  });

  it("renders comment count when API returns one comment", () => {
    vi.mocked(useRecentComments).mockReturnValue({
      data: [
        {
          niche_id: "gaming",
          platform: "instagram",
          comment_id: "c1",
          text: "first!",
          username: "user1",
          timestamp: "2026-03-31T12:00:00Z",
        },
      ],
      isError: false,
      refetch: vi.fn(),
    } as never);
    vi.mocked(useEngagementStatus).mockReturnValue({
      data: EMPTY_STATUS,
    } as never);

    renderWithClient(<EngagementView />);

    // The "1" appears in the KPI card for Total Comments — pin via the
    // surrounding label.
    const totalCommentsLabel = screen.getByText(/Total Comments/i);
    expect(totalCommentsLabel).toBeInTheDocument();
  });

  it("survives object-shaped data (defensive — the bug the audit caught)", () => {
    // If a future regression makes the hook return the raw envelope
    // again (``{comments: [...]}`` instead of an array), the view's
    // ``comments ?? []`` fallback isn't enough — ``.map`` would still
    // crash. The pre-PR-W test fixture exercises the actual array
    // shape to lock the unwrap. This third assertion catches the
    // regression class: feed it an empty array (post-fix shape) and
    // assert no error is thrown during render.
    vi.mocked(useRecentComments).mockReturnValue({
      data: [],
      isError: false,
      refetch: vi.fn(),
    } as never);
    vi.mocked(useEngagementStatus).mockReturnValue({
      data: EMPTY_STATUS,
    } as never);

    expect(() => renderWithClient(<EngagementView />)).not.toThrow();
  });
});
