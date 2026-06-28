import { useQuery } from "@tanstack/react-query";
import { RefreshCw, Server, Database, Shield, Monitor } from "lucide-react";
import { healthDetailed } from "@/api/client";
import type { DetailedHealthResponse, LaunchAgentInfo } from "@/api/types";
import { queryKeys } from "@/api/query-keys";
import { PageHeader } from "@/components/shared/page-header";
import { getActiveNiches, getNicheInfo } from "@/niches/registry";
import { PLATFORM_IDS, getPlatformInfo } from "@/lib/platforms";

// ── Types ─────────────────────────────────────────────────────────

type InfraStatus = "up" | "down" | "unknown";
type TokenStatus = "configured" | "expiring" | "missing" | "not_configured";
type AgentStatus = "running" | "stopped" | "scheduled";

// ── Constants ─────────────────────────────────────────────────────

const ALWAYS_ON_PREFIXES = [
  "com.genlab.review-server",
  "com.genlab.engagement",
  "com.genlab.metric",
  "com.genlab.daily-intel",
];

// ── Helpers ───────────────────────────────────────────────────────

function cleanAgentName(label: string): string {
  return label
    .replace(/^com\.genlab\./, "")
    .replace(/-/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function parseAgentStatus(info: LaunchAgentInfo | string): AgentStatus {
  const status = typeof info === "string" ? info : (info.status ?? "");
  const s = status.toLowerCase();
  if (s === "running") return "running";
  if (s === "scheduled") return "scheduled";
  return "stopped";
}

function isAlwaysOn(label: string): boolean {
  return ALWAYS_ON_PREFIXES.some((prefix) => label.startsWith(prefix));
}

function parseInfraStatus(status?: string): InfraStatus {
  if (!status) return "unknown";
  const s = status.toLowerCase();
  if (s === "ok" || s === "up" || s === "healthy" || s === "connected") return "up";
  if (s === "down" || s === "error" || s === "unreachable" || s === "failed") return "down";
  return "unknown";
}

function parseTokenStatus(value?: string | null): TokenStatus {
  if (!value) return "not_configured";
  const v = value.toLowerCase();
  if (v === "ok" || v === "valid" || v === "configured") return "configured";
  if (v === "expiring" || v === "refresh_soon" || v === "warning") return "expiring";
  if (v === "missing" || v === "expired" || v === "critical" || v === "invalid") return "missing";
  if (v === "not_configured" || v === "n/a" || v === "none") return "not_configured";
  // If we have a non-empty value we don't recognise, treat as configured
  return "configured";
}

// ── Sub-components ────────────────────────────────────────────────

function StatusDot({ status, size = 8 }: { status: InfraStatus; size?: number }) {
  const color =
    status === "up"      ? "var(--color-green)" :
    status === "down"    ? "var(--color-red)"   :
                           "var(--text-ghost)";
  return (
    <span
      className="inline-block rounded-full shrink-0"
      style={{
        width: size,
        height: size,
        background: color,
        boxShadow: status === "up" ? `0 0 6px ${color}66` : undefined,
      }}
    />
  );
}

function TokenDot({ status }: { status: TokenStatus }) {
  const color =
    status === "configured"     ? "var(--color-green)" :
    status === "expiring"       ? "var(--color-amber)" :
    status === "missing"        ? "var(--color-red)"   :
                                  "var(--text-ghost)";
  const title =
    status === "configured"     ? "Configured" :
    status === "expiring"       ? "Expiring soon" :
    status === "missing"        ? "Missing / expired" :
                                  "Not configured";
  return (
    <span
      title={title}
      className="inline-block w-2 h-2 rounded-full shrink-0"
      style={{ background: color }}
    />
  );
}

function AgentBadge({ status, isScheduled }: { status: AgentStatus; isScheduled?: boolean }) {
  const cfg =
    status === "running"   ? { label: "Running",   bg: "var(--color-green-dim)",  color: "var(--color-green)" } :
    status === "scheduled" ? { label: "Scheduled", bg: "var(--color-blue-dim)",   color: "var(--color-blue)"  } :
    isScheduled            ? { label: "Idle",      bg: "rgba(255,255,255,0.06)",  color: "var(--text-muted)"  } :
                             { label: "Stopped",   bg: "rgba(239,68,68,0.12)",    color: "var(--color-red)"   };
  return (
    <span
      className="text-xs font-medium px-2 py-0.5 rounded-full whitespace-nowrap"
      style={{ background: cfg.bg, color: cfg.color }}
    >
      {cfg.label}
    </span>
  );
}

// ── Section: Infrastructure ────────────────────────────────────────

interface InfraService {
  key: string;
  label: string;
  icon: React.ReactNode;
  status: InfraStatus;
  detail?: string;
}

function InfraCard({ svc }: { svc: InfraService }) {
  return (
    <div className="bg-bg-elevated border border-border rounded-lg p-4 flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-text-muted">{svc.icon}</span>
        <StatusDot status={svc.status} size={10} />
      </div>
      <div>
        <div className="text-sm font-semibold text-text-primary">
          {svc.label}
        </div>
        <div
          className="text-xs mt-0.5"
          style={{
            color:
              svc.status === "up"   ? "var(--color-green)" :
              svc.status === "down" ? "var(--color-red)"   :
                                      "var(--text-muted)",
          }}
        >
          {svc.status === "up" ? "Operational" : svc.status === "down" ? "Down" : "Unknown"}
        </div>
        {svc.detail && (
          <div className="text-xs text-text-ghost mt-0.5 font-mono">
            {svc.detail}
          </div>
        )}
      </div>
    </div>
  );
}

function InfraSection({ data }: { data?: DetailedHealthResponse }) {
  const services = data?.services ?? {};
  const postgres = data?.postgres ?? services.postgres;

  const cards: InfraService[] = [
    {
      key: "postgres",
      label: "PostgreSQL",
      icon: <Database size={16} />,
      status: parseInfraStatus(postgres?.status),
      detail: postgres?.response_time_ms != null ? `${postgres.response_time_ms}ms` : undefined,
    },
    {
      key: "redis",
      label: "Redis",
      icon: <Server size={16} />,
      status: parseInfraStatus(services.redis?.status),
      detail: services.redis?.response_time_ms != null ? `${services.redis.response_time_ms}ms` : undefined,
    },
    {
      key: "cloudflare_tunnel",
      label: "Cloudflare Tunnel",
      icon: <Shield size={16} />,
      status: parseInfraStatus(services.cloudflare_tunnel?.status),
    },
    {
      key: "dashboard",
      label: "Dashboard",
      icon: <Monitor size={16} />,
      status: parseInfraStatus(services.dashboard?.status),
    },
  ];

  return (
    <div className="grid grid-cols-4 gap-3">
      {cards.map((svc) => (
        <InfraCard key={svc.key} svc={svc} />
      ))}
    </div>
  );
}

// ── Section: Token Matrix ──────────────────────────────────────────

function TokenMatrixSection({ data }: { data?: DetailedHealthResponse }) {
  // token_matrix shape: niche_id → platform → status string
  const matrix = data?.token_matrix ?? {};
  const niches = getActiveNiches();
  const platformIds = PLATFORM_IDS;

  return (
    <div className="overflow-x-auto">
      <table className="data-table w-full table-fixed text-xs">
        <thead>
          <tr>
            {/* Niche column header */}
            <th className="w-[140px] px-3 py-2 text-left text-text-muted font-medium text-xs tracking-wide uppercase border-b border-border">
              Niche
            </th>
            {platformIds.map((pId) => {
              const p = getPlatformInfo(pId);
              return (
                <th
                  key={pId}
                  className="px-1 py-2 text-center border-b border-border font-semibold text-xs tracking-wide"
                  style={{ color: p.hex }}
                >
                  {p.shortLabel}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {niches.map((niche, i) => {
            const nicheTokens = matrix[niche.id] ?? {};
            const nicheInfo = getNicheInfo(niche.id);
            return (
              <tr
                key={niche.id}
                className={i % 2 !== 0 ? "bg-white/[0.015]" : ""}
              >
                <td className="px-3 py-2 border-b border-border-subtle">
                  <div className="flex items-center gap-2">
                    <span
                      className="w-1.5 h-1.5 rounded-full shrink-0 inline-block"
                      style={{ background: nicheInfo.hex }}
                    />
                    <span className="text-text-secondary font-medium">
                      {niche.displayName}
                    </span>
                  </div>
                </td>
                {platformIds.map((platformId) => {
                  const rawVal = nicheTokens[platformId] ?? nicheTokens[platformId.replace("_twitter", "")];
                  const tokenStatus = parseTokenStatus(rawVal);
                  return (
                    <td
                      key={platformId}
                      className="px-1 py-2 text-center border-b border-border-subtle align-middle"
                    >
                      <div className="flex justify-center items-center">
                        <TokenDot status={tokenStatus} />
                      </div>
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
      {/* Legend */}
      <div className="flex gap-4 mt-3 flex-wrap">
        {(
          [
            { status: "configured"    as TokenStatus, label: "Configured" },
            { status: "expiring"      as TokenStatus, label: "Expiring soon" },
            { status: "missing"       as TokenStatus, label: "Missing / expired" },
            { status: "not_configured"as TokenStatus, label: "Not configured" },
          ] as Array<{ status: TokenStatus; label: string }>
        ).map(({ status, label }) => (
          <div
            key={status}
            className="flex items-center gap-1"
          >
            <TokenDot status={status} />
            <span className="text-xs text-text-muted">{label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Section: LaunchAgents ─────────────────────────────────────────

interface AgentRow {
  label: string;
  displayName: string;
  status: AgentStatus;
  pid?: number | null;
  isScheduled?: boolean;
}

function AgentRow({ agent }: { agent: AgentRow }) {
  // H6 audit fix (2026-03-31): a scheduled agent in its idle window
  // (waiting for next launchd timer fire) reports status="stopped" —
  // but that's expected behaviour, not an alarm. The badge already
  // surfaces "Idle" in grey for scheduled agents (see ``AgentBadge``);
  // make the status dot match so we don't paint a red dot next to a
  // grey "Idle" badge. Without this fix, "Daily Intel" and every other
  // cron-style agent stays red between runs, training operators to
  // ignore the red signal (alarm fatigue).
  const dotStatus: InfraStatus =
    agent.status === "running"
      ? "up"
      : agent.status === "scheduled"
        ? "unknown"
        : agent.isScheduled
          ? "unknown" // scheduled-but-currently-idle → grey, NOT red
          : "down";
  return (
    <div className="flex items-center justify-between px-3 py-2 border-b border-border-subtle gap-3">
      <div className="flex items-center gap-2 min-w-0">
        <StatusDot
          status={dotStatus}
          size={7}
        />
        <span
          className="text-sm text-text-secondary font-mono whitespace-nowrap overflow-hidden text-ellipsis"
          title={agent.label}
        >
          {agent.displayName}
        </span>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {agent.pid != null && agent.status === "running" && (
          <span className="text-xs text-text-ghost font-mono">
            PID {agent.pid}
          </span>
        )}
        <AgentBadge status={agent.status} isScheduled={agent.isScheduled} />
      </div>
    </div>
  );
}

function AgentGroup({ title, agents }: { title: string; agents: AgentRow[] }) {
  if (agents.length === 0) return null;
  return (
    <div>
      <div className="label-caps px-3 py-2 border-b border-border">
        {title}
      </div>
      {agents.map((a) => (
        <AgentRow key={a.label} agent={a} />
      ))}
    </div>
  );
}

function LaunchAgentsSection({ data }: { data?: DetailedHealthResponse }) {
  const raw = data?.launch_agents ?? {};

  const agents: AgentRow[] = Object.entries(raw).map(([label, info]) => ({
    label,
    displayName: cleanAgentName(label),
    status: parseAgentStatus(info),
    pid: typeof info === "object" ? info.pid : null,
  }));

  const alwaysOn   = agents.filter((a) => isAlwaysOn(a.label));
  const scheduled  = agents.filter((a) => !isAlwaysOn(a.label)).map((a) => ({ ...a, isScheduled: true }));

  if (agents.length === 0) {
    return (
      <div className="bg-bg-elevated border border-border rounded-lg p-6 text-center text-text-ghost text-sm">
        No LaunchAgent data available
      </div>
    );
  }

  return (
    <div className="bg-bg-elevated border border-border rounded-lg overflow-hidden">
      <AgentGroup title="Always-On Services" agents={alwaysOn} />
      <AgentGroup title="Scheduled Agents"   agents={scheduled} />
    </div>
  );
}

// ── Local Section Header (not shared — has subtitle) ─────────────

function HealthSectionHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="pb-3 border-b border-border mb-4">
      <h2 className="text-lg font-semibold text-text-primary m-0">
        {title}
      </h2>
      {subtitle && (
        <p className="text-xs text-text-muted mt-1 mb-0">
          {subtitle}
        </p>
      )}
    </div>
  );
}

// ── Skeleton ───────────────────────────────────────────────────────

function Skeleton({ height, borderRadius = 8 }: { height: number; borderRadius?: number }) {
  return (
    <div
      className="shimmer bg-bg-elevated"
      style={{ height, borderRadius }}
    />
  );
}

// ── Root View ──────────────────────────────────────────────────────

export default function SystemHealthView() {
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: queryKeys.healthDetailed(),
    queryFn:  () => healthDetailed.get(),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

  const checkedAt = data?.checked_at
    ? new Date(data.checked_at).toLocaleTimeString()
    : null;

  return (
    <>
      <PageHeader
        title="System Health"
        subtitle="Infrastructure, platform tokens, and service status"
        actions={
          <div className="flex items-center gap-3 shrink-0">
            {checkedAt && (
              <span className="text-xs text-text-ghost">
                Updated {checkedAt}
              </span>
            )}
            <button
              onClick={() => refetch()}
              disabled={isFetching}
              className="flex items-center gap-1 px-3 py-1.5 bg-bg-elevated border border-border rounded-md text-text-secondary text-xs cursor-pointer transition-opacity disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RefreshCw size={12} className={isFetching ? "animate-spin" : ""} />
              Refresh
            </button>
          </div>
        }
      />

      {/* Error state */}
      {isError && (
        <div
          className="rounded-md px-4 py-3 text-sm mb-6"
          style={{
            background: "var(--color-red-dim)",
            border: "1px solid var(--color-red)",
            color: "var(--color-red)",
          }}
        >
          Failed to load health data: {error instanceof Error ? error.message : "Unknown error"}
        </div>
      )}

      {/* Section 1: Infrastructure */}
      <section className="mb-8">
        <HealthSectionHeader
          title="Infrastructure"
          subtitle="Core services powering the Gen Lab pipeline"
        />
        {isLoading ? (
          <div className="grid grid-cols-4 gap-3">
            {[1, 2, 3, 4].map((i) => (
              <Skeleton key={i} height={90} borderRadius={12} />
            ))}
          </div>
        ) : (
          <InfraSection data={data} />
        )}
      </section>

      {/* Section 2: Platform Tokens */}
      <section className="mb-8">
        <HealthSectionHeader
          title="Platform Tokens"
          subtitle="OAuth token status per niche x platform"
        />
        {isLoading ? (
          <Skeleton height={200} borderRadius={8} />
        ) : (
          <div className="bg-bg-elevated border border-border rounded-lg p-4">
            <TokenMatrixSection data={data} />
          </div>
        )}
      </section>

      {/* Section 3: LaunchAgents */}
      <section className="mb-8">
        <HealthSectionHeader
          title="LaunchAgents"
          subtitle="macOS launchd services — pipeline schedulers and always-on pollers"
        />
        {isLoading ? (
          <Skeleton height={240} borderRadius={12} />
        ) : (
          <LaunchAgentsSection data={data} />
        )}
      </section>

      {/* spin keyframe */}
      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </>
  );
}
