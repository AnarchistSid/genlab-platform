import {
  Instagram,
  Youtube,
  Facebook,
  CheckCircle,
  AlertCircle,
  AlertTriangle,
  Settings,
} from "lucide-react";
import { useChannelHealth } from "@/hooks/use-channel-health";
import { PageHeader } from "@/components/shared/page-header";
import { LoadingSkeleton } from "@/components/shared/loading-skeleton";
import { ErrorState } from "@/components/shared/error-state";

type HealthStatus = "ok" | "degraded" | "error" | "not_configured" | "unknown";

interface PlatformDef {
  key: string;
  label: string;
  icon: React.ReactNode;
}

const PLATFORMS: PlatformDef[] = [
  { key: "instagram", label: "Instagram", icon: <Instagram size={20} /> },
  { key: "youtube", label: "YouTube", icon: <Youtube size={20} /> },
  { key: "x_twitter", label: "X / Twitter", icon: <span className="text-lg font-bold">&#120143;</span> },
  { key: "facebook", label: "Facebook", icon: <Facebook size={20} /> },
  { key: "tiktok", label: "TikTok", icon: <span className="text-sm font-bold font-sans">TK</span> },
  { key: "threads", label: "Threads", icon: <span className="text-sm font-bold font-sans">TH</span> },
];

function StatusDot({ status }: { status: HealthStatus }) {
  if (status === "ok") return <span className="ch-dot ch-dot-ok" />;
  if (status === "degraded") return <span className="ch-dot ch-dot-degraded" />;
  if (status === "error") return <span className="ch-dot ch-dot-error" />;
  return <span className="ch-dot ch-dot-unknown" />;
}

const STATUS_STYLES: Record<HealthStatus, string> = {
  ok: "text-success",
  degraded: "text-warning",
  error: "text-error",
  not_configured: "text-text-muted",
  unknown: "text-text-muted",
};

const STATUS_LABELS: Record<HealthStatus, { label: string; icon: React.ReactNode }> = {
  ok: { label: "Operational", icon: <CheckCircle size={14} /> },
  degraded: { label: "Degraded", icon: <AlertTriangle size={14} /> },
  error: { label: "Error", icon: <AlertCircle size={14} /> },
  not_configured: { label: "Not Configured", icon: <Settings size={14} /> },
  unknown: { label: "Unknown", icon: <AlertCircle size={14} /> },
};

function StatusLabel({ status }: { status: HealthStatus }) {
  const info = STATUS_LABELS[status] || STATUS_LABELS.unknown;
  return (
    <span className={`flex items-center gap-1 text-xs ${STATUS_STYLES[status] ?? "text-text-muted"}`}>
      {info.icon} {info.label}
    </span>
  );
}

function PlatformCard({ def, status }: { def: PlatformDef; status: HealthStatus }) {
  return (
    <div className="flex items-center gap-3 px-4 py-3 bg-bg-surface border border-border rounded-lg transition-colors hover:border-border-strong">
      <div className="flex items-center justify-center size-9 rounded-md bg-bg-elevated text-text-secondary shrink-0">
        {def.icon}
      </div>
      <div className="flex-1 flex flex-col gap-0.5">
        <span className="text-sm font-medium text-text-primary">{def.label}</span>
        <StatusLabel status={status} />
      </div>
      <StatusDot status={status} />
    </div>
  );
}

export default function ChannelHealthView() {
  const { data: healthResp, isLoading, isError, refetch } = useChannelHealth();

  const health = healthResp?.data;

  if (isError && !health) {
    return (
      <div className="max-w-3xl mx-auto">
        <PageHeader
          title="Channel Health"
          subtitle="Monitor platform connectivity and publishing service status"
        />
        <ErrorState
          title="Unable to load channel health"
          message="Could not connect to the API."
          onRetry={() => void refetch()}
        />
      </div>
    );
  }

  const getStatus = (key: string): HealthStatus => {
    if (!health) return "unknown";
    return (health as unknown as Record<string, HealthStatus>)[key] || "unknown";
  };

  // Compute overall health score
  const allStatuses = health
    ? PLATFORMS.map((p) => getStatus(p.key))
    : [];
  const okCount = allStatuses.filter((s) => s === "ok").length;
  const totalCount = allStatuses.length;
  const score = totalCount > 0 ? Math.round((okCount / totalCount) * 100) : 0;

  return (
    <div className="max-w-3xl mx-auto">
      <PageHeader
        title="Channel Health"
        subtitle="Monitor platform connectivity and publishing service status"
      />

      {/* Overall Score */}
      <div className="flex items-center gap-4 p-4 bg-bg-surface border border-border rounded-lg mb-6">
        <div className="ch-score-ring">
          <svg viewBox="0 0 100 100" className="ch-score-svg">
            <circle cx="50" cy="50" r="42" className="ch-score-bg" />
            <circle
              cx="50"
              cy="50"
              r="42"
              className="ch-score-fill"
              style={{
                strokeDasharray: `${(score / 100) * 264} 264`,
                stroke: score >= 80 ? "var(--color-green)" : score >= 50 ? "var(--color-amber)" : "var(--color-red)",
              }}
            />
          </svg>
          <span className="ch-score-value">{isLoading ? "\u2014" : `${score}%`}</span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-semibold text-text-primary">Health Score</span>
          <span className="text-xs text-text-muted">
            {okCount}/{totalCount} services operational
          </span>
        </div>
      </div>

      {/* Platform Cards */}
      {isLoading ? (
        <LoadingSkeleton variant="card-list" rows={6} />
      ) : (
        <div className="flex flex-col gap-2">
          {PLATFORMS.map((def) => (
            <PlatformCard key={def.key} def={def} status={getStatus(def.key)} />
          ))}
        </div>
      )}
    </div>
  );
}
