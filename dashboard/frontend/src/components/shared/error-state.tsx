import { cn } from "@/lib/utils";

interface ErrorStateProps {
  title?: string;
  message?: string;
  onRetry?: () => void;
  className?: string;
}

export function ErrorState({
  title = "Unable to load data",
  message = "Could not connect to the API.",
  onRetry,
  className,
}: ErrorStateProps) {
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center justify-center gap-3 p-8 text-center",
        className,
      )}
    >
      <div className="size-12 rounded-full bg-bg-elevated flex items-center justify-center text-error text-xl font-bold">
        !
      </div>
      <h3 className="text-lg text-text-primary font-semibold">{title}</h3>
      <p className="text-sm text-text-muted">{message}</p>
      {onRetry && (
        <button className="btn-primary mt-2" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}
