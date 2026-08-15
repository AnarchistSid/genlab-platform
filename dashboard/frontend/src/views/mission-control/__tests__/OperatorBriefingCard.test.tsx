/**
 * Phase 5.D card tests.
 *
 * Load-bearing signals: cold-start copy, summary render, email
 * badges (sent / failed / skipped), review-count badge.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/api/client", () => ({
  operatorBriefings: { latest: vi.fn() },
}));

import { operatorBriefings } from "@/api/client";
import { OperatorBriefingCard } from "../OperatorBriefingCard";
import type { OperatorBriefing } from "@/api/types";

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const _stub = (
  overrides: Partial<OperatorBriefing> = {},
): OperatorBriefing => ({
  id: "abc",
  generated_at: new Date(Date.now() - 3600 * 1000).toISOString(),
  summary_md: "**Wins today**\n- One reel hit 5k views\n- Zero misses",
  structured: {},
  email_sent: true,
  email_recipient: "op@example.com",
  email_error: null,
  llm_cost_usd: 0.0032,
  n_pending_flag_flips: 2,
  n_pending_strategist_proposals: 3,
  ...overrides,
});

describe("OperatorBriefingCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows cold-start copy when data null", async () => {
    vi.mocked(operatorBriefings.latest).mockResolvedValue(null);
    renderWithClient(<OperatorBriefingCard />);
    expect(
      await screen.findByText(/No briefing yet/i),
    ).toBeInTheDocument();
  });

  it("renders summary text", async () => {
    vi.mocked(operatorBriefings.latest).mockResolvedValue(_stub());
    renderWithClient(<OperatorBriefingCard />);
    expect(
      await screen.findByText(/Wins today/),
    ).toBeInTheDocument();
    expect(screen.getByText(/One reel hit 5k views/)).toBeInTheDocument();
  });

  it("shows total review count badge", async () => {
    vi.mocked(operatorBriefings.latest).mockResolvedValue(_stub());
    renderWithClient(<OperatorBriefingCard />);
    // 2 + 3 = 5
    expect(await screen.findByText(/5 to review/)).toBeInTheDocument();
  });

  it("shows queue-clear badge when no pending", async () => {
    vi.mocked(operatorBriefings.latest).mockResolvedValue(
      _stub({ n_pending_flag_flips: 0, n_pending_strategist_proposals: 0 }),
    );
    renderWithClient(<OperatorBriefingCard />);
    expect(await screen.findByText(/queue clear/)).toBeInTheDocument();
  });

  it("shows email-sent badge when delivered", async () => {
    vi.mocked(operatorBriefings.latest).mockResolvedValue(_stub());
    renderWithClient(<OperatorBriefingCard />);
    expect(await screen.findByText(/email sent/)).toBeInTheDocument();
  });

  it("shows email-failed badge when error present", async () => {
    vi.mocked(operatorBriefings.latest).mockResolvedValue(
      _stub({ email_sent: false, email_error: "AUTH_FAILED" }),
    );
    renderWithClient(<OperatorBriefingCard />);
    expect(await screen.findByText(/email failed/)).toBeInTheDocument();
  });

  it("shows email-skipped badge when no error but not sent", async () => {
    vi.mocked(operatorBriefings.latest).mockResolvedValue(
      _stub({ email_sent: false, email_error: null }),
    );
    renderWithClient(<OperatorBriefingCard />);
    expect(await screen.findByText(/email skipped/)).toBeInTheDocument();
  });

  it("shows LLM cost", async () => {
    vi.mocked(operatorBriefings.latest).mockResolvedValue(_stub());
    renderWithClient(<OperatorBriefingCard />);
    expect(await screen.findByText(/\$0\.0032/)).toBeInTheDocument();
  });
});
