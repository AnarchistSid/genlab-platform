import { useState, useMemo, useCallback } from "react";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import type { DragEndEvent, DragStartEvent } from "@dnd-kit/core";
import {
  ChevronLeft,
  ChevronRight,
  CalendarDays,
  CalendarPlus,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area";
import { TimeSlot } from "@/components/schedule/time-slot";
import { DragCard, DragCardOverlay } from "@/components/schedule/drag-card";
import { AssignSlotDialog } from "@/components/schedule/assign-slot-dialog";
import { EmptyState } from "@/components/shared/empty-state";
import { SchedulePreview } from "@/components/schedule/schedule-preview";
import { useSchedule, useReorderSchedule } from "@/hooks/use-schedule";
import { useBlueprints } from "@/hooks/use-blueprints";
import { cn } from "@/lib/utils";
import { getTodayISO } from "@/lib/format";
import { getActiveNiches, getNicheInfo } from "@/niches/registry";
import type { Blueprint, ScheduleDay } from "@/api/types";

// ---------------------------------------------------------------------------
// Date helpers
// ---------------------------------------------------------------------------

function addDays(date: Date, days: number): Date {
  const result = new Date(date);
  result.setDate(result.getDate() + days);
  return result;
}

function getWeekStart(date: Date): Date {
  const d = new Date(date);
  const day = d.getDay(); // 0 = Sunday
  const diff = day === 0 ? -6 : 1 - day; // shift to Monday
  d.setDate(d.getDate() + diff);
  d.setHours(0, 0, 0, 0);
  return d;
}

function formatDayHeader(date: Date): string {
  const dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  return `${dayNames[date.getDay()]} ${date.getDate()}`;
}

function toISO(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

// Default slots — overridden by API response when available
const DEFAULT_TIME_SLOTS = ["12:00"];

/** Short labels for schedule board niche pills. */
const SHORT_LABELS: Record<string, string> = {
  ai_creators: "BB", gaming: "CR", sports: "CW", movies: "SR", anime: "FD",
};

/** All active niches, used to create per-niche rows in the schedule grid. */
const NICHE_ROWS = getActiveNiches().map(n => ({
  id: n.id,
  label: SHORT_LABELS[n.id] ?? n.id.slice(0, 2).toUpperCase(),
  color: n.accentHex,
}));

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ScheduleBoard() {
  const [weekStart, setWeekStart] = useState<Date>(() =>
    getWeekStart(new Date()),
  );

  // Active drag state
  const [activeBlueprint, setActiveBlueprint] = useState<Blueprint | null>(
    null,
  );

  // Quick-assign dialog state
  const [assignTarget, setAssignTarget] = useState<Blueprint | null>(null);

  // Preview panel state
  const [previewBlueprint, setPreviewBlueprint] = useState<Blueprint | null>(null);

  // Build the full 7-day range (Mon–Sun), displayed in a scrollable 5-column grid
  const days = useMemo(
    () => Array.from({ length: 7 }, (_, i) => addDays(weekStart, i)),
    [weekStart],
  );

  const fromISO = toISO(days[0]);
  const toISO_ = toISO(days[6]);
  const todayISO = getTodayISO();

  // Fetch schedule data
  const { data: scheduleData, isLoading } = useSchedule(fromISO, toISO_);
  const reorder = useReorderSchedule();

  // Use schedule_slots from API response, fall back to default
  const timeSlots: string[] = (scheduleData as Record<string, unknown>)?.schedule_slots as string[] ?? DEFAULT_TIME_SLOTS;

  // Fetch unscheduled VISUAL_READY posts (rendered + approved, ready to schedule).
  // DRAFTED posts are excluded — they need rendering before they can be scheduled.
  const { data: unscheduledData } = useBlueprints({
    status: "VISUAL_READY",
    action_taken: "approved",
    unscheduled: "true",
  });

  const unscheduledBlueprints: Blueprint[] = useMemo(
    () =>
      (unscheduledData?.data ?? []).filter(
        (bp) => !bp.scheduled_for,
      ),
    [unscheduledData],
  );

  // Index schedule days by ISO date for O(1) lookup
  const scheduleByDate = useMemo(() => {
    const map = new Map<string, ScheduleDay>();
    for (const day of scheduleData?.data ?? []) {
      map.set(day.date, day);
    }
    return map;
  }, [scheduleData]);

  // dnd-kit sensors
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  );

  // Navigation
  const goToPrevWeek = useCallback(
    () => setWeekStart((prev) => addDays(prev, -7)),
    [],
  );
  const goToNextWeek = useCallback(
    () => setWeekStart((prev) => addDays(prev, 7)),
    [],
  );
  const goToToday = useCallback(
    () => setWeekStart(getWeekStart(new Date())),
    [],
  );

  // R-73: shared niche-collision check for BOTH the drag path and the
  // quick-assign dialog (the dialog previously committed with no warning, so the
  // same niche could be silently double-booked on a date). Returns true if it's
  // OK to proceed (no conflict, or the user confirmed the override).
  const confirmNoNicheCollision = useCallback(
    (nicheId: string | undefined, targetDate: string, blueprintId: string): boolean => {
      if (!nicheId) return true;
      const existingSlots = scheduleByDate.get(targetDate)?.slots ?? [];
      const conflict = existingSlots.find(
        (s) => s.niche_id === nicheId && s.status !== "empty" && s.blueprint?.id !== blueprintId,
      );
      if (!conflict) return true;
      const nicheLabel = getNicheInfo(nicheId).label;
      return window.confirm(`${nicheLabel} already has a post on ${targetDate}. Schedule anyway?`);
    },
    [scheduleByDate],
  );

  // Drag handlers
  const handleDragStart = useCallback(
    (event: DragStartEvent) => {
      const bp = event.active.data.current?.blueprint as Blueprint | undefined;
      setActiveBlueprint(bp ?? null);
    },
    [],
  );

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      setActiveBlueprint(null);

      const { active, over } = event;
      if (!over) return;

      // Don't allow reordering of published blueprints
      const draggedBp = active.data.current?.blueprint as Blueprint | undefined;
      if (draggedBp?.status === "PUBLISHED") return;

      const blueprintId = active.id as string;
      const overData = over.data.current as
        | { time: string; date: string }
        | undefined;

      if (!overData) return;

      // Skip if dropped on the same slot it came from
      const sourceSlotId = `${draggedBp?.scheduled_for?.slice(0, 10)}_${overData.time}`;
      if (over.id === sourceSlotId && draggedBp?.scheduled_for?.slice(0, 10) === overData.date) return;

      // Conflict detection — warn if same niche already has a post on target date
      if (!confirmNoNicheCollision(draggedBp?.niche_id, overData.date, blueprintId)) return;

      reorder.mutate({
        blueprint_id: blueprintId,
        to_slot: overData.time,
        to_date: overData.date,
      });
    },
    [reorder, confirmNoNicheCollision],
  );

  const handleDragCancel = useCallback(() => {
    setActiveBlueprint(null);
  }, []);

  // Quick-assign handler
  const handleQuickAssign = useCallback(
    (blueprintId: string, date: string, slot: string) => {
      // R-73: apply the same niche-collision warning the drag path has. Resolve
      // the niche from the unscheduled list (the dialog's source) or any already
      // scheduled slot, then confirm before booking.
      const nicheId =
        unscheduledBlueprints.find((b) => b.id === blueprintId)?.niche_id ??
        scheduleByDate.get(date)?.slots.find((s) => s.blueprint?.id === blueprintId)?.niche_id;
      if (!confirmNoNicheCollision(nicheId, date, blueprintId)) return;

      reorder.mutate({
        blueprint_id: blueprintId,
        to_slot: slot,
        to_date: date,
      });
    },
    [reorder, confirmNoNicheCollision, unscheduledBlueprints, scheduleByDate],
  );

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  return (
    <DndContext
      sensors={sensors}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
      onDragCancel={handleDragCancel}
    >
      <div className="space-y-4">
        {/* Navigation bar */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Button variant="outline" size="icon" onClick={goToPrevWeek} aria-label="Previous week">
              <ChevronLeft className="size-4" />
            </Button>
            <Button variant="outline" size="sm" onClick={goToToday}>
              Today
            </Button>
            <Button variant="outline" size="icon" onClick={goToNextWeek} aria-label="Next week">
              <ChevronRight className="size-4" />
            </Button>
          </div>
          <span className="text-sm font-medium text-text-secondary">
            {days[0].toLocaleDateString("en-US", {
              month: "short",
              day: "numeric",
            })}{" "}
            &ndash;{" "}
            {days[6].toLocaleDateString("en-US", {
              month: "short",
              day: "numeric",
              year: "numeric",
            })}
          </span>
        </div>

        {/* Loading skeleton */}
        {isLoading && <BoardSkeleton />}

        {/* Week grid — 5 niche rows per day */}
        {!isLoading && (
          <ScrollArea className="w-full">
            {/* Week summary bar */}
            {(() => {
              const weekFilled = days.reduce((sum, day) => {
                const iso = toISO(day);
                const slots = scheduleByDate.get(iso)?.slots ?? [];
                return sum + slots.filter((s) => s.status !== "empty").length;
              }, 0);
              const weekTotal = days.length * NICHE_ROWS.length;
              const pct = Math.round((weekFilled / weekTotal) * 100);
              return (
                <div className="mb-4 flex items-center gap-3 rounded-lg border border-border bg-bg-surface px-4 py-3">
                  <div className="flex-1">
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="text-xs font-medium text-text-muted">Week Coverage</span>
                      <span className="text-xs font-bold text-text-primary">{weekFilled}/{weekTotal} slots filled</span>
                    </div>
                    <div className="h-2 rounded-full bg-bg-elevated overflow-hidden">
                      <div
                        className="h-full rounded-full transition-all duration-500"
                        style={{
                          width: `${pct}%`,
                          backgroundColor: pct === 100 ? "#22c55e" : pct >= 60 ? "#6366f1" : "#f59e0b",
                        }}
                      />
                    </div>
                  </div>
                  <div className="text-2xl font-bold" style={{ color: pct === 100 ? "#22c55e" : pct >= 60 ? "#6366f1" : "#f59e0b" }}>
                    {pct}%
                  </div>
                </div>
              );
            })()}

            <div className="flex gap-3" style={{ minWidth: "max-content" }}>
              {days.map((day) => {
                const iso = toISO(day);
                const scheduleDay = scheduleByDate.get(iso);
                const slots = scheduleDay?.slots ?? [];
                const isToday = iso === todayISO;
                const isPast = iso < todayISO;

                const filledCount = slots.filter(
                  (s) => s.status !== "empty",
                ).length;

                return (
                  <div
                    key={iso}
                    style={{ width: "240px", minWidth: "240px" }}
                    className={cn(
                      "shrink-0 space-y-1.5 rounded-lg border p-2 transition-colors",
                      isToday
                        ? "border-indigo-500/40 bg-indigo-500/5 shadow-sm shadow-indigo-500/10"
                        : isPast
                          ? "border-border/50 bg-bg-primary/50 opacity-60"
                          : "border-border bg-bg-primary",
                    )}
                  >
                    {/* Day header */}
                    <div className="flex items-center justify-between px-1 pb-1 border-b border-border/50">
                      <div>
                        <span
                          className={cn(
                            "text-[11px] font-bold uppercase tracking-wide",
                            isToday ? "text-indigo-400" : "text-text-secondary",
                          )}
                        >
                          {formatDayHeader(day)}
                        </span>
                        {isToday && (
                          <span className="ml-1.5 rounded-full bg-indigo-500 px-1.5 py-0.5 text-[9px] font-bold text-white">
                            TODAY
                          </span>
                        )}
                      </div>
                      <span
                        className={cn(
                          "flex size-6 items-center justify-center rounded-full text-[10px] font-bold",
                          filledCount === NICHE_ROWS.length
                            ? "bg-green-500/20 text-green-400"
                            : filledCount > 0
                              ? "bg-indigo-500/15 text-indigo-400"
                              : "bg-bg-hover text-text-disabled",
                        )}
                      >
                        {filledCount}/{NICHE_ROWS.length}
                      </span>
                    </div>

                    {/* One row per niche */}
                    {slots.map((slot, idx) => {
                      const nicheId = slot.niche_id ?? NICHE_ROWS[idx]?.id ?? "";
                      const nicheRow = NICHE_ROWS.find((n) => n.id === nicheId);

                      return (
                        <div
                          key={`${iso}_${nicheId}`}
                          className="flex items-stretch gap-1.5"
                        >
                          {/* Niche label pill */}
                          <div
                            className="flex w-8 shrink-0 items-center justify-center rounded-md text-[9px] font-bold text-white"
                            style={{ backgroundColor: nicheRow?.color ?? "#555" }}
                            title={nicheId}
                          >
                            {nicheRow?.label ?? "?"}
                          </div>
                          {/* Slot */}
                          <div className="flex-1">
                            <TimeSlot
                              time={slot.time}
                              date={iso}
                              blueprint={slot.blueprint ?? null}
                              status={slot.status}
                              nicheColor={nicheRow?.color}
                              onPreview={setPreviewBlueprint}
                            />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                );
              })}
            </div>
            <ScrollBar orientation="horizontal" />
          </ScrollArea>
        )}

        {/* Unscheduled pool */}
        {!isLoading && (
          <div className="space-y-2">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-text-muted">
              Unscheduled ({unscheduledBlueprints.length})
            </h3>

            {unscheduledBlueprints.length === 0 ? (
              <EmptyState
                icon={CalendarDays}
                title="All caught up"
                description="No unscheduled blueprints here. Check Content Review for posts awaiting approval."
                className="border-dashed"
              />
            ) : (
              <ScrollArea className="w-full">
                <div className="flex gap-2 pb-2">
                  {unscheduledBlueprints.map((bp) => {
                    const nicheRow = NICHE_ROWS.find((n) => n.id === bp.niche_id);
                    return (
                      <div key={bp.id} className="w-60 shrink-0 space-y-1">
                        {/* Niche badge */}
                        {nicheRow && (
                          <div
                            className="inline-block rounded px-1.5 py-0.5 text-[10px] font-bold text-white"
                            style={{ backgroundColor: nicheRow.color }}
                          >
                            {nicheRow.label}
                          </div>
                        )}
                        <DragCard blueprint={bp} />
                        {/* Quick-assign button */}
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setAssignTarget(bp);
                          }}
                          className="flex w-full items-center justify-center gap-1.5 rounded-md border border-dashed border-bg-hover bg-bg-primary py-1 text-[11px] text-text-muted transition-colors hover:border-indigo-500/40 hover:bg-indigo-500/5 hover:text-indigo-400"
                        >
                          <CalendarPlus className="size-3" />
                          Assign to slot
                        </button>
                      </div>
                    );
                  })}
                </div>
                <ScrollBar orientation="horizontal" />
              </ScrollArea>
            )}
          </div>
        )}
      </div>

      {/* Drag overlay */}
      <DragOverlay dropAnimation={null}>
        {activeBlueprint ? (
          <DragCardOverlay blueprint={activeBlueprint} compact />
        ) : null}
      </DragOverlay>

      {/* Preview panel */}
      <SchedulePreview
        blueprint={previewBlueprint}
        onClose={() => setPreviewBlueprint(null)}
      />

      {/* Quick-assign dialog */}
      <AssignSlotDialog
        blueprint={assignTarget}
        open={!!assignTarget}
        onOpenChange={(open) => {
          if (!open) setAssignTarget(null);
        }}
        onAssign={handleQuickAssign}
        scheduleSlots={timeSlots}
      />
    </DndContext>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function BoardSkeleton() {
  return (
    <div className="flex gap-3" style={{ minWidth: "max-content" }}>
      {Array.from({ length: 7 }).map((_, col) => (
        <div key={col} className="space-y-1.5" style={{ width: "240px", minWidth: "240px" }}>
          <Skeleton className="h-8 w-full rounded-md" />
          {Array.from({ length: 5 }).map((__, row) => (
            <Skeleton key={row} className="h-[96px] w-full rounded-md" />
          ))}
        </div>
      ))}
    </div>
  );
}
