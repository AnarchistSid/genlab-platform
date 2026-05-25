import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";

const mockScheduleMutate = vi.fn();

// R-73: FocusOverlay pulls these hooks directly. Mock them so it renders
// without a QueryClient; the schedule mutation is never pending in these tests.
vi.mock("@/hooks/use-blueprints", () => ({
  useUpdateContent: () => ({ mutate: vi.fn(), isPending: false }),
  useApproveAndSchedule: () => ({ mutate: mockScheduleMutate, isPending: false }),
}));

import { FocusOverlay } from "../FocusOverlay";

const bp = {
  id: "bp1",
  status: "VISUAL_READY",
  niche_id: "gaming",
  action_taken: "",
  hook_text: "A real specific hook",
} as never;

function renderOverlay(isReviewing: boolean, onApprove = vi.fn()) {
  render(
    <FocusOverlay
      blueprints={[bp]}
      currentIndex={0}
      isOpen
      onNavigate={vi.fn()}
      onApprove={onApprove}
      onReject={vi.fn()}
      onClose={vi.fn()}
      isReviewing={isReviewing}
    />,
  );
  return onApprove;
}

describe("FocusOverlay keyboard double-submit guard (R-73)", () => {
  beforeEach(() => mockScheduleMutate.mockClear());

  it("the 'a' shortcut does NOT fire onApprove while a review is in flight", () => {
    const onApprove = renderOverlay(true);
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "a" }));
    expect(onApprove).not.toHaveBeenCalled();
  });

  it("the 'a' shortcut fires onApprove once when not reviewing", () => {
    const onApprove = renderOverlay(false);
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "a" }));
    expect(onApprove).toHaveBeenCalledTimes(1);
    expect(onApprove).toHaveBeenCalledWith("bp1");
  });

  it("the 'r' shortcut is likewise guarded while reviewing", () => {
    const onReject = vi.fn();
    render(
      <FocusOverlay
        blueprints={[bp]}
        currentIndex={0}
        isOpen
        onNavigate={vi.fn()}
        onApprove={vi.fn()}
        onReject={onReject}
        onClose={vi.fn()}
        isReviewing
      />,
    );
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "r" }));
    expect(onReject).not.toHaveBeenCalled();
  });
});
