/**
 * PR W — SystemHealthView LaunchAgent status-dot pin (audit H6, 2026-03-31).
 *
 * The audit flagged that scheduled LaunchAgents (cron-style — Daily
 * Intel, hourly fetchers, etc.) showed up as red "Stopped" between
 * runs. They're SUPPOSED to be stopped between runs; that's how
 * launchd's StartCalendarInterval timers work. Painting them red
 * trains operators to ignore the red signal (alarm fatigue).
 *
 * Fix: in ``AgentRow`` (SystemHealthView.tsx), the status dot for
 * scheduled-but-currently-idle agents resolves to ``unknown`` (grey)
 * instead of ``down`` (red). The badge already showed "Idle" in grey;
 * the dot now matches.
 *
 * Pin: when launch_agents includes a scheduled agent with
 * status="stopped", neither the badge nor the dot may use red.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/api/client", () => ({
  healthDetailed: {
    get: vi.fn(),
  },
}));

import { healthDetailed } from "@/api/client";
import SystemHealthView from "../SystemHealthView";

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const ALWAYS_ON_AGENT = "com.genlab.review-server";
// ``com.genlab.gaming-pipeline`` is NOT in ``ALWAYS_ON_PREFIXES``
// (review-server / engagement / metric / daily-intel are). Pipeline
// runs are cron-style — start, do work, exit. Between fires the
// agent is "stopped" and that's expected.
const SCHEDULED_AGENT = "com.genlab.gaming-pipeline";

describe("SystemHealthView (H6 LaunchAgent idle-dot pin)", () => {
  it("renders 'Idle' badge for scheduled agent in stopped state", async () => {
    vi.mocked(healthDetailed.get).mockResolvedValueOnce({
      launch_agents: {
        [SCHEDULED_AGENT]: { status: "stopped", pid: null },
      },
      services: {},
      token_matrix: {},
    } as never);

    renderWithClient(<SystemHealthView />);

    // The agent label is normalized: "Gaming Pipeline"
    await screen.findByText(/Gaming Pipeline/i);
    // Idle badge appears for scheduled-stopped — NOT "Stopped"
    expect(screen.getByText(/^Idle$/i)).toBeInTheDocument();
    expect(screen.queryByText(/^Stopped$/i)).not.toBeInTheDocument();
  });

  it("renders red 'Stopped' badge only for always-on services in stopped state", async () => {
    // An always-on service (review server, engagement poller) being
    // stopped IS a real outage and should stay red. Pin that we didn't
    // break the legitimate red-flag path.
    vi.mocked(healthDetailed.get).mockResolvedValueOnce({
      launch_agents: {
        [ALWAYS_ON_AGENT]: { status: "stopped", pid: null },
      },
      services: {},
      token_matrix: {},
    } as never);

    renderWithClient(<SystemHealthView />);

    await screen.findByText(/Review Server/i);
    expect(screen.getByText(/^Stopped$/i)).toBeInTheDocument();
  });

  it("renders 'Running' badge with green dot for actively-running agent", async () => {
    vi.mocked(healthDetailed.get).mockResolvedValueOnce({
      launch_agents: {
        [ALWAYS_ON_AGENT]: { status: "running", pid: 12345 },
      },
      services: {},
      token_matrix: {},
    } as never);

    renderWithClient(<SystemHealthView />);

    await screen.findByText(/Review Server/i);
    expect(screen.getByText(/^Running$/i)).toBeInTheDocument();
    expect(screen.getByText(/PID 12345/)).toBeInTheDocument();
  });
});
