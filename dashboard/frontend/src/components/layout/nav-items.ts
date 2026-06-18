/**
 * Sidebar navigation items.
 *
 * Extracted from ``sidebar.tsx`` so HMR can hot-replace the Sidebar
 * component without losing state. (react-refresh/only-export-components
 * fires when a component file also exports non-component values.)
 */
import {
  Activity,
  BarChart3,
  Brain,
  Calendar,
  ClipboardCheck,
  Compass,
  ListChecks,
  MessageCircle,
  Settings,
  ShieldCheck,
  TrendingUp,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

export interface NavItem {
  to: string;
  icon: LucideIcon;
  label: string;
  shortcut: string;
  liveDot?: boolean;
}

export const navItems: NavItem[] = [
  { to: "/", icon: Compass, label: "Mission Control", shortcut: "G M" },
  { to: "/analytics", icon: BarChart3, label: "Analytics", shortcut: "G A" },
  {
    to: "/pipeline",
    icon: Activity,
    label: "Pipeline",
    shortcut: "G P",
    liveDot: true,
  },
  { to: "/schedule", icon: Calendar, label: "Schedule", shortcut: "G S" },
  {
    to: "/queue",
    icon: ListChecks,
    label: "Publishing Queue",
    shortcut: "G Q",
  },
  { to: "/monetisation", icon: TrendingUp, label: "Monetisation", shortcut: "G $" },
  {
    to: "/content",
    icon: ClipboardCheck,
    label: "Content Review",
    shortcut: "G C",
  },
  { to: "/learning", icon: Brain, label: "Learning", shortcut: "G L" },
  {
    to: "/engagement",
    icon: MessageCircle,
    label: "Engagement",
    shortcut: "G E",
  },
  { to: "/health", icon: ShieldCheck, label: "System Health", shortcut: "G H" },
];

export const bottomNavItems: NavItem[] = [
  { to: "/settings", icon: Settings, label: "Settings", shortcut: "G ," },
];
