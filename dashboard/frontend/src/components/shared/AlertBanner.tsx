import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { X } from "lucide-react";

interface Props {
  message: string;
  type?: "error" | "warning" | "info";
  dismissable?: boolean;
  link?: string;
}

const COLORS = {
  error: { bg: "rgba(239,68,68,0.06)", border: "rgba(239,68,68,0.2)", icon: "#ef4444" },
  warning: { bg: "rgba(245,158,11,0.06)", border: "rgba(245,158,11,0.2)", icon: "#f59e0b" },
  info: { bg: "rgba(59,130,246,0.06)", border: "rgba(59,130,246,0.2)", icon: "#3b82f6" },
};

export function AlertBanner({ message, type = "error", dismissable = true, link }: Props) {
  const [dismissed, setDismissed] = useState(false);
  const navigate = useNavigate();
  if (dismissed) return null;

  const c = COLORS[type];
  return (
    <div
      className={`flex items-center gap-2 rounded-lg px-3.5 py-2 text-[11px] mb-4 animate-in fade-in duration-300${link ? " cursor-pointer" : ""}`}
      style={{
        background: c.bg,
        border: `1px solid ${c.border}`,
      }}
      onClick={link ? () => navigate(link) : undefined}
    >
      <span className="font-bold" style={{ color: c.icon }}>
        {type === "error" ? "!" : type === "warning" ? "⚠" : "ℹ"}
      </span>
      <span className="flex-1">{message}</span>
      {dismissable && (
        <button
          onClick={() => setDismissed(true)}
          className="bg-transparent border-none text-text-ghost cursor-pointer p-0.5"
        >
          <X size={14} />
        </button>
      )}
    </div>
  );
}
