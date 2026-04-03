import { useRef, useEffect, useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import {
  Bell,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Info,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
import { relativeTime } from "@/lib/format";
import { useNotifications } from "@/hooks/use-notifications";
import type { NotificationType } from "@/stores/notification-store";

const MAX_SHOWN = 50;

function notificationIcon(type: NotificationType) {
  switch (type) {
    case "pipeline_complete":
    case "publish_success":
      return <CheckCircle2 className="size-4 shrink-0 text-emerald-500" />;
    case "pipeline_error":
    case "publish_failure":
      return <XCircle className="size-4 shrink-0 text-red-500" />;
    case "review_needed":
      return <AlertTriangle className="size-4 shrink-0 text-amber-500" />;
    case "info":
    default:
      return <Info className="size-4 shrink-0 text-blue-400" />;
  }
}

export function NotificationCenter() {
  const [open, setOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const navigate = useNavigate();
  const { notifications, unreadCount, markRead, markAllRead } =
    useNotifications();

  const close = useCallback(() => setOpen(false), []);

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      const target = e.target as Node;
      if (
        panelRef.current &&
        !panelRef.current.contains(target) &&
        buttonRef.current &&
        !buttonRef.current.contains(target)
      ) {
        close();
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open, close]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") close();
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [open, close]);

  const shown = notifications.slice(0, MAX_SHOWN);

  return (
    <div className="relative">
      {/* Bell button */}
      <Button
        ref={buttonRef}
        variant="ghost"
        size="icon"
        className="relative"
        onClick={() => setOpen((prev) => !prev)}
        aria-label="Notifications"
      >
        <Bell className="size-4" />
        {unreadCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 flex items-center justify-center min-w-[18px] h-[18px] rounded-full bg-red-600 text-[10px] font-bold text-white px-1">
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        )}
      </Button>

      {/* Dropdown panel */}
      <AnimatePresence>
      {open && (
        <motion.div
          ref={panelRef}
          initial={{ opacity: 0, scale: 0.95, y: -4 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.95, y: -4 }}
          transition={{ duration: 0.15, ease: "easeOut" }}
          className="absolute right-0 top-full mt-2 w-[380px] rounded-lg border border-border bg-bg-elevated shadow-lg z-50"
          style={{ transformOrigin: "top right" }}
        >
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3">
            <h3 className="text-sm font-semibold text-text-primary">
              Notifications
            </h3>
            {unreadCount > 0 && (
              <Button
                variant="ghost"
                size="xs"
                className="text-text-secondary hover:text-text-primary"
                onClick={() => markAllRead()}
              >
                Mark all read
              </Button>
            )}
          </div>
          <Separator />

          {/* List */}
          {shown.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-text-secondary">
              <Bell className="size-8 opacity-30 mb-2" />
              <p className="text-sm">No notifications</p>
            </div>
          ) : (
            <ScrollArea className="max-h-[400px]">
              <div className="py-1">
                {shown.map((item) => (
                  <button
                    key={item.id}
                    className={cn(
                      "w-full text-left flex items-start gap-3 px-4 py-3 hover:bg-bg-surface transition-colors",
                      !item.read && "bg-bg-surface/50",
                    )}
                    onClick={() => {
                      markRead(item.id);
                      if (item.entity_id && item.entity_type === "blueprint") {
                        navigate(`/blueprints/${item.entity_id}`);
                        close();
                      } else if (
                        item.entity_id &&
                        item.entity_type === "pipeline_run"
                      ) {
                        navigate(`/runs/${item.entity_id}`);
                        close();
                      }
                    }}
                  >
                    <div className="mt-0.5">{notificationIcon(item.type)}</div>
                    <div className="flex-1 min-w-0">
                      <p
                        className={cn(
                          "text-sm leading-tight",
                          item.read
                            ? "text-text-secondary"
                            : "text-text-primary font-medium",
                        )}
                      >
                        {item.title}
                      </p>
                      <p className="text-xs text-text-muted mt-0.5 truncate">
                        {item.body}
                      </p>
                      <p className="text-[10px] text-text-muted mt-1">
                        {relativeTime(item.created_at)}
                      </p>
                    </div>
                    {!item.read && (
                      <div className="mt-1.5 size-2 rounded-full bg-accent shrink-0" />
                    )}
                  </button>
                ))}
              </div>
            </ScrollArea>
          )}
        </motion.div>
      )}
      </AnimatePresence>
    </div>
  );
}
