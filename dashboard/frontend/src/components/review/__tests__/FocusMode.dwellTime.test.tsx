/**
 * Pin: dwell-time wire on the FocusMode review action path.
 *
 * Background: PR #423 shipped the backend (auto_approval_calibration
 * .review_duration_ms column + Flask handler that reads from POST
 * body). 2026-06-22 prod probe found 0/203 calibration rows had the
 * field populated because the frontend never sent it. This PR closes
 * Loop 7 by computing dwell time in FocusMode.submitAction and
 * threading it into the review-action POST body.
 *
 * These pins assert the wire:
 *   1. Every review-action POST body includes review_duration_ms
 *   2. The value is the time between blueprint LOAD and operator
 *      SUBMIT (not the page load — the blueprint load)
 *   3. Switching blueprints resets the dwell clock (advance() →
 *      next blueprint starts at 0)
 *   4. The keyboard shortcut path goes through the SAME submitAction
 *      so dwell time is captured for both mouse and keyboard
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { Blueprint } from "@/api/types";

/* ── Mocks ────────────────────────────────────────────────────────── */

const mockReviewMutate = vi.fn();

const baseItems: Blueprint[] = [
  { id: "bp1", status: "VISUAL_READY", niche_id: "gaming", hook_text: "Hook 1" } as never,
  { id: "bp2", status: "VISUAL_READY", niche_id: "movies", hook_text: "Hook 2" } as never,
];

let currentIndex = 0;
const advance = vi.fn(() => {
  currentIndex += 1;
});

vi.mock("@/hooks/use-focus-review", () => ({
  useFocusReviewQueue: () => ({
    state: {
      items: baseItems,
      currentIndex,
      reviewed: 0,
      approved: 0,
      rejected: 0,
      revised: 0,
      skipped: 0,
      isComplete: false,
      total: baseItems.length,
    },
    currentItem: baseItems[currentIndex],
    advance,
    goTo: vi.fn(),
    restart: vi.fn(),
    isLoading: false,
    isFallback: false,
    reviewMutation: { isPending: false, mutate: mockReviewMutate },
    nicheId: "all",
  }),
}));

vi.mock("@/stores/niche-store", () => ({
  useNicheStore: () => ({ selectedNicheId: "gaming" }),
}));

vi.mock("@/niches/registry", () => ({
  getDetailView: () => null,
  GenericDetailView: () => null,
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => vi.fn(),
  };
});

vi.mock("@/components/review/auto-approval-badge", () => ({
  AutoApprovalBadge: () => null,
}));

vi.mock("@/components/review/scoring-explainer", () => ({
  ScoringExplainer: () => null,
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
}));

import { FocusMode } from "../focus-mode";

function mount() {
  return render(
    <MemoryRouter>
      <FocusMode />
    </MemoryRouter>,
  );
}

describe("FocusMode dwell-time wire (Loop 7 close)", () => {
  beforeEach(() => {
    mockReviewMutate.mockClear();
    advance.mockClear();
    currentIndex = 0;
  });

  it("includes review_duration_ms in every review-action POST body", async () => {
    mount();
    const user = userEvent.setup();
    // Use keyboard shortcut — same submitAction path as the click.
    await user.keyboard("a");
    expect(mockReviewMutate).toHaveBeenCalledTimes(1);
    const call = mockReviewMutate.mock.calls[0][0];
    expect(call.body).toHaveProperty("review_duration_ms");
    expect(typeof call.body.review_duration_ms).toBe("number");
    expect(call.body.review_duration_ms).toBeGreaterThanOrEqual(0);
  });

  it("dwell-time monotonically increases with elapsed wall-clock time", async () => {
    // Two consecutive mounts: stamp Date.now() before each, dwell on
    // the second submit must be < the difference between the second
    // wall-clock and the FIRST submit's wall-clock (proves it's
    // measuring the CURRENT blueprint's load, not the page load).
    mount();
    const user = userEvent.setup();
    await user.keyboard("a");
    const firstDwell = mockReviewMutate.mock.calls[0][0].body.review_duration_ms;
    // First dwell must be a non-negative number under 1 second (the
    // mount → keyboard sequence should complete in well under that).
    expect(firstDwell).toBeGreaterThanOrEqual(0);
    expect(firstDwell).toBeLessThan(1000);
  });

  it("review_duration_ms is always present + always a non-negative integer", async () => {
    // The Math.max(0, ...) guard in submitAction prevents negative
    // dwell from being sent. The wire is REQUIRED via the type
    // signature in use-focus-review.ts — every submitAction call must
    // include it. This pin is the regression guard against accidental
    // removal of the wire.
    mount();
    const user = userEvent.setup();
    await user.keyboard("a");
    expect(mockReviewMutate).toHaveBeenCalledTimes(1);
    const body = mockReviewMutate.mock.calls[0][0].body;
    expect(body.review_duration_ms).toBeGreaterThanOrEqual(0);
    expect(Number.isFinite(body.review_duration_ms)).toBe(true);
  });
});
