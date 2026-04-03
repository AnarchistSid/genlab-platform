import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

interface StatRowProps {
  label: string;
  value: string | number;
  mono?: boolean;
  valueColor?: string;
  icon?: LucideIcon;
  className?: string;
}

export function StatRow({ label, value, mono = true, valueColor, icon: Icon, className }: StatRowProps) {
  return (
    <div className={cn("flex items-center gap-2", className)}>
      {Icon && <Icon className="size-3.5 text-text-muted shrink-0" />}
      <span className="flex-1 text-xs text-text-secondary">{label}</span>
      <span
        className={cn(
          "text-sm font-semibold tabular-nums",
          mono && "font-mono",
        )}
        style={valueColor ? { color: valueColor } : undefined}
      >
        {value}
      </span>
    </div>
  );
}
