import { formatCompact } from "@/lib/format";

interface ChartTooltipProps {
  active?: boolean;
  payload?: Array<{ name: string; value: number; color?: string }>;
  label?: string;
  formatter?: (value: number) => string;
}

export function ChartTooltip({ active, payload, label, formatter }: ChartTooltipProps) {
  if (!active || !payload?.length) return null;
  const fmt = formatter ?? formatCompact;

  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-date">{label ?? ""}</div>
      {payload.map((entry) => (
        <div key={entry.name} className="chart-tooltip-row">
          <span style={{ color: entry.color }}>{entry.name}</span>
          <span className="chart-tooltip-value">{fmt(entry.value)}</span>
        </div>
      ))}
    </div>
  );
}
