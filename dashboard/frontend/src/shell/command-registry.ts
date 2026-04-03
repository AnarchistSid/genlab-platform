import type { ComponentType } from "react";
import type { NavigateFunction } from "react-router-dom";
import type { NicheId, NicheDefinition } from "@/niches/registry";
import {
  Compass,
  Activity,
  BarChart3,
  Brain,
  Calendar,
  ClipboardCheck,
  Layers,
  MessageCircle,
  Settings,
  ShieldCheck,
  TrendingUp,
  Zap,
  CheckCircle,
  X,
  Focus,
  CircleDot,
} from "lucide-react";

export type CommandGroup =
  | "navigation"
  | "niche"
  | "actions"
  | "review";

export interface Command {
  id: string;
  group: CommandGroup;
  label: string;
  icon: ComponentType<{ className?: string }>;
  shortcut?: string;
  keywords?: string;
  disabled?: boolean;
  action: () => void;
}

export interface CommandRegistryDeps {
  navigate: NavigateFunction;
  niches: NicheDefinition[];
  setNiche: (id: NicheId | "all") => void;
  triggerPipeline: () => void;
  selectedCount: number;
  approveAll: () => void;
  clearSelection: () => void;
  openFocusReview: () => void;
}

export function buildCommandRegistry(deps: CommandRegistryDeps): Command[] {
  const {
    navigate,
    niches,
    setNiche,
    triggerPipeline,
    selectedCount,
    approveAll,
    clearSelection,
    openFocusReview,
  } = deps;

  const commands: Command[] = [
    // ── Navigation ──────────────────────────────────
    {
      id: "nav:mission-control",
      group: "navigation",
      label: "Mission Control",
      icon: Compass,
      shortcut: "G M",
      keywords: "home dashboard overview",
      action: () => navigate("/"),
    },
    {
      id: "nav:focus-review",
      group: "navigation",
      label: "Focus Review",
      icon: Focus,
      shortcut: "G F",
      keywords: "review approve reject tinder",
      action: () => navigate("/focus-review"),
    },
    {
      id: "nav:pipeline",
      group: "navigation",
      label: "Pipeline",
      icon: Activity,
      shortcut: "G P",
      keywords: "pipeline runs stages",
      action: () => navigate("/pipeline"),
    },
    {
      id: "nav:analytics",
      group: "navigation",
      label: "Analytics",
      icon: BarChart3,
      shortcut: "G A",
      keywords: "analytics stats metrics performance",
      action: () => navigate("/analytics"),
    },
    {
      id: "nav:schedule",
      group: "navigation",
      label: "Schedule",
      icon: Calendar,
      shortcut: "G S",
      keywords: "schedule calendar queue",
      action: () => navigate("/schedule"),
    },
    {
      id: "nav:stories",
      group: "navigation",
      label: "Stories",
      icon: Layers,
      shortcut: "G T",
      keywords: "stories news items sources",
      action: () => navigate("/stories"),
    },
    {
      id: "nav:content",
      group: "navigation",
      label: "Go to Content Review",
      icon: ClipboardCheck,
      shortcut: "G C",
      keywords: "content review approve reject blueprints",
      action: () => navigate("/content"),
    },
    {
      id: "nav:learning",
      group: "navigation",
      label: "Go to Learning",
      icon: Brain,
      shortcut: "G L",
      keywords: "learning bandit thompson sampling performance",
      action: () => navigate("/learning"),
    },
    {
      id: "nav:engagement",
      group: "navigation",
      label: "Go to Engagement",
      icon: MessageCircle,
      shortcut: "G E",
      keywords: "engagement replies comments social",
      action: () => navigate("/engagement"),
    },
    {
      id: "nav:health",
      group: "navigation",
      label: "Go to System Health",
      icon: ShieldCheck,
      shortcut: "G H",
      keywords: "health system status tokens credentials",
      action: () => navigate("/health"),
    },
    {
      id: "nav:settings",
      group: "navigation",
      label: "Settings",
      icon: Settings,
      shortcut: "G ,",
      keywords: "settings config preferences",
      action: () => navigate("/settings"),
    },

    // ── Niche switching ─────────────────────────────
    {
      id: "niche:all",
      group: "niche",
      label: "Switch to All Niches",
      icon: CircleDot,
      keywords: "niche all global",
      action: () => setNiche("all"),
    },
    ...niches.map((n) => ({
      id: `niche:${n.id}` as const,
      group: "niche" as CommandGroup,
      label: `Switch to ${n.displayName}`,
      icon: CircleDot,
      keywords: `niche ${n.tagline.toLowerCase()} ${n.displayName.toLowerCase()}`,
      disabled: !n.isActive,
      action: () => {
        if (n.isActive) setNiche(n.id);
      },
    })),

    // ── Actions ─────────────────────────────────────
    {
      id: "action:trigger-pipeline",
      group: "actions",
      label: "Trigger Pipeline",
      icon: Zap,
      keywords: "run pipeline trigger start execute",
      action: triggerPipeline,
    },
    {
      id: "action:focus-review",
      group: "actions",
      label: "Open Focus Review",
      icon: Focus,
      keywords: "review tinder swipe",
      action: openFocusReview,
    },
    {
      id: "action:show-top-posts",
      group: "actions",
      label: "Show Top Posts",
      icon: TrendingUp,
      keywords: "top posts analytics best performing",
      action: () => navigate("/analytics"),
    },

    // ── Review ──────────────────────────────────────
    {
      id: "review:approve-all",
      group: "review",
      label: `Approve All Selected (${selectedCount})`,
      icon: CheckCircle,
      keywords: "approve accept batch",
      disabled: selectedCount === 0,
      action: approveAll,
    },
    {
      id: "review:clear-selection",
      group: "review",
      label: "Clear Selection",
      icon: X,
      keywords: "clear deselect reset",
      action: clearSelection,
    },
  ];

  return commands.filter((c) => !c.disabled);
}
