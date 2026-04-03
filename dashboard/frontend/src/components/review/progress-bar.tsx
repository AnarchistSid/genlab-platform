import { cn } from "@/lib/utils";

interface ProgressBarProps {
  current: number;
  total: number;
  approved: number;
  rejected: number;
  revised: number;
  skipped: number;
}

export function ProgressBar({
  current,
  total,
  approved,
  rejected,
  revised,
  skipped,
}: ProgressBarProps) {
  const safeTotal = Math.max(total, 1);

  const segments = [
    { count: approved, color: "bg-green-500", label: "approved" },
    { count: rejected, color: "bg-red-500", label: "rejected" },
    { count: revised, color: "bg-yellow-500", label: "revised" },
    { count: skipped, color: "bg-text-ghost", label: "skipped" },
  ];

  return (
    <div className="w-full bg-bg-primary border-t border-bg-hover px-6 py-3">
      {/* Segmented bar */}
      <div className="h-2 w-full rounded-full bg-bg-elevated overflow-hidden flex">
        {segments.map((seg) =>
          seg.count > 0 ? (
            <div
              key={seg.label}
              className={cn("h-full transition-all duration-500", seg.color)}
              style={{ width: `${(seg.count / safeTotal) * 100}%` }}
            />
          ) : null
        )}
      </div>

      {/* Stats row */}
      <div className="mt-2 flex items-center justify-between text-xs text-text-secondary">
        <span>
          {current} of {total} reviewed
        </span>
        <div className="flex items-center gap-4">
          {segments.map((seg) => (
            <span key={seg.label} className="flex items-center gap-1.5">
              <span
                className={cn("inline-block size-2 rounded-full", seg.color)}
              />
              {seg.count} {seg.label}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
