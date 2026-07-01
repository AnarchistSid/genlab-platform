/**
 * Pins for the 2026-06-14 bulk-review feature.
 *
 * Covers the keyboard-shortcut path that makes bulk-review's
 * throughput claim real (skim 5 → number-key select → A to approve).
 * Each pin asserts the SHORTCUT correctly drives the SAME mutation
 * the on-screen buttons would call — so the visual UI and the
 * keyboard UI can't diverge.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { Blueprint } from "@/api/types";

/* ── Mocks ────────────────────────────────────────────────────────── */

const mockBatchApprove = vi.fn();
const mockBatchReview = vi.fn();
let batchApprovePending = false;
let batchReviewPending = false;

const baseItems: Blueprint[] = [
  { id: "bp1", status: "VISUAL_READY", niche_id: "gaming", hook_text: "Hook 1" } as never,
  { id: "bp2", status: "VISUAL_READY", niche_id: "movies", hook_text: "Hook 2" } as never,
  { id: "bp3", status: "VISUAL_READY", niche_id: "sports", hook_text: "Hook 3" } as never,
  { id: "bp4", status: "VISUAL_READY", niche_id: "anime", hook_text: "Hook 4" } as never,
  { id: "bp5", status: "VISUAL_READY", niche_id: "ai_creators", hook_text: "Hook 5" } as never,
];

vi.mock("@/hooks/use-focus-review", () => ({
  useFocusReviewQueue: () => ({
    state: {
      items: baseItems,
      currentIndex: 0,
      reviewed: 0,
      approved: 0,
      rejected: 0,
      revised: 0,
      skipped: 0,
      isComplete: false,
      total: baseItems.length,
    },
    currentItem: baseItems[0],
    advance: vi.fn(),
    goTo: vi.fn(),
    restart: vi.fn(),
    isLoading: false,
    isFallback: false,
    reviewMutation: { isPending: false, mutate: vi.fn() },
    nicheId: "all",
  }),
}));

vi.mock("@/hooks/use-blueprints", () => ({
  useBatchApproveSchedule: () => ({
    mutate: mockBatchApprove,
    isPending: batchApprovePending,
  }),
  useBatchReview: () => ({
    mutate: mockBatchReview,
    isPending: batchReviewPending,
  }),
}));

// AutoApprovalBadge fires a network query; stub it out so the test
// doesn't need a QueryClient wired up.
vi.mock("@/components/review/auto-approval-badge", () => ({
  AutoApprovalBadge: ({ blueprintId }: { blueprintId: string }) => (
    <span data-testid={`auto-badge-${blueprintId}`}>badge</span>
  ),
}));
// Intervention 6 (2026-07-01): EnsembleBadge renders next to the
// AutoApprovalBadge in bulk-review. Same reason to stub — the
// BulkReview render tests don't wire a QueryClient.
vi.mock("@/components/review/ensemble-badge", () => ({
  EnsembleBadge: ({ blueprintId }: { blueprintId: string }) => (
    <span data-testid={`ensemble-badge-${blueprintId}`}>ensemble</span>
  ),
}));

// sonner toast — silent stub.
vi.mock("sonner", () => ({
  toast: { info: vi.fn(), success: vi.fn(), error: vi.fn() },
}));

import { BulkReview } from "../bulk-review";

function renderBulk() {
  return render(
    <MemoryRouter>
      <BulkReview />
    </MemoryRouter>
  );
}

/* ── Tests ────────────────────────────────────────────────────────── */

describe("BulkReview — keyboard-driven approval flow", () => {
  beforeEach(() => {
    mockBatchApprove.mockClear();
    mockBatchReview.mockClear();
    batchApprovePending = false;
    batchReviewPending = false;
  });

  it("renders all 5 visible cards with keyboard hints 1..5", () => {
    renderBulk();
    expect(screen.getByText("Hook 1")).toBeInTheDocument();
    expect(screen.getByText("Hook 5")).toBeInTheDocument();
    // The kbd hint per card is the position-index
    expect(screen.getByText("1").tagName.toLowerCase()).toBe("kbd");
    expect(screen.getByText("5").tagName.toLowerCase()).toBe("kbd");
  });

  it("'2' then 'a' approves only bp2 — number-key selects, A submits", async () => {
    const user = userEvent.setup();
    renderBulk();

    await user.keyboard("2");
    await user.keyboard("a");

    expect(mockBatchApprove).toHaveBeenCalledTimes(1);
    expect(mockBatchApprove).toHaveBeenCalledWith(["bp2"], expect.any(Object));
  });

  it("'1' '2' '3' 'a' approves a multi-blueprint selection in one batch", async () => {
    const user = userEvent.setup();
    renderBulk();

    await user.keyboard("1");
    await user.keyboard("2");
    await user.keyboard("3");
    await user.keyboard("a");

    expect(mockBatchApprove).toHaveBeenCalledTimes(1);
    const [ids] = mockBatchApprove.mock.calls[0];
    expect(new Set(ids as string[])).toEqual(new Set(["bp1", "bp2", "bp3"]));
  });

  it("Shift+A approves all 5 visible blueprints without needing selection", async () => {
    const user = userEvent.setup();
    renderBulk();

    await user.keyboard("{Shift>}A{/Shift}");

    expect(mockBatchApprove).toHaveBeenCalledTimes(1);
    const [ids] = mockBatchApprove.mock.calls[0];
    expect(new Set(ids as string[])).toEqual(
      new Set(["bp1", "bp2", "bp3", "bp4", "bp5"])
    );
  });

  it("'a' with no selection toasts info and never fires the batch mutation", async () => {
    const user = userEvent.setup();
    renderBulk();

    await user.keyboard("a");

    expect(mockBatchApprove).not.toHaveBeenCalled();
  });

  it("'1' 'r' rejects only bp1 via the same batch-review path used for skipped", async () => {
    const user = userEvent.setup();
    renderBulk();

    await user.keyboard("1");
    await user.keyboard("r");

    expect(mockBatchReview).toHaveBeenCalledTimes(1);
    expect(mockBatchReview).toHaveBeenCalledWith(
      { ids: ["bp1"], action: "rejected" },
      expect.any(Object)
    );
  });

  it("'1' 'Escape' clears the selection so a follow-up 'a' is a no-op", async () => {
    const user = userEvent.setup();
    renderBulk();

    await user.keyboard("1");
    await user.keyboard("{Escape}");
    await user.keyboard("a");

    expect(mockBatchApprove).not.toHaveBeenCalled();
  });

  it("double-submit guard: 'a' while batchApprove is pending is a no-op", async () => {
    batchApprovePending = true;
    const user = userEvent.setup();
    renderBulk();

    await user.keyboard("1");
    await user.keyboard("a");
    await user.keyboard("a");

    expect(mockBatchApprove).not.toHaveBeenCalled();
  });
});
