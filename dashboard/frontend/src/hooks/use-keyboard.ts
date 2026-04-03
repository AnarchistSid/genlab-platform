import { useEffect, useRef, useCallback } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { useReviewBlueprint } from "@/hooks/use-blueprints";
import { useSelectionStore } from "@/stores/selection-store";
import { useCommandPaletteStore } from "@/stores/command-palette-store";

/**
 * Returns true if the active element is an input-like element
 * where keyboard shortcuts should be suppressed.
 */
function isTyping(): boolean {
  const el = document.activeElement;
  if (!el) return false;
  const tag = el.tagName.toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return true;
  if ((el as HTMLElement).isContentEditable) return true;
  return false;
}

const NAV_MAP: Record<string, string> = {
  p: "/",
  b: "/blueprints",
  s: "/schedule",
  a: "/analytics",
  t: "/stories",
  r: "/runs",
  l: "/learning",
  e: "/engagement",
  c: "/content",
  h: "/health",
};

/**
 * Global keyboard shortcut handler.
 * Must be called inside a react-router context (needs useNavigate).
 */
export function useKeyboardShortcuts(): void {
  const navigate = useNavigate();
  const location = useLocation();
  const reviewMutation = useReviewBlueprint();
  const toggle = useSelectionStore((s) => s.toggle);
  const selectedIds = useSelectionStore((s) => s.selectedIds);
  const clearAll = useSelectionStore((s) => s.clearAll);
  const togglePalette = useCommandPaletteStore((s) => s.toggle);
  const closePalette = useCommandPaletteStore((s) => s.close);

  // Clear selections when navigating to a different route
  useEffect(() => {
    clearAll();
  }, [location.pathname, clearAll]);

  // Stabilize mutation.mutate via ref so the keydown effect doesn't re-register every render
  const reviewRef = useRef(reviewMutation.mutate);
  reviewRef.current = reviewMutation.mutate;

  // "g" prefix state for two-key navigation combos
  const gPrefixRef = useRef(false);
  const gTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearGPrefix = useCallback(() => {
    gPrefixRef.current = false;
    if (gTimerRef.current) {
      clearTimeout(gTimerRef.current);
      gTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent): void {
      const metaOrCtrl = e.metaKey || e.ctrlKey;

      // Cmd/Ctrl+K — toggle command palette
      if (metaOrCtrl && e.key === "k") {
        e.preventDefault();
        togglePalette();
        return;
      }

      // Cmd/Ctrl+/ — toggle keyboard help
      if (metaOrCtrl && e.key === "/") {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent("toggle-keyboard-help"));
        return;
      }

      // Skip remaining shortcuts when typing in an input
      if (isTyping()) return;

      // Escape — close any open panel/dialog
      if (e.key === "Escape") {
        closePalette();
        window.dispatchEvent(new CustomEvent("toggle-keyboard-help-close"));
        return;
      }

      // Two-key "g" prefix navigation
      if (e.key === "g" && !metaOrCtrl && !e.altKey && !e.shiftKey) {
        if (!gPrefixRef.current) {
          gPrefixRef.current = true;
          gTimerRef.current = setTimeout(clearGPrefix, 500);
          return;
        }
      }

      if (gPrefixRef.current) {
        const target = NAV_MAP[e.key];
        if (target) {
          e.preventDefault();
          navigate(target);
          clearGPrefix();
          return;
        }
        // If the second key doesn't match, clear the prefix
        clearGPrefix();
      }

      // Review shortcuts — only on /blueprints route
      const onBlueprints = location.pathname === "/blueprints" || location.pathname.startsWith("/blueprints/");
      if (onBlueprints) {
        // Get the first selected ID for single-item review actions
        const firstSelectedId = selectedIds.size > 0 ? [...selectedIds][0] : null;

        if (e.key === "a" && firstSelectedId) {
          e.preventDefault();
          reviewRef.current({
            id: firstSelectedId,
            body: { action: "approved" },
          });
          return;
        }

        if (e.key === "r" && firstSelectedId) {
          e.preventDefault();
          reviewRef.current({
            id: firstSelectedId,
            body: { action: "rejected" },
          });
          return;
        }

        if (e.key === "v" && firstSelectedId) {
          e.preventDefault();
          reviewRef.current({
            id: firstSelectedId,
            body: { action: "revised" },
          });
          return;
        }

        if (e.key === " " && firstSelectedId) {
          e.preventDefault();
          toggle(firstSelectedId);
          return;
        }
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      clearGPrefix();
    };
  }, [navigate, location.pathname, selectedIds, toggle, clearGPrefix, togglePalette, closePalette]);
}
