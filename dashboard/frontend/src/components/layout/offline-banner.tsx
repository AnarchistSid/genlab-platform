import { WifiOff, Loader2 } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { useOnlineStatus } from "@/hooks/use-offline";
import { useConnectionStatus } from "@/hooks/use-socket";

export function OfflineBanner() {
  const online = useOnlineStatus();
  const { status, reconnectAttempt } = useConnectionStatus();

  const showBanner = !online || status === "reconnecting" || status === "disconnected";

  const message = !online
    ? "You are offline. Some features may be unavailable."
    : status === "reconnecting"
      ? `Reconnecting to server... (attempt ${reconnectAttempt})`
      : "Connection to server lost. Real-time updates are paused.";

  const isReconnecting = online && status === "reconnecting";

  return (
    <AnimatePresence>
      {showBanner && (
        <motion.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: "auto", opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: 0.2 }}
          className="overflow-hidden shrink-0"
        >
          <div
            className="flex items-center justify-center gap-2 px-4 py-1.5 text-sm font-medium"
            style={{
              background: isReconnecting
                ? "var(--color-amber-dim)"
                : "var(--color-red-dim)",
              color: isReconnecting
                ? "var(--color-amber)"
                : "var(--color-red)",
            }}
          >
            {isReconnecting
              ? <Loader2 className="size-3.5 animate-spin" />
              : <WifiOff className="size-3.5" />}
            <span>{message}</span>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
