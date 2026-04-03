import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

interface ProgressBarProps {
  value: number;
  color?: string;
  thresholds?: boolean;
  height?: number;
  animated?: boolean;
  label?: string;
  valueLabel?: string;
  className?: string;
}

export function ProgressBar({
  value,
  color,
  thresholds = false,
  height = 6,
  animated = true,
  label,
  valueLabel,
  className,
}: ProgressBarProps) {
  const [width, setWidth] = useState(animated ? 0 : value);

  useEffect(() => {
    if (animated) {
      const t = setTimeout(() => setWidth(Math.max(value, 0.5)), 100);
      return () => clearTimeout(t);
    }
    setWidth(value);
  }, [value, animated]);

  const barColor = thresholds
    ? value >= 100 ? "var(--color-green)" : value >= 50 ? "var(--color-amber)" : "var(--color-red)"
    : color ?? "var(--niche-current)";

  return (
    <div className={cn(className)}>
      {(label || valueLabel) && (
        <div className="flex items-center justify-between mb-1.5">
          {label && <span className="text-xs text-text-muted">{label}</span>}
          {valueLabel && <span className="text-xs font-semibold tabular-nums text-text-secondary">{valueLabel}</span>}
        </div>
      )}
      <div
        role="progressbar"
        aria-valuenow={Math.round(value)}
        aria-valuemin={0}
        aria-valuemax={100}
        className="overflow-hidden rounded-full bg-bg-elevated"
        style={{ height }}
      >
        <div
          className="h-full rounded-full"
          style={{
            width: `${Math.min(width, 100)}%`,
            background: barColor,
            transition: animated ? "width 0.8s cubic-bezier(0.16, 1, 0.3, 1)" : undefined,
            minWidth: width > 0 ? 3 : 0,
          }}
        />
      </div>
    </div>
  );
}
