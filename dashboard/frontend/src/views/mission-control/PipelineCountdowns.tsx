import { useState, useEffect } from "react";
import { Timer } from "lucide-react";
import { getNicheInfo } from "@/niches/registry";

const SCHEDULES = [
  { id: "ai_creators", utcHour: 2, utcMinute: 30 },
  { id: "gaming", utcHour: 4, utcMinute: 0 },
  { id: "anime", utcHour: 6, utcMinute: 0 },
  { id: "movies", utcHour: 8, utcMinute: 0 },
  { id: "sports", utcHour: 10, utcMinute: 0 },
].map((s) => {
  const info = getNicheInfo(s.id);
  return { ...s, name: info.shortLabel, accent: info.hex };
});

function getNextRun(utcHour: number, utcMinute: number): Date {
  const now = new Date();
  const next = new Date(now);
  next.setUTCHours(utcHour, utcMinute, 0, 0);
  if (next <= now) {
    next.setUTCDate(next.getUTCDate() + 1);
  }
  return next;
}

function formatCountdown(ms: number): string {
  if (ms <= 0) return "Now";
  const totalSec = Math.floor(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  return `${h.toString().padStart(2, "0")}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

function formatUtcTime(h: number, m: number): string {
  return `${h.toString().padStart(2, "0")}:${m.toString().padStart(2, "0")} UTC`;
}

export function PipelineCountdowns() {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="bento-card">
      <h3 className="card-title">
        <Timer size={12} style={{ marginRight: 4, verticalAlign: "middle" }} />
        Pipeline Countdowns
      </h3>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {SCHEDULES.map((sched) => {
          const next = getNextRun(sched.utcHour, sched.utcMinute);
          const remaining = next.getTime() - now;
          const isImminent = remaining < 30 * 60 * 1000; // < 30 min

          return (
            <div key={sched.id} className="bg-bg-elevated rounded-md py-2 px-3">
              <div className="flex items-center gap-2 mb-1">
                <span
                  className="niche-dot"
                  style={{ backgroundColor: sched.accent }}
                />
                <span className="text-xs font-semibold text-text-primary flex-1">{sched.name}</span>
                <span className="text-[10px] font-mono text-text-muted">
                  {formatUtcTime(sched.utcHour, sched.utcMinute)}
                </span>
              </div>
              <span
                className="font-mono text-lg font-semibold text-text-secondary tabular-nums"
                style={isImminent ? { color: "var(--color-green)" } : undefined}
              >
                {formatCountdown(remaining)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
