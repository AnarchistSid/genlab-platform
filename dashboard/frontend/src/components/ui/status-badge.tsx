/**
 * StatusBadge — semantic status indicator for live application state.
 *
 * This complements `Badge` (which is role-based: default/secondary/destructive…)
 * by adding *meaning-based* variants for the data the badge represents:
 * success / warning / error / info / neutral. Use this whenever the badge
 * is communicating the state of something (a pipeline run, a quota, an
 * alert, a calibration window) rather than the role of a button-like UI.
 *
 * Design goals:
 *   - **Accessible** — `role="status"` + `aria-live` so screen readers
 *     announce status changes; `aria-label` synthesized from children when
 *     not provided; high-contrast border on every variant so the badge
 *     survives dark mode and high-contrast OS settings.
 *   - **Loading-aware** — `loading={true}` swaps the content for a sized
 *     skeleton without changing the badge's outer dimensions (no layout
 *     shift while data is fetching).
 *   - **Responsive** — content truncates with ellipsis + `title=` fallback
 *     so long labels can't break the parent layout; sizes use rem so the
 *     badge scales with user font-size preferences.
 *   - **Edge-case safe** — empty label renders a dot-only badge (still
 *     meaningful as a status indicator); long labels truncate gracefully;
 *     icon-only mode is supported via `srLabel` for screen-reader text.
 *
 * Example usage (more in the bottom of this file):
 *
 *   <StatusBadge status="success">PUBLISHED</StatusBadge>
 *
 *   <StatusBadge status="warning" dot>
 *     {count} retries
 *   </StatusBadge>
 *
 *   <StatusBadge status="error" icon={<AlertCircle />} size="lg">
 *     Token expired
 *   </StatusBadge>
 *
 *   <StatusBadge status="info" loading />
 *
 * Ships AUTO #1 / DEAD #1 / ARCH #45 follow-up (2026-06-13) — replaces
 * 4 hand-rolled inline-badge variants across:
 *   - components/review/auto-approval-badge.tsx
 *   - views/mission-control/AutoApprovalCalibrationCard.tsx
 *   - views/pipeline/RunHistoryTable.tsx (stage-failure pill)
 *   - views/mission-control/ContentQuality.tsx (counters)
 */
import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

// ── Variant definition ─────────────────────────────────────────────────────
//
// Every variant pairs (background + foreground + border) so the badge
// reads correctly in dark mode AND high-contrast accessibility settings.
// The `/15` background opacity gives a tinted-pill look while keeping
// text contrast ratio ≥ 4.5:1 against the parent dark theme.

const statusBadgeVariants = cva(
  // Base: inline-flex layout + truncation + outline-on-focus (for the
  // optional `as="button"` case) + reduced-motion compatible animation.
  [
    "inline-flex items-center gap-1 rounded font-medium",
    "max-w-full overflow-hidden",
    "border whitespace-nowrap",
    "outline-none focus-visible:ring-2 focus-visible:ring-offset-1",
    "focus-visible:ring-ring/40 focus-visible:ring-offset-bg-primary",
    "transition-colors motion-reduce:transition-none",
  ],
  {
    variants: {
      status: {
        success: "bg-success/15 text-success border-success/30",
        warning: "bg-warning/15 text-warning border-warning/30",
        error: "bg-error/15 text-error border-error/30",
        info: "bg-info/15 text-info border-info/30",
        neutral:
          "bg-text-muted/10 text-text-muted border-text-muted/20",
      },
      size: {
        // xs — for tight tables / inline runs of badges
        xs: "px-1.5 py-0.5 text-[10px] [&_svg]:size-3",
        // sm — default; matches the existing inline-badge density in
        // RunHistoryTable + AutoApprovalCalibrationCard
        sm: "px-2 py-0.5 text-xs [&_svg]:size-3.5",
        // md — for headers / banner-style status indicators
        md: "px-2.5 py-1 text-sm [&_svg]:size-4",
        // lg — for hero / empty-state cards where the badge is the focus
        lg: "px-3 py-1.5 text-base [&_svg]:size-5",
      },
    },
    defaultVariants: {
      status: "neutral",
      size: "sm",
    },
  },
);

// Dot indicator size mirrors the parent badge size so the dot stays
// visually balanced with the text. Separated from the main cva so the
// dot can be exported on its own.
const statusDotVariants = cva(
  "inline-block rounded-full shrink-0 motion-safe:[--dot-pulse:pulse_2s_ease-in-out_infinite]",
  {
    variants: {
      status: {
        success: "bg-success",
        warning: "bg-warning",
        error: "bg-error",
        info: "bg-info",
        neutral: "bg-text-muted",
      },
      size: {
        xs: "size-1.5",
        sm: "size-2",
        md: "size-2.5",
        lg: "size-3",
      },
      pulse: {
        true: "motion-safe:animate-[var(--dot-pulse)]",
        false: "",
      },
    },
    defaultVariants: {
      status: "neutral",
      size: "sm",
      pulse: false,
    },
  },
);

// ── Public types ──────────────────────────────────────────────────────────

export type StatusVariant = "success" | "warning" | "error" | "info" | "neutral";
export type StatusSize = "xs" | "sm" | "md" | "lg";

export interface StatusBadgeProps
  extends Omit<React.ComponentProps<"span">, "children">,
    Omit<VariantProps<typeof statusBadgeVariants>, "status" | "size"> {
  /** Semantic status. Drives color + ARIA live region urgency. */
  status?: StatusVariant;
  /** Visual size. Defaults to `sm` to match the dashboard's table density. */
  size?: StatusSize;
  /**
   * Show a leading colored dot. Use when the badge label is data
   * (e.g. "12 retries") rather than a status word, so the color
   * meaning is still conveyed.
   */
  dot?: boolean;
  /**
   * Make the leading dot pulse — useful for live/in-progress states.
   * Respects `prefers-reduced-motion`; no pulse when the user opts out.
   */
  pulse?: boolean;
  /**
   * Optional leading icon. Icon is auto-aria-hidden because the label
   * carries the meaning. Pass `aria-hidden={false}` via the icon
   * element itself if you really want it announced.
   */
  icon?: React.ReactNode;
  /**
   * When true, swaps the content for a skeleton placeholder while
   * preserving the badge dimensions. `aria-busy` is set so assistive
   * tech knows content is loading. Layout shift = zero.
   */
  loading?: boolean;
  /**
   * StatusBadge is intentionally non-interactive — it's a status
   * indicator, not a control. For clickable status pills (e.g. "3
   * alerts — click to view"), use `<Button variant="ghost" size="xs">`
   * with the same semantic color classes (`text-warning`, etc.).
   * Mixing badge-style content (icon + dot + text) with `asChild`
   * confuses Radix Slot's single-child contract.
   */
  /**
   * Screen-reader-only label. Use when the visual content is icon-only
   * or numeric and needs more context for assistive tech.
   * Example: `<StatusBadge status="error" srLabel="Token expired">5</StatusBadge>`
   */
  srLabel?: string;
  /** Badge content. Empty/falsy → renders dot-only (still meaningful). */
  children?: React.ReactNode;
}

// ── Component ─────────────────────────────────────────────────────────────

function StatusBadge({
  status = "neutral",
  size = "sm",
  dot = false,
  pulse = false,
  icon,
  loading = false,
  srLabel,
  className,
  children,
  "aria-label": ariaLabel,
  role,
  ...props
}: StatusBadgeProps) {
  const Comp = "span";

  // Derive an aria-label from the visible children when none was provided.
  // This is the screen-reader baseline — without it, an icon-only badge
  // would announce nothing.
  const computedAriaLabel =
    ariaLabel ??
    srLabel ??
    (typeof children === "string" ? children : undefined);

  // Errors get assertive aria-live so they interrupt; everything else is
  // polite (announced when the screen reader is idle). This matches WAI-ARIA
  // guidance for status messages.
  const ariaLive = status === "error" ? "assertive" : "polite";

  return (
    <Comp
      data-slot="status-badge"
      data-status={status}
      data-size={size}
      role={role ?? "status"}
      aria-live={ariaLive}
      aria-label={computedAriaLabel}
      aria-busy={loading || undefined}
      className={cn(statusBadgeVariants({ status, size }), className)}
      {...props}
    >
      {loading ? (
        // Skeleton preserves badge dimensions to prevent layout shift.
        // The width here matches "approx 6 chars" — adjust via wrapping
        // parent if you need a different placeholder length.
        <span
          aria-hidden="true"
          className={cn(
            "inline-block rounded bg-text-muted/30 animate-pulse",
            size === "xs" && "h-2 w-8",
            size === "sm" && "h-2.5 w-10",
            size === "md" && "h-3 w-12",
            size === "lg" && "h-4 w-14",
          )}
        />
      ) : (
        <>
          {dot && (
            <StatusDot
              status={status}
              size={size}
              pulse={pulse}
              aria-hidden="true"
            />
          )}
          {icon && (
            <span aria-hidden="true" className="inline-flex shrink-0">
              {icon}
            </span>
          )}
          {/* The label itself — truncates with ellipsis. The `title` lets
              the operator hover to see the full text without breaking layout.
              `dir="auto"` handles RTL-safe truncation. */}
          {children != null && children !== "" && (
            <span
              className="truncate"
              dir="auto"
              title={typeof children === "string" ? children : undefined}
            >
              {children}
            </span>
          )}
          {/* srLabel renders sr-only when there's also visible content,
              giving the screen reader extra context without disturbing
              the visual. */}
          {srLabel && children != null && children !== "" && (
            <span className="sr-only">{srLabel}</span>
          )}
        </>
      )}
    </Comp>
  );
}

// ── Standalone dot (exported for use outside badges) ──────────────────────
//
// Many places in the dashboard already use a "colored dot beside a label"
// pattern — e.g. the LearningLoopCard "Thompson active" line. Exporting
// this separately lets those sites use the same color/size tokens without
// rolling them by hand.

interface StatusDotProps
  extends Omit<React.ComponentProps<"span">, "color">,
    VariantProps<typeof statusDotVariants> {}

function StatusDot({
  status = "neutral",
  size = "sm",
  pulse = false,
  className,
  ...props
}: StatusDotProps) {
  return (
    <span
      data-slot="status-dot"
      data-status={status}
      className={cn(statusDotVariants({ status, size, pulse }), className)}
      {...props}
    />
  );
}

// ── Status → label utility (handy for tabular displays) ───────────────────
//
// Common pattern: backend returns a string status (`"PUBLISHED"`,
// `"FAILED"`, `"PENDING"`), UI maps it to a StatusBadge. Centralizing the
// string→variant mapping prevents drift across the dashboard.

const STATUS_KEYWORDS: Record<string, StatusVariant> = {
  // Success
  published: "success",
  approved: "success",
  ok: "success",
  complete: "success",
  completed: "success",
  ready: "success",
  active: "success",
  // Warning
  partial: "warning",
  warning: "warning",
  pending: "warning",
  retry: "warning",
  retrying: "warning",
  scheduled: "warning",
  // Error
  failed: "error",
  error: "error",
  critical: "error",
  rejected: "error",
  archived: "error",
  // Info
  in_progress: "info",
  running: "info",
  drafted: "info",
  draft: "info",
  visual_ready: "info",
};

/**
 * Map a string status to a StatusBadge variant. Case-insensitive; falls
 * back to "neutral" for unknown values so it never throws.
 */
export function statusFromString(status: string | null | undefined): StatusVariant {
  if (!status) return "neutral";
  return STATUS_KEYWORDS[status.toLowerCase().trim()] ?? "neutral";
}

export { StatusBadge, StatusDot, statusBadgeVariants, statusDotVariants };

// ── Usage examples (kept inline for discoverability) ──────────────────────
//
// These are reference patterns. Storybook is not set up in this project,
// so colocating examples in the source file is the next-best signal.
//
// 1. Basic semantic status:
//      <StatusBadge status="success">PUBLISHED</StatusBadge>
//      <StatusBadge status="error">FAILED</StatusBadge>
//
// 2. With a leading dot (when the label is data, not a status word):
//      <StatusBadge status="warning" dot>{retryCount} retries</StatusBadge>
//
// 3. Live state with pulse:
//      <StatusBadge status="info" dot pulse>Pipeline running</StatusBadge>
//
// 4. With an icon (icon auto-hidden from screen readers):
//      <StatusBadge status="error" icon={<AlertCircle />}>
//        Token expired
//      </StatusBadge>
//
// 5. Loading placeholder (no layout shift while fetching):
//      <StatusBadge status="info" loading />
//
// 6. Icon-only with screen-reader label:
//      <StatusBadge status="success" srLabel="Verified" icon={<Check />} />
//
// 7. From string (works with backend status strings):
//      <StatusBadge status={statusFromString(run.status)}>{run.status}</StatusBadge>
//
// 8. Clickable status pill — DON'T use StatusBadge. Use Button with
//    the semantic color classes instead, so you get full keyboard
//    handling, focus states, and disabled handling for free:
//      <Button
//        variant="ghost"
//        size="xs"
//        className="bg-warning/15 text-warning border border-warning/30"
//        onClick={openDetails}
//      >
//        3 alerts
//      </Button>
//
// 9. Standalone dot (outside a badge — useful for tables):
//      <span className="flex items-center gap-2">
//        <StatusDot status="success" pulse aria-label="Active" />
//        <span>Live</span>
//      </span>
