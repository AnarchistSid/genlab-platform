import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const statusConfig: Record<string, { label: string; className: string }> = {
  INTEL_READY: {
    label: "Intel Ready",
    className: "bg-blue-500/15 text-blue-400 border-blue-500/25",
  },
  DRAFTED: {
    label: "Drafted",
    className: "bg-yellow-500/15 text-yellow-400 border-yellow-500/25",
  },
  VISUAL_READY: {
    label: "Visual Ready",
    className: "bg-purple-500/15 text-purple-400 border-purple-500/25",
  },
  SCHEDULED: {
    label: "Scheduled",
    className: "bg-cyan-500/15 text-cyan-400 border-cyan-500/25",
  },
  PUBLISHED: {
    label: "Published",
    className: "bg-green-500/15 text-green-400 border-green-500/25",
  },
  ERROR: {
    label: "Error",
    className: "bg-red-500/15 text-red-400 border-red-500/25",
  },
  NEEDS_REVIEW: {
    label: "Needs Review",
    className: "bg-orange-500/15 text-orange-400 border-orange-500/25",
  },
  ARCHIVED: {
    label: "Archived",
    className: "bg-gray-500/15 text-gray-400 border-gray-500/25",
  },
};

interface StatusBadgeProps {
  status: string;
  className?: string;
}

export function StatusBadge({ status, className }: StatusBadgeProps) {
  const config = statusConfig[status] ?? {
    label: status,
    className: "bg-muted text-muted-foreground border-border",
  };

  return (
    <Badge
      variant="outline"
      className={cn(config.className, className)}
    >
      {config.label}
    </Badge>
  );
}
