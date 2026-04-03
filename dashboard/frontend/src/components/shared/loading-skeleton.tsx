import { cn } from "@/lib/utils";

interface LoadingSkeletonProps {
  variant: "kpi-row" | "card-list" | "card-grid" | "table" | "bento";
  rows?: number;
  cols?: number;
  className?: string;
}

export function LoadingSkeleton({ variant, rows = 3, cols = 4, className }: LoadingSkeletonProps) {
  return (
    <div aria-busy="true" aria-label="Loading" className={cn(className)}>
      {variant === "kpi-row" && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {Array.from({ length: cols }).map((_, i) => (
            <div key={i} className="shimmer h-24 rounded-lg border border-border" />
          ))}
        </div>
      )}
      {variant === "card-list" && (
        <div className="flex flex-col gap-2">
          {Array.from({ length: rows }).map((_, i) => (
            <div key={i} className="shimmer h-20 rounded-lg border border-border" />
          ))}
        </div>
      )}
      {variant === "card-grid" && (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
          {Array.from({ length: rows * 3 }).map((_, i) => (
            <div key={i} className="shimmer h-32 rounded-lg border border-border" />
          ))}
        </div>
      )}
      {variant === "table" && (
        <div className="space-y-2">
          <div className="shimmer h-8 rounded" />
          {Array.from({ length: rows }).map((_, i) => (
            <div key={i} className="shimmer h-10 rounded" />
          ))}
        </div>
      )}
      {variant === "bento" && (
        <div className="mc-grid-v2">
          <div className="area-kpi">
            <div className="grid grid-cols-4 gap-3">
              {[1, 2, 3, 4].map((i) => <div key={i} className="shimmer h-24 rounded-lg border border-border" />)}
            </div>
          </div>
          {["area-spotlight", "area-learning", "area-insight", "area-timeline", "area-upcoming"].map((area) => (
            <div key={area} className={area}>
              <div className="shimmer h-28 rounded-lg border border-border" />
            </div>
          ))}
          <div className="area-channels">
            <div className="grid grid-cols-5 gap-3">
              {[1, 2, 3, 4, 5].map((i) => <div key={i} className="shimmer h-24 rounded-lg border border-border" />)}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
