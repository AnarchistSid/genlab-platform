import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const mockScheduleMutate = vi.fn();
let scheduleIsPending = false;

// R-73: ContentCard pulls useApproveAndSchedule() directly. Mock it so the card
// renders without a QueryClient and so we can drive its isPending state.
vi.mock("@/hooks/use-blueprints", () => ({
  useApproveAndSchedule: () => ({ mutate: mockScheduleMutate, isPending: scheduleIsPending }),
}));

import { ContentCard } from "../ContentCard";

// A VISUAL_READY, not-yet-scheduled blueprint → the approve/reject/schedule
// action row renders (isVisualReady && !isScheduled).
const baseBp = {
  id: "bp1",
  status: "VISUAL_READY",
  niche_id: "gaming",
  action_taken: "",
  hook_text: "A real specific hook",
} as never;

describe("ContentCard double-submit guard (R-73)", () => {
  beforeEach(() => {
    mockScheduleMutate.mockClear();
    scheduleIsPending = false;
  });

  it("disables approve/reject while a review is in flight", () => {
    render(
      <ContentCard blueprint={baseBp} onApprove={vi.fn()} onReject={vi.fn()} isReviewing />,
    );
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
  });

  it("approve is enabled when not reviewing and fires onApprove exactly once", async () => {
    const onApprove = vi.fn();
    render(
      <ContentCard
        blueprint={baseBp}
        onApprove={onApprove}
        onReject={vi.fn()}
        isReviewing={false}
      />,
    );
    const approve = screen.getByRole("button", { name: "Approve" });
    expect(approve).toBeEnabled();
    await userEvent.click(approve);
    expect(onApprove).toHaveBeenCalledTimes(1);
    expect(onApprove).toHaveBeenCalledWith("bp1");
  });

  it("a disabled approve button cannot fire onApprove on click", async () => {
    const onApprove = vi.fn();
    render(<ContentCard blueprint={baseBp} onApprove={onApprove} onReject={vi.fn()} isReviewing />);
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(onApprove).not.toHaveBeenCalled();
  });

  it("the Schedule button disables itself while its own mutation is pending", () => {
    scheduleIsPending = true;
    render(<ContentCard blueprint={baseBp} onApprove={vi.fn()} onReject={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Schedule" })).toBeDisabled();
  });
});
