import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogHeader,
} from "@/components/ui/dialog";

interface Shortcut {
  keys: string[];
  description: string;
}

const navigationShortcuts: Shortcut[] = [
  { keys: ["G", "P"], description: "Go to Pipeline" },
  { keys: ["G", "B"], description: "Go to Blueprints" },
  { keys: ["G", "S"], description: "Go to Schedule" },
  { keys: ["G", "A"], description: "Go to Analytics" },
  { keys: ["G", "T"], description: "Go to Stories" },
  { keys: ["G", "R"], description: "Go to Runs" },
];

const isMac = typeof navigator !== "undefined" && /Mac|iPod|iPhone|iPad/.test(navigator.userAgent);
const modKey = isMac ? "\u2318" : "Ctrl";

const actionShortcuts: Shortcut[] = [
  { keys: [modKey, "K"], description: "Command Palette" },
  { keys: [modKey, "/"], description: "This help" },
  { keys: ["Esc"], description: "Close panel / dialog" },
];

const reviewShortcuts: Shortcut[] = [
  { keys: ["J"], description: "Move focus down" },
  { keys: ["K"], description: "Move focus up" },
  { keys: ["Enter"], description: "Open focused blueprint" },
  { keys: ["X"], description: "Toggle select focused" },
  { keys: ["A"], description: "Approve selected" },
  { keys: ["R"], description: "Reject selected" },
  { keys: ["V"], description: "Revise selected" },
  { keys: ["Space"], description: "Toggle selection" },
  { keys: ["Shift", "Click"], description: "Range select" },
];

function Kbd({ children }: { children: string }) {
  return (
    <kbd className="inline-flex items-center justify-center min-w-[24px] h-6 px-2 py-0.5 bg-bg-elevated border border-border rounded font-mono text-xs text-text-secondary">
      {children}
    </kbd>
  );
}

function ShortcutRow({ shortcut }: { shortcut: Shortcut }) {
  return (
    <div className="flex items-center justify-between py-1.5">
      <span className="text-sm text-text-secondary">{shortcut.description}</span>
      <div className="flex items-center gap-1">
        {shortcut.keys.map((key, i) => (
          <span key={i} className="flex items-center gap-1">
            {i > 0 && <span className="text-xs text-text-muted">+</span>}
            <Kbd>{key}</Kbd>
          </span>
        ))}
      </div>
    </div>
  );
}

function ShortcutGroup({
  title,
  shortcuts,
}: {
  title: string;
  shortcuts: Shortcut[];
}) {
  return (
    <div>
      <h3 className="text-xs font-semibold uppercase tracking-wider text-text-muted mb-2">
        {title}
      </h3>
      <div className="space-y-0.5">
        {shortcuts.map((s, i) => (
          <ShortcutRow key={i} shortcut={s} />
        ))}
      </div>
    </div>
  );
}

export function KeyboardHelp() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    function handleToggle() {
      setOpen((prev) => !prev);
    }
    function handleClose() {
      setOpen(false);
    }

    window.addEventListener("toggle-keyboard-help", handleToggle);
    window.addEventListener("toggle-keyboard-help-close", handleClose);
    return () => {
      window.removeEventListener("toggle-keyboard-help", handleToggle);
      window.removeEventListener("toggle-keyboard-help-close", handleClose);
    };
  }, []);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="sm:max-w-md bg-bg-primary border-border">
        <DialogHeader>
          <DialogTitle className="text-text-primary">
            Keyboard Shortcuts
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-5 mt-2">
          <ShortcutGroup title="Navigation" shortcuts={navigationShortcuts} />
          <div className="border-t border-border" />
          <ShortcutGroup title="Actions" shortcuts={actionShortcuts} />
          <div className="border-t border-border" />
          <ShortcutGroup
            title="Review (Blueprints page)"
            shortcuts={reviewShortcuts}
          />
        </div>
      </DialogContent>
    </Dialog>
  );
}
