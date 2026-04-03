import { motion } from "framer-motion";
import { AreaChart, Area, ResponsiveContainer } from "recharts";
import { useCountUp } from "@/hooks/use-count-up";
import { formatCompact } from "@/lib/format";

interface KPICardProps {
  label: string;
  value: number | null;
  previousValue?: number | null;
  format?: "number" | "percent" | "currency";
  sparklineData?: { value: number }[];
  accentColor?: string;
  loading?: boolean;
  index?: number;
}

const cardVariants = {
  hidden: { opacity: 0, y: 8 },
  visible: { opacity: 1, y: 0 },
};

export function AnimatedKPICard({
  label,
  value,
  previousValue,
  format = "number",
  sparklineData,
  accentColor = "var(--color-blue)",
  loading,
  index = 0,
}: KPICardProps) {
  const formatter =
    format === "percent"
      ? (n: number) => `${n.toFixed(1)}%`
      : format === "currency"
        ? (n: number) => `₹${formatCompact(n)}`
        : formatCompact;

  const display = useCountUp(value ?? 0, { formatter });

  const delta =
    previousValue && previousValue > 0 && value != null
      ? ((value - previousValue) / previousValue) * 100
      : null;
  const isPositive = (delta ?? 0) >= 0;

  // Stable gradient ID from label
  const gradId = `spark-${label.replace(/\s+/g, "-").toLowerCase()}`;

  if (loading) return <KPISkeleton />;

  return (
    <motion.div
      className="bg-bg-surface border border-border rounded-lg p-5 flex flex-col gap-1"
      variants={cardVariants}
      transition={{
        delay: index * 0.08,
        duration: 0.35,
        ease: [0.16, 1, 0.3, 1],
      }}
    >
      <span className="label-caps">{label}</span>
      <div className="flex items-baseline gap-3 mt-1">
        <span className="text-2xl font-bold text-text-primary tracking-tight tabular-nums">
          {value != null ? display : "\u2014"}
        </span>
        {delta != null && (
          <span
            className="text-xs font-medium"
            style={{ color: isPositive ? "var(--color-green)" : "var(--color-red)" }}
          >
            {isPositive ? "↑" : "↓"} {Math.abs(delta).toFixed(1)}%
          </span>
        )}
      </div>

      {sparklineData && sparklineData.length > 1 && (
        <div className="mt-3 -mx-1" style={{ height: 36 }}>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={sparklineData}>
              <defs>
                <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={accentColor} stopOpacity={0.25} />
                  <stop offset="100%" stopColor={accentColor} stopOpacity={0} />
                </linearGradient>
              </defs>
              <Area
                type="monotone"
                dataKey="value"
                stroke={accentColor}
                strokeWidth={1.5}
                fill={`url(#${gradId})`}
                dot={false}
                isAnimationActive
                animationDuration={800}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </motion.div>
  );
}

function KPISkeleton() {
  return (
    <div className="bg-bg-surface border border-border rounded-lg p-5 flex flex-col gap-3">
      <div className="shimmer" style={{ width: "40%", height: 12, borderRadius: 4 }} />
      <div className="shimmer" style={{ width: "60%", height: 28, borderRadius: 4 }} />
      <div className="shimmer" style={{ width: "100%", height: 36, borderRadius: 4 }} />
    </div>
  );
}
