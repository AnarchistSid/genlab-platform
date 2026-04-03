interface Props {
  level: "safe" | "review" | "toxic";
}

const STYLES = {
  safe: { bg: "rgba(34,197,94,0.12)", color: "#22c55e" },
  review: { bg: "rgba(245,158,11,0.12)", color: "#f59e0b" },
  toxic: { bg: "rgba(239,68,68,0.12)", color: "#ef4444" },
};

export function ToxicityBadge({ level }: Props) {
  const s = STYLES[level];
  return (
    <span
      style={{
        fontSize: 8,
        padding: "1px 5px",
        borderRadius: 3,
        background: s.bg,
        color: s.color,
        fontWeight: 500,
      }}
    >
      {level}
    </span>
  );
}
