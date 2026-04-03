import { useCallback, useMemo, useEffect } from "react";
import { Command } from "cmdk";
import { useNavigate } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { useTriggerPipeline } from "@/hooks/use-pipeline";
import { useBatchReview } from "@/hooks/use-blueprints";
import { useSelectionStore } from "@/stores/selection-store";
import { useCommandPaletteStore } from "@/stores/command-palette-store";
import { useNicheStore } from "@/stores/niche-store";
import { getAllNiches } from "@/niches/registry";
import {
  buildCommandRegistry,
  type Command as Cmd,
  type CommandGroup,
} from "@/shell/command-registry";

const RECENT_KEY = "gl-recent-commands";
const MAX_RECENT = 5;

const GROUP_LABELS: Record<CommandGroup, string> = {
  navigation: "Navigation",
  niche: "Switch Niche",
  actions: "Actions",
  review: "Review",
};

const GROUP_ORDER: CommandGroup[] = ["navigation", "actions", "niche", "review"];

function getRecentIds(): string[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.filter((r): r is string => typeof r === "string")
      : [];
  } catch {
    return [];
  }
}

function pushRecentId(id: string) {
  const recent = getRecentIds().filter((r) => r !== id);
  recent.unshift(id);
  localStorage.setItem(RECENT_KEY, JSON.stringify(recent.slice(0, MAX_RECENT)));
}

const overlayVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1 },
};

const panelVariants = {
  hidden: { opacity: 0, scale: 0.96, y: -8 },
  visible: {
    opacity: 1,
    scale: 1,
    y: 0,
    transition: { type: "spring" as const, damping: 28, stiffness: 380 },
  },
  exit: { opacity: 0, scale: 0.96, y: -8, transition: { duration: 0.12 } },
};

export function CommandPalette() {
  const { isOpen, close } = useCommandPaletteStore();
  const navigate = useNavigate();
  const triggerPipeline = useTriggerPipeline();
  const batchReview = useBatchReview();
  const selectedIds = useSelectionStore((s) => s.selectedIds);
  const clearAll = useSelectionStore((s) => s.clearAll);
  const setNiche = useNicheStore((s) => s.setNiche);
  const niches = useMemo(() => getAllNiches(), []);

  // Build registry
  const commands = useMemo(
    () =>
      buildCommandRegistry({
        navigate,
        niches,
        setNiche,
        triggerPipeline: () => triggerPipeline.mutate({}),
        selectedCount: selectedIds.size,
        approveAll: () => {
          if (selectedIds.size > 0)
            batchReview.mutate({ ids: [...selectedIds], action: "approved" });
        },
        clearSelection: clearAll,
        openFocusReview: () => navigate("/focus-review"),
      }),
    [navigate, niches, setNiche, triggerPipeline, batchReview, selectedIds, clearAll],
  );

  const recentIds = getRecentIds();
  const recentCommands = recentIds
    .map((id) => commands.find((c) => c.id === id))
    .filter((c): c is Cmd => c !== undefined);

  // Group commands by their group
  const grouped = useMemo(() => {
    const map = new Map<CommandGroup, Cmd[]>();
    for (const cmd of commands) {
      const arr = map.get(cmd.group) ?? [];
      arr.push(cmd);
      map.set(cmd.group, arr);
    }
    return map;
  }, [commands]);

  const handleSelect = useCallback(
    (cmd: Cmd) => {
      pushRecentId(cmd.id);
      cmd.action();
      close();
    },
    [close],
  );

  // Close on Escape key (backup — cmdk handles this too)
  useEffect(() => {
    if (!isOpen) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") close();
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [isOpen, close]);

  return (
    <AnimatePresence>
      {isOpen && (
        <div className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh]">
          {/* Backdrop */}
          <motion.div
            className="absolute inset-0"
            style={{ backgroundColor: "rgba(0, 0, 0, 0.5)" }}
            variants={overlayVariants}
            initial="hidden"
            animate="visible"
            exit="hidden"
            transition={{ duration: 0.15 }}
            onClick={close}
          />

          {/* Panel */}
          <motion.div
            className="relative w-full max-w-lg rounded-xl border overflow-hidden"
            style={{
              background: "rgba(15, 15, 23, 0.92)",
              backdropFilter: "blur(20px) saturate(180%)",
              WebkitBackdropFilter: "blur(20px) saturate(180%)",
              borderColor: "rgba(255, 255, 255, 0.08)",
              boxShadow:
                "0 25px 60px -12px rgba(0, 0, 0, 0.5), 0 0 0 1px rgba(255, 255, 255, 0.05) inset",
            }}
            variants={panelVariants}
            initial="hidden"
            animate="visible"
            exit="exit"
          >
            <Command loop label="Command palette">
              {/* Search input */}
              <div
                className="flex items-center gap-3 px-4 border-b"
                style={{ borderColor: "rgba(255, 255, 255, 0.06)" }}
              >
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="shrink-0 text-text-muted"
                >
                  <circle cx="11" cy="11" r="8" />
                  <line x1="21" y1="21" x2="16.65" y2="16.65" />
                </svg>
                <Command.Input
                  placeholder="Type a command or search..."
                  className="flex-1 h-12 bg-transparent border-none text-sm text-text-primary placeholder:text-text-ghost focus:outline-none"
                  autoFocus
                />
                <kbd
                  className="text-[10px] font-mono px-1.5 py-0.5 rounded border text-text-muted"
                  style={{
                    borderColor: "rgba(255, 255, 255, 0.08)",
                    background: "rgba(255, 255, 255, 0.04)",
                  }}
                >
                  ESC
                </kbd>
              </div>

              <Command.List className="max-h-[min(60vh,520px)] overflow-y-auto p-1.5">
                <Command.Empty className="px-4 py-8 text-sm text-center text-text-ghost">
                  No commands found.
                </Command.Empty>

                {/* Recent */}
                {recentCommands.length > 0 && (
                  <Command.Group heading="Recent">
                    {recentCommands.map((cmd) => (
                      <CommandRow
                        key={`recent-${cmd.id}`}
                        cmd={cmd}
                        valuePrefix="recent "
                        onSelect={handleSelect}
                      />
                    ))}
                  </Command.Group>
                )}

                {/* Grouped commands */}
                {GROUP_ORDER.map((group) => {
                  const items = grouped.get(group);
                  if (!items || items.length === 0) return null;
                  return (
                    <Command.Group key={group} heading={GROUP_LABELS[group]}>
                      {items.map((cmd) => (
                        <CommandRow
                          key={cmd.id}
                          cmd={cmd}
                          onSelect={handleSelect}
                        />
                      ))}
                    </Command.Group>
                  );
                })}
              </Command.List>

              {/* Footer */}
              <div
                className="flex items-center justify-between px-4 py-2 border-t text-[10px] text-text-muted"
                style={{
                  borderColor: "rgba(255, 255, 255, 0.06)",
                }}
              >
                <div className="flex items-center gap-3">
                  <span className="flex items-center gap-1">
                    <kbd className="font-mono px-1 py-px rounded" style={{ background: "rgba(255,255,255,0.06)" }}>
                      {"\u2191\u2193"}
                    </kbd>
                    navigate
                  </span>
                  <span className="flex items-center gap-1">
                    <kbd className="font-mono px-1 py-px rounded" style={{ background: "rgba(255,255,255,0.06)" }}>
                      {"\u21B5"}
                    </kbd>
                    select
                  </span>
                </div>
                <span>
                  {"\u2318"}K to toggle
                </span>
              </div>
            </Command>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}

// ── Command row ─────────────────────────────────
function CommandRow({
  cmd,
  onSelect,
  valuePrefix = "",
}: {
  cmd: Cmd;
  onSelect: (cmd: Cmd) => void;
  valuePrefix?: string;
}) {
  const Icon = cmd.icon;
  const value = `${valuePrefix}${cmd.label} ${cmd.keywords ?? ""}`;

  return (
    <Command.Item
      value={value}
      onSelect={() => onSelect(cmd)}
      className="flex items-center gap-3 px-3 py-2.5 mx-0.5 rounded-lg text-sm cursor-pointer transition-colors data-[selected=true]:text-text-primary text-text-secondary"
      data-selected-style
    >
      <Icon className="h-4 w-4 shrink-0 opacity-60" />
      <span className="flex-1 truncate">{cmd.label}</span>
      {cmd.shortcut && (
        <kbd
          className="text-[10px] font-mono px-1.5 py-0.5 rounded border text-text-muted"
          style={{
            borderColor: "rgba(255, 255, 255, 0.08)",
            background: "rgba(255, 255, 255, 0.04)",
          }}
        >
          {cmd.shortcut}
        </kbd>
      )}
    </Command.Item>
  );
}
