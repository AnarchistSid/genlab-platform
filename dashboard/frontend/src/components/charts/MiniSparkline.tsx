import { useMemo } from "react";

interface Props {
  data: number[];
  color: string;
  width?: number;
  height?: number;
}

export function MiniSparkline({ data, color, width = 100, height = 24 }: Props) {
  const gradientId = useMemo(
    () => `spark-${color.replace(/[^a-z0-9]/gi, "")}-${Math.random().toString(36).slice(2, 6)}`,
    [color],
  );

  const path = useMemo(() => {
    if (data.length < 2) return "";
    const max = Math.max(...data, 1);
    const step = width / (data.length - 1);
    return data
      .map((v, i) => `${i === 0 ? "M" : "L"} ${(i * step).toFixed(1)} ${(height - (v / max) * height * 0.9).toFixed(1)}`)
      .join(" ");
  }, [data, width, height]);

  if (!path) return null;

  return (
    <svg width={width} height={height} style={{ display: "block" }}>
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.3" />
          <stop offset="100%" stopColor={color} stopOpacity="0.02" />
        </linearGradient>
      </defs>
      <path
        d={`${path} L ${width} ${height} L 0 ${height} Z`}
        fill={`url(#${gradientId})`}
      />
      <path
        d={path}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
