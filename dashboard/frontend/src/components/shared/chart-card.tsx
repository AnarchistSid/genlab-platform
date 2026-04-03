import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface ChartCardProps {
  title: string;
  subtitle?: string;
  headerAction?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function ChartCard({ title, subtitle, headerAction, children, className }: ChartCardProps) {
  return (
    <div className={cn("chart-card", className)}>
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="text-sm font-semibold text-text-primary">{title}</div>
          {subtitle && <div className="text-xs text-text-muted mt-0.5">{subtitle}</div>}
        </div>
        {headerAction}
      </div>
      {children}
    </div>
  );
}
