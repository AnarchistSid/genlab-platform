/**
 * Behavior + accessibility pins for StatusBadge.
 *
 * Coverage organized by what could regress when someone touches the
 * component without reading the source:
 *
 *   1. Variant mapping (status × size → expected classes / data attrs)
 *   2. Accessibility contract (role / aria-live / aria-label / aria-busy)
 *   3. Loading state (skeleton swap, dimensions preserved, aria-busy on)
 *   4. Edge cases (empty children, very long content, falsy/null children)
 *   5. Composition (asChild, custom className merge, ref forwarding via Slot)
 *   6. `statusFromString` mapping (known + unknown + null)
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import {
  StatusBadge,
  StatusDot,
  statusFromString,
  type StatusVariant,
} from "../status-badge";

describe("StatusBadge — variants", () => {
  it.each<[StatusVariant, string]>([
    ["success", "text-success"],
    ["warning", "text-warning"],
    ["error", "text-error"],
    ["info", "text-info"],
    ["neutral", "text-text-muted"],
  ])("status=%s applies the matching token class", (status, expectedClass) => {
    render(<StatusBadge status={status}>label</StatusBadge>);
    const badge = screen.getByText("label").closest("[data-slot='status-badge']");
    expect(badge).toHaveClass(expectedClass);
    expect(badge).toHaveAttribute("data-status", status);
  });

  it("defaults to neutral when status is omitted", () => {
    render(<StatusBadge>plain</StatusBadge>);
    const badge = screen.getByText("plain").closest("[data-slot='status-badge']");
    expect(badge).toHaveAttribute("data-status", "neutral");
  });

  it.each(["xs", "sm", "md", "lg"] as const)(
    "size=%s round-trips through data attribute",
    (size) => {
      render(
        <StatusBadge status="success" size={size}>
          x
        </StatusBadge>,
      );
      expect(
        screen.getByText("x").closest("[data-slot='status-badge']"),
      ).toHaveAttribute("data-size", size);
    },
  );
});

describe("StatusBadge — accessibility", () => {
  it("defaults role to 'status' for live announcements", () => {
    render(<StatusBadge status="success">ok</StatusBadge>);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("uses aria-live=polite for non-error variants", () => {
    render(<StatusBadge status="warning">heads up</StatusBadge>);
    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
  });

  it("uses aria-live=assertive for error variant — interrupts screen reader", () => {
    render(<StatusBadge status="error">broke</StatusBadge>);
    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "assertive");
  });

  it("synthesizes aria-label from string children when none provided", () => {
    render(<StatusBadge status="success">PUBLISHED</StatusBadge>);
    // Both the visible text AND the aria-label should equal the same thing
    // so screen readers don't double-announce.
    expect(screen.getByRole("status")).toHaveAttribute("aria-label", "PUBLISHED");
  });

  it("prefers explicit aria-label over auto-derived one", () => {
    render(
      <StatusBadge status="success" aria-label="Custom announce">
        VISIBLE
      </StatusBadge>,
    );
    expect(screen.getByRole("status")).toHaveAttribute(
      "aria-label",
      "Custom announce",
    );
  });

  it("uses srLabel as fallback aria-label when no children", () => {
    render(<StatusBadge status="error" srLabel="Token expired" />);
    expect(screen.getByRole("status")).toHaveAttribute(
      "aria-label",
      "Token expired",
    );
  });

  it("renders srLabel as sr-only text alongside visible children", () => {
    render(
      <StatusBadge status="error" srLabel="(critical)">
        5
      </StatusBadge>,
    );
    const srSpan = screen.getByText("(critical)");
    expect(srSpan).toHaveClass("sr-only");
    // Visible content unchanged
    expect(screen.getByText("5")).toBeVisible();
  });

  it("marks decorative icons as aria-hidden", () => {
    render(
      <StatusBadge status="info" icon={<svg data-testid="i" />}>
        info
      </StatusBadge>,
    );
    // The wrapper around the icon should be aria-hidden so the icon
    // doesn't get announced separately from the label.
    const icon = screen.getByTestId("i");
    expect(icon.parentElement).toHaveAttribute("aria-hidden", "true");
  });

  it("marks the dot as aria-hidden — color alone can't be announced", () => {
    const { container } = render(
      <StatusBadge status="success" dot>
        ok
      </StatusBadge>,
    );
    const dot = container.querySelector("[data-slot='status-dot']");
    expect(dot).toHaveAttribute("aria-hidden", "true");
  });

  it("allows custom role override (e.g. for use inside a live region)", () => {
    render(
      <StatusBadge status="info" role="presentation">
        decorative
      </StatusBadge>,
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByText("decorative").closest("[role='presentation']")).toBeInTheDocument();
  });
});

describe("StatusBadge — loading", () => {
  it("hides children and shows skeleton when loading", () => {
    render(
      <StatusBadge status="info" loading>
        will not render
      </StatusBadge>,
    );
    // The literal child text must NOT be in the DOM during loading,
    // because the skeleton replaces it.
    expect(screen.queryByText("will not render")).not.toBeInTheDocument();
  });

  it("sets aria-busy when loading so assistive tech knows to wait", () => {
    render(<StatusBadge status="info" loading />);
    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true");
  });

  it("removes aria-busy when not loading (no stale attribute)", () => {
    render(<StatusBadge status="success">done</StatusBadge>);
    expect(screen.getByRole("status")).not.toHaveAttribute("aria-busy");
  });

  it("skeleton element is aria-hidden — only the badge's aria-busy speaks", () => {
    const { container } = render(<StatusBadge status="info" loading />);
    const skeleton = container.querySelector(".animate-pulse");
    expect(skeleton).toHaveAttribute("aria-hidden", "true");
  });
});

describe("StatusBadge — edge cases", () => {
  it("renders without children when only a dot is needed", () => {
    const { container } = render(<StatusBadge status="success" dot />);
    expect(container.querySelector("[data-slot='status-dot']")).toBeInTheDocument();
    // Empty truncated span should NOT appear
    expect(container.querySelector(".truncate")).not.toBeInTheDocument();
  });

  it("ignores empty string children gracefully (no empty truncated span)", () => {
    const { container } = render(<StatusBadge status="warning">{""}</StatusBadge>);
    expect(container.querySelector(".truncate")).not.toBeInTheDocument();
  });

  it("attaches title=label so long content can be inspected on hover", () => {
    const long = "A really long status message that should truncate visually";
    render(<StatusBadge status="info">{long}</StatusBadge>);
    expect(screen.getByText(long)).toHaveAttribute("title", long);
  });

  it("renders non-string children without crashing on title attribute", () => {
    render(
      <StatusBadge status="success">
        <strong data-testid="bold">bold</strong>
      </StatusBadge>,
    );
    // No `title` is set when children isn't a string — that's intentional;
    // we don't want '[object Object]' style tooltips.
    expect(screen.getByTestId("bold")).toBeInTheDocument();
  });
});

describe("StatusBadge — composition", () => {
  it("merges custom className with variant classes", () => {
    render(
      <StatusBadge status="success" className="custom-extra">
        x
      </StatusBadge>,
    );
    const badge = screen.getByText("x").closest("[data-slot='status-badge']");
    expect(badge).toHaveClass("custom-extra");
    expect(badge).toHaveClass("text-success"); // variant class survives merge
  });

  it("renders as a span (non-interactive) — clickable status pills use Button", () => {
    // StatusBadge is intentionally non-interactive: it's a status indicator.
    // For clickable pills, callers use <Button> with the semantic color
    // classes. Pin the contract so a future PR that re-adds asChild
    // breaks this test loudly and forces a design conversation.
    render(<StatusBadge status="warning">heads up</StatusBadge>);
    const badge = screen.getByText("heads up").closest("[data-slot='status-badge']");
    expect(badge?.tagName).toBe("SPAN");
    // No tabindex / no implicit interaction — purely an indicator
    expect(badge).not.toHaveAttribute("tabindex");
    expect(badge).not.toHaveAttribute("onclick");
  });
});

describe("StatusDot", () => {
  it("renders standalone with status data attribute", () => {
    const { container } = render(<StatusDot status="error" />);
    const dot = container.querySelector("[data-slot='status-dot']");
    expect(dot).toBeInTheDocument();
    expect(dot).toHaveAttribute("data-status", "error");
    expect(dot).toHaveClass("bg-error");
  });

  it("pulse=true adds animation class (motion-safe gated)", () => {
    const { container } = render(<StatusDot status="success" pulse />);
    // The animation class is wrapped in motion-safe, so it's a class on
    // the element; the actual animation suppression is handled by Tailwind.
    expect(container.querySelector("[data-slot='status-dot']")?.className).toMatch(
      /animate-/,
    );
  });
});

describe("statusFromString", () => {
  it.each<[string, StatusVariant]>([
    ["PUBLISHED", "success"],
    ["published", "success"],
    ["  approved  ", "success"], // whitespace tolerant
    ["FAILED", "error"],
    ["pending", "warning"],
    ["running", "info"],
    ["DRAFT", "info"],
    ["VISUAL_READY", "info"],
  ])("maps %s → %s", (input, expected) => {
    expect(statusFromString(input)).toBe(expected);
  });

  it("returns neutral for unknown values (never throws)", () => {
    expect(statusFromString("totally_unknown_status")).toBe("neutral");
    expect(statusFromString("")).toBe("neutral");
    expect(statusFromString(null)).toBe("neutral");
    expect(statusFromString(undefined)).toBe("neutral");
  });
});
