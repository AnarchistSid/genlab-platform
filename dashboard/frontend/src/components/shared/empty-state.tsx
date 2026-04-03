import type { LucideIcon } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  description?: string;
  action?: {
    label: string;
    onClick: () => void;
  };
  className?: string;
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  return (
    <Card className={cn("border-dashed", className)}>
      <CardContent className="flex flex-col items-center justify-center py-12 text-center">
        <Icon className="size-12 text-muted-foreground mb-4" />
        <h3 className="text-lg font-semibold text-foreground">{title}</h3>
        {description && (
          <p className="mt-1 text-sm text-muted-foreground max-w-sm">
            {description}
          </p>
        )}
        {action && (
          <Button onClick={action.onClick} className="mt-4" size="sm">
            {action.label}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
