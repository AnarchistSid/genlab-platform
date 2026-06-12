import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Mock the data hook so the component can render without a QueryClient
// or any network. Each test overrides the return value.
let mockReturn: unknown = undefined;
vi.mock("@/hooks/use-critical-alerts", () => ({
  useCriticalAlerts: () => ({ data: mockReturn }),
}));

import { CriticalAlertsBanner } from "../CriticalAlertsBanner";

function makeRow(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "row-1",
    niche_id: "ai_creators",
    check_name: "warp_down",
    message:
      "warp-svc not active (ActiveState=inactive). All yt-dlp downloads will fail. Run: systemctl restart warp-svc",
    details: { active_state: "inactive" },
    auto_fix_applied: "not attempted (would need warp-cli reconfig)",
    created_at: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(), // 3h ago
    ...overrides,
  };
}

describe("CriticalAlertsBanner", () => {
  it("renders nothing when there is no data yet", () => {
    mockReturn = undefined;
    const { container } = render(<CriticalAlertsBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when count is zero", () => {
    mockReturn = { unresolved_critical: [], count: 0 };
    const { container } = render(<CriticalAlertsBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the alert message verbatim so the remediation command survives", () => {
    mockReturn = {
      unresolved_critical: [makeRow()],
      count: 1,
    };
    render(<CriticalAlertsBanner />);
    // The literal command must be present — that's the whole reason the
    // banner exists. Anything that interprets / summarises risks
    // hiding the literal text the operator needs to copy-paste.
    expect(
      screen.getByText(/systemctl restart warp-svc/),
    ).toBeInTheDocument();
  });

  it("shows check_name + niche_id + age in the row header", () => {
    mockReturn = {
      unresolved_critical: [makeRow()],
      count: 1,
    };
    render(<CriticalAlertsBanner />);
    expect(screen.getByText("warp_down")).toBeInTheDocument();
    expect(screen.getByText("[ai_creators]")).toBeInTheDocument();
    // 3h ago — exact label rendered by formatAge
    expect(screen.getByText(/3h ago/)).toBeInTheDocument();
  });

  it("dedupes by (niche_id, check_name) and shows ×N occurrences", () => {
    mockReturn = {
      unresolved_critical: [
        makeRow({ id: "1", created_at: new Date().toISOString() }),
        makeRow({ id: "2", created_at: new Date().toISOString() }),
        makeRow({ id: "3", created_at: new Date().toISOString() }),
      ],
      count: 3,
    };
    render(<CriticalAlertsBanner />);
    // 3 rows, same (niche, check_name) → 1 dedup'd group with ×3 badge
    expect(screen.getByText(/×3 occurrences/)).toBeInTheDocument();
    // Header count shows the group count (1), not the raw row count.
    expect(
      screen.getByText(/1 unresolved CRITICAL system alert/),
    ).toBeInTheDocument();
  });

  it("renders separate groups for different (niche, check_name) pairs", () => {
    mockReturn = {
      unresolved_critical: [
        makeRow({
          id: "1",
          niche_id: "ai_creators",
          check_name: "warp_down",
        }),
        makeRow({
          id: "2",
          niche_id: "anime",
          check_name: "download_failure",
          message: "3 consecutive runs with 0 clip downloads",
          auto_fix_applied: "yt-dlp update failed (rc=2)",
        }),
      ],
      count: 2,
    };
    render(<CriticalAlertsBanner />);
    expect(screen.getByText("warp_down")).toBeInTheDocument();
    expect(screen.getByText("download_failure")).toBeInTheDocument();
    expect(
      screen.getByText(/2 unresolved CRITICAL system alerts/),
    ).toBeInTheDocument();
  });

  it("renders auto_fix outcome on its own sub-row when present", () => {
    mockReturn = {
      unresolved_critical: [
        makeRow({
          auto_fix_applied:
            "skipped yt-dlp update — WARP proxy is down (see warp_down alert)",
        }),
      ],
      count: 1,
    };
    render(<CriticalAlertsBanner />);
    expect(screen.getByText(/auto-fix:/)).toBeInTheDocument();
    expect(
      screen.getByText(/skipped yt-dlp update/),
    ).toBeInTheDocument();
  });

  it("hides the auto_fix sub-row when no auto-fix was attempted", () => {
    mockReturn = {
      unresolved_critical: [makeRow({ auto_fix_applied: null })],
      count: 1,
    };
    render(<CriticalAlertsBanner />);
    expect(screen.queryByText(/auto-fix:/)).not.toBeInTheDocument();
  });

  it("collapses + expands on header click while preserving content", async () => {
    mockReturn = {
      unresolved_critical: [makeRow()],
      count: 1,
    };
    const user = userEvent.setup();
    render(<CriticalAlertsBanner />);

    const header = screen.getByRole("button");
    // Default expanded — message visible.
    expect(
      screen.getByText(/systemctl restart warp-svc/),
    ).toBeInTheDocument();

    await user.click(header);
    expect(
      screen.queryByText(/systemctl restart warp-svc/),
    ).not.toBeInTheDocument();

    await user.click(header);
    expect(
      screen.getByText(/systemctl restart warp-svc/),
    ).toBeInTheDocument();
  });

  it("survives a malformed payload (defensive — keeps the dashboard up)", () => {
    // No `unresolved_critical` key at all: the banner must hide
    // gracefully rather than crash on `.map(undefined)`.
    mockReturn = { count: 5 };
    const { container } = render(<CriticalAlertsBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it("uses role='alert' so screen readers announce new critical alerts", () => {
    mockReturn = {
      unresolved_critical: [makeRow()],
      count: 1,
    };
    render(<CriticalAlertsBanner />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});
