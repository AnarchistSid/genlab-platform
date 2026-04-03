import { ShieldCheck, Ban, Copy, CheckCircle2 } from "lucide-react";
import { StatRow } from "@/components/shared/stat-row";
import { useQualityStats } from "@/hooks/use-quality-stats";

export function ContentQuality() {
  const { data, isLoading } = useQualityStats();

  const passRate =
    data?.qc_total
      ? Math.round((data.qc_passed / data.qc_total) * 100) + "%"
      : "—";

  const stats = [
    { icon: ShieldCheck, label: "Hooks generated", value: isLoading ? undefined : String(data?.hooks_generated ?? "—"), color: "var(--color-blue)" },
    { icon: Ban, label: "QC rejected", value: isLoading ? undefined : String(data?.qc_failed ?? "—"), color: "var(--color-red)" },
    { icon: Copy, label: "Videos fixed", value: isLoading ? undefined : String(data?.videos_fixed ?? "—"), color: "var(--color-amber)" },
    { icon: CheckCircle2, label: "QC pass rate", value: isLoading ? undefined : passRate, color: "var(--color-green)" },
  ];

  return (
    <div className="bento-card">
      <h3 className="card-title">Content Quality</h3>

      <div className="flex flex-col gap-2">
        {stats.map((stat) => (
          <div key={stat.label}>
            {isLoading ? (
              <div className="flex items-center gap-2 py-1">
                <div className="size-4 rounded bg-bg-elevated animate-pulse" />
                <div className="h-3 w-24 rounded bg-bg-elevated animate-pulse" />
                <div className="ml-auto h-3 w-8 rounded bg-bg-elevated animate-pulse" />
              </div>
            ) : (
              <StatRow
                icon={stat.icon}
                label={stat.label}
                value={stat.value!}
                valueColor={stat.color}
              />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
