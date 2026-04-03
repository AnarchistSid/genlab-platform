import type { LucideIcon } from "lucide-react";
import { useCountUp } from "@/hooks/use-count-up";
import { cn } from "@/lib/utils";
import { formatCompact } from "@/lib/format";

interface KpiCardProps {
  label: string;
  value: string | number;
  subtitle?: string;
  icon?: LucideIcon;
  accentColor?: string;
  estimated?: boolean;
  formatter?: (n: number) => string;
  animate?: boolean;
  variant?: "default" | "hero";
  className?: string;
}

export function KpiCard({
  label,
  value,
  subtitle,
  icon: Icon,
  accentColor,
  estimated,
  formatter,
  animate = true,
  variant = "default",
  className,
}: KpiCardProps) {
  const numericValue = typeof value === "number" ? value : null;
  const defaultFormatter = formatter ?? formatCompact;
  const animated = useCountUp(numericValue ?? 0, { formatter: defaultFormatter });
  const display = numericValue != null && animate ? animated : (typeof value === "number" ? defaultFormatter(value) : value);

  const isHero = variant === "hero";

  return (
    <div
      className={cn(
        "flex flex-col gap-1.5 rounded-lg border p-4",
        isHero ? "bg-bg-elevated border-border-strong" : "bg-bg-surface border-border",
        estimated && "border-t-2 border-t-warning",
        className,
      )}
      style={accentColor && isHero ? { borderTopColor: accentColor } : undefined}
    >
      <div className="flex items-center justify-between">
        <span className="label-caps">{label}</span>
        {Icon && <Icon className="size-4 text-text-muted" style={accentColor ? { color: accentColor } : undefined} />}
      </div>
      <div className="flex items-baseline gap-1.5">
        <span className={cn(
          "font-bold text-text-primary tracking-tight tabular-nums leading-none",
          isHero ? "text-[32px]" : "text-[28px]",
        )}>
          {display}
        </span>
        {estimated && <span className="text-[9px] font-medium text-warning uppercase">est.</span>}
      </div>
      {subtitle && <span className="text-[10px] text-text-muted">{subtitle}</span>}
    </div>
  );
}
