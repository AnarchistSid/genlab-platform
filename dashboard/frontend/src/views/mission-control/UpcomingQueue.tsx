import { Calendar, CheckCircle2, Clock, AlertCircle } from "lucide-react";
import { usePublishingQueue } from "@/hooks/use-publishing-queue";
import { NICHE_HEX } from "@/niches/registry";

function formatScheduleDate(iso: string | null | undefined): string {
  if (!iso) return "Unscheduled";
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case "APPROVED":
      return <CheckCircle2 size={12} style={{ color: "var(--color-green)" }} />;
    case "HELD":
      return <AlertCircle size={12} style={{ color: "var(--color-amber)" }} />;
    case "PENDING_APPROVAL":
      return <Clock size={12} style={{ color: "var(--text-muted)" }} />;
    default:
      return <Clock size={12} style={{ color: "var(--text-muted)" }} />;
  }
}

export function UpcomingQueue() {
  const { data } = usePublishingQueue();
  const items = data?.data ?? [];

  // Get next 3 FUTURE scheduled items (exclude past dates and published).
  // ``Date.now()`` is intentional impure read — see TrendRadar.tsx for
  // the same pattern + rationale (queue refreshes at the React Query
  // cadence; 60s drift is fine for "upcoming in N hours" UX).
  // eslint-disable-next-line react-hooks/purity
  const now = Date.now();
  const upcoming = items
    .filter((item) => {
      if (!item.scheduled_for || item.queue_status === "PUBLISHED") return false;
      return new Date(item.scheduled_for).getTime() > now;
    })
    .sort((a, b) => {
      const dateA = a.scheduled_for ? new Date(a.scheduled_for).getTime() : Infinity;
      const dateB = b.scheduled_for ? new Date(b.scheduled_for).getTime() : Infinity;
      return dateA - dateB;
    })
    .slice(0, 3);

  return (
    <div className="bento-card">
      <h3 className="card-title">
        <Calendar size={12} style={{ marginRight: 4, verticalAlign: "middle" }} />
        Upcoming Queue
      </h3>

      {upcoming.length === 0 ? (
        <p className="text-sm text-text-muted my-2">
          No upcoming posts scheduled
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          {upcoming.map((item) => {
            const accent = NICHE_HEX[item.niche_id ?? ""] ?? "var(--text-muted)";
            return (
              <div key={item.id} className="flex gap-2 p-2 rounded-md bg-bg-elevated">
                <div className="flex flex-col items-center gap-1 pt-0.5">
                  <span
                    className="niche-dot"
                    style={{ backgroundColor: accent }}
                  />
                  <StatusIcon status={item.queue_status} />
                </div>
                <div className="flex-1 min-w-0">
                  <span className="text-sm text-text-primary block truncate">
                    {item.hook_text?.slice(0, 50) ?? "Untitled"}
                  </span>
                  <span className="text-xs text-text-muted">
                    {formatScheduleDate(item.scheduled_for)}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
