import { useState, useMemo, useCallback } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/shared/status-badge";
import { useSchedule } from "@/hooks/use-schedule";
import { cn } from "@/lib/utils";
import { getTodayISO } from "@/lib/format";
import type { ScheduleDay, ScheduleSlot } from "@/api/types";

// ---------------------------------------------------------------------------
// Date helpers
// ---------------------------------------------------------------------------

function getMonthStart(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

function toISO(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function addMonths(date: Date, months: number): Date {
  const result = new Date(date);
  result.setMonth(result.getMonth() + months);
  return result;
}

/** Returns calendar grid dates including padding from prev/next month (Mon-start). */
function buildCalendarGrid(year: number, month: number): Date[] {
  const firstDay = new Date(year, month, 1);
  const lastDay = new Date(year, month + 1, 0);

  // dayOfWeek: 0=Sun, convert to Mon-start (0=Mon ... 6=Sun)
  const startDow = (firstDay.getDay() + 6) % 7;

  const dates: Date[] = [];

  // Padding days from previous month
  for (let i = startDow - 1; i >= 0; i--) {
    const d = new Date(year, month, 1);
    d.setDate(d.getDate() - i - 1);
    dates.push(d);
  }

  // Current month days
  for (let d = 1; d <= lastDay.getDate(); d++) {
    dates.push(new Date(year, month, d));
  }

  // Padding days from next month to complete the last row
  const remaining = (7 - ((dates.length) % 7)) % 7;
  for (let i = 1; i <= remaining; i++) {
    dates.push(new Date(year, month + 1, i));
  }

  return dates;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DAY_HEADERS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function CalendarMonth() {
  const [currentMonth, setCurrentMonth] = useState<Date>(
    () => getMonthStart(new Date()),
  );
  const [expandedDate, setExpandedDate] = useState<string | null>(null);

  const todayISO = getTodayISO();
  const monthStart = getMonthStart(currentMonth);

  // Fetch entire month range (with a bit of padding for grid edges)
  const gridDates = useMemo(
    () => buildCalendarGrid(monthStart.getFullYear(), monthStart.getMonth()),
    [monthStart],
  );

  const fetchFrom = toISO(gridDates[0]);
  const fetchTo = toISO(gridDates[gridDates.length - 1]);

  const { data: scheduleData, isLoading } = useSchedule(fetchFrom, fetchTo);

  // Index schedule by date
  const scheduleByDate = useMemo(() => {
    const map = new Map<string, ScheduleDay>();
    for (const day of scheduleData?.data ?? []) {
      map.set(day.date, day);
    }
    return map;
  }, [scheduleData]);

  // Navigation
  const goToPrevMonth = useCallback(
    () => setCurrentMonth((prev) => addMonths(prev, -1)),
    [],
  );
  const goToNextMonth = useCallback(
    () => setCurrentMonth((prev) => addMonths(prev, 1)),
    [],
  );
  const goToToday = useCallback(() => {
    setCurrentMonth(getMonthStart(new Date()));
    setExpandedDate(null);
  }, []);

  // Toggle expanded day
  const handleDayClick = useCallback((iso: string) => {
    setExpandedDate((prev) => (prev === iso ? null : iso));
  }, []);

  // Split grid into weeks (rows of 7)
  const weeks = useMemo(() => {
    const rows: Date[][] = [];
    for (let i = 0; i < gridDates.length; i += 7) {
      rows.push(gridDates.slice(i, i + 7));
    }
    return rows;
  }, [gridDates]);

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  return (
    <div className="space-y-4">
      {/* Navigation */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Button variant="outline" size="icon" onClick={goToPrevMonth} aria-label="Previous month">
            <ChevronLeft className="size-4" />
          </Button>
          <Button variant="outline" size="sm" onClick={goToToday}>
            Today
          </Button>
          <Button variant="outline" size="icon" onClick={goToNextMonth} aria-label="Next month">
            <ChevronRight className="size-4" />
          </Button>
        </div>
        <span className="text-sm font-medium text-text-secondary">
          {currentMonth.toLocaleDateString("en-US", {
            month: "long",
            year: "numeric",
          })}
        </span>
      </div>

      {/* Loading */}
      {isLoading && <CalendarSkeleton />}

      {/* Calendar grid */}
      {!isLoading && (
        <div>
          {/* Day-of-week headers */}
          <div className="grid grid-cols-7 gap-1 mb-1">
            {DAY_HEADERS.map((d) => (
              <div
                key={d}
                className="py-1.5 text-center text-[11px] font-semibold uppercase tracking-wider text-text-muted"
              >
                {d}
              </div>
            ))}
          </div>

          {/* Weeks */}
          {weeks.map((week, weekIdx) => {
            // Check if any day in this week is expanded
            const expandedInRow = week.find(
              (d) => toISO(d) === expandedDate,
            );

            return (
              <div key={weekIdx}>
                {/* Day cells row */}
                <div className="grid grid-cols-7 gap-1">
                  {week.map((date) => {
                    const iso = toISO(date);
                    const isCurrentMonth =
                      date.getMonth() === monthStart.getMonth();
                    const isToday = iso === todayISO;
                    const isExpanded = iso === expandedDate;
                    const scheduleDay = scheduleByDate.get(iso);
                    const slots = scheduleDay?.slots ?? [];
                    const filledSlots = slots.filter(
                      (s) => s.status !== "empty",
                    );

                    return (
                      <button
                        key={iso}
                        type="button"
                        onClick={() => handleDayClick(iso)}
                        className={cn(
                          "relative rounded-md border p-2 text-left transition-all hover:bg-bg-elevated",
                          isCurrentMonth
                            ? "border-bg-hover bg-bg-surface"
                            : "border-transparent bg-bg-primary",
                          isToday && "border-indigo-500/50",
                          isExpanded && "ring-1 ring-indigo-500/50",
                        )}
                      >
                        {/* Day number */}
                        <span
                          className={cn(
                            "text-sm font-medium",
                            isCurrentMonth
                              ? "text-text-primary"
                              : "text-text-muted",
                            isToday && "text-indigo-400",
                          )}
                        >
                          {date.getDate()}
                        </span>

                        {/* Slot dots */}
                        <div className="mt-1.5 flex items-center gap-1">
                          {Array.from({ length: 2 }).map((_, slotIdx) => {
                            const slot = slots[slotIdx] as
                              | ScheduleSlot
                              | undefined;
                            const dotColor =
                              slot?.status === "published"
                                ? "bg-green-500"
                                : slot?.status === "scheduled"
                                  ? "bg-indigo-500"
                                  : "bg-bg-hover";
                            return (
                              <span
                                key={slotIdx}
                                className={cn(
                                  "size-1.5 rounded-full",
                                  dotColor,
                                )}
                              />
                            );
                          })}
                        </div>

                        {/* Coverage text */}
                        {isCurrentMonth && (
                          <span className="mt-1 block text-[10px] text-text-muted">
                            {filledSlots.length}/2
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>

                {/* Expanded day detail row */}
                <AnimatePresence>
                  {expandedInRow && (
                    <ExpandedDayRow
                      date={toISO(expandedInRow)}
                      scheduleDay={scheduleByDate.get(
                        toISO(expandedInRow),
                      )}
                    />
                  )}
                </AnimatePresence>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Expanded day detail (inline below the week row)
// ---------------------------------------------------------------------------

interface ExpandedDayRowProps {
  date: string;
  scheduleDay: ScheduleDay | undefined;
}

function ExpandedDayRow({ date, scheduleDay }: ExpandedDayRowProps) {
  const slots = scheduleDay?.slots ?? [];
  const defaultSlots: Array<{ time: string; status: "empty" }> = [
    { time: "12:00", status: "empty" },
  ];

  const displaySlots = defaultSlots.map((ds) => {
    const found = slots.find((s) => s.time === ds.time);
    return found ?? { ...ds, blueprint: null };
  });

  return (
    <motion.div
      initial={{ height: 0, opacity: 0 }}
      animate={{ height: "auto", opacity: 1 }}
      exit={{ height: 0, opacity: 0 }}
      transition={{ duration: 0.2 }}
      className="overflow-hidden"
    >
      <div className="my-1 rounded-md border border-bg-hover bg-bg-elevated p-3">
        <p className="mb-2 text-xs font-semibold text-text-secondary">
          {new Date(date + "T00:00:00").toLocaleDateString("en-US", {
            weekday: "long",
            month: "short",
            day: "numeric",
          })}
        </p>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {displaySlots.map((slot) => (
            <div
              key={slot.time}
              className={cn(
                "rounded-md border p-2",
                slot.status === "published"
                  ? "border-l-2 border-l-green-500 border-bg-hover bg-bg-surface"
                  : slot.status === "scheduled"
                    ? "border-l-2 border-l-indigo-500 border-bg-hover bg-bg-surface"
                    : "border-dashed border-bg-hover bg-bg-surface",
              )}
            >
              <span className="block text-[11px] font-medium text-text-muted">
                {slot.time}
              </span>
              {"blueprint" in slot && slot.blueprint ? (
                <div className="mt-1">
                  <p className="truncate text-xs font-medium text-text-primary">
                    {slot.blueprint.hook_text || "Untitled"}
                  </p>
                  <div className="mt-1">
                    <StatusBadge
                      status={slot.blueprint.status}
                      className="text-[10px] px-1 py-0"
                    />
                  </div>
                </div>
              ) : (
                <p className="mt-1 text-[11px] text-text-muted">Empty</p>
              )}
            </div>
          ))}
        </div>
      </div>
    </motion.div>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function CalendarSkeleton() {
  return (
    <div className="space-y-1">
      {/* Day headers */}
      <div className="grid grid-cols-7 gap-1">
        {Array.from({ length: 7 }).map((_, i) => (
          <Skeleton key={i} className="h-6 w-full rounded" />
        ))}
      </div>
      {/* 5 rows of 7 cells */}
      {Array.from({ length: 5 }).map((_, row) => (
        <div key={row} className="grid grid-cols-7 gap-1">
          {Array.from({ length: 7 }).map((__, col) => (
            <Skeleton key={col} className="h-20 w-full rounded-md" />
          ))}
        </div>
      ))}
    </div>
  );
}
