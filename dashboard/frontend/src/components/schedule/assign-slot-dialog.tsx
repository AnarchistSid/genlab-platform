import { useState, useMemo } from "react";
import { CalendarPlus, Clock } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { Blueprint } from "@/api/types";

interface AssignSlotDialogProps {
  blueprint: Blueprint | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAssign: (blueprintId: string, date: string, slot: string) => void;
  scheduleSlots?: string[];
}

const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function addDays(date: Date, days: number): Date {
  const result = new Date(date);
  result.setDate(result.getDate() + days);
  return result;
}

function toISO(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export function AssignSlotDialog({
  blueprint,
  open,
  onOpenChange,
  onAssign,
  scheduleSlots = ["12:00"],
}: AssignSlotDialogProps) {
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [selectedSlot, setSelectedSlot] = useState<string | null>(null);

  // Generate next 14 days for quick date picking
  const dateOptions = useMemo(() => {
    const today = new Date();
    return Array.from({ length: 14 }, (_, i) => {
      const d = addDays(today, i);
      return {
        iso: toISO(d),
        dayName: DAY_NAMES[d.getDay()],
        dayNum: d.getDate(),
        month: d.toLocaleDateString("en-US", { month: "short" }),
        isToday: i === 0,
        isTomorrow: i === 1,
      };
    });
  }, []);

  const canAssign = selectedDate && selectedSlot && blueprint;

  const handleAssign = () => {
    if (!canAssign) return;
    onAssign(blueprint.id, selectedDate, selectedSlot);
    onOpenChange(false);
    setSelectedDate(null);
    setSelectedSlot(null);
  };

  const handleOpenChange = (val: boolean) => {
    if (!val) {
      setSelectedDate(null);
      setSelectedSlot(null);
    }
    onOpenChange(val);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="border-bg-hover bg-bg-primary sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-text-primary">
            <CalendarPlus className="size-5 text-indigo-400" />
            Assign to Slot
          </DialogTitle>
          <DialogDescription className="text-text-secondary">
            {blueprint?.hook_text
              ? `Schedule "${blueprint.hook_text.slice(0, 50)}${blueprint.hook_text.length > 50 ? "..." : ""}"`
              : "Pick a date and time slot"}
          </DialogDescription>
        </DialogHeader>

        {/* Date grid */}
        <div className="space-y-2">
          <label className="text-xs font-medium text-text-muted uppercase tracking-wider">
            Date
          </label>
          <div className="grid grid-cols-7 gap-1.5">
            {dateOptions.map((d) => (
              <button
                key={d.iso}
                type="button"
                onClick={() => setSelectedDate(d.iso)}
                className={cn(
                  "flex flex-col items-center rounded-md px-1 py-1.5 text-center transition-colors",
                  selectedDate === d.iso
                    ? "bg-indigo-500/20 border border-indigo-500/50 text-indigo-300"
                    : "border border-bg-hover bg-bg-surface text-text-secondary hover:border-text-disabled hover:bg-bg-elevated",
                  d.isToday && selectedDate !== d.iso && "border-indigo-500/30",
                )}
              >
                <span className="text-[10px] font-medium leading-tight">
                  {d.isToday ? "Today" : d.isTomorrow ? "Tmrw" : d.dayName}
                </span>
                <span className="text-sm font-semibold leading-tight">{d.dayNum}</span>
                <span className="text-[9px] leading-tight opacity-60">{d.month}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Slot picker */}
        <div className="space-y-2">
          <label className="text-xs font-medium text-text-muted uppercase tracking-wider">
            Time Slot
          </label>
          <div className="flex gap-2">
            {scheduleSlots.map((slot) => (
              <button
                key={slot}
                type="button"
                onClick={() => setSelectedSlot(slot)}
                className={cn(
                  "flex flex-1 items-center justify-center gap-2 rounded-md border px-3 py-2.5 text-sm font-medium transition-colors",
                  selectedSlot === slot
                    ? "bg-indigo-500/20 border-indigo-500/50 text-indigo-300"
                    : "border-bg-hover bg-bg-surface text-text-secondary hover:border-text-disabled hover:bg-bg-elevated",
                )}
              >
                <Clock className="size-4" />
                {slot}
              </button>
            ))}
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            size="sm"
            onClick={() => handleOpenChange(false)}
            className="border-bg-hover text-text-secondary hover:bg-bg-elevated"
          >
            Cancel
          </Button>
          <Button
            size="sm"
            disabled={!canAssign}
            onClick={handleAssign}
            className="bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-40"
          >
            Assign
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
