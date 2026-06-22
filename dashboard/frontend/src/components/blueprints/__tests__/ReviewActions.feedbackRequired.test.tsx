/**
 * Pin: feedback_category dropdown is REQUIRED for Reject/Revise.
 *
 * Background: PR #424 (2026-06-21) shipped the backend column +
 * reader for `auto_approval_calibration.feedback_category`. The
 * frontend dropdown existed but was OPTIONAL — operators could
 * click Reject → Confirm without picking a category. Prod probe
 * on 2026-06-22 found 0 rows had `feedback_category` populated.
 *
 * This fix disables the Confirm button until a category is
 * selected (in both ReviewActions and FocusMode FeedbackForm).
 * The pins below assert the disabled-state contract — the wire
 * to the backend was already correct; the only fix needed was
 * making the operator pick something.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const mockReviewMutate = vi.fn();
let reviewPending = false;

vi.mock("@/hooks/use-blueprints", () => ({
  useReviewBlueprint: () => ({ mutate: mockReviewMutate, isPending: reviewPending }),
}));

import { ReviewActions } from "../review-actions";

describe("ReviewActions feedback_category required (Loop 8 close)", () => {
  beforeEach(() => {
    mockReviewMutate.mockClear();
    reviewPending = false;
  });

  it("Confirm button is disabled when no issue is selected (Reject)", async () => {
    render(<ReviewActions blueprintId="bp1" />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /reject/i }));
    // After clicking Reject, the feedback form appears with a Confirm
    // button that should be DISABLED until issue is picked.
    const confirm = screen.getByRole("button", { name: /confirm reject/i });
    expect(confirm).toBeDisabled();
  });

  it("Confirm button is disabled when no issue is selected (Revise)", async () => {
    render(<ReviewActions blueprintId="bp1" />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /revise/i }));
    const confirm = screen.getByRole("button", { name: /confirm revise/i });
    expect(confirm).toBeDisabled();
  });

  it("Approve does NOT show feedback form (no category required)", async () => {
    render(<ReviewActions blueprintId="bp1" />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /approve/i }));
    // Approve fires directly — no Confirm button to find, and the
    // mutation is called immediately.
    expect(mockReviewMutate).toHaveBeenCalledTimes(1);
    const body = mockReviewMutate.mock.calls[0][0].body;
    expect(body.action).toBe("approved");
    expect(body.issue).toBeUndefined(); // Approve never sends issue
  });

  it("Skip does NOT show feedback form (no category required)", async () => {
    render(<ReviewActions blueprintId="bp1" />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /skip/i }));
    expect(mockReviewMutate).toHaveBeenCalledTimes(1);
    const body = mockReviewMutate.mock.calls[0][0].body;
    expect(body.action).toBe("skipped");
    expect(body.issue).toBeUndefined();
  });

  it("required-marker aria-required is set on the Select trigger", async () => {
    render(<ReviewActions blueprintId="bp1" />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /reject/i }));
    // Find the combobox role (Select component renders as combobox).
    const select = screen.getByRole("combobox");
    expect(select).toHaveAttribute("aria-required", "true");
  });
});
