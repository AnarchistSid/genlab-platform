/**
 * BulkReview — 5-blueprints-at-a-time review surface.
 *
 * Built 2026-06-14 to accelerate the operator review marathon after a
 * deep-dive surfaced "operator review is the bottleneck, not gate
 * accuracy" (see [[session-2026-06-13-autonomy-deep-dive-v2]]).
 *
 * Throughput compared to focus-mode:
 *   - focus-mode: 1 blueprint at a time. ~60s/each (open, watch, decide).
 *   - bulk-review: 5 at a time, skim grid, select & approve.
 *     ~15s scan + ~5s for "A". ~20s for 5 → ~7-15x speedup.
 *
 * Keyboard shortcuts (the whole point — no mouse required):
 *   1..5      Toggle selection of the Nth visible card
 *   a         Approve all currently SELECTED blueprints
 *   r         Reject all selected (with a confirmation toast)
 *   s         Skip all selected (action_taken=skipped, stays in queue)
 *   Shift+A   Approve all visible (skip the selection step)
 *   Enter     Load the next batch (advance window of 5)
 *   Esc       Clear selection
 *
 * Each card surfaces the AutoApprovalBadge so the operator can A/B
 * their own judgment against the gate's verdict — the foundation that
 * the calibration logger (PR #177) measures, that will eventually
 * justify AUTO #2 enforcement.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ArrowRight, Check, X, SkipForward, Eye } from "lucide-react";

import { AutoApprovalBadge } from "@/components/review/auto-approval-badge";
import { EnsembleBadge } from "@/components/review/ensemble-badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useFocusReviewQueue } from "@/hooks/use-focus-review";
import {
  useBatchApproveSchedule,
  useBatchReview,
} from "@/hooks/use-blueprints";
import type { Blueprint } from "@/api/types";

const BATCH_SIZE = 5;

/* ------------------------------------------------------------------ */
/*  Card                                                              */
/* ------------------------------------------------------------------ */

interface CardProps {
  blueprint: Blueprint;
  index: number; // 1..BATCH_SIZE for the keyboard hint
  selected: boolean;
  onToggle: () => void;
}

function BulkCard({ blueprint, index, selected, onToggle }: CardProps) {
  const hook = blueprint.hook_text || blueprint.title || "(no hook)";
  const niche = blueprint.niche_id ?? "?";

  return (
    <Card
      data-testid={`bulk-card-${blueprint.id}`}
      onClick={onToggle}
      className={[
        "flex cursor-pointer flex-col gap-2 border-2 p-3 transition-colors",
        selected
          ? "border-green-500 bg-green-500/10"
          : "border-border hover:border-blue-500/50",
      ].join(" ")}
    >
      <div className="flex items-center justify-between text-xs text-text-muted">
        <span className="rounded bg-card-foreground/10 px-1.5 py-0.5 font-mono uppercase">
          {niche}
        </span>
        <kbd className="rounded border border-border bg-background px-1.5 py-0.5 font-mono">
          {index}
        </kbd>
      </div>

      <p className="line-clamp-3 text-sm font-medium">{hook}</p>

      <div className="flex flex-wrap items-center gap-2">
        <AutoApprovalBadge blueprintId={blueprint.id} />
        <EnsembleBadge blueprintId={blueprint.id} />
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  Main view                                                         */
/* ------------------------------------------------------------------ */

export function BulkReview() {
  const navigate = useNavigate();
  const { state, isLoading } = useFocusReviewQueue();
  const items = state.items;
  const total = items.length;

  const [windowStart, setWindowStart] = useState(0);
  const visible = useMemo(
    () => items.slice(windowStart, windowStart + BATCH_SIZE),
    [items, windowStart]
  );

  // selectedIds: keep as Set<string> so we can toggle by id, not index
  // (rendering may reorder if the underlying list updates).
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const batchApprove = useBatchApproveSchedule();
  const batchReview = useBatchReview();

  /* --------------------------------------------------------------- *
   * H2 (2026-07-08 audit): double-tap confirmation for Shift+A.
   *
   * Batch-approving 5 blueprints on one keystroke is the highest-
   * blast-radius action in bulk-review — an errant Shift+A during
   * a stressful review session ships 5 posts. Yet a full modal
   * would break the keyboard-first velocity the whole feature is
   * built around.
   *
   * Compromise: first Shift+A "arms" the action + toasts. Second
   * Shift+A within 3s proceeds. Any other keystroke or 3s timeout
   * disarms silently. Fast operators pay two keystrokes instead of
   * one; accidental single-taps are a no-op.
   *
   * Implementation: uses refs (not React state) so the arm flag can
   * be read + written inside the same handler tick without waiting
   * for React to flush. A useState-based version fails the pin test
   * because the second Shift+A fires before the first's state
   * update commits.
   * --------------------------------------------------------------- */
  const shiftAArmedRef = useRef(false);
  const shiftATimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const disarmShiftA = useCallback(() => {
    if (shiftATimeoutRef.current !== null) {
      clearTimeout(shiftATimeoutRef.current);
      shiftATimeoutRef.current = null;
    }
    shiftAArmedRef.current = false;
  }, []);

  // Clean up the pending arm-timeout on unmount so timers don't
  // fire against a stale component if the operator navigates away
  // during the 3s window.
  useEffect(() => {
    return () => {
      if (shiftATimeoutRef.current !== null) {
        clearTimeout(shiftATimeoutRef.current);
      }
    };
  }, []);

  const clampWindowStart = useCallback(
    (idx: number) => {
      // Don't let the window stranded past the end of the list.
      if (idx >= items.length) return Math.max(0, items.length - 1);
      if (idx < 0) return 0;
      return idx;
    },
    [items.length]
  );

  const advanceBatch = useCallback(() => {
    setSelectedIds(new Set());
    setWindowStart((prev) => clampWindowStart(prev + BATCH_SIZE));
  }, [clampWindowStart]);

  const toggleByPosition = useCallback(
    (pos: number) => {
      // pos is 0-indexed; key bindings hand us 1-indexed and adjust.
      const bp = visible[pos];
      if (!bp) return;
      setSelectedIds((prev) => {
        const next = new Set(prev);
        if (next.has(bp.id)) next.delete(bp.id);
        else next.add(bp.id);
        return next;
      });
    },
    [visible]
  );

  const clearSelection = useCallback(() => setSelectedIds(new Set()), []);

  const submitApproveSelected = useCallback(() => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) {
      toast.info("No blueprints selected — press 1-5 to select, or Shift+A to approve all visible");
      return;
    }
    if (batchApprove.isPending) return;
    batchApprove.mutate(ids, {
      onSuccess: () => {
        clearSelection();
        advanceBatch();
      },
    });
  }, [selectedIds, batchApprove, clearSelection, advanceBatch]);

  const submitReviewSelected = useCallback(
    (action: "rejected" | "skipped") => {
      const ids = Array.from(selectedIds);
      if (ids.length === 0) {
        toast.info("No blueprints selected — press 1-5 first");
        return;
      }
      if (batchReview.isPending) return;
      batchReview.mutate(
        { ids, action },
        {
          onSuccess: () => {
            clearSelection();
            advanceBatch();
          },
        }
      );
    },
    [selectedIds, batchReview, clearSelection, advanceBatch]
  );

  const submitApproveAllVisible = useCallback(() => {
    const ids = visible.map((bp) => bp.id);
    if (ids.length === 0) return;
    if (batchApprove.isPending) return;

    // H2 confirmation: first press arms + toasts, second within 3s
    // proceeds. Ref-based so both branches see the same value in
    // rapid succession (no stale-closure hazard).
    if (!shiftAArmedRef.current) {
      shiftAArmedRef.current = true;
      toast.info(
        `Press Shift+A again within 3s to approve ALL ${ids.length} visible`,
      );
      if (shiftATimeoutRef.current !== null) {
        clearTimeout(shiftATimeoutRef.current);
      }
      shiftATimeoutRef.current = setTimeout(() => {
        shiftAArmedRef.current = false;
        shiftATimeoutRef.current = null;
      }, 3000);
      return;
    }

    // Second Shift+A inside the arm window — proceed.
    disarmShiftA();
    batchApprove.mutate(ids, {
      onSuccess: () => {
        clearSelection();
        advanceBatch();
      },
    });
  }, [visible, batchApprove, clearSelection, advanceBatch, disarmShiftA]);

  /* -------- Keyboard shortcuts -------- */
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      const el = document.activeElement;
      if (el) {
        const tag = el.tagName.toLowerCase();
        if (
          tag === "input" ||
          tag === "textarea" ||
          tag === "select" ||
          (el as HTMLElement).isContentEditable
        ) {
          return;
        }
      }

      // Anything that isn't Shift+A disarms the H2 confirm state.
      // Modifier keys ("Shift", "Control", "Alt", "Meta") emit their
      // own keydown events as part of a chord — treat those as
      // in-progress input, NOT as "different action" that disarms.
      // Otherwise the second Shift+A's Shift keydown would disarm
      // before the A even arrives.
      //
      // MUST run BEFORE the number-key early-return below, otherwise
      // pressing "1" would toggle selection but leave the arm intact.
      const isModifierKey =
        e.key === "Shift" ||
        e.key === "Control" ||
        e.key === "Alt" ||
        e.key === "Meta";
      if (!isModifierKey && e.key !== "A" && shiftAArmedRef.current) {
        disarmShiftA();
      }

      // Number keys 1..BATCH_SIZE: toggle the Nth visible card
      if (e.key >= "1" && e.key <= String(BATCH_SIZE)) {
        const pos = Number(e.key) - 1;
        e.preventDefault();
        toggleByPosition(pos);
        return;
      }

      switch (e.key) {
        case "a":
          e.preventDefault();
          submitApproveSelected();
          break;
        case "A": // shift+A — approve all visible (double-tap confirm, see H2)
          e.preventDefault();
          submitApproveAllVisible();
          break;
        case "r":
          e.preventDefault();
          submitReviewSelected("rejected");
          break;
        case "s":
          e.preventDefault();
          submitReviewSelected("skipped");
          break;
        case "Enter":
          e.preventDefault();
          advanceBatch();
          break;
        case "Escape":
          e.preventDefault();
          clearSelection();
          break;
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [
    toggleByPosition,
    submitApproveSelected,
    submitApproveAllVisible,
    submitReviewSelected,
    advanceBatch,
    clearSelection,
    disarmShiftA,
  ]);

  /* -------- Render -------- */

  if (isLoading) {
    return (
      <div className="flex h-[60vh] items-center justify-center text-text-muted">
        Loading review queue…
      </div>
    );
  }

  if (total === 0) {
    return (
      <div className="flex h-[60vh] flex-col items-center justify-center gap-3 text-center">
        <Check className="size-12 text-green-500" />
        <p className="text-lg font-medium">Queue is empty</p>
        <p className="text-sm text-text-muted">
          All pending reviews have been processed. Check back after the next pipeline run.
        </p>
        <Button variant="ghost" onClick={() => navigate("/")}>
          Back to mission control
        </Button>
      </div>
    );
  }

  if (visible.length === 0) {
    // Reached the end of the queue while paginating.
    return (
      <div className="flex h-[60vh] flex-col items-center justify-center gap-3 text-center">
        <Check className="size-12 text-green-500" />
        <p className="text-lg font-medium">End of queue</p>
        <Button variant="ghost" onClick={() => setWindowStart(0)}>
          Restart from top
        </Button>
      </div>
    );
  }

  const selectedCount = selectedIds.size;

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4 p-4">
      {/* Header — counts + the keyboard legend */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Bulk Review</h1>
          <p className="text-sm text-text-muted">
            Showing {windowStart + 1}–{Math.min(windowStart + BATCH_SIZE, total)} of {total} ·
            {" "}
            <span className="font-mono">{selectedCount}</span> selected
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={submitApproveAllVisible}
            disabled={batchApprove.isPending}
          >
            <Check className="size-3.5" />
            Approve all visible <kbd className="ml-1 text-xs">⇧A</kbd>
          </Button>
          <Button variant="ghost" size="sm" onClick={advanceBatch}>
            Next batch <ArrowRight className="size-3.5" />
            <kbd className="ml-1 text-xs">⏎</kbd>
          </Button>
        </div>
      </div>

      {/* Grid */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-5">
        {visible.map((bp, i) => (
          <BulkCard
            key={bp.id}
            blueprint={bp}
            index={i + 1}
            selected={selectedIds.has(bp.id)}
            onToggle={() => toggleByPosition(i)}
          />
        ))}
      </div>

      {/* Action bar — fires on the SELECTED set */}
      <div className="sticky bottom-2 mt-2 flex items-center justify-center gap-2 rounded-lg border border-border bg-background/95 p-2 backdrop-blur">
        <Button
          onClick={submitApproveSelected}
          disabled={selectedCount === 0 || batchApprove.isPending}
          style={{ background: "var(--color-green)" }}
          className="text-white"
        >
          <Check className="size-3.5" />
          Approve selected ({selectedCount}) <kbd className="ml-1 text-xs">A</kbd>
        </Button>
        <Button
          variant="outline"
          onClick={() => submitReviewSelected("skipped")}
          disabled={selectedCount === 0 || batchReview.isPending}
        >
          <SkipForward className="size-3.5" />
          Skip <kbd className="ml-1 text-xs">S</kbd>
        </Button>
        <Button
          variant="outline"
          onClick={() => submitReviewSelected("rejected")}
          disabled={selectedCount === 0 || batchReview.isPending}
          className="text-red-600"
        >
          <X className="size-3.5" />
          Reject <kbd className="ml-1 text-xs">R</kbd>
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate("/focus-review")}
        >
          <Eye className="size-3.5" />
          Switch to focus mode
        </Button>
      </div>
    </div>
  );
}
