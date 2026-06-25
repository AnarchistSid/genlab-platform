import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

/**
 * PR #554 (2026-06-25): SR-F wire pass 14 consumer.
 *
 * The banner reads `data.global._sr_f_scoped` + `data.global._sr_f_note`
 * from the cross-niche overview endpoint (producer shipped in PR #553).
 * Mock the hook to control the shape per-test.
 *
 * The component must:
 *
 *   * Hide when data is undefined (loading) or _sr_f_scoped !== true
 *     (admin / unrestricted operator) — back-compat
 *   * Render the literal _sr_f_note verbatim when scoped (so the
 *     backend can evolve the message without a coordinated frontend
 *     deploy)
 *   * Use a NON-DISMISSABLE pattern (mirrors CriticalAlertsBanner —
 *     UI never lies about server-side authorization state)
 */

let mockReturn: unknown = undefined;
import { vi } from "vitest";
vi.mock("@/hooks/use-cross-niche-overview", () => ({
  useCrossNicheOverview: () => ({ data: mockReturn }),
}));

import { ScopedAccessBanner } from "../ScopedAccessBanner";

describe("ScopedAccessBanner", () => {
  it("renders nothing when data is undefined (loading state)", () => {
    mockReturn = undefined;
    const { container } = render(<ScopedAccessBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for unrestricted admin (no _sr_f_scoped flag)", () => {
    mockReturn = {
      global: {
        total_pending_review: 30,
        total_published_today: 9,
        // no _sr_f_scoped key — admin session, full overview
      },
      niches: [],
      schedule_today: [],
    };
    const { container } = render(<ScopedAccessBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when _sr_f_scoped is explicitly false", () => {
    mockReturn = {
      global: { _sr_f_scoped: false, _sr_f_note: "shouldnt show" },
    };
    const { container } = render(<ScopedAccessBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the server note verbatim when scoped", () => {
    const note =
      "Scoped to your niche allowlist. total_reach is 14-day " +
      "(vs. global 30-day); total_likes/total_comments unavailable " +
      "in scoped mode.";
    mockReturn = {
      global: { _sr_f_scoped: true, _sr_f_note: note },
    };
    render(<ScopedAccessBanner />);
    // The literal note must be present so the operator understands
    // the data fidelity AND the backend can evolve copy without a
    // coordinated frontend deploy.
    expect(screen.getByText(note)).toBeInTheDocument();
    expect(screen.getByText("Scoped access")).toBeInTheDocument();
  });

  it("falls back to default copy when _sr_f_note is missing", () => {
    mockReturn = {
      global: { _sr_f_scoped: true },
    };
    render(<ScopedAccessBanner />);
    // Defensive fallback when the server flag is set but note is
    // missing — never render an empty banner.
    expect(
      screen.getByText("Scoped to your niche allowlist."),
    ).toBeInTheDocument();
  });

  it("uses role='status' (non-dismissable, informational)", () => {
    mockReturn = {
      global: { _sr_f_scoped: true, _sr_f_note: "n" },
    };
    render(<ScopedAccessBanner />);
    // Pin the a11y role — status (not alert, not button) matches
    // the informational + non-dismissable design choice.
    const banner = screen.getByRole("status");
    expect(banner).toBeInTheDocument();
    // No dismiss button — banner reflects server state, not UI state
    expect(banner.querySelector("button")).toBeNull();
  });

  it("survives unexpected shapes without crashing (defensive)", () => {
    // Various malformed payloads that could come from a degraded
    // server response or version skew. Component must not crash.
    const cases: unknown[] = [
      null,
      "string-instead-of-object",
      { global: null },
      { global: "string" },
      { global: { _sr_f_scoped: "yes-string-not-bool" } }, // strict === true check
    ];
    for (const payload of cases) {
      mockReturn = payload;
      const { container, unmount } = render(<ScopedAccessBanner />);
      expect(container).toBeEmptyDOMElement();
      unmount();
    }
  });
});
