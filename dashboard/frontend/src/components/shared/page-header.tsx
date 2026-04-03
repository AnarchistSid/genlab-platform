import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface PageHeaderProps {
  title: string;
  subtitle?: string;
  badge?: ReactNode;
  actions?: ReactNode;
  className?: string;
}

export function PageHeader({ title, subtitle, badge, actions, className }: PageHeaderProps) {
  return (
    <header className={cn("mb-6", className)}>
      <div className="flex items-baseline gap-3">
        <h1 className="text-2xl font-bold text-text-primary tracking-tight leading-tight">
          {title}
        </h1>
        {badge}
        {actions && <div className="ml-auto flex items-center gap-2">{actions}</div>}
      </div>
      {subtitle && (
        <p className="mt-1 text-sm text-text-muted">{subtitle}</p>
      )}
    </header>
  );
}
