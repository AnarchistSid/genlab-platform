import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import { toast } from "sonner";
import {
  ArrowLeft,
  Check,
  X,
  RotateCcw,
  SkipForward,
  Loader2,
  CheckCircle2,
  ImageIcon,
  Keyboard,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { CarouselViewer } from "@/components/shared/carousel-viewer";
import { ProgressBar } from "@/components/review/progress-bar";
import { AutoApprovalBadge } from "@/components/review/auto-approval-badge";
import { cn } from "@/lib/utils";
import { useFocusReviewQueue } from "@/hooks/use-focus-review";
import { useNicheStore } from "@/stores/niche-store";
import { getDetailView, type NicheId } from "@/niches/registry";
import { GenericDetailView } from "@/niches/detail-views/GenericDetailView";
import { isVideoUrl } from "@/lib/media";

type ReviewAction = "approved" | "rejected" | "revised" | "skipped";

const ISSUE_OPTIONS = [
  { value: "weak_hook", label: "Weak Hook" },
  { value: "too_generic", label: "Too Generic" },
  { value: "unsupported_claim", label: "Unsupported Claim" },
  { value: "bad_fit", label: "Bad Fit" },
  { value: "too_long", label: "Too Long" },
  { value: "low_value", label: "Low Value" },
] as const;

type IssueValue = (typeof ISSUE_OPTIONS)[number]["value"];

const ACTION_CONFIG: Record<
  ReviewAction,
  {
    label: string;
    icon: typeof Check;
    shortcut: string;
    colorVar: string;
    needsFeedback: boolean;
  }
> = {
  approved: {
    label: "Approve",
    icon: Check,
    shortcut: "A",
    colorVar: "var(--color-green)",
    needsFeedback: false,
  },
  rejected: {
    label: "Reject",
    icon: X,
    shortcut: "R",
    colorVar: "var(--color-red)",
    needsFeedback: true,
  },
  revised: {
    label: "Revise",
    icon: RotateCcw,
    shortcut: "V",
    colorVar: "var(--color-amber)",
    needsFeedback: true,
  },
  skipped: {
    label: "Skip",
    icon: SkipForward,
    shortcut: "S",
    colorVar: "var(--text-muted)",
    needsFeedback: false,
  },
};

/* ------------------------------------------------------------------ */
/*  Loading state                                                      */
/* ------------------------------------------------------------------ */
function FocusLoading() {
  return (
    <div
      className="flex h-screen w-screen items-center justify-center bg-bg-primary"
    >
      <div className="flex flex-col items-center gap-4">
        <Loader2 className="size-8 animate-spin" style={{ color: "var(--niche-current)" }} />
        <p className="text-sm text-text-muted">
          Loading review queue...
        </p>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Empty state                                                        */
/* ------------------------------------------------------------------ */
function FocusEmpty() {
  const navigate = useNavigate();
  return (
    <div
      className="flex h-screen w-screen items-center justify-center bg-bg-primary"
    >
      <div className="flex flex-col items-center gap-4 text-center">
        <CheckCircle2 className="size-12 text-color-green" />
        <h2 className="text-lg font-semibold text-text-primary">
          No items pending review
        </h2>
        <p className="text-sm max-w-sm text-text-muted">
          All items were auto-processed. Check the Archived tab for details on
          what was filtered and why.
        </p>
        <Button
          variant="outline"
          className="mt-2 border-border"
          onClick={() => navigate("/blueprints?status=ARCHIVED")}
        >
          View Archived
        </Button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Fallback state — no pending items, showing recently published      */
/* ------------------------------------------------------------------ */
function FocusFallback({
  items,
  onBack,
}: {
  items: { id: string; hook_text?: string; status?: string; niche_id?: string }[];
  onBack: () => void;
}) {
  return (
    <div
      className="flex h-screen w-screen items-center justify-center bg-bg-primary"
    >
      <div className="flex flex-col items-center gap-4 text-center max-w-lg">
        <ImageIcon className="size-12 text-text-disabled" />
        <h2 className="text-lg font-semibold text-text-primary">
          No items pending review
        </h2>
        <p className="text-sm text-text-muted">
          All blueprints have been reviewed or the pipeline is in auto-publish mode.
          Here are the most recently published items:
        </p>
        <div className="w-full space-y-2 mt-2">
          {items.map((item) => (
            <div
              key={item.id}
              className="flex items-center justify-between rounded-md px-3 py-2 text-left text-sm bg-bg-raised border border-border text-text-primary"
            >
              <span className="truncate flex-1">{item.hook_text || `Blueprint ${item.id}`}</span>
              <span
                className="ml-2 shrink-0 rounded px-1.5 py-0.5 text-xs font-medium"
                style={{ background: "var(--color-green-dim)", color: "var(--color-green)" }}
              >
                {item.status || "PUBLISHED"}
              </span>
            </div>
          ))}
        </div>
        <Button
          variant="outline"
          className="mt-2 border-border"
          onClick={onBack}
        >
          Back to Dashboard
        </Button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Completion screen                                                  */
/* ------------------------------------------------------------------ */
function CompletionScreen({
  approved,
  rejected,
  revised,
  skipped,
  onRestart,
}: {
  approved: number;
  rejected: number;
  revised: number;
  skipped: number;
  onRestart: () => void;
}) {
  const navigate = useNavigate();
  const total = approved + rejected + revised + skipped;

  return (
    <div
      className="flex h-screen w-screen items-center justify-center bg-bg-primary"
    >
      <motion.div
        initial={{ scale: 0.8, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ type: "spring", stiffness: 200, damping: 20 }}
        className="w-full max-w-md rounded-xl p-8 bg-bg-surface border border-border"
      >
        <div className="text-center">
          <h2 className="text-2xl font-bold text-text-primary">
            Review Complete!
          </h2>
          <p className="mt-1 text-sm text-text-muted">
            You reviewed {total} blueprint{total !== 1 ? "s" : ""}
          </p>
        </div>

        <div className="mt-6 grid grid-cols-2 gap-3">
          <StatCard label="Approved" count={approved} colorVar="var(--color-green)" />
          <StatCard label="Rejected" count={rejected} colorVar="var(--color-red)" />
          <StatCard label="Revised" count={revised} colorVar="var(--color-amber)" />
          <StatCard label="Skipped" count={skipped} colorVar="var(--text-muted)" />
        </div>

        <div className="mt-8 flex gap-3">
          <Button
            variant="outline"
            className="flex-1 border-border"
            onClick={() => navigate("/")}
          >
            Back to Dashboard
          </Button>
          <Button
            className="flex-1 text-white"
            style={{ background: "var(--niche-current)" }}
            onClick={onRestart}
          >
            Review More
          </Button>
        </div>
      </motion.div>
    </div>
  );
}

function StatCard({ label, count, colorVar }: { label: string; count: number; colorVar: string }) {
  return (
    <div
      className="rounded-lg p-3 text-center bg-bg-elevated border border-border"
    >
      <div className="text-2xl font-bold" style={{ color: colorVar }}>{count}</div>
      <div className="mt-0.5 text-xs text-text-muted">{label}</div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Media fallback wrappers                                            */
/* ------------------------------------------------------------------ */
function VideoWithFallback({ src }: { src: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div
        className="flex h-full items-center justify-center rounded-lg bg-bg-surface border border-border"
      >
        <div className="text-center">
          <ImageIcon className="mx-auto size-12 text-text-disabled" />
          <p className="mt-2 text-sm text-text-muted">Video unavailable</p>
        </div>
      </div>
    );
  }
  return (
    <div className="flex items-center justify-center">
      <video
        src={src}
        controls
        autoPlay
        loop
        muted
        className="max-h-[75vh] w-auto rounded-lg"
        onError={() => setFailed(true)}
      />
    </div>
  );
}

function ImageWithFallback({ src }: { src: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div
        className="flex h-full items-center justify-center rounded-lg bg-bg-surface border border-border"
      >
        <div className="text-center">
          <ImageIcon className="mx-auto size-12 text-text-disabled" />
          <p className="mt-2 text-sm text-text-muted">Image unavailable</p>
        </div>
      </div>
    );
  }
  return (
    <div className="flex items-center justify-center">
      <img
        src={src}
        alt="Preview"
        className="max-h-[75vh] w-auto rounded-lg object-contain"
        onError={() => setFailed(true)}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Media display                                                      */
/* ------------------------------------------------------------------ */
function MediaDisplay({
  slidePreviews,
  visualPaths,
  allMediaUrls,
}: {
  slidePreviews: Array<{ url: string }> | null | undefined;
  visualPaths: string | null | undefined;
  allMediaUrls?: string[];
}) {
  // Collect all media sources — slides, visual_paths, allMediaUrls
  const mediaItems = useMemo(() => {
    if (slidePreviews && slidePreviews.length > 0) {
      return slidePreviews.map((s, i) => ({ url: s.url, alt: `Slide ${i + 1}` }));
    }
    if (allMediaUrls && allMediaUrls.length > 0) {
      return allMediaUrls
        .filter((u) => !u.includes("_landscape"))
        .map((u, i) => ({ url: u, alt: `Slide ${i + 1}` }));
    }
    return null;
  }, [slidePreviews, allMediaUrls]);

  // Single video — render VideoWithFallback directly (CarouselViewer only supports <img>)
  if (mediaItems && mediaItems.length === 1 && isVideoUrl(mediaItems[0].url)) {
    return <VideoWithFallback src={mediaItems[0].url} />;
  }

  // Multiple images — carousel
  if (mediaItems && mediaItems.length > 1) {
    return <CarouselViewer images={mediaItems} className="max-h-[75vh]" />;
  }

  // Single image from slides
  if (mediaItems && mediaItems.length === 1) {
    return <ImageWithFallback src={mediaItems[0].url} />;
  }

  // Fallback to visualPaths string
  if (visualPaths) {
    if (isVideoUrl(visualPaths)) {
      return <VideoWithFallback src={visualPaths} />;
    }
    return <ImageWithFallback src={visualPaths} />;
  }

  return (
    <div
      className="flex h-full items-center justify-center rounded-lg bg-bg-surface border border-border"
    >
      <div className="text-center">
        <ImageIcon className="mx-auto size-12 text-text-disabled" />
        <p className="mt-2 text-sm text-text-muted">No media available</p>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Inline feedback form                                               */
/* ------------------------------------------------------------------ */
function FeedbackForm({
  action,
  onSubmit,
  onCancel,
  isPending,
}: {
  action: ReviewAction;
  onSubmit: (issue: string, notes: string) => void;
  onCancel: () => void;
  isPending: boolean;
}) {
  const [issue, setIssue] = useState<IssueValue | "">("");
  const [notes, setNotes] = useState("");

  const config = ACTION_CONFIG[action];

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 8 }}
      className="absolute bottom-full left-1/2 mb-3 w-full max-w-md -translate-x-1/2 rounded-lg p-4 shadow-2xl bg-bg-surface border border-border"
    >
      <p className="mb-3 text-sm font-medium text-text-primary">
        {config.label} — Add feedback
      </p>

      {/* 2026-06-22 Loop 8 close — feedback_category is now REQUIRED
          for Reject/Revise so calibration_logger captures the operator's
          categorical reason every time. Backend (PR #424) has been
          collecting the field since 2026-06-21 but 0 rows were
          populated on prod because the dropdown was skippable. The
          asterisk + disabled-Confirm enforce a meaningful choice. */}
      <label className="mb-1 block text-xs font-medium text-text-secondary">
        Why? <span className="text-red-400">*</span>
      </label>
      <Select value={issue} onValueChange={(val) => setIssue(val as IssueValue)}>
        <SelectTrigger size="sm" className="w-full" aria-required="true">
          <SelectValue placeholder="Select an issue..." />
        </SelectTrigger>
        <SelectContent>
          {ISSUE_OPTIONS.map((opt) => (
            <SelectItem key={opt.value} value={opt.value}>
              {opt.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        placeholder="Additional notes (optional)..."
        className="mt-2 min-h-[60px] resize-none text-sm"
        onKeyDown={(e) => e.stopPropagation()}
      />

      <div className="mt-3 flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel} disabled={isPending}>
          Cancel
        </Button>
        <Button
          size="sm"
          onClick={() => onSubmit(issue, notes)}
          disabled={isPending || !issue}
          className="text-white"
          style={{ background: config.colorVar }}
          title={!issue ? "Select an issue above to enable" : undefined}
        >
          {isPending && <Loader2 className="size-3.5 animate-spin" />}
          Confirm {config.label}
        </Button>
      </div>
    </motion.div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main FocusMode component                                           */
/* ------------------------------------------------------------------ */
export function FocusMode() {
  const navigate = useNavigate();
  const { state, currentItem, advance, goTo, restart, isLoading, isFallback, reviewMutation, nicheId: _nicheId } =
    useFocusReviewQueue();
  const { selectedNicheId } = useNicheStore();

  const [feedbackAction, setFeedbackAction] = useState<ReviewAction | null>(null);
  const feedbackOpenRef = useRef(false);
  // Ref mutation must happen in an effect, not during render
  // (react-hooks/refs).
  useEffect(() => {
    feedbackOpenRef.current = feedbackAction !== null;
  }, [feedbackAction]);

  // Resolve niche-specific DetailView. Note: ``DetailView`` here SELECTS
  // an existing top-level component from the niche registry — it does
  // NOT define a new component (which is what react-hooks/static-components
  // is meant to flag). The lint rule fires at the JSX usage site, so the
  // disable comment lives there (not here).
  const DetailView = useMemo(() => {
    const resolvedId = selectedNicheId === "all" ? "ai_creators" : selectedNicheId;
    return getDetailView(resolvedId as NicheId) ?? GenericDetailView;
  }, [selectedNicheId]);

  const submitAction = useCallback(
    (action: ReviewAction, issue?: string, notes?: string) => {
      if (!currentItem) return;
      // R-73: block double-submit on the same blueprint. The on-screen buttons
      // are disabled while pending, but the keyboard path (a/r/v/s) bypassed
      // that guard — a fast "a a" could fire two reviews before advance() moved
      // off the item. Guarding the single chokepoint covers every entry point.
      if (reviewMutation.isPending) return;

      const body: { action: string; issue?: string; notes?: string } = { action };
      if (issue) body.issue = issue;
      if (notes?.trim()) body.notes = notes.trim();

      reviewMutation.mutate(
        { id: currentItem.id, body },
        {
          onSuccess: () => {
            toast.success(`Blueprint ${action}`, {
              duration: 5000,
              action: {
                label: "Undo",
                onClick: () => toast.info("Undo is not yet supported"),
              },
            });
            setFeedbackAction(null);
            advance(action);
          },
        }
      );
    },
    [currentItem, reviewMutation, advance]
  );

  const handleAction = useCallback(
    (action: ReviewAction) => {
      if (ACTION_CONFIG[action].needsFeedback) {
        setFeedbackAction(action);
        return;
      }
      submitAction(action);
    },
    [submitAction]
  );

  const handleFeedbackSubmit = useCallback(
    (issue: string, notes: string) => {
      if (!feedbackAction) return;
      submitAction(feedbackAction, issue, notes);
    },
    [feedbackAction, submitAction]
  );

  const handleFeedbackCancel = useCallback(() => {
    setFeedbackAction(null);
  }, []);

  /* -------- Keyboard shortcuts -------- */
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      const el = document.activeElement;
      if (el) {
        const tag = el.tagName.toLowerCase();
        if (tag === "input" || tag === "textarea" || tag === "select" || (el as HTMLElement).isContentEditable) {
          return;
        }
      }

      if (feedbackOpenRef.current) {
        if (e.key === "Escape") {
          e.preventDefault();
          setFeedbackAction(null);
        }
        return;
      }

      switch (e.key) {
        case "a":
          e.preventDefault();
          handleAction("approved");
          break;
        case "r":
          e.preventDefault();
          handleAction("rejected");
          break;
        case "v":
          e.preventDefault();
          handleAction("revised");
          break;
        case "s":
          e.preventDefault();
          handleAction("skipped");
          break;
        case "e":
          e.preventDefault();
          if (currentItem) {
            navigate(`/blueprints/${currentItem.id}`);
          }
          break;
        case "Escape":
          e.preventDefault();
          navigate("/");
          break;
        case "ArrowLeft":
          e.preventDefault();
          goTo(state.currentIndex - 1);
          break;
        case "ArrowRight":
          e.preventDefault();
          goTo(state.currentIndex + 1);
          break;
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handleAction, navigate, goTo, state.currentIndex, currentItem]);

  /* -------- Render -------- */

  if (isLoading) return <FocusLoading />;
  if (state.items.length === 0) return <FocusEmpty />;
  if (isFallback) return <FocusFallback items={state.items} onBack={() => navigate("/")} />;
  if (state.isComplete) {
    return (
      <CompletionScreen
        approved={state.approved}
        rejected={state.rejected}
        revised={state.revised}
        skipped={state.skipped}
        onRestart={restart}
      />
    );
  }

  return (
    <div
      className="flex h-screen w-screen flex-col overflow-hidden bg-bg-primary"
    >
      {/* ---- Top bar ---- */}
      <div
        className="flex shrink-0 items-center justify-between px-4 py-2 bg-bg-primary border-b border-border"
      >
        <div className="flex items-center gap-3">
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => navigate("/")}
                  className="text-text-muted"
                >
                  <ArrowLeft className="size-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Back to Dashboard (Esc)</TooltipContent>
            </Tooltip>
          </TooltipProvider>
          <h1 className="text-sm font-semibold text-text-primary">
            Focus Review
          </h1>
          <span className="text-sm text-text-muted">
            {state.currentIndex + 1} / {state.items.length}
          </span>

          {/* AUTO #1 (2026-06-13): gate verdict for THIS blueprint.
              Read-only preview — operator still makes the call. Sits next
              to the counter so the verdict is visible at the moment the
              operator forms their own opinion. */}
          {currentItem?.id && (
            <AutoApprovalBadge blueprintId={String(currentItem.id)} />
          )}

          {/* Prev/Next mini buttons */}
          <div className="flex items-center gap-1 ml-2">
            <button
              onClick={() => goTo(state.currentIndex - 1)}
              disabled={state.currentIndex === 0}
              className="rounded p-1 transition-colors disabled:opacity-30 text-text-muted"
            >
              <ChevronLeft className="size-4" />
            </button>
            <button
              onClick={() => goTo(state.currentIndex + 1)}
              disabled={state.currentIndex >= state.items.length - 1}
              className="rounded p-1 transition-colors disabled:opacity-30 text-text-muted"
            >
              <ChevronRight className="size-4" />
            </button>
          </div>
        </div>

        <div className="flex items-center gap-1.5 text-xs text-text-disabled">
          <Keyboard className="size-3.5" />
          <span>a=approve r=reject v=revise s=skip e=edit esc=exit</span>
        </div>
      </div>

      {/* ---- Center content ---- */}
      <div className="flex min-h-0 flex-1">
        <AnimatePresence mode="wait">
          <motion.div
            key={currentItem?.id ?? "empty"}
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -20 }}
            transition={{ duration: 0.2 }}
            className="flex min-h-0 flex-1 gap-6 p-6"
          >
            {/* Left — Media (60%) */}
            <div className="flex w-[60%] items-center justify-center">
              {currentItem ? (
                <MediaDisplay
                  slidePreviews={currentItem.slide_previews}
                  visualPaths={currentItem.visual_paths}
                  allMediaUrls={currentItem.all_media_urls}
                />
              ) : (
                <Skeleton className="h-96 w-full rounded-lg" />
              )}
            </div>

            {/* Right — Niche-specific DetailView (40%). The lookup happens
                in a useMemo above; DetailView is SELECTED from a static
                registry, not defined in-component. The lint rule can't
                see that distinction so we suppress at the usage site. */}
            <div className="flex w-[40%] flex-col justify-center">
              {currentItem ? (
                // eslint-disable-next-line react-hooks/static-components
                <DetailView item={currentItem} />
              ) : (
                <div className="space-y-3">
                  <Skeleton className="h-6 w-3/4" />
                  <Skeleton className="h-20 w-full" />
                  <Skeleton className="h-4 w-1/2" />
                </div>
              )}
            </div>
          </motion.div>
        </AnimatePresence>
      </div>

      {/* ---- Action bar ---- */}
      <div
        className="relative shrink-0 px-6 py-4 bg-bg-primary border-t border-border"
      >
        <AnimatePresence>
          {feedbackAction && (
            <FeedbackForm
              action={feedbackAction}
              onSubmit={handleFeedbackSubmit}
              onCancel={handleFeedbackCancel}
              isPending={reviewMutation.isPending}
            />
          )}
        </AnimatePresence>

        <div className="mx-auto flex max-w-2xl gap-3">
          {(
            Object.entries(ACTION_CONFIG) as Array<
              [ReviewAction, (typeof ACTION_CONFIG)[ReviewAction]]
            >
          ).map(([action, config]) => {
            const Icon = config.icon;
            const isActive = feedbackAction === action;

            return (
              <button
                key={action}
                disabled={reviewMutation.isPending}
                className={cn(
                  "flex h-14 flex-1 items-center justify-center gap-2 rounded-lg text-base font-medium transition-all",
                  reviewMutation.isPending && "opacity-50 cursor-not-allowed",
                )}
                style={{
                  background: isActive
                    ? `color-mix(in srgb, ${config.colorVar} 20%, transparent)`
                    : "var(--surface-2)",
                  border: `1px solid ${isActive ? config.colorVar : "var(--border)"}`,
                  color: isActive ? config.colorVar : "var(--text-secondary)",
                }}
                onClick={() => handleAction(action as ReviewAction)}
              >
                {reviewMutation.isPending && feedbackAction === action ? (
                  <Loader2 className="size-5 animate-spin" />
                ) : (
                  <Icon className="size-5" />
                )}
                <span>{config.label}</span>
                <kbd
                  className="ml-1 rounded px-1.5 py-0.5 text-[10px] font-mono bg-bg-raised text-text-disabled"
                >
                  {config.shortcut}
                </kbd>
              </button>
            );
          })}
        </div>
      </div>

      {/* ---- Progress bar ---- */}
      <ProgressBar
        current={state.reviewed}
        total={state.items.length}
        approved={state.approved}
        rejected={state.rejected}
        revised={state.revised}
        skipped={state.skipped}
      />
    </div>
  );
}
