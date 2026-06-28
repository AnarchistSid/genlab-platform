/**
 * PR W — UpcomingQueue past-date filter pin (audit H5, 2026-03-31).
 *
 * The audit flagged that UpcomingQueue showed past-dated rows (Mar 27-29
 * leaking in even though they were in the past). The fix lives at
 * UpcomingQueue.tsx:37 — ``new Date(item.scheduled_for).getTime() > now``.
 *
 * Without this filter, blueprints whose scheduled_for has passed but
 * whose queue_status is still APPROVED/HELD (e.g. publisher failed,
 * publisher hasn't caught up yet) leak into the "upcoming" surface and
 * confuse operators about what's actually coming next.
 *
 * Pin: a fixture mixing past + future scheduled_for values must
 * surface only the future rows.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/hooks/use-publishing-queue", () => ({
  usePublishingQueue: vi.fn(),
}));

import { usePublishingQueue } from "@/hooks/use-publishing-queue";
import { UpcomingQueue } from "../UpcomingQueue";

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("UpcomingQueue (H5 past-date filter pin)", () => {
  it("filters out items whose scheduled_for is in the past", () => {
    const past = new Date(Date.now() - 24 * 3600_000).toISOString();
    const future1 = new Date(Date.now() + 1 * 3600_000).toISOString();
    const future2 = new Date(Date.now() + 6 * 3600_000).toISOString();

    vi.mocked(usePublishingQueue).mockReturnValue({
      data: {
        data: [
          {
            id: "p1",
            scheduled_for: past,
            queue_status: "APPROVED",
            hook_text: "PAST — should not render",
            niche_id: "gaming",
          },
          {
            id: "f1",
            scheduled_for: future1,
            queue_status: "APPROVED",
            hook_text: "FUTURE in 1h",
            niche_id: "sports",
          },
          {
            id: "f2",
            scheduled_for: future2,
            queue_status: "PENDING_APPROVAL",
            hook_text: "FUTURE in 6h",
            niche_id: "movies",
          },
        ],
      },
    } as never);

    renderWithClient(<UpcomingQueue />);

    // Both future rows should appear
    expect(screen.getByText(/FUTURE in 1h/)).toBeInTheDocument();
    expect(screen.getByText(/FUTURE in 6h/)).toBeInTheDocument();
    // Past row MUST NOT appear — the audit's exact symptom
    expect(screen.queryByText(/PAST — should not render/)).not.toBeInTheDocument();
  });

  it("filters out PUBLISHED items even if scheduled_for is in the future", () => {
    // A published item is by definition no longer "upcoming" — pin
    // that the queue_status filter at line 36 also still works post-
    // PR-W.
    const future = new Date(Date.now() + 1 * 3600_000).toISOString();

    vi.mocked(usePublishingQueue).mockReturnValue({
      data: {
        data: [
          {
            id: "pub",
            scheduled_for: future,
            queue_status: "PUBLISHED",
            hook_text: "ALREADY PUBLISHED",
            niche_id: "anime",
          },
        ],
      },
    } as never);

    renderWithClient(<UpcomingQueue />);

    expect(screen.queryByText(/ALREADY PUBLISHED/)).not.toBeInTheDocument();
    expect(screen.getByText(/No upcoming posts scheduled/i)).toBeInTheDocument();
  });

  it("shows empty state when all items are past-dated", () => {
    const past1 = new Date(Date.now() - 24 * 3600_000).toISOString();
    const past2 = new Date(Date.now() - 1 * 3600_000).toISOString();

    vi.mocked(usePublishingQueue).mockReturnValue({
      data: {
        data: [
          {
            id: "p1",
            scheduled_for: past1,
            queue_status: "APPROVED",
            hook_text: "past A",
            niche_id: "gaming",
          },
          {
            id: "p2",
            scheduled_for: past2,
            queue_status: "PENDING_APPROVAL",
            hook_text: "past B",
            niche_id: "sports",
          },
        ],
      },
    } as never);

    renderWithClient(<UpcomingQueue />);

    expect(screen.getByText(/No upcoming posts scheduled/i)).toBeInTheDocument();
  });
});
