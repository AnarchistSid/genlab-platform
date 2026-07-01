/**
 * Intervention 6 badge tests (2026-07-01).
 *
 * Pin the render-mapping contract from ``recommendation`` to
 * visible-badge / no-badge. The badge exists to surface
 * ``worth_your_look`` disagreements to the operator; the other
 * recommendation labels either duplicate the rule gate (auto_approve /
 * auto_reject) or are "not applicable" states (disabled /
 * insufficient_data / error) that should NOT render.
 *
 * A future PR that changes the render rules can catch its own
 * regression here without needing to run the whole dashboard e2e.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/api/client", async () => {
  const actual =
    await vi.importActual<typeof import("@/api/client")>("@/api/client");
  return {
    ...actual,
    blueprints: {
      ...actual.blueprints,
      autoApprovalPreview: vi.fn(),
    },
  };
});

import { blueprints, type AutoApprovalPreview } from "@/api/client";
import { EnsembleBadge } from "../ensemble-badge";

function renderBadge() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <EnsembleBadge blueprintId="bp-1" />
    </QueryClientProvider>,
  );
}

function _stub(
  recommendation: NonNullable<AutoApprovalPreview["ensemble"]>["recommendation"],
  overrides: Partial<NonNullable<AutoApprovalPreview["ensemble"]>> = {},
): AutoApprovalPreview {
  return {
    record_id: "bp-1",
    approved: false,
    confidence: 0.5,
    passed_checks: [],
    failed_checks: [],
    reasons: [],
    would_publish: false,
    preview_only: true,
    ensemble: {
      score: 0.5,
      disagreement: 0.02,
      n_voters: 3,
      recommendation,
      votes: [
        { component: "bandit", score: 0.6, weight: 0.25, reason: "posterior" },
        {
          component: "hook_classifier",
          score: 0.5,
          weight: 0.2,
          reason: "proba",
        },
      ],
      reasons: [],
      ...overrides,
    },
  };
}

describe("EnsembleBadge", () => {
  it("renders NOTHING when recommendation is 'disabled'", async () => {
    vi.mocked(blueprints.autoApprovalPreview).mockResolvedValueOnce(
      _stub("disabled"),
    );
    renderBadge();
    // No badge should be visible at all — silently absent.
    await waitFor(() => {
      expect(screen.queryByTestId("ensemble-badge")).not.toBeInTheDocument();
    });
  });

  it("renders NOTHING when recommendation is 'insufficient_data'", async () => {
    vi.mocked(blueprints.autoApprovalPreview).mockResolvedValueOnce(
      _stub("insufficient_data"),
    );
    renderBadge();
    await waitFor(() => {
      expect(screen.queryByTestId("ensemble-badge")).not.toBeInTheDocument();
    });
  });

  it("renders NOTHING when the entire ensemble payload is absent", async () => {
    vi.mocked(blueprints.autoApprovalPreview).mockResolvedValueOnce({
      record_id: "bp-1",
      approved: false,
      confidence: 0.5,
      passed_checks: [],
      failed_checks: [],
      reasons: [],
      would_publish: false,
      preview_only: true,
      // no ensemble field at all — happens on rows the server
      // hasn't populated yet OR when the preview endpoint predates
      // Intervention 6
    });
    renderBadge();
    await waitFor(() => {
      expect(screen.queryByTestId("ensemble-badge")).not.toBeInTheDocument();
    });
  });

  it("renders a WARNING badge for 'worth_your_look' — the whole point", async () => {
    vi.mocked(blueprints.autoApprovalPreview).mockResolvedValueOnce(
      _stub("worth_your_look", { disagreement: 0.12 }),
    );
    renderBadge();
    const badge = await screen.findByTestId("ensemble-badge");
    expect(badge).toBeInTheDocument();
    expect(badge.getAttribute("data-recommendation")).toBe("worth_your_look");
  });

  it("renders a SUCCESS badge for 'auto_approve'", async () => {
    vi.mocked(blueprints.autoApprovalPreview).mockResolvedValueOnce(
      _stub("auto_approve", { score: 0.85 }),
    );
    renderBadge();
    const badge = await screen.findByTestId("ensemble-badge");
    expect(badge.getAttribute("data-recommendation")).toBe("auto_approve");
  });

  it("renders an ERROR badge for 'auto_reject'", async () => {
    vi.mocked(blueprints.autoApprovalPreview).mockResolvedValueOnce(
      _stub("auto_reject", { score: 0.15 }),
    );
    renderBadge();
    const badge = await screen.findByTestId("ensemble-badge");
    expect(badge.getAttribute("data-recommendation")).toBe("auto_reject");
  });
});
