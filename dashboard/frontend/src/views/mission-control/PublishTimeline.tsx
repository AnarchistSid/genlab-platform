import { Check, X as XIcon, Clock } from "lucide-react";
import { useCrossNicheOverview } from "@/hooks/use-cross-niche-overview";
import { PlatformIcon } from "@/components/shared/PlatformIcon";
import { NICHE_HEX } from "@/niches/registry";

const NICHE_TIMES: Record<string, string> = {
  ai_creators: "02:30",
  gaming: "04:00",
  anime: "06:00",
  movies: "08:00",
  sports: "10:00",
};

const NICHE_ORDER = ["ai_creators", "gaming", "anime", "movies", "sports"];

export function PublishTimeline() {
  const { data } = useCrossNicheOverview();

  // Build timeline from schedule_today or fallback to niche-based rows
  const rows = NICHE_ORDER.map((nicheId) => {
    const scheduleItem = data?.schedule_today.find((s) => s.niche_id === nicheId);
    const nicheData = data?.niches.find((n) => n.id === nicheId);
    const time = scheduleItem?.slot_time_ist ?? NICHE_TIMES[nicheId] ?? "\u2014";
    // Determine status: check both schedule_today AND niche published_today count
    const hasScheduleItem = !!scheduleItem;
    const publishedToday = nicheData?.published_today ?? 0;
    const status = scheduleItem?.status === "published" || publishedToday > 0
      ? "published"
      : hasScheduleItem ? (scheduleItem?.status ?? "scheduled") : "scheduled";
    const platforms = ["instagram", "youtube", "facebook"];
    const color = NICHE_HEX[nicheId] ?? "var(--text-muted)";
    // Per-platform results from API (e.g., {instagram: "PUBLISHED", youtube: "FAILED"})
    const platformResults: Record<string, string> = scheduleItem?.platform_results ?? {};

    return { nicheId, time, status, platforms, color, platformResults };
  });

  return (
    <div className="bento-card">
      <h3 className="card-title">Today's Publishes</h3>

      <div className="flex flex-col gap-2">
        {rows.map((row) => (
          <div key={row.nicheId} className="flex items-center gap-2">
            <code className="font-mono text-xs text-text-secondary w-10 shrink-0">{row.time}</code>
            <span
              className="niche-dot"
              style={{ backgroundColor: row.color }}
            />
            <div className="flex gap-1.5 flex-1">
              {row.platforms.map((p) => {
                // Use per-platform result if available, else fall back to row status
                const platStatus = row.platformResults[p]?.toUpperCase?.() ?? "";
                const isSuccess = platStatus === "PUBLISHED" || (row.status === "published" && !platStatus);
                const isFail = platStatus === "FAILED";
                return (
                  <span key={p} className="inline-flex items-center gap-0.5">
                    <PlatformIcon platform={p} size={14} />
                    {isSuccess ? (
                      <Check size={8} className="text-success" />
                    ) : isFail ? (
                      <XIcon size={8} className="text-error" />
                    ) : (
                      <Clock size={8} className="text-text-disabled" />
                    )}
                  </span>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
