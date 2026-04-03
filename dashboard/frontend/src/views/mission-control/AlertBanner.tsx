import { useState } from "react";
import { AlertTriangle, Info } from "lucide-react";
import { usePublishingAlerts } from "@/hooks/use-publishing-alerts";

interface AlertData {
  critical: Array<{ type: string; count?: number; niches?: string[] }>;
  warning: Array<{ type: string; count?: number; rate?: number; days_left?: number }>;
  info: Array<{ type: string; published?: number; failed?: number; uploads_today?: number }>;
  total_unresolved: number;
}

export function PublishingAlertBanner() {
  const { data: raw } = usePublishingAlerts();
  const data = raw as AlertData | undefined;
  const [expanded, setExpanded] = useState(false);

  if (!data || data.total_unresolved === 0) return null;

  const hasCritical = data.critical.length > 0;
  const bgColor = hasCritical ? "bg-red-500/10 border-red-500/30" : "bg-amber-500/10 border-amber-500/30";
  const textColor = hasCritical ? "text-red-400" : "text-amber-400";
  const Icon = hasCritical ? AlertTriangle : Info;

  return (
    <div className={`rounded-lg border ${bgColor} p-3 mb-4`}>
      <button
        className="flex items-center gap-2 w-full text-left"
        onClick={() => setExpanded(!expanded)}
      >
        <Icon className={`size-4 ${textColor} shrink-0`} />
        <span className={`text-sm font-medium ${textColor}`}>
          {data.total_unresolved} publishing alert{data.total_unresolved !== 1 ? "s" : ""}
        </span>
        <span className="text-xs text-text-muted ml-auto">
          {expanded ? "collapse" : "expand"}
        </span>
      </button>

      {expanded && (
        <div className="mt-3 space-y-2 text-xs">
          {data.critical.map((a, i) => (
            <div key={`c-${i}`} className="flex items-center gap-2 text-red-400">
              <span className="w-1.5 h-1.5 rounded-full bg-red-500 shrink-0" />
              {a.type === "publish_failed" && `${a.count} blueprint(s) permanently failed`}
              {a.type === "stuck_publishing" && `${a.count} blueprint(s) stuck in PUBLISHING`}
            </div>
          ))}
          {data.warning.map((a, i) => (
            <div key={`w-${i}`} className="flex items-center gap-2 text-amber-400">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-500 shrink-0" />
              {a.type === "high_failure_rate" && `${a.rate}% failure rate (24h)`}
              {a.type === "partial_publish" && `${a.count} partial publish(es) pending retry`}
              {a.type === "stale_visual_ready" && `${a.count} stale blueprint(s) >7 days`}
              {a.type === "token_expiring" && `Token expiring in ${a.days_left}d`}
            </div>
          ))}
          {data.info.map((a, i) => (
            <div key={`i-${i}`} className="flex items-center gap-2 text-text-muted">
              <span className="w-1.5 h-1.5 rounded-full bg-text-ghost shrink-0" />
              {a.type === "today_summary" && `Today: ${a.published} published, ${a.failed} failed`}
              {a.type === "youtube_quota" && `YouTube: ${a.uploads_today} uploads today`}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
