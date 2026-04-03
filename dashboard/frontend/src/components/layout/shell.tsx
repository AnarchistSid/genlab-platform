import { useState, useCallback, useEffect } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { Command, Settings, ChevronRight } from "lucide-react";
import { Sidebar, navItems } from "./sidebar";
import { NotificationCenter } from "./notification-center";
import { ActivityFeed, ActivityToggle } from "./activity-feed";
import { OfflineBanner } from "./offline-banner";
import { SkipLink } from "./skip-link";
import { useNicheStore } from "@/stores/niche-store";
import { useCommandPaletteStore } from "@/stores/command-palette-store";
import { cn } from "@/lib/utils";

const PAGE_LABELS: Record<string, string> = {
  "/": "Mission Control",
  "/analytics": "Analytics",
  "/pipeline": "Pipeline",
  "/schedule": "Schedule",
  "/queue": "Publishing Queue",
  "/monetisation": "Monetisation",
  "/content": "Content Review",
  "/learning": "Learning",
  "/engagement": "Engagement",
  "/health": "System Health",
  "/settings": "Settings",
  "/focus-review": "Focus Review",
  "/stories": "Stories",
};

function Breadcrumb({ selectedNiche }: { selectedNiche: { displayName: string; accentHex: string } | null }) {
  const { pathname } = useLocation();
  const pageLabel = PAGE_LABELS[pathname] ?? pathname.slice(1).replace(/-/g, " ").replace(/^\w/, (c) => c.toUpperCase());

  return (
    <div className="flex items-center gap-1.5 text-sm">
      <span className="text-text-secondary">
        {selectedNiche ? selectedNiche.displayName : "All Niches"}
      </span>
      {pathname !== "/" && (
        <>
          <ChevronRight className="size-3 text-text-ghost" />
          <span className="text-text-muted">{pageLabel}</span>
        </>
      )}
    </div>
  );
}

// 5 primary tabs shown in the mobile bottom tab bar
const MOBILE_TABS = [
  navItems[0], // Mission Control — Compass
  navItems[1], // Analytics — BarChart3
  navItems[7], // Learning — Brain
  navItems[8], // Engagement — MessageCircle
  { to: "/settings", icon: Settings, label: "Settings", shortcut: "" },
];

function useIsMobile(breakpoint = 640): boolean {
  const [isMobile, setIsMobile] = useState(() =>
    typeof window !== "undefined" ? window.innerWidth < breakpoint : false
  );

  useEffect(() => {
    function handleResize() {
      setIsMobile(window.innerWidth < breakpoint);
    }
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [breakpoint]);

  return isMobile;
}

function BottomTabBar() {
  return (
    <nav
      role="navigation"
      aria-label="Mobile navigation"
      className="fixed bottom-0 left-0 right-0 z-30 border-t border-border bg-bg-elevated"
    >
      <div className="flex items-stretch h-14">
        {MOBILE_TABS.map((item) => (
          <NavLink
            key={item.label}
            to={item.to}
            end={item.to === "/"}
            className={({ isActive }) =>
              cn(
                "flex-1 flex flex-col items-center justify-center gap-0.5 text-[10px] transition-colors",
                isActive
                  ? "text-text-primary"
                  : "text-text-muted hover:text-text-secondary",
              )
            }
            style={({ isActive }) =>
              isActive
                ? {
                    color: "var(--niche-current)",
                    transitionDuration: "var(--duration-base)",
                  }
                : { transitionDuration: "var(--duration-base)" }
            }
          >
            <item.icon className="h-5 w-5 shrink-0" />
            <span className="font-medium leading-none">{item.label.split(" ")[0]}</span>
          </NavLink>
        ))}
      </div>
    </nav>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const [activityOpen, setActivityOpen] = useState(false);
  const toggleActivity = useCallback(
    () => setActivityOpen((prev) => !prev),
    [],
  );
  const closeActivity = useCallback(() => setActivityOpen(false), []);
  const { selectedNiche } = useNicheStore();
  const openPalette = useCommandPaletteStore((s) => s.open);
  const isMobile = useIsMobile(640);

  return (
    <div className="flex h-screen overflow-hidden bg-bg-primary">
      <SkipLink />
      {/* Sidebar: hidden on mobile (<640px), visible otherwise */}
      {!isMobile && <Sidebar />}
      <div className="flex-1 flex flex-col overflow-hidden">
        <OfflineBanner />
        {/* Top bar — 48px */}
        <header
          role="banner"
          className="h-12 shrink-0 flex items-center justify-between px-6 border-b border-border bg-bg-surface"
        >
          {/* Left: Breadcrumb — niche > current page */}
          <Breadcrumb selectedNiche={selectedNiche} />

          {/* Right: Actions */}
          <div className="flex items-center gap-2">
            <button
              onClick={openPalette}
              aria-label="Open command palette"
              className="flex items-center gap-1.5 px-2 py-1 rounded-md text-xs text-text-muted hover:text-text-secondary hover:bg-bg-elevated transition-colors"
              style={{
                transitionDuration: "var(--duration-base)",
                border: "1px solid var(--border)",
              }}
            >
              <Command size={12} />
              <span className="font-mono">{"\u2318"}K</span>
            </button>
            <ActivityToggle onClick={toggleActivity} />
            <NotificationCenter />
            {/* Avatar + Logout */}
            <div className="relative group">
              <div
                role="img"
                aria-label="User avatar"
                className="h-7 w-7 rounded-full flex items-center justify-center text-xs font-medium cursor-pointer"
                style={{
                  backgroundColor: "color-mix(in srgb, var(--niche-current) 20%, transparent)",
                  color: "var(--niche-current)",
                }}
              >
                SA
              </div>
              <div className="absolute right-0 top-full mt-1 hidden group-hover:block bg-bg-surface border border-border rounded-md shadow-lg py-1 z-50 min-w-[120px]">
                <a
                  href="/logout"
                  className="block px-3 py-1.5 text-xs text-text-secondary hover:bg-bg-elevated hover:text-text-primary transition-colors no-underline"
                >
                  Sign out
                </a>
              </div>
            </div>
          </div>
        </header>

        {/* Main content — add bottom padding on mobile to clear the tab bar */}
        <main
          id="main-content"
          role="main"
          className="flex-1 overflow-auto bg-bg-primary"
        >
          <div className={cn("p-6 max-w-7xl mx-auto", isMobile && "pb-20")}>{children}</div>
        </main>
      </div>
      {/* Activity feed overlay */}
      <ActivityFeed open={activityOpen} onClose={closeActivity} />
      {/* Mobile bottom tab bar: only at <640px */}
      {isMobile && <BottomTabBar />}
    </div>
  );
}
