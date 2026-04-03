import { useState, useEffect, useCallback, useRef } from "react";
import {
  Activity,
  Zap,
  FileText,
  AlertTriangle,
  X,
  Trash2,
} from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { cn, uuid } from "@/lib/utils";
import { getSocket } from "@/api/socket";
import { relativeTime } from "@/lib/format";
import type { PipelineProgressEvent } from "@/api/types";
import type { SocketEvents } from "@/api/socket";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type EventCategory = "pipeline" | "content" | "error";

interface ActivityEvent {
  id: string;
  type: EventCategory;
  title: string;
  timestamp: string;
  color: "green" | "red" | "blue" | "yellow";
}

type FilterTab = "all" | "pipeline" | "content" | "errors";

const MAX_EVENTS = 100;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function eventIcon(type: EventCategory, color: ActivityEvent["color"]) {
  const colorClass = {
    green: "text-emerald-500",
    red: "text-red-500",
    blue: "text-blue-400",
    yellow: "text-amber-400",
  }[color];

  switch (type) {
    case "pipeline":
      return <Zap className={cn("size-4 shrink-0", colorClass)} />;
    case "content":
      return <FileText className={cn("size-4 shrink-0", colorClass)} />;
    case "error":
      return <AlertTriangle className={cn("size-4 shrink-0", colorClass)} />;
  }
}

function dotColor(color: ActivityEvent["color"]) {
  return {
    green: "bg-emerald-500",
    red: "bg-red-500",
    blue: "bg-blue-400",
    yellow: "bg-amber-400",
  }[color];
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ActivityFeed({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [filter, setFilter] = useState<FilterTab>("all");
  const panelRef = useRef<HTMLDivElement>(null);

  const addEvent = useCallback(
    (ev: Omit<ActivityEvent, "id" | "timestamp">) => {
      setEvents((prev) => {
        const next: ActivityEvent = {
          ...ev,
          id: uuid(),
          timestamp: new Date().toISOString(),
        };
        return [next, ...prev].slice(0, MAX_EVENTS);
      });
    },
    [],
  );

  // Socket subscriptions
  useEffect(() => {
    const socket = getSocket();

    function onPipelineProgress(event: PipelineProgressEvent) {
      addEvent({
        type: "pipeline",
        title: `Pipeline: ${event.step}`,
        color: "yellow",
      });
    }

    function onPipelineComplete(
      _event: SocketEvents["pipeline_complete"],
    ) {
      addEvent({
        type: "pipeline",
        title: "Pipeline Complete",
        color: "green",
      });
    }

    function onBlueprintUpdated(
      _event: SocketEvents["blueprint_updated"],
    ) {
      addEvent({
        type: "content",
        title: "Blueprint Updated",
        color: "blue",
      });
    }

    function onBlueprintsUpdated(
      _event: SocketEvents["blueprints_updated"],
    ) {
      addEvent({
        type: "content",
        title: "Blueprints Refreshed",
        color: "blue",
      });
    }

    function onExpressProgress(
      event: SocketEvents["express_progress"],
    ) {
      addEvent({
        type: "pipeline",
        title: `Express: ${event.step ?? event.type}`,
        color: "yellow",
      });
    }

    // Use underscore event names to match server-side socketio.emit()
    socket.on("pipeline_progress", onPipelineProgress);
    socket.on("pipeline_complete", onPipelineComplete);
    socket.on("blueprint_updated", onBlueprintUpdated);
    socket.on("blueprints_updated", onBlueprintsUpdated);
    socket.on("express_progress", onExpressProgress);

    return () => {
      socket.off("pipeline_progress", onPipelineProgress);
      socket.off("pipeline_complete", onPipelineComplete);
      socket.off("blueprint_updated", onBlueprintUpdated);
      socket.off("blueprints_updated", onBlueprintsUpdated);
      socket.off("express_progress", onExpressProgress);
    };
  }, [addEvent]);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (
        panelRef.current &&
        !panelRef.current.contains(e.target as Node)
      ) {
        onClose();
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open, onClose]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [open, onClose]);

  const filtered = events.filter((ev) => {
    if (filter === "all") return true;
    if (filter === "pipeline") return ev.type === "pipeline";
    if (filter === "content") return ev.type === "content";
    if (filter === "errors") return ev.type === "error";
    return true;
  });

  const tabs: { key: FilterTab; label: string }[] = [
    { key: "all", label: "All" },
    { key: "pipeline", label: "Pipeline" },
    { key: "content", label: "Content" },
    { key: "errors", label: "Errors" },
  ];

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          ref={panelRef}
          initial={{ x: 300, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          exit={{ x: 300, opacity: 0 }}
          transition={{ type: "spring", damping: 25, stiffness: 300 }}
          className="fixed right-0 top-0 bottom-0 w-[300px] z-50 border-l border-border bg-bg-elevated shadow-2xl flex flex-col"
        >
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 shrink-0">
            <div className="flex items-center gap-2">
              <Activity className="size-4 text-accent" />
              <h3 className="text-sm font-semibold text-text-primary">
                Activity
              </h3>
              {events.length > 0 && (
                <Badge
                  variant="secondary"
                  className="text-[10px] px-1.5 py-0"
                >
                  {events.length}
                </Badge>
              )}
            </div>
            <div className="flex items-center gap-1">
              <Button
                variant="ghost"
                size="icon"
                className="size-7"
                onClick={() => setEvents([])}
                aria-label="Clear activity feed"
              >
                <Trash2 className="size-3.5 text-text-secondary" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="size-7"
                onClick={onClose}
                aria-label="Close activity feed"
              >
                <X className="size-3.5 text-text-secondary" />
              </Button>
            </div>
          </div>
          <Separator />

          {/* Filter tabs */}
          <div className="flex items-center gap-1 px-3 py-2 shrink-0">
            {tabs.map((tab) => (
              <button
                key={tab.key}
                className={cn(
                  "px-2.5 py-1 text-xs rounded-md transition-colors",
                  filter === tab.key
                    ? "bg-accent/20 text-accent font-medium"
                    : "text-text-secondary hover:text-text-primary hover:bg-bg-surface",
                )}
                onClick={() => setFilter(tab.key)}
              >
                {tab.label}
              </button>
            ))}
          </div>
          <Separator />

          {/* Event list */}
          {filtered.length === 0 ? (
            <div className="flex-1 flex flex-col items-center justify-center text-text-secondary">
              <Activity className="size-8 opacity-30 mb-2" />
              <p className="text-sm">No activity yet</p>
            </div>
          ) : (
            <ScrollArea className="flex-1">
              <div className="py-1">
                {filtered.map((ev) => (
                  <div
                    key={ev.id}
                    className="flex items-start gap-3 px-4 py-2.5 hover:bg-bg-surface/50 transition-colors"
                  >
                    <div className="mt-0.5">
                      {eventIcon(ev.type, ev.color)}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-text-primary leading-tight truncate">
                        {ev.title}
                      </p>
                      <p className="text-[10px] text-text-muted mt-0.5">
                        {relativeTime(ev.timestamp)}
                      </p>
                    </div>
                    <div
                      className={cn(
                        "mt-1.5 size-2 rounded-full shrink-0",
                        dotColor(ev.color),
                      )}
                    />
                  </div>
                ))}
              </div>
            </ScrollArea>
          )}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

// ---------------------------------------------------------------------------
// Toggle button (used in Shell header)
// ---------------------------------------------------------------------------

export function ActivityToggle({ onClick }: { onClick: () => void }) {
  return (
    <Button
      variant="ghost"
      size="icon"
      className="relative"
      onClick={onClick}
      aria-label="Toggle activity feed"
    >
      <Activity className="size-4" />
    </Button>
  );
}
