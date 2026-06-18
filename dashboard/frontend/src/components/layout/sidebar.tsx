import { useState, useRef, useEffect } from "react";
import { NavLink } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import {
  ChevronDown,
  Lock,
  Menu,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useNicheStore } from "@/stores/niche-store";
import { getAllNiches } from "@/niches/registry";
import { crossNiche } from "@/api/client";
import { queryKeys } from "@/api/query-keys";
import { getSocket } from "@/api/socket";
import { navItems, bottomNavItems } from "./nav-items";

type SocketStatus = "connected" | "reconnecting" | "disconnected";

function useSocketStatus(): SocketStatus {
  const [status, setStatus] = useState<SocketStatus>(() => {
    try {
      const s = getSocket();
      return s.connected ? "connected" : "disconnected";
    } catch {
      return "disconnected";
    }
  });

  useEffect(() => {
    const socket = getSocket();

    let disconnectTimer: ReturnType<typeof setTimeout> | null = null;
    function onConnect() { if (disconnectTimer) { clearTimeout(disconnectTimer); disconnectTimer = null; } setStatus("connected"); }
    function onDisconnect() { disconnectTimer = setTimeout(() => setStatus("disconnected"), 2000); }
    function onReconnectAttempt() { if (disconnectTimer) { clearTimeout(disconnectTimer); disconnectTimer = null; } setStatus("reconnecting"); }
    function onReconnect() { setStatus("connected"); }

    socket.on("connect", onConnect);
    socket.on("disconnect", onDisconnect);
    socket.io.on("reconnect_attempt", onReconnectAttempt);
    socket.io.on("reconnect", onReconnect);

    // Sync initial state after attaching listeners. One-shot
    // external-system sync — the rule's render-cascading concern
    // doesn't apply here (state only changes when the socket emits).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setStatus(socket.connected ? "connected" : "disconnected");

    return () => {
      socket.off("connect", onConnect);
      socket.off("disconnect", onDisconnect);
      socket.io.off("reconnect_attempt", onReconnectAttempt);
      socket.io.off("reconnect", onReconnect);
    };
  }, []);

  return status;
}

function useIsNarrow(breakpoint = 768): boolean {
  const [isNarrow, setIsNarrow] = useState(() =>
    typeof window !== "undefined" ? window.innerWidth < breakpoint : false
  );

  useEffect(() => {
    function handleResize() {
      setIsNarrow(window.innerWidth < breakpoint);
    }
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [breakpoint]);

  return isNarrow;
}

// navItems + bottomNavItems moved to ./nav-items so this file only
// exports React components (react-refresh/only-export-components).
const bottomItems = bottomNavItems;

function NicheSwitcher({ collapsed }: { collapsed?: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const { selectedNicheId, selectedNiche, setNiche } = useNicheStore();

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const allNiches = getAllNiches();
  const displayName = selectedNiche ? selectedNiche.displayName : "All Niches";

  if (collapsed) {
    return (
      <div ref={ref} className="relative px-1.5 mb-1 flex justify-center">
        <button
          onClick={() => setOpen(!open)}
          aria-label={`Switch niche, currently ${displayName}`}
          aria-expanded={open}
          className="flex items-center justify-center w-9 h-9 rounded-md hover:bg-bg-raised transition-colors"
          style={{ transitionDuration: "var(--duration-base)" }}
        >
          <span
            className="h-2.5 w-2.5 rounded-full shrink-0"
            style={{ backgroundColor: selectedNiche ? selectedNiche.accentHex : "var(--text-muted)" }}
          />
        </button>

        <AnimatePresence>
          {open && (
            <motion.div
              initial={{ opacity: 0, scale: 0.95, x: -4 }}
              animate={{ opacity: 1, scale: 1, x: 0 }}
              exit={{ opacity: 0, scale: 0.95, x: -4 }}
              transition={{ duration: 0.15, ease: "easeOut" }}
              className="absolute left-full ml-2 top-0 z-50 rounded-lg border border-border bg-bg-surface py-1 min-w-40"
              style={{ boxShadow: "var(--shadow-lg)", transformOrigin: "left" }}
            >
              <button
                onClick={() => { setNiche("all"); setOpen(false); }}
                className={cn(
                  "flex items-center gap-2 w-full px-3 py-2 text-sm transition-colors hover:bg-bg-elevated",
                  selectedNicheId === "all" && "bg-bg-elevated",
                )}
              >
                <span className="h-2 w-2 rounded-full bg-text-muted" />
                <span className="text-text-primary">All Niches</span>
              </button>
              <div className="h-px bg-border mx-2 my-1" />
              {allNiches.map((niche) => (
                <button
                  key={niche.id}
                  onClick={() => {
                    if (niche.isActive) { setNiche(niche.id); setOpen(false); }
                  }}
                  disabled={!niche.isActive}
                  className={cn(
                    "flex items-center gap-2 w-full px-3 py-2 text-sm transition-colors",
                    niche.isActive && "hover:bg-bg-elevated cursor-pointer",
                    !niche.isActive && "opacity-40 cursor-not-allowed",
                    selectedNicheId === niche.id && "bg-bg-elevated",
                  )}
                >
                  <span
                    className="h-2 w-2 rounded-full shrink-0"
                    style={{ backgroundColor: niche.accentHex }}
                  />
                  <span className="text-text-primary">{niche.displayName}</span>
                </button>
              ))}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    );
  }

  return (
    <div ref={ref} className="relative px-3 mb-1">
      <button
        onClick={() => setOpen(!open)}
        aria-label={`Switch niche, currently ${displayName}`}
        aria-expanded={open}
        className="flex items-center gap-2 w-full px-2 py-1.5 rounded-md text-sm hover:bg-bg-raised transition-colors"
        style={{ transitionDuration: "var(--duration-base)" }}
      >
        {selectedNiche && (
          <span
            className="h-2 w-2 rounded-full shrink-0"
            style={{ backgroundColor: selectedNiche.accentHex }}
          />
        )}
        {!selectedNiche && (
          <span className="h-2 w-2 rounded-full shrink-0 bg-text-muted" />
        )}
        <span className="flex-1 text-left text-text-primary truncate">{displayName}</span>
        <ChevronDown
          size={14}
          className={cn(
            "text-text-muted transition-transform",
            open && "rotate-180",
          )}
          style={{ transitionDuration: "var(--duration-base)" }}
        />
      </button>

      <AnimatePresence>
        {open && (
        <motion.div
          initial={{ opacity: 0, scale: 0.95, y: -4 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.95, y: -4 }}
          transition={{ duration: 0.15, ease: "easeOut" }}
          className="absolute left-3 right-3 top-full mt-1 z-50 rounded-lg border border-border bg-bg-surface py-1"
          style={{ boxShadow: "var(--shadow-lg)", transformOrigin: "top" }}
        >
          {/* All Niches option */}
          <button
            onClick={() => { setNiche("all"); setOpen(false); }}
            className={cn(
              "flex items-center gap-2 w-full px-3 py-2 text-sm transition-colors hover:bg-bg-elevated",
              selectedNicheId === "all" && "bg-bg-elevated",
            )}
          >
            <span className="h-2 w-2 rounded-full bg-text-muted" />
            <span className="text-text-primary">All Niches</span>
          </button>

          <div className="h-px bg-border mx-2 my-1" />

          {allNiches.map((niche) => (
            <button
              key={niche.id}
              onClick={() => {
                if (niche.isActive) { setNiche(niche.id); setOpen(false); }
              }}
              disabled={!niche.isActive}
              className={cn(
                "flex items-center gap-2 w-full px-3 py-2 text-sm transition-colors",
                niche.isActive && "hover:bg-bg-elevated cursor-pointer",
                !niche.isActive && "opacity-40 cursor-not-allowed",
                selectedNicheId === niche.id && "bg-bg-elevated",
              )}
            >
              <span
                className="h-2 w-2 rounded-full shrink-0"
                style={{ backgroundColor: niche.accentHex }}
              />
              <div className="flex-1 text-left">
                <div className="text-text-primary">{niche.displayName}</div>
                <div className="text-xs text-text-muted">{niche.tagline}</div>
              </div>
              {!niche.isActive && (
                <span className="flex items-center gap-1 text-xs text-text-disabled">
                  <Lock size={10} />
                  Soon
                </span>
              )}
            </button>
          ))}
        </motion.div>
      )}
      </AnimatePresence>
    </div>
  );
}

const SOCKET_DOT_STYLES: Record<SocketStatus, { color: string; animate: boolean }> = {
  connected:    { color: "var(--color-green)",  animate: true },
  reconnecting: { color: "var(--color-amber)",  animate: true },
  disconnected: { color: "var(--color-muted, var(--text-muted))", animate: false },
};

export function Sidebar() {
  const socketStatus = useSocketStatus();
  const dotStyle = SOCKET_DOT_STYLES[socketStatus];
  const isNarrow = useIsNarrow(768);
  const [overlayOpen, setOverlayOpen] = useState(false);

  // Live pending review count from cross-niche overview
  const { data: overview } = useQuery({
    queryKey: queryKeys.crossNiche.overview(),
    queryFn: () => crossNiche.overview(),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });
  const agentsRunning = (overview?.global?.agents_running ?? 0) > 0;

  // Close overlay when switching to desktop (React 19 idiom: track
  // previous + reset during render — see stories.tsx).
  const [prevIsNarrow, setPrevIsNarrow] = useState(isNarrow);
  if (prevIsNarrow !== isNarrow) {
    setPrevIsNarrow(isNarrow);
    if (!isNarrow) setOverlayOpen(false);
  }

  // Icon-only collapsed sidebar for 640px–767px
  if (isNarrow) {
    return (
      <>
        {/* Collapsed icon-only sidebar (48px) */}
        <aside
          role="navigation"
          aria-label="Main"
          className="w-12 shrink-0 border-r border-border flex flex-col h-screen bg-bg-elevated"
        >
          {/* Hamburger toggle */}
          <div className="h-14 flex items-center justify-center border-b border-border">
            <button
              onClick={() => setOverlayOpen(true)}
              aria-label="Open navigation menu"
              className="flex items-center justify-center w-9 h-9 rounded-md text-text-secondary hover:text-text-primary hover:bg-bg-raised transition-colors"
              style={{ transitionDuration: "var(--duration-base)" }}
            >
              <Menu size={18} />
            </button>
          </div>

          {/* Niche dot */}
          <div className="py-2">
            <NicheSwitcher collapsed />
          </div>

          <div className="h-px bg-border mx-2" />

          {/* Icon-only nav */}
          <nav className="flex-1 py-2 px-1.5 space-y-0.5 overflow-y-auto">
            {navItems.map((item) => (
              <NavLink
                key={item.label}
                to={item.to}
                end={item.to === "/"}
                aria-label={item.label}
                className={({ isActive }) =>
                  cn(
                    "flex items-center justify-center w-9 h-9 rounded-md transition-colors relative mx-auto",
                    isActive
                      ? "text-text-primary"
                      : "text-text-secondary hover:text-text-primary hover:bg-bg-raised",
                  )
                }
                style={({ isActive }) =>
                  isActive
                    ? {
                        backgroundColor: "var(--surface-3)",
                        borderLeft: "2px solid var(--niche-current)",
                        transitionDuration: "var(--duration-base)",
                      }
                    : { transitionDuration: "var(--duration-base)" }
                }
              >
                <item.icon className="h-4 w-4 shrink-0" />
                {(item as { liveDot?: boolean }).liveDot && (
                  <span
                    className={cn(
                      "absolute top-1 right-1 h-1.5 w-1.5 rounded-full",
                      agentsRunning ? "bg-color-green" : "bg-text-muted",
                    )}
                  />
                )}
              </NavLink>
            ))}
          </nav>

          <div className="h-px bg-border mx-2" />

          {/* Bottom icon nav */}
          <div className="py-2 px-1.5 space-y-0.5">
            {bottomItems.map((item) => (
              <NavLink
                key={item.label}
                to={item.to}
                aria-label={item.label}
                className={({ isActive }) =>
                  cn(
                    "flex items-center justify-center w-9 h-9 rounded-md transition-colors mx-auto",
                    isActive
                      ? "text-text-primary bg-bg-raised"
                      : "text-text-secondary hover:text-text-primary hover:bg-bg-raised",
                  )
                }
                style={{ transitionDuration: "var(--duration-base)" }}
              >
                <item.icon className="h-4 w-4 shrink-0" />
              </NavLink>
            ))}
          </div>

          {/* Socket dot */}
          <div className="px-2 py-3 border-t border-border flex justify-center">
            <span
              title={`Socket: ${socketStatus}`}
              style={{
                display: "inline-block",
                width: "6px",
                height: "6px",
                borderRadius: "50%",
                backgroundColor: dotStyle.color,
                animation: dotStyle.animate ? "gl-pulse 2s ease-in-out infinite" : "none",
              }}
            />
          </div>
        </aside>

        {/* Full-width overlay when hamburger is open */}
        <AnimatePresence>
          {overlayOpen && (
            <>
              {/* Backdrop */}
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="fixed inset-0 z-40 bg-black/50"
                onClick={() => setOverlayOpen(false)}
              />

              {/* Overlay sidebar */}
              <motion.aside
                initial={{ x: -240 }}
                animate={{ x: 0 }}
                exit={{ x: -240 }}
                transition={{ duration: 0.2, ease: "easeOut" }}
                role="navigation"
                aria-label="Main (expanded)"
                className="fixed left-0 top-0 bottom-0 z-50 w-60 border-r border-border flex flex-col h-screen bg-bg-elevated"
              >
                {/* Header with close button */}
                <div className="h-14 flex items-center px-5 border-b border-border">
                  <div className="flex items-center gap-2 flex-1">
                    <span className="text-lg font-bold" style={{ color: "var(--niche-current)" }}>
                      ◈
                    </span>
                    <span className="text-sm font-semibold tracking-tight text-text-primary">
                      Gen Lab
                    </span>
                    <span className="text-xs text-text-muted ml-0.5">v2</span>
                    <span
                      title={`Socket: ${socketStatus}`}
                      style={{
                        display: "inline-block",
                        width: "6px",
                        height: "6px",
                        borderRadius: "50%",
                        backgroundColor: dotStyle.color,
                        animation: dotStyle.animate ? "gl-pulse 2s ease-in-out infinite" : "none",
                        flexShrink: 0,
                      }}
                    />
                  </div>
                  <button
                    onClick={() => setOverlayOpen(false)}
                    aria-label="Close navigation menu"
                    className="flex items-center justify-center w-8 h-8 rounded-md text-text-muted hover:text-text-primary hover:bg-bg-raised transition-colors"
                    style={{ transitionDuration: "var(--duration-base)" }}
                  >
                    <X size={16} />
                  </button>
                </div>

                {/* Niche Switcher */}
                <div className="py-2">
                  <NicheSwitcher />
                </div>

                <div className="h-px bg-border mx-3" />

                {/* Navigation */}
                <nav className="flex-1 py-2 px-2 space-y-0.5 overflow-y-auto">
                  {navItems.map((item) => (
                    <NavLink
                      key={item.label}
                      to={item.to}
                      end={item.to === "/"}
                      onClick={() => setOverlayOpen(false)}
                      className={({ isActive }) =>
                        cn(
                          "flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors relative",
                          isActive
                            ? "text-text-primary"
                            : "text-text-secondary hover:text-text-primary hover:bg-bg-raised",
                        )
                      }
                      style={({ isActive }) =>
                        isActive
                          ? {
                              backgroundColor: "var(--surface-3)",
                              borderLeft: "2px solid var(--niche-current)",
                              paddingLeft: "10px",
                              transitionDuration: "var(--duration-base)",
                            }
                          : { transitionDuration: "var(--duration-base)" }
                      }
                    >
                      <item.icon className="h-4 w-4 shrink-0" />
                      <span className="flex-1">{item.label}</span>
                      {(item as { newBadge?: boolean }).newBadge && (
                        <span
                          className="text-[9px] font-semibold px-1.5 py-0.5 rounded-full uppercase tracking-wide"
                          style={{
                            backgroundColor: "rgba(139,92,246,0.15)",
                            color: "#8b5cf6",
                          }}
                        >
                          new
                        </span>
                      )}
                      {item.liveDot && (
                        <span
                          className={cn("h-1.5 w-1.5 rounded-full", agentsRunning ? "bg-color-green" : "bg-text-muted")}
                        />
                      )}
                    </NavLink>
                  ))}
                </nav>

                <div className="h-px bg-border mx-3" />

                {/* Bottom nav */}
                <div className="py-2 px-2 space-y-0.5">
                  {bottomItems.map((item) => (
                    <NavLink
                      key={item.label}
                      to={item.to}
                      onClick={() => setOverlayOpen(false)}
                      className={({ isActive }) =>
                        cn(
                          "flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors",
                          isActive
                            ? "text-text-primary bg-bg-raised"
                            : "text-text-secondary hover:text-text-primary hover:bg-bg-raised",
                        )
                      }
                      style={{ transitionDuration: "var(--duration-base)" }}
                    >
                      <item.icon className="h-4 w-4 shrink-0" />
                      <span className="flex-1">{item.label}</span>
                    </NavLink>
                  ))}
                </div>

                {/* Bottom status widget */}
                <div className="px-3 py-3 border-t border-border">
                  <div className="flex items-center gap-2 text-xs text-text-muted">
                    <span className={cn("h-1.5 w-1.5 rounded-full", agentsRunning ? "bg-color-green animate-pulse" : "bg-text-muted")} />
                    <span>{agentsRunning ? `${overview?.global?.agents_running ?? 0} agent(s) running` : "All agents idle"}</span>
                  </div>
                </div>
              </motion.aside>
            </>
          )}
        </AnimatePresence>
      </>
    );
  }

  // Full sidebar for ≥768px
  return (
    <aside
      role="navigation"
      aria-label="Main"
      className="w-60 shrink-0 border-r border-border flex flex-col h-screen bg-bg-elevated"
    >
      {/* Wordmark */}
      <div className="h-14 flex items-center px-5 border-b border-border">
        <div className="flex items-center gap-2">
          <span
            className="text-lg font-bold"
            style={{ color: "var(--niche-current)" }}
          >
            ◈
          </span>
          <span
            className="text-sm font-semibold tracking-tight text-text-primary"
          >
            Gen Lab
          </span>
          <span className="text-xs text-text-muted ml-0.5">v2</span>
          <span
            title={`Socket: ${socketStatus}`}
            style={{
              display: "inline-block",
              width: "6px",
              height: "6px",
              borderRadius: "50%",
              backgroundColor: dotStyle.color,
              animation: dotStyle.animate ? "gl-pulse 2s ease-in-out infinite" : "none",
              flexShrink: 0,
            }}
          />
        </div>
      </div>

      {/* Niche Switcher */}
      <div className="py-2">
        <NicheSwitcher />
      </div>

      <div className="h-px bg-border mx-3" />

      {/* Navigation */}
      <nav className="flex-1 py-2 px-2 space-y-0.5 overflow-y-auto">
        {navItems.map((item) => (
          <NavLink
            key={item.label}
            to={item.to}
            end={item.to === "/"}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors relative",
                isActive
                  ? "text-text-primary"
                  : "text-text-secondary hover:text-text-primary hover:bg-bg-raised",
              )
            }
            style={({ isActive }) =>
              isActive
                ? {
                    backgroundColor: "var(--surface-3)",
                    borderLeft: "2px solid var(--niche-current)",
                    paddingLeft: "10px",
                    transitionDuration: "var(--duration-base)",
                  }
                : { transitionDuration: "var(--duration-base)" }
            }
          >
            <item.icon className="h-4 w-4 shrink-0" />
            <span className="flex-1">{item.label}</span>
            {(item as { newBadge?: boolean }).newBadge && (
              <span
                className="text-[9px] font-semibold px-1.5 py-0.5 rounded-full uppercase tracking-wide"
                style={{
                  backgroundColor: "rgba(139,92,246,0.15)",
                  color: "#8b5cf6",
                }}
              >
                new
              </span>
            )}
            {item.liveDot && (
              <span
                className={cn("h-1.5 w-1.5 rounded-full", agentsRunning ? "bg-color-green" : "bg-text-muted")}
              />
            )}
            <kbd className="text-[10px] text-text-muted font-mono hidden lg:inline">
              {item.shortcut}
            </kbd>
          </NavLink>
        ))}
      </nav>

      <div className="h-px bg-border mx-3" />

      {/* Bottom nav */}
      <div className="py-2 px-2 space-y-0.5">
        {bottomItems.map((item) => (
          <NavLink
            key={item.label}
            to={item.to}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors",
                isActive
                  ? "text-text-primary bg-bg-raised"
                  : "text-text-secondary hover:text-text-primary hover:bg-bg-raised",
              )
            }
            style={{ transitionDuration: "var(--duration-base)" }}
          >
            <item.icon className="h-4 w-4 shrink-0" />
            <span className="flex-1">{item.label}</span>
            <kbd className="text-[10px] text-text-muted font-mono hidden lg:inline">
              {item.shortcut}
            </kbd>
          </NavLink>
        ))}
      </div>

      {/* Bottom status widget */}
      <div className="px-3 py-3 border-t border-border">
        <div className="flex items-center gap-2 text-xs text-text-muted">
          <span className={cn("h-1.5 w-1.5 rounded-full", agentsRunning ? "bg-color-green animate-pulse" : "bg-text-muted")} />
          <span>{agentsRunning ? `${overview?.global?.agents_running ?? 0} agent(s) running` : "All agents idle"}</span>
        </div>
      </div>
    </aside>
  );
}
